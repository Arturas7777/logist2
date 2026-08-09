"""
AI-извлечение данных из отсканированных документов (PDF / JPG / PNG).

Поддерживает два типа документов:
  * TITLE         — US car title (физический титул автомобиля).
  * DOCK_RECEIPT  — Dock Receipt (US shipping document от Atlantic Express и пр.).

Пайплайн распознавания VIN (главный источник ошибок раньше):

  1. Первый проход: все страницы документа → Claude Vision → JSON по схеме.
  2. Второй проход: страницы нарезаются на перекрывающиеся тайлы (Anthropic
     ужимает изображения до ~1568 px по длинной стороне, поэтому «просто
     повысить DPI» не работает — а 4 тайла дают ~2x эффективное разрешение),
     и Claude читает VIN ПОСИМВОЛЬНО. Расхождение с первым проходом —
     сигнал низкой уверенности.
  3. Детерминированная пост-обработка (core.services.vin_corrector):
     нормализация запрещённых I/O/Q, автокоррекция по контрольной цифре,
     валидация NHTSA, итоговый уровень уверенности high/medium/low.

Важно про рендер: Claude API режет картинки больше 5 MB (base64), а всё,
что длиннее 1568 px по большой стороне, сервер сам уменьшает. Поэтому
страницы кодируются с long side ≤ 1568 и JPEG-подгонкой под лимит.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Рендер документов под Claude Vision ───────────────────────────────────

# Anthropic limit: 5 MB на одно изображение (поле base64). Сама base64-строка
# в ~1.34 раза больше исходных байт, поэтому raw держим заметно ниже.
_MAX_RAW_IMAGE_BYTES = int(3.6 * 1024 * 1024)  # ≈ 4.8 MB после base64

# Anthropic сам уменьшает изображения до ~1568 px по длинной стороне —
# отправлять больше бессмысленно (только трафик и токены).
_MAX_LONG_SIDE = 1568

# DPI рендера PDF-страниц. Для полной страницы хватает 150 (всё равно будет
# ужато до 1568 px); тайлы для посимвольного чтения VIN рендерим в 300 —
# после нарезки на 4 части каждая укладывается в лимит почти без потерь.
_FULL_PAGE_DPI = 150
_TILE_PAGE_DPI = 300

# Перекрытие тайлов, чтобы VIN на границе не разрезало пополам.
_TILE_OVERLAP = 0.14

# Сколько страниц документа максимум отправляем на верификацию VIN
# (титул — это 1-2 страницы; ограничение страхует от гигантских PDF).
_MAX_VERIFY_PAGES = 2

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SUPPORTED_EXTENSIONS = {".pdf", *_IMAGE_EXTENSIONS}


def _import_pillow():
    try:
        from PIL import Image
    except ImportError:
        logger.error("Pillow не установлен. Запустите: pip install Pillow")
        raise
    return Image


def _load_page_images(path: str, *, dpi: int) -> list:
    """Возвращает список PIL.Image страниц документа.

    PDF рендерится через PyMuPDF с заданным dpi; JPG/PNG открываются напрямую
    (dpi для них не имеет смысла — берём как есть).
    """
    Image = _import_pillow()
    ext = os.path.splitext(path)[1].lower()

    if ext in _IMAGE_EXTENSIONS:
        img = Image.open(path)
        return [img.convert("RGB")]

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF не установлен. Запустите: pip install pymupdf")
        raise

    pages = []
    doc = fitz.open(path)
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    finally:
        doc.close()
    return pages


def _encode_jpeg_under_limit(img, *, max_side: int = _MAX_LONG_SIDE) -> tuple[str, str]:
    """PIL.Image → ``("image/jpeg", base64)`` с гарантией лимита Anthropic.

    Сначала ужимаем до max_side по длинной стороне, затем понижаем JPEG
    quality, пока raw-размер не уложится в ``_MAX_RAW_IMAGE_BYTES``.
    """
    Image = _import_pillow()
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)

    buf = io.BytesIO()
    for quality in (88, 80, 72, 65):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= _MAX_RAW_IMAGE_BYTES:
            break
    else:
        logger.warning("scan_extractor: image didn't fit limit even at q=65 (%d bytes)", buf.tell())
    return ("image/jpeg", base64.b64encode(buf.getvalue()).decode("utf-8"))


def render_document_images(path: str) -> list[tuple[str, str]]:
    """Полные страницы документа как ``(media_type, base64)`` — первый проход."""
    out = []
    for idx, img in enumerate(_load_page_images(path, dpi=_FULL_PAGE_DPI)):
        payload = _encode_jpeg_under_limit(img)
        out.append(payload)
        logger.info(
            "scan_extractor: rendered page %d of %s (%d bytes raw)",
            idx,
            os.path.basename(path),
            len(payload[1]) * 3 // 4,
        )
    return out


def _tile_image(img, *, overlap: float = _TILE_OVERLAP) -> list:
    """Режет изображение на 4 перекрывающихся квадранта (2×2)."""
    w, h = img.size
    tw = int(w * (0.5 + overlap / 2))
    th = int(h * (0.5 + overlap / 2))
    boxes = [
        (0, 0, tw, th),
        (w - tw, 0, w, th),
        (0, h - th, tw, h),
        (w - tw, h - th, w, h),
    ]
    return [img.crop(box) for box in boxes]


def render_vin_verification_images(path: str) -> list[tuple[str, str]]:
    """Тайлы страниц крупным планом для посимвольного чтения VIN."""
    out: list[tuple[str, str]] = []
    pages = _load_page_images(path, dpi=_TILE_PAGE_DPI)
    for img in pages[:_MAX_VERIFY_PAGES]:
        for tile in _tile_image(img):
            out.append(_encode_jpeg_under_limit(tile))
    return out


# ── Промпты ────────────────────────────────────────────────────────────────

_VIN_OCR_HINTS = """Про VIN:
- VIN всегда ровно 17 символов.
- В VIN НИКОГДА не бывает букв I, O, Q — если тебе видится I/O/Q,
  это 1/0/0.
- Типичные путаницы на сканах: 0/D, 1/L, 5/S, 8/B, 2/Z, 6/G, 4/A.
  Вглядывайся в каждый символ.
- Извлекай ТОЧНО как видишь, НЕ выдумывай. Если символ не читается —
  лучше верни null вместо всего VIN, чем угадывай."""

TITLE_SCHEMA = """
{
  "vins": ["VIN1"],
  "year": 2020,
  "make": "BMW",
  "model": "X5",
  "title_number": "12345678",
  "title_state": "TX",
  "title_issue_date": "YYYY-MM-DD",
  "color": "BLACK",
  "odometer": 45123,
  "owner_name": "John Doe",
  "lien_holder": "Bank of America",
  "notes": "что-то важное"
}
"""

TITLE_PROMPT = f"""Ты — система обработки физических US car titles (титулов автомобилей).
Тебе дают отсканированный документ одного титула. Извлеки структурированные данные.

{_VIN_OCR_HINTS}

Правила:
- Если на странице несколько титулов — верни ВСЕ VIN-ы в массиве "vins".
  Обычно один скан = один титул, но бывают исключения (например, страница с двумя
  титулами рядом).
- year: 4-значный год выпуска авто.
- make: марка (BMW, FORD, TESLA, ...).
- model: модель ("X5", "F-150", ...) — если читается, иначе null.
- title_number: номер титула, обычно цифровой код в верхней части документа.
- title_state: 2-буквенный код штата (TX, FL, NJ, CA, ...).
- title_issue_date: дата выдачи в формате YYYY-MM-DD; null если не читается.
- odometer: целое число пробега (мили), если указан в титуле.
- owner_name: ФИО владельца на титуле (если читается).
- lien_holder: название банка/организации с залоговым правом, если указан.
- Если поле не читается / отсутствует — ставь null или пустую строку.

Верни ТОЛЬКО валидный JSON по этой схеме (без markdown):
{TITLE_SCHEMA}
"""


DOCK_RECEIPT_SCHEMA = """
{
  "container_number": "MSDU1234567",
  "booking_number": "BKG12345",
  "vessel_name": "MAERSK ATLANTIC",
  "voyage_number": "045E",
  "exporting_carrier": "MAEU MAERSK LINE",
  "port_of_loading": "Newark, NJ",
  "port_of_discharge": "Klaipeda, LT",
  "shipper": "Acme Logistics",
  "consignee": "Caromoto Lithuania",
  "seal_number": "AE12345",
  "document_date": "YYYY-MM-DD",
  "vehicles": [
    {
      "vin": "WBAJA5C58JG123456",
      "year": 2018,
      "make": "BMW",
      "model": "330I",
      "weight_kg": 2040
    }
  ],
  "notes": "что-то важное"
}
"""

DOCK_RECEIPT_PROMPT = f"""Ты — система обработки Dock Receipts (документов о приёме груза в порту).
Тебе дают отсканированный Dock Receipt. Извлеки структурированные данные.

{_VIN_OCR_HINTS}

Правила:
- container_number: номер контейнера (4 буквы + 7 цифр, например MSDU1234567).
- booking_number: номер букинга — обычно отдельной строкой "Booking No." или "BKG".
- vessel_name: название судна (Vessel).
- voyage_number: рейс (Voyage).
- exporting_carrier: значение из графы "Exporting Carrier" / "Carrier" / "Ocean Carrier"
  ровно как написано в документе (например "MAEU MAERSK LINE", "MSCU MEDITERRANEAN
  SHIPPING", "CMDU CMA CGM"). Это поле критично — на его основе подбирается
  морская линия. Если такого поля нет — оставь null.
- port_of_loading / port_of_discharge: порты в формате "Город, штат/страна".
- shipper / consignee: компании-отправитель и получатель.
- seal_number: номер пломбы контейнера (если указан).
- document_date: дата на документе в YYYY-MM-DD.

- vehicles: массив машин в контейнере. Для КАЖДОЙ машины:
  * vin: 17-символьный VIN (точно как в документе).
  * year, make, model: год / марка / модель (если читается).
  * weight_kg: масса в КИЛОГРАММАХ (целое или дробное число).
    Это поле обычно называется "Weight", "Gross Weight", "GW", "Mass".
    ВАЖНО: в наших документах масса УЖЕ В КИЛОГРАММАХ — извлекай число
    как есть, БЕЗ КОНВЕРТАЦИИ. Если рядом указана единица "KG"/"KGS" —
    подтверждение. Если стоит "LBS" — всё равно извлеки число и пометь
    в notes "weight in lbs!", но в weight_kg запиши число как видишь.

- Если поле не читается / отсутствует — ставь null.
- Если в документе несколько машин — все должны быть в массиве "vehicles".

Верни ТОЛЬКО валидный JSON по этой схеме (без markdown):
{DOCK_RECEIPT_SCHEMA}
"""

VIN_VERIFY_PROMPT = f"""Ты — верификатор VIN-кодов на отсканированных документах.
Тебе дают увеличенные фрагменты ОДНОГО документа (перекрывающиеся части страниц).
Найди все VIN-коды (17 символов) и прочитай каждый ПОСИМВОЛЬНО, максимально внимательно.

{_VIN_OCR_HINTS}

- Один и тот же VIN может попасть в несколько фрагментов — верни его ОДИН раз.
- Сначала выпиши VIN по символам в массив "characters" (это заставляет
  вглядеться в каждый символ), затем собери строку.

Верни ТОЛЬКО валидный JSON (без markdown):
{{
  "vins": [
    {{"characters": ["1", "G", "K", ...], "vin": "1GK..."}}
  ]
}}
Если ни одного VIN не читается — {{"vins": []}}.
"""


# ── Вызов Claude Vision ────────────────────────────────────────────────────


def _get_model_name() -> str:
    """Модель берётся из settings (SCAN_AI_MODEL / AGENT_MODEL), не хардкодом."""
    try:
        from django.conf import settings

        return getattr(settings, "SCAN_AI_MODEL", None) or getattr(settings, "AGENT_MODEL", "claude-sonnet-5")
    except Exception:
        return "claude-sonnet-5"


def _call_claude_vision(
    images: Iterable[tuple[str, str]],
    system_prompt: str,
    user_text: str,
) -> dict[str, Any]:
    """Отправляет изображения в Claude Vision и парсит JSON-ответ.

    ``images`` — итерируемое ``(media_type, base64)`` пар.
    Возвращает dict (даже при ошибке парсинга — пустой). Бросает только
    при отсутствии API-ключа или сетевых ошибках.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic не установлен. Запустите: pip install anthropic")
        raise

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY не настроен в .env")

    client = anthropic.Anthropic(api_key=api_key)

    content_blocks: list[dict[str, Any]] = []
    for media_type, b64 in images:
        content_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            }
        )
    content_blocks.append({"type": "text", "text": user_text})

    # temperature не передаём: у новых моделей Anthropic параметр deprecated
    # (API отвечает 400 invalid_request_error).
    # max_tokens=4000: у claude-sonnet-5 thinking-блоки расходуют тот же
    # бюджет токенов — при 2000 текст ответа может обрезаться.
    response = client.messages.create(
        model=_get_model_name(),
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": content_blocks}],
    )

    from core.services.llm_text import anthropic_response_text

    raw = anthropic_response_text(response)
    # Стрипаем markdown-обёртку, если Claude её всё-таки добавил.
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw = "\n".join(lines)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude Vision response as JSON: %s", raw[:500])
        return {}


def _second_pass_read_vins(path: str) -> list[str] | None:
    """Второй проход: посимвольное чтение VIN с увеличенных тайлов.

    Возвращает список прочитанных VIN или None, если проход не удался
    (сетевая ошибка и т.п. — верификация опциональна и не роняет пайплайн).
    """
    try:
        tiles = render_vin_verification_images(path)
        if not tiles:
            return None
        data = _call_claude_vision(
            tiles,
            system_prompt=VIN_VERIFY_PROMPT,
            user_text="Прочитай все VIN на этих фрагментах посимвольно.",
        )
    except Exception as e:
        logger.warning("scan_extractor: second-pass VIN read failed for %s: %s", path, e)
        return None
    vins = []
    for item in data.get("vins") or []:
        if isinstance(item, dict):
            vin = item.get("vin") or "".join(item.get("characters") or [])
        else:
            vin = item
        if vin and isinstance(vin, str):
            vins.append(vin.strip().upper())
    return vins


def _match_second_pass(vin: str, second_pass_vins: list[str] | None) -> str | None:
    """Подбирает к VIN первого прохода ближайший VIN второго прохода.

    Возвращает None, если второй проход не выполнялся или не нашёл ни одного
    похожего VIN (расстояние Хэмминга > 5 — это уже другой VIN, а не
    расхождение чтения).
    """
    if not second_pass_vins:
        return None
    from core.services.vin_corrector import hamming_distance, normalize_vin

    target, _ = normalize_vin(vin)
    best: tuple[int, str] | None = None
    for candidate in second_pass_vins:
        cand_norm, _ = normalize_vin(candidate)
        dist = hamming_distance(target, cand_norm)
        if dist is None:
            continue
        if best is None or dist < best[0]:
            best = (dist, candidate)
    if best is None or best[0] > 5:
        return None
    return best[1]


# ── Публичные функции ─────────────────────────────────────────────────────


def extract_title(path: str, *, use_second_pass: bool = True) -> dict[str, Any]:
    """Извлечь данные из скана US car title (PDF/JPG/PNG).

    Возвращает dict вида ``TITLE_SCHEMA`` + ключи:
      * vins — уже нормализованные/исправленные VIN,
      * vin_processing — детали пост-обработки каждого VIN,
      * vin_validations — результаты валидации (checksum + NHTSA),
      * vin_confidences — уровень уверенности по каждому VIN.
    """
    images = render_document_images(path)
    if not images:
        logger.warning("Title %s не дал изображений", path)
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=TITLE_PROMPT,
        user_text="Это отсканированный US car title. Извлеки данные по схеме.",
    )
    _postprocess_title_vins(data, path, use_second_pass=use_second_pass)
    return data


def extract_dock_receipt(path: str, *, use_second_pass: bool = True) -> dict[str, Any]:
    """Извлечь данные из скана Dock Receipt (PDF/JPG/PNG).

    Возвращает dict вида ``DOCK_RECEIPT_SCHEMA``. Каждое vehicle дополняется
    ``vin_validation``, ``vin_confidence`` и ``vin_processing``.
    """
    images = render_document_images(path)
    if not images:
        logger.warning("Dock Receipt %s не дал изображений", path)
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=DOCK_RECEIPT_PROMPT,
        user_text="Это отсканированный US Dock Receipt. Извлеки данные по схеме.",
    )
    _postprocess_dock_receipt_vins(data, path, use_second_pass=use_second_pass)
    return data


# ── Пост-обработка VIN (нормализация, коррекция, валидация, уверенность) ──


def _postprocess_title_vins(data: dict[str, Any], path: str, *, use_second_pass: bool) -> None:
    """Прогоняет VIN-ы титула через vin_corrector, обновляет data in-place."""
    from core.services.vin_corrector import process_extracted_vin

    vins = data.get("vins") or []
    if not isinstance(vins, list):
        return
    vins = [v for v in vins if v and isinstance(v, str)]
    if not vins:
        data["vin_validations"] = []
        return

    second_pass_vins = _second_pass_read_vins(path) if use_second_pass else None

    final_vins: list[str] = []
    processing: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    confidences: list[dict[str, Any]] = []
    for vin in vins:
        res = process_extracted_vin(
            vin,
            ai_make=data.get("make") or "",
            ai_model=data.get("model") or "",
            ai_year=data.get("year"),
            second_pass_vin=_match_second_pass(vin, second_pass_vins),
        )
        final_vins.append(res["vin"])
        processing.append(
            {
                "original": res["original"],
                "vin": res["vin"],
                "changes": res["changes"],
                "was_corrected": res["was_corrected"],
                "checksum_candidates": res["checksum_candidates"],
                "second_pass_agrees": res["second_pass_agrees"],
            }
        )
        if res["validation"]:
            validations.append(res["validation"])
        confidences.append({"vin": res["vin"], **res["confidence"]})

    data["vins"] = final_vins
    data["vin_processing"] = processing
    data["vin_validations"] = validations
    data["vin_confidences"] = confidences


def _postprocess_dock_receipt_vins(data: dict[str, Any], path: str, *, use_second_pass: bool) -> None:
    """Прогоняет VIN каждого vehicle через vin_corrector, обновляет in-place."""
    from core.services.vin_corrector import process_extracted_vin

    vehicles = data.get("vehicles") or []
    if not isinstance(vehicles, list):
        return
    has_vins = any(isinstance(v, dict) and v.get("vin") for v in vehicles)
    second_pass_vins = _second_pass_read_vins(path) if (use_second_pass and has_vins) else None

    for veh in vehicles:
        if not isinstance(veh, dict) or not veh.get("vin"):
            continue
        res = process_extracted_vin(
            veh["vin"],
            ai_make=veh.get("make") or "",
            ai_model=veh.get("model") or "",
            ai_year=veh.get("year"),
            second_pass_vin=_match_second_pass(veh["vin"], second_pass_vins),
        )
        veh["vin"] = res["vin"]
        veh["vin_processing"] = {
            "original": res["original"],
            "changes": res["changes"],
            "was_corrected": res["was_corrected"],
            "checksum_candidates": res["checksum_candidates"],
            "second_pass_agrees": res["second_pass_agrees"],
        }
        if res["validation"]:
            veh["vin_validation"] = res["validation"]
        veh["vin_confidence"] = res["confidence"]


# Хелпер конвертации lbs → kg, чтобы scan_applier и admin использовали
# одну и ту же формулу.
LBS_TO_KG = 0.45359237


def lbs_to_kg(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value) * LBS_TO_KG, 2)
    except (TypeError, ValueError):
        return None
