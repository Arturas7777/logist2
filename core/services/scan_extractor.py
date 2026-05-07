"""
AI-извлечение данных из отсканированных PDF.

Поддерживает два типа документов:
  * TITLE         — US car title (физический титул автомобиля).
  * DOCK_RECEIPT  — Dock Receipt (US shipping document от Atlantic Express и пр.).

Использует Claude Sonnet 4 Vision (та же модель, что invoice_audit_service).
Конвертация PDF → PNG → base64 — переиспользует ``extract_images_from_pdf``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.services.invoice_audit_service import (
    _parse_llm_json as _parse_invoice_json,  # noqa: F401  (на будущее)
    extract_images_from_pdf,
)

logger = logging.getLogger(__name__)


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


def _call_claude_vision(images_b64: list[str], system_prompt: str, user_text: str) -> dict[str, Any]:
    """Отправляет страницы PDF в Claude Sonnet 4 Vision и парсит JSON-ответ.

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
    for b64 in images_b64:
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
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

    Возвращает dict вида ``TITLE_SCHEMA`` (см. модуль) либо ``{}`` при ошибке.
    Никогда не бросает исключений из-за плохого качества скана — только
    при недоступности API/PyMuPDF.
    """
    images = extract_images_from_pdf(pdf_path)
    if not images:
        logger.warning("Title PDF %s не дал изображений", pdf_path)
        return {}
    return _call_claude_vision(
        images,
        system_prompt=TITLE_PROMPT,
        user_text="Это отсканированный US car title. Извлеки данные по схеме.",
    )


def extract_dock_receipt(pdf_path: str) -> dict[str, Any]:
    """Извлечь данные из скана Dock Receipt.

    Возвращает dict вида ``DOCK_RECEIPT_SCHEMA``. Если в документе
    несколько машин, все они будут в ``vehicles``. ``weight_kg``
    автоматически НЕ конвертируется здесь — это работа scan_applier.
    """
    images = extract_images_from_pdf(pdf_path)
    if not images:
        logger.warning("Dock Receipt PDF %s не дал изображений", pdf_path)
        return {}
    return _call_claude_vision(
        images,
        system_prompt=DOCK_RECEIPT_PROMPT,
        user_text="Это отсканированный US Dock Receipt. Извлеки данные по схеме.",
    )


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
