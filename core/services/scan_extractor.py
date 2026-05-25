"""
AI-извлечение данных из отсканированных PDF.

Поддерживает два типа документов:
  * TITLE         — US car title (физический титул автомобиля).
  * DOCK_RECEIPT  — Dock Receipt (US shipping document от Atlantic Express и пр.).

Использует Claude Sonnet 4 Vision (та же модель, что invoice_audit_service).

Важно про рендер: Claude API режет картинки больше 5 MB (base64). Поэтому
здесь используется свой рендер ``_render_pdf_pages_for_vision`` — JPEG с
auto-downgrade качества/DPI, чтобы каждая страница точно проходила лимит,
без потери читаемости текста.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Iterable

from core.services.invoice_audit_service import (
    _parse_llm_json as _parse_invoice_json,  # noqa: F401  (на будущее)
)

logger = logging.getLogger(__name__)


# ── Рендер PDF под Claude Vision ──────────────────────────────────────────

# Anthropic limit: 5 MB на одно изображение (поле base64). Сама base64-строка
# в ~1.34 раза больше исходных байт, поэтому raw держим заметно ниже.
_MAX_RAW_IMAGE_BYTES = int(3.6 * 1024 * 1024)  # ≈ 4.8 MB после base64

# Пресеты от лучшего к худшему: dpi, jpeg_quality, max_side (resize если задан).
# 200 dpi/85q обычно укладывается; для очень тяжёлых сканов (плотный текст,
# сложные графики) downgrade до 150 dpi/70q + resize до 2200 px.
_RENDER_PRESETS: list[tuple[int, int, int | None]] = [
    (200, 85, None),
    (180, 82, None),
    (160, 78, None),
    (150, 72, 2400),
    (130, 65, 2000),
]


def _render_pdf_pages_for_vision(pdf_path: str) -> list[tuple[str, str]]:
    """Возвращает список ``(media_type, base64)`` страниц PDF.

    Использует PyMuPDF + Pillow. Каждая страница ужимается до тех пор, пока
    raw-размер JPEG не станет ≤ ``_MAX_RAW_IMAGE_BYTES`` (см. пресеты).
    Гарантирует, что Anthropic API не отвергнет изображение по размеру.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF не установлен. Запустите: pip install pymupdf")
        raise
    try:
        from PIL import Image
    except ImportError:
        logger.error("Pillow не установлен. Запустите: pip install Pillow")
        raise

    out: list[tuple[str, str]] = []
    doc = fitz.open(pdf_path)
    try:
        for page_idx, page in enumerate(doc):
            payload = _render_single_page(page, Image)
            out.append(payload)
            logger.info(
                "scan_extractor: rendered page %d of %s (%d bytes raw)",
                page_idx, os.path.basename(pdf_path), len(payload[1]) * 3 // 4,
            )
    finally:
        doc.close()
    return out


def _render_single_page(page, Image) -> tuple[str, str]:
    """Рендерит страницу с подбором пресета — пока размер не уложится в лимит."""
    last_buf: io.BytesIO | None = None
    last_quality = 0
    for dpi, quality, max_side in _RENDER_PRESETS:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if max_side and max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        last_buf, last_quality = buf, quality
        if buf.tell() <= _MAX_RAW_IMAGE_BYTES:
            data = base64.b64encode(buf.getvalue()).decode('utf-8')
            return ("image/jpeg", data)
    # Все пресеты не уложились — отдаём последний (минимальный); если и он
    # больше лимита, лучше пусть API вернёт ошибку, чем мы потеряем картинку.
    logger.warning(
        "scan_extractor: page didn't fit any preset (last=%d bytes @ q=%d)",
        last_buf.tell() if last_buf else -1, last_quality,
    )
    assert last_buf is not None
    return ("image/jpeg", base64.b64encode(last_buf.getvalue()).decode('utf-8'))


# ── Промпты ────────────────────────────────────────────────────────────────

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
Тебе дают отсканированный PDF одного титула. Извлеки структурированные данные.

Правила:
- VIN всегда 17 символов (буквы и цифры). Извлекай ТОЧНО как написано.
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
- НЕ выдумывай. Если плохо видно VIN — лучше null, чем угадывать.

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
Тебе дают отсканированный PDF Dock Receipt. Извлеки структурированные данные.

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
- НЕ выдумывай VIN. Если плохо видно — лучше null.

Верни ТОЛЬКО валидный JSON по этой схеме (без markdown):
{DOCK_RECEIPT_SCHEMA}
"""


# ── Вызов Claude Vision ────────────────────────────────────────────────────


def _call_claude_vision(
    images: Iterable[tuple[str, str]],
    system_prompt: str,
    user_text: str,
) -> dict[str, Any]:
    """Отправляет страницы PDF в Claude Sonnet 4 Vision и парсит JSON-ответ.

    ``images`` — итерируемое ``(media_type, base64)`` пар (по странице).
    Возвращает dict (даже при ошибке парсинга — пустой). Бросает только
    при отсутствии API-ключа или сетевых ошибках.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic не установлен. Запустите: pip install anthropic")
        raise

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY не настроен в .env")

    client = anthropic.Anthropic(api_key=api_key)

    content_blocks: list[dict[str, Any]] = []
    for media_type, b64 in images:
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        })
    content_blocks.append({"type": "text", "text": user_text})

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": content_blocks}],
    )

    raw = response.content[0].text.strip()
    # Стрипаем markdown-обёртку, если Claude её всё-таки добавил.
    if raw.startswith('```'):
        lines = raw.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        raw = '\n'.join(lines)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude Vision response as JSON: %s", raw[:500])
        return {}


# ── Публичные функции ─────────────────────────────────────────────────────


def extract_title(pdf_path: str) -> dict[str, Any]:
    """Извлечь данные из скана US car title.

    Возвращает dict вида ``TITLE_SCHEMA`` + ключ ``vin_validations``
    (список dict-ов с результатами cross-check на каждый VIN).
    """
    images = _render_pdf_pages_for_vision(pdf_path)
    if not images:
        logger.warning("Title PDF %s не дал изображений", pdf_path)
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=TITLE_PROMPT,
        user_text="Это отсканированный US car title. Извлеки данные по схеме.",
    )
    _attach_vin_validations_for_title(data)
    return data


def extract_dock_receipt(pdf_path: str) -> dict[str, Any]:
    """Извлечь данные из скана Dock Receipt.

    Возвращает dict вида ``DOCK_RECEIPT_SCHEMA``. Каждое vehicle
    дополняется ``vin_validation`` (см. vin_validator.cross_check_with_ai_data).
    """
    images = _render_pdf_pages_for_vision(pdf_path)
    if not images:
        logger.warning("Dock Receipt PDF %s не дал изображений", pdf_path)
        return {}
    data = _call_claude_vision(
        images,
        system_prompt=DOCK_RECEIPT_PROMPT,
        user_text="Это отсканированный US Dock Receipt. Извлеки данные по схеме.",
    )
    _attach_vin_validations_for_dock_receipt(data)
    return data


# ── Пост-обработка: validate каждый VIN ────────────────────────────────────


def _attach_vin_validations_for_title(data: dict[str, Any]) -> None:
    """Прогоняет каждый VIN тайтла через VIN-валидатор и cross-check.

    Записывает результат в ``data['vin_validations']`` (list).
    Не бросает исключения — на любую ошибку просто логирует.
    """
    try:
        from core.services.vin_validator import cross_check_with_ai_data
    except ImportError:
        return
    vins = data.get('vins') or []
    if not isinstance(vins, list):
        return
    ai_make = data.get('make') or ''
    ai_model = data.get('model') or ''
    ai_year = data.get('year')
    out = []
    for vin in vins:
        if not vin or not isinstance(vin, str):
            continue
        try:
            res = cross_check_with_ai_data(
                vin,
                ai_make=ai_make,
                ai_model=ai_model,
                ai_year=ai_year,
            )
            out.append(res)
        except Exception as e:
            logger.warning("VIN validation failed for %s: %s", vin, e)
    data['vin_validations'] = out


def _attach_vin_validations_for_dock_receipt(data: dict[str, Any]) -> None:
    """Прогоняет каждое vehicle из dock receipt через валидатор."""
    try:
        from core.services.vin_validator import cross_check_with_ai_data
    except ImportError:
        return
    vehicles = data.get('vehicles') or []
    if not isinstance(vehicles, list):
        return
    for veh in vehicles:
        if not isinstance(veh, dict):
            continue
        vin = veh.get('vin')
        if not vin:
            continue
        try:
            veh['vin_validation'] = cross_check_with_ai_data(
                vin,
                ai_make=veh.get('make') or '',
                ai_model=veh.get('model') or '',
                ai_year=veh.get('year'),
            )
        except Exception as e:
            logger.warning("VIN validation failed for %s: %s", vin, e)


# Хелпер конвертации lbs → kg, чтобы scan_applier и admin использовали
# одну и ту же формулу.
LBS_TO_KG = 0.45359237


def lbs_to_kg(value) -> float | None:
    if value is None or value == '':
        return None
    try:
        return round(float(value) * LBS_TO_KG, 2)
    except (TypeError, ValueError):
        return None
