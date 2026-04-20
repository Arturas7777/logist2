"""
InvoiceAuditService
===================
1. Извлекает текст из PDF (pdfplumber)
2. Отправляет текст в OpenAI GPT-4o с промптом на структурированное извлечение
3. Запускает движок сравнения с данными в БД
4. Сохраняет результаты в модель InvoiceAudit

Поддерживает счета от ЛЮБЫХ контрагентов на любом языке.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Схема JSON, которую должен вернуть LLM ─────────────────────────────────

EXTRACTION_SCHEMA = """
{
  "counterparty": "название компании-продавца",
  "invoice_number": "номер счёта или null",
  "invoice_date": "YYYY-MM-DD или null",
  "currency": "EUR/USD/GBP/etc",
  "total": 0.00,
  "items": [
    {
      "vins": ["VIN1", "VIN2"],
      "brand": "марка автомобиля или null",
      "service_type": "UNLOADING|THS|STORAGE|TRANSPORT|DECLARATION|BDK|DOCS|COMPENSATION|OTHER",
      "description": "оригинальное название из счёта",
      "unit_price": 0.00,
      "quantity": 1,
      "total": 0.00,
      "storage_days_per_vin": {"VIN1": 3, "VIN2": 5}
    }
  ],
  "notes": "любые важные пометки, компенсации без VIN, доп. сборы"
}
"""

SYSTEM_PROMPT = f"""Ты — система обработки логистических счетов для компании по импорту автомобилей.
Твоя задача: извлечь структурированные данные из счёта-фактуры.

Правила:
- VIN-номера: обычно 17-значные коды (буквы и цифры). Извлекай ВСЕ VIN-номера точно, как написано.
- ЧАСТИЧНЫЕ VIN: некоторые контрагенты (например, Atlantic Express) указывают только последние 6 цифр VIN
  вместо полного номера. Извлекай их как есть (например, "123456") в поле vins.
  В таком случае ОБЯЗАТЕЛЬНО заполни поле "brand" (марка авто), если она указана в счёте — это нужно для сопоставления.
- brand: если в строке счёта указана марка/модель автомобиля, запиши её в поле "brand". Если не указана — null.
- service_type — выбери наиболее подходящий:
    UNLOADING  = разгрузка/погрузка контейнера, перевозка в порту ("Konteinerio pervežimas", "Выгрузка", "Handling")
    THS        = портовые сборы, терминальные сборы ("Vietiniai uosto mokesčiai", "THC", "Terminal Handling")
    STORAGE    = хранение авто на складе ("Sandėliavimas", "Storage", "Хранение")
    TRANSPORT  = автоперевозка, доставка ("Transport", "Delivery", "Перевозка")
    DECLARATION = таможенная декларация ("Декларация", "Declaration", "Custom clearance")
    BDK        = постановка на BDK, BDK administration
    DOCS       = подготовка документов ("Dokumentų paruošimas", "Documents")
    COMPENSATION = компенсация, возврат (отрицательная сумма или слово "kompensacija")
    OTHER      = всё остальное
- storage_days_per_vin: заполняй ТОЛЬКО для STORAGE позиций, указывая платные дни для каждого VIN.
  Пример: "WBAJA... 3 d.; KL4... 2 d." → {{"WBAJA...": 3, "KL4...": 2}}
- COMPENSATION (компенсация/возврат/скидка): ОБЯЗАТЕЛЬНО создай отдельную запись в items с:
  * service_type="COMPENSATION"
  * unit_price = ОТРИЦАТЕЛЬНОЕ число (например, -280.00)
  * total = ОТРИЦАТЕЛЬНОЕ число
  * vins = [] если компенсация не привязана к конкретному VIN, или [VIN] если привязана
  * НЕ записывай компенсации только в notes — каждая компенсация должна быть отдельным item!
- description: ОБЯЗАТЕЛЬНО сохраняй оригинальное название услуги из счёта точно как написано.
  Это критично для сопоставления с услугами в системе.
- Если VIN-номера перечислены через запятую для одной строки — все включай в массив vins.
- Не выдумывай данных — только то, что есть в тексте.

Верни ТОЛЬКО валидный JSON по этой схеме:
{EXTRACTION_SCHEMA}
"""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Извлекает текст из PDF файла."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            return "\n\n".join(pages_text)
    except ImportError:
        logger.error("pdfplumber не установлен. Запустите: pip install pdfplumber")
        raise
    except Exception as e:
        logger.error(f"Ошибка при чтении PDF: {e}")
        raise


def extract_images_from_pdf(pdf_path: str) -> list[str]:
    """Renders PDF pages to base64-encoded PNG images for Vision API (scanned PDFs)."""
    import base64
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF не установлен. Запустите: pip install pymupdf")
        raise

    images = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        images.append(base64.b64encode(pix.tobytes("png")).decode('utf-8'))
    doc.close()
    return images


def call_llm(text: str) -> dict:
    """
    Отправляет текст счёта в Anthropic Claude и получает структурированный JSON.
    """
    import os
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic не установлен. Запустите: pip install anthropic")
        raise

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY не настроен в .env")

    client = anthropic.Anthropic(api_key=api_key)

    user_message = f"Вот текст счёта-фактуры. Извлеки данные по схеме. Верни ТОЛЬКО JSON, без markdown:\n\n{text}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message},
        ],
    )

    return _parse_llm_json(response.content[0].text)


def _parse_llm_json(text: str) -> dict:
    """Strip markdown fences and parse JSON from LLM response."""
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        text = '\n'.join(lines)
    parsed = json.loads(text)
    return _sanitize_extracted(parsed)


def _sanitize_extracted(data) -> dict:
    """Приводит ответ LLM к ожидаемой форме.

    LLM может возвращать null в любом поле (см. схему — там `invoice_number:
    null`). Это ломает дальнейший код, когда мы делаем `value[:N]`,
    `for v in value` или `value.items()` — получаем
    "NoneType object is not subscriptable / iterable".

    Нормализуем один раз на входе:
      - верхний уровень: None → {};
      - строковые поля → '' если None;
      - items → [] если None; каждый элемент — такой же dict с vins=[],
        storage_days_per_vin={}, description='', brand='', service_type='OTHER'.
    """
    if not isinstance(data, dict):
        return {'items': []}

    data.setdefault('items', [])
    if data.get('items') is None:
        data['items'] = []

    for key in ('counterparty', 'invoice_number', 'invoice_date', 'currency', 'notes'):
        if data.get(key) is None:
            data[key] = ''

    cleaned_items = []
    for raw in data.get('items') or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get('vins') is None:
            item['vins'] = []
        if item.get('storage_days_per_vin') is None:
            item['storage_days_per_vin'] = {}
        if item.get('description') is None:
            item['description'] = ''
        if item.get('brand') is None:
            item['brand'] = ''
        if item.get('service_type') is None:
            item['service_type'] = 'OTHER'
        cleaned_items.append(item)
    data['items'] = cleaned_items

    return data


def call_llm_with_images(images_b64: list[str]) -> dict:
    """
    Sends PDF page images to Anthropic Claude Vision API for structured extraction.
    Used as fallback when PDF has no extractable text (scanned documents).
    """
    import os
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic не установлен. Запустите: pip install anthropic")
        raise

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY не настроен в .env")

    client = anthropic.Anthropic(api_key=api_key)

    content_blocks = []
    for b64 in images_b64:
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        })
    content_blocks.append({
        "type": "text",
        "text": "Вот отсканированный счёт-фактура. Извлеки данные по схеме. Верни ТОЛЬКО JSON, без markdown.",
    })

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content_blocks}],
    )

    return _parse_llm_json(response.content[0].text)


def _find_cars_by_vins(vins: set, brand_hints: dict | None = None) -> dict:
    """
    Find cars by VIN with fuzzy matching for padded VINs and partial VINs.
    Returns: dict {pdf_vin -> Car} (using the PDF's VIN as key).

    brand_hints: optional dict {vin -> brand_string} for disambiguation of partial VINs.

    Matching strategy:
    1. Exact match (vin__in)
    2. For unmatched: DB VIN starts with / ends with the PDF VIN
    3. For short partial VINs (< 10 chars): endswith + brand disambiguation
    """
    from core.models import Car

    if not vins:
        return {}

    brand_hints = brand_hints or {}
    result = {}

    # 1. Exact match
    exact = Car.objects.filter(vin__in=vins).select_related('client', 'container')
    for car in exact:
        result[car.vin] = car

    remaining = vins - set(result.keys())
    if not remaining:
        return result

    # 2. For each unmatched VIN, try startswith / endswith
    from django.db.models import Q
    q = Q()
    for vin in remaining:
        q |= Q(vin__startswith=vin) | Q(vin__endswith=vin)
    candidates = Car.objects.filter(q).select_related('client', 'container')

    for vin in remaining:
        matches = []
        for car in candidates:
            if car.vin.startswith(vin) or car.vin.endswith(vin) or car.vin.rstrip('-').upper() == vin:
                matches.append(car)

        if len(matches) == 1:
            result[vin] = matches[0]
        elif len(matches) > 1:
            # Partial VIN matched multiple cars — use brand hint to disambiguate
            brand = brand_hints.get(vin, '').upper()
            if brand:
                brand_matches = [c for c in matches if brand in c.brand.upper()]
                if len(brand_matches) == 1:
                    result[vin] = brand_matches[0]
                elif brand_matches:
                    result[vin] = brand_matches[0]
                else:
                    result[vin] = matches[0]
            else:
                result[vin] = matches[0]

    return result


def _fuzzy_match_service_name(description: str, entity_name_map: dict) -> int | None:
    """
    Match invoice description against entity service names.
    Handles OCR errors via fuzzy matching (SequenceMatcher).
    Returns service_id or None.
    """
    import unicodedata
    from difflib import SequenceMatcher

    if not entity_name_map or not description:
        return None

    def _normalize(s: str) -> str:
        s = s.strip().upper()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s

    desc_norm = _normalize(description)

    # 1. Exact match (normalized)
    for name, sid in entity_name_map.items():
        if _normalize(name) == desc_norm:
            return sid

    # 2. Containment match (normalized)
    for name, sid in entity_name_map.items():
        name_norm = _normalize(name)
        if name_norm in desc_norm or desc_norm in name_norm:
            return sid

    # 3. Fuzzy match — tolerant to OCR errors (threshold 80%)
    best_sid = None
    best_ratio = 0.0
    for name, sid in entity_name_map.items():
        name_norm = _normalize(name)
        # Compare against the beginning of desc (same length as service name)
        desc_prefix = desc_norm[:len(name_norm) + 5]
        ratio = SequenceMatcher(None, name_norm, desc_prefix).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_sid = sid

    if best_ratio >= 0.8:
        return best_sid

    return None


def _get_service_model(provider_type: str):
    """Returns the service model class for a given provider_type."""
    from core.models import CarService
    return CarService.SERVICE_MODEL_MAP.get(provider_type)


def _get_entity_field(provider_type: str) -> str | None:
    """Returns the FK field name on the service model that points to the entity."""
    return {
        'WAREHOUSE': 'warehouse_id',
        'LINE':      'line_id',
        'CARRIER':   'carrier_id',
        'COMPANY':   'company_id',
    }.get(provider_type)


def compare_with_db(extracted: dict) -> dict:
    """
    Сравнивает извлечённые данные из счёта с данными в БД.
    Использует маппинг контрагента для сопоставления с правильными услугами.
    Возвращает dict с полями:
      - discrepancies: list — список расхождений
      - cars_found: int
      - cars_missing: int
      - issues_count: int
    """
    from core.models import CarService

    discrepancies = []
    cars_found    = 0
    cars_missing  = 0

    # ── Определяем контрагента и его услуги ──────────────────────────────────
    counterparty_name = extracted.get('counterparty', '')
    mapping = _load_service_mapping()
    counterparty_conf = _resolve_counterparty(counterparty_name, mapping)

    provider_type = counterparty_conf.get('provider_type') if counterparty_conf else None
    service_map   = counterparty_conf.get('services', {}) if counterparty_conf else {}
    entity_id     = counterparty_conf.get('entity_id') if counterparty_conf else None

    # Загружаем все услуги контрагента для сопоставления по названию
    entity_services = {}  # service_id → service_obj
    entity_name_map = {}  # normalized_name → service_id
    if provider_type and entity_id:
        svc_model = _get_service_model(provider_type)
        if svc_model:
            filter_field = _get_entity_field(provider_type)
            if filter_field:
                for svc in svc_model.objects.filter(**{filter_field: entity_id}):
                    entity_services[svc.pk] = svc
                    entity_name_map[svc.name.strip().upper()] = svc.pk
                    if hasattr(svc, 'short_name') and svc.short_name:
                        entity_name_map[svc.short_name.strip().upper()] = svc.pk

    # Собираем все VIN и brand-подсказки из счёта
    all_vins_in_invoice = set()
    brand_hints         = {}  # vin → brand (для частичных VIN)
    storage_days_map    = {}  # vin → paid_days

    for item in extracted.get('items', []):
        item_brand = (item.get('brand') or '').strip()
        for vin in item.get('vins', []):
            vin_clean = vin.strip().upper()
            if vin_clean:
                all_vins_in_invoice.add(vin_clean)
                if item_brand:
                    brand_hints[vin_clean] = item_brand
        if item.get('service_type') == 'STORAGE':
            for vin, days in item.get('storage_days_per_vin', {}).items():
                storage_days_map[vin.strip().upper()] = int(days)

    # Загружаем машины из БД (с fuzzy-поиском + brand для частичных VIN)
    found_cars = _find_cars_by_vins(all_vins_in_invoice, brand_hints)

    # Загружаем CarService для найденных машин, фильтруя по провайдеру если известен
    car_services = {}  # car_id → list of CarService
    if found_cars:
        car_ids = [c.pk for c in found_cars.values()]
        qs = CarService.objects.filter(car_id__in=car_ids)
        if provider_type:
            qs = qs.filter(service_type=provider_type)
        for cs in qs:
            car_services.setdefault(cs.car_id, []).append(cs)

    def _find_service(services_list, stype, description=''):
        """Найти CarService: маппинг → name matching (с fuzzy для OCR)."""
        # 1. По маппингу service_type → service_id
        target_sid = service_map.get(stype)
        if target_sid is not None:
            matched = [s for s in services_list if s.service_id == target_sid]
            if matched:
                total = sum(float(s.custom_price or 0) for s in matched)
                return matched, total

        # 2. Fallback: fuzzy name matching
        name_sid = _fuzzy_match_service_name(description, entity_name_map)
        if name_sid is not None:
            matched = [s for s in services_list if s.service_id == name_sid]
            if matched:
                total = sum(float(s.custom_price or 0) for s in matched)
                return matched, total

        return [], 0.0

    # ── Проверяем каждый элемент счёта ──────────────────────────────────────
    for item in extracted.get('items', []):
        stype     = item.get('service_type', 'OTHER')
        vins      = [v.strip().upper() for v in item.get('vins', []) if v.strip()]
        unit_price = float(item.get('unit_price', 0) or 0)
        descr      = item.get('description', '')

        for vin in vins:
            if not vin:
                continue

            car = found_cars.get(vin)

            if car is None:
                cars_missing += 1
                discrepancies.append({
                    'type':        'MISSING_CAR',
                    'severity':    'error',
                    'vin':         vin,
                    'car':         None,
                    'client':      None,
                    'container':   None,
                    'description': descr,
                    'neto_amount': unit_price,
                    'our_amount':  None,
                    'diff':        None,
                    'message':     f'Машина есть в счёте ({descr}: {unit_price:.2f} €), но не найдена в системе',
                })
                continue

            cars_found += 1
            services = car_services.get(car.pk, [])

            # ── UNLOADING / DECLARATION ──────────────────────────────────────
            if stype == 'UNLOADING':
                our_services_list, our_price = _find_service(services, 'UNLOADING', descr)
                if not our_services_list:
                    discrepancies.append({
                        'type':        'UNLOADING_NOT_SET',
                        'severity':    'warning',
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': descr,
                        'neto_amount': unit_price,
                        'our_amount':  0.0,
                        'diff':        -unit_price,
                        'message':     f'Услуга разгрузки ({counterparty_name}) не найдена в системе (счёт: {unit_price:.2f} €)',
                    })
                elif abs(our_price - unit_price) > 1.0:
                    discrepancies.append({
                        'type':        'UNLOADING_MISMATCH',
                        'severity':    'warning',
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': descr,
                        'neto_amount': unit_price,
                        'our_amount':  our_price,
                        'diff':        our_price - unit_price,
                        'message':     f'Разгрузка ({counterparty_name}): счёт={unit_price:.2f}€, у нас={our_price:.2f}€ (разница {our_price - unit_price:+.2f}€)',
                    })

            # ── THS ──────────────────────────────────────────────────────────
            elif stype == 'THS':
                our_services_list, our_ths = _find_service(services, 'THS', descr)
                diff = our_ths - unit_price

                if not our_services_list and unit_price > 0:
                    discrepancies.append({
                        'type':        'THS_NOT_SET',
                        'severity':    'warning',
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': 'THS',
                        'neto_amount': unit_price,
                        'our_amount':  0.0,
                        'diff':        -unit_price,
                        'message':     f'THS ({counterparty_name}) не выставлен клиенту (счёт: {unit_price:.2f} €)',
                    })
                elif abs(diff) > 1.0:
                    severity = 'error' if diff < -5 else 'warning'
                    msg = (
                        f'THS ({counterparty_name}): счёт={unit_price:.2f}€, клиенту={our_ths:.2f}€ — убыток {abs(diff):.2f}€'
                        if diff < 0 else
                        f'THS ({counterparty_name}): счёт={unit_price:.2f}€, клиенту={our_ths:.2f}€ — наценка {diff:.2f}€'
                    )
                    discrepancies.append({
                        'type':        'THS_MISMATCH',
                        'severity':    severity,
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': 'THS',
                        'neto_amount': unit_price,
                        'our_amount':  our_ths,
                        'diff':        diff,
                        'message':     msg,
                    })

            # ── STORAGE ──────────────────────────────────────────────────────
            elif stype == 'STORAGE':
                our_services_list, our_storage_price = _find_service(services, 'STORAGE', descr)
                neto_paid_days   = storage_days_map.get(vin, 0)
                our_days         = car.days or 0
                our_storage_cost = float(car.storage_cost or 0)

                if neto_paid_days > 0:
                    expected_client_days = neto_paid_days + 2
                    neto_cost = neto_paid_days * 5.0
                    day_diff = our_days - expected_client_days
                    if day_diff != 0:
                        severity = 'info' if day_diff > 0 else 'warning'
                        msg = (
                            f'Хранение ({counterparty_name}): счёт={neto_paid_days} пл. дней → ожидаем {expected_client_days} у клиента, у нас={our_days} (+{day_diff} дней)'
                            if day_diff > 0 else
                            f'Хранение ({counterparty_name}): счёт={neto_paid_days} пл. дней → ожидаем {expected_client_days} у клиента, у нас={our_days} (на {abs(day_diff)} меньше!)'
                        )
                        discrepancies.append({
                            'type':        'STORAGE_DAYS_MISMATCH',
                            'severity':    severity,
                            'vin':         vin,
                            'car':         str(car),
                            'client':      str(car.client) if car.client else None,
                            'container':   car.container.number if car.container else None,
                            'description': 'Хранение',
                            'neto_amount': neto_cost,
                            'our_amount':  our_storage_cost,
                            'diff':        our_storage_cost - neto_cost,
                            'message':     msg,
                        })
                elif unit_price > 0:
                    if not our_services_list:
                        discrepancies.append({
                            'type':        'STORAGE_NOT_SET',
                            'severity':    'warning',
                            'vin':         vin,
                            'car':         str(car),
                            'client':      str(car.client) if car.client else None,
                            'container':   car.container.number if car.container else None,
                            'description': 'Хранение',
                            'neto_amount': unit_price,
                            'our_amount':  0.0,
                            'diff':        -unit_price,
                            'message':     f'Хранение ({counterparty_name}): не найдено в системе (счёт: {unit_price:.2f} €)',
                        })
                    elif abs(our_storage_price - unit_price) > 1.0:
                        discrepancies.append({
                            'type':        'STORAGE_MISMATCH',
                            'severity':    'warning',
                            'vin':         vin,
                            'car':         str(car),
                            'client':      str(car.client) if car.client else None,
                            'container':   car.container.number if car.container else None,
                            'description': 'Хранение',
                            'neto_amount': unit_price,
                            'our_amount':  our_storage_price,
                            'diff':        our_storage_price - unit_price,
                            'message':     f'Хранение ({counterparty_name}): счёт={unit_price:.2f}€, у нас={our_storage_price:.2f}€ (разница {our_storage_price - unit_price:+.2f}€)',
                        })

            # ── Прочие услуги с VIN (TRANSPORT, DECLARATION, DOCS, BDK) ──────
            elif stype in ('TRANSPORT', 'DECLARATION', 'DOCS', 'BDK'):
                our_services_list, our_price = _find_service(services, stype, descr)
                if not our_services_list:
                    discrepancies.append({
                        'type':        f'{stype}_NOT_SET',
                        'severity':    'warning',
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': descr,
                        'neto_amount': unit_price,
                        'our_amount':  0.0,
                        'diff':        -unit_price,
                        'message':     f'{descr} ({counterparty_name}): не найдено в системе (счёт: {unit_price:.2f} €)',
                    })
                elif abs(our_price - unit_price) > 1.0:
                    discrepancies.append({
                        'type':        f'{stype}_MISMATCH',
                        'severity':    'warning',
                        'vin':         vin,
                        'car':         str(car),
                        'client':      str(car.client) if car.client else None,
                        'container':   car.container.number if car.container else None,
                        'description': descr,
                        'neto_amount': unit_price,
                        'our_amount':  our_price,
                        'diff':        our_price - unit_price,
                        'message':     f'{descr} ({counterparty_name}): счёт={unit_price:.2f}€, у нас={our_price:.2f}€ (разница {our_price - unit_price:+.2f}€)',
                    })

    # ── Особые позиции: COMPENSATION / без VIN ───────────────────────────────
    for item in extracted.get('items', []):
        stype = item.get('service_type', 'OTHER')
        vins  = [v.strip().upper() for v in item.get('vins', []) if v.strip()]
        descr = item.get('description', '')
        total = float(item.get('total', 0) or 0)

        if stype == 'COMPENSATION':
            discrepancies.append({
                'type':        'COMPENSATION',
                'severity':    'info',
                'vin':         ', '.join(vins) if vins else '—',
                'car':         None,
                'client':      None,
                'container':   None,
                'description': descr,
                'neto_amount': total,
                'our_amount':  None,
                'diff':        None,
                'message':     f'Компенсация от {counterparty_name}: {descr} ({total:.2f} €) — убедитесь, что учтена',
            })
        elif stype in ('BDK', 'DOCS', 'OTHER') and not vins:
            discrepancies.append({
                'type':        'EXTRA_CHARGE',
                'severity':    'info',
                'vin':         '—',
                'car':         None,
                'client':      None,
                'container':   None,
                'description': descr,
                'neto_amount': total,
                'our_amount':  None,
                'diff':        None,
                'message':     f'Доп. позиция без VIN ({counterparty_name}): {descr} ({total:.2f} €) — проверьте, выставлено ли клиенту',
            })

    issues_count = sum(
        1 for d in discrepancies
        if d['severity'] in ('error', 'warning')
    )

    return {
        'discrepancies': discrepancies,
        'cars_found':    cars_found,
        'cars_missing':  cars_missing,
        'issues_count':  issues_count,
        'found_cars':    found_cars,
    }


def _load_service_mapping():
    """Загружает маппинг контрагентов из JSON-файла."""
    import os
    mapping_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'invoice_service_mapping.json')
    try:
        with open(mapping_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data.pop('_comment', None)
        # Runtime-валидация дублей service_id у одного контрагента
        for key, conf in data.items():
            seen: dict[int, str] = {}
            for ai_type, sid in (conf.get('services') or {}).items():
                if sid in seen and seen[sid] != ai_type:
                    logger.warning(
                        "invoice_service_mapping: контрагент %s имеет дубль service_id=%s "
                        "(AI-типы %s и %s) — проверьте mapping",
                        key, sid, seen[sid], ai_type,
                    )
                seen[sid] = ai_type
        return data
    except FileNotFoundError:
        logger.warning(f"Файл маппинга не найден: {mapping_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга маппинга: {e}")
        return {}


# Минимальная длина ключа для substring-fallback. Короткие (2-3 символа)
# ключи типа "ONE", "CMA", "TTG" категорически нельзя матчить подстрочно —
# они ложно сработают в большинстве длинных названий.
_FUZZY_MIN_KEY_LEN = 5


def _resolve_counterparty(counterparty_name: str, mapping: dict) -> dict | None:
    """Находит конфиг контрагента по имени или алиасу (нечувствителен к регистру).

    Ступени:
      1) точное совпадение ключа или алиаса;
      2) токенизированное совпадение (целое слово);
      3) substring-fallback — только для длинных ключей (>= _FUZZY_MIN_KEY_LEN).
    """
    import re

    name_upper = (counterparty_name or '').strip().upper()
    if not name_upper:
        return None

    # 1) Точное совпадение
    for key, conf in mapping.items():
        if key.upper() == name_upper:
            return conf
        for alias in conf.get('aliases', []) or []:
            if alias.upper() == name_upper:
                return conf

    # 2) Совпадение по целому слову
    tokens = set(re.findall(r'[A-ZА-ЯЁ0-9]+', name_upper))
    for key, conf in mapping.items():
        if key.upper() in tokens:
            return conf
        for alias in conf.get('aliases', []) or []:
            alias_u = alias.upper()
            if alias_u in tokens:
                return conf
            alias_tokens = set(re.findall(r'[A-ZА-ЯЁ0-9]+', alias_u))
            if alias_tokens and alias_tokens.issubset(tokens):
                return conf

    # 3) Substring-fallback — только для длинных ключей
    for key, conf in mapping.items():
        key_u = key.upper()
        if len(key_u) >= _FUZZY_MIN_KEY_LEN and (key_u in name_upper):
            logger.info(
                "invoice_service_mapping: substring-match для %r → %s "
                "(подозрительно, проверьте вручную)",
                counterparty_name, key,
            )
            return conf

    return None


def _find_car_service(car, provider_type: str, service_id: int):
    """Ищет CarService у машины по provider_type и service_id."""
    from core.models import CarService
    if not car:
        return None
    try:
        return CarService.objects.get(
            car=car,
            service_type=provider_type,
            service_id=service_id,
        )
    except CarService.DoesNotExist:
        return None


def create_supplier_costs(audit, extracted: dict, found_cars: dict) -> dict:
    """
    Создаёт записи SupplierCost для каждого VIN+услуги из извлечённых данных.
    Использует маппинг из invoice_service_mapping.json + сопоставление по названию для привязки к CarService.
    found_cars: dict vin -> Car (уже загруженные из БД).
    Returns: dict с метриками привязки {linked, unlinked, no_mapping}.
    """
    from core.models_invoice_audit import SupplierCost

    mapping = _load_service_mapping()
    # LLM может вернуть null вместо строки — dict.get(k, default) не спасает:
    # default применяется только если ключа НЕТ, а не если он = None. Поэтому
    # везде используем (val or fallback).
    counterparty = (extracted.get('counterparty') or '')[:200]
    counterparty_conf = _resolve_counterparty(counterparty, mapping)

    provider_type = counterparty_conf.get('provider_type') if counterparty_conf else None
    service_map   = counterparty_conf.get('services', {}) if counterparty_conf else {}
    entity_id     = counterparty_conf.get('entity_id') if counterparty_conf else None

    # Загружаем все услуги контрагента для name-based matching
    entity_name_map = {}  # normalized_name → service_id
    if provider_type and entity_id:
        svc_model = _get_service_model(provider_type)
        filter_field = _get_entity_field(provider_type)
        if svc_model and filter_field:
            for svc in svc_model.objects.filter(**{filter_field: entity_id}):
                entity_name_map[svc.name.strip().upper()] = svc.pk
                if hasattr(svc, 'short_name') and svc.short_name:
                    entity_name_map[svc.short_name.strip().upper()] = svc.pk

    def _resolve_car_service(car, stype: str, description: str):
        """Найти CarService: маппинг → name matching, возвращает (car_service, status)."""
        if not car or not provider_type:
            return None, 'no_mapping'

        # 1. По маппингу service_type → service_id
        mapped_sid = service_map.get(stype)
        if mapped_sid is not None:
            cs = _find_car_service(car, provider_type, mapped_sid)
            if cs:
                return cs, 'linked'

        # 2. По названию услуги (fuzzy для OCR)
        name_sid = _fuzzy_match_service_name(description, entity_name_map)
        if name_sid is not None:
            cs = _find_car_service(car, provider_type, name_sid)
            if cs:
                return cs, 'linked'

        return None, 'unlinked' if (mapped_sid or name_sid) else 'no_mapping'

    storage_days_map = {}
    for item in extracted.get('items', []):
        if item.get('service_type') == 'STORAGE':
            for vin, days in item.get('storage_days_per_vin', {}).items():
                storage_days_map[vin.strip().upper()] = int(days)

    valid_stypes = dict(SupplierCost.SERVICE_TYPE_CHOICES)
    costs_to_create = []
    stats = {'linked': 0, 'unlinked': 0, 'no_mapping': 0}

    for item in extracted.get('items', []):
        stype      = item.get('service_type', 'OTHER')
        vins       = [v.strip().upper() for v in item.get('vins', []) if v.strip()]
        unit_price = float(item.get('unit_price', 0) or 0)
        descr      = (item.get('description') or '')[:300]
        st         = stype if stype in valid_stypes else 'OTHER'

        if not vins:
            total = float(item.get('total', 0) or 0)
            costs_to_create.append(SupplierCost(
                car=None,
                car_service=None,
                audit=audit,
                source='INVOICE',
                counterparty=counterparty,
                service_type=st,
                amount=Decimal(str(total)),
                vin='',
                description=descr,
            ))
            continue

        for vin in vins:
            if not vin:
                continue

            car = found_cars.get(vin)
            car_service, status = _resolve_car_service(car, stype, descr)
            stats[status] += 1

            costs_to_create.append(SupplierCost(
                car=car,
                car_service=car_service,
                audit=audit,
                source='INVOICE',
                counterparty=counterparty,
                service_type=st,
                amount=Decimal(str(unit_price)),
                storage_days=storage_days_map.get(vin, 0) if st == 'STORAGE' else 0,
                vin=vin,
                description=descr,
            ))

    # Auto-confirm zero-cost services: if a car appears in the invoice but
    # a mapped service (e.g. STORAGE) is NOT mentioned, create a 0€ SupplierCost.
    # This covers the case where free storage days mean no invoice line item.
    if provider_type and service_map and found_cars:
        all_invoice_vins = set()
        vins_with_service = {}  # service_type -> set of VINs
        for item in extracted.get('items', []):
            item_vins = {v.strip().upper() for v in item.get('vins', []) if v.strip()}
            all_invoice_vins |= item_vins
            st = item.get('service_type', 'OTHER')
            vins_with_service.setdefault(st, set()).update(item_vins)

        for stype_key, mapped_sid in service_map.items():
            vins_with_this = vins_with_service.get(stype_key, set())
            vins_without = all_invoice_vins - vins_with_this

            for vin in vins_without:
                car = found_cars.get(vin)
                if not car:
                    continue
                car_service = _find_car_service(car, provider_type, mapped_sid)
                if not car_service:
                    continue

                costs_to_create.append(SupplierCost(
                    car=car,
                    car_service=car_service,
                    audit=audit,
                    source='INVOICE',
                    counterparty=counterparty,
                    service_type=stype_key if stype_key in valid_stypes else 'OTHER',
                    amount=Decimal('0'),
                    vin=vin,
                    description=f'Не указано в счёте (0€ — в рамках бесплатного периода)',
                ))
                stats['linked'] += 1

    if costs_to_create:
        SupplierCost.objects.filter(audit=audit).delete()
        SupplierCost.objects.bulk_create(costs_to_create)
        logger.info(
            f"InvoiceAudit #{audit.pk}: создано {len(costs_to_create)} SupplierCost "
            f"(привязано={stats['linked']}, без услуги={stats['unlinked']}, без маппинга={stats['no_mapping']})"
        )

    return stats


def process_invoice_audit(audit_id: int) -> None:
    """
    Основная функция обработки: читает PDF → LLM → сравнение → сохраняет.
    Вызывается асинхронно (через threading или Celery).
    """
    from core.models_invoice_audit import InvoiceAudit

    try:
        audit = InvoiceAudit.objects.get(pk=audit_id)
    except InvoiceAudit.DoesNotExist:
        logger.error(f"InvoiceAudit #{audit_id} не найден")
        return

    audit.status = InvoiceAudit.STATUS_PROCESSING
    audit.save(update_fields=['status'])

    try:
        # 1. Извлекаем текст из PDF
        pdf_path = audit.pdf_file.path
        text = extract_text_from_pdf(pdf_path)

        if text.strip():
            # 2a. Текстовый PDF → извлечение из текста
            extracted = call_llm(text)
        else:
            # 2b. Отсканированный PDF → рендерим страницы и отправляем в Vision API
            logger.info(f"InvoiceAudit #{audit_id}: текст не найден, используем Vision API для сканированного PDF")
            images_b64 = extract_images_from_pdf(pdf_path)
            if not images_b64:
                raise ValueError("PDF не содержит ни текста, ни изображений")
            extracted = call_llm_with_images(images_b64)

        # 3. Парсим дату
        invoice_date = None
        if extracted.get('invoice_date'):
            try:
                invoice_date = datetime.strptime(extracted['invoice_date'], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass

        # 4. Парсим сумму
        total_amount = None
        try:
            total_amount = Decimal(str(extracted.get('total', 0) or 0))
        except (InvalidOperation, TypeError):
            pass

        # 5. Сравниваем с БД (или пропускаем, если skip_ai_comparison)
        skip_comparison = False
        if audit.invoice_id:
            skip_comparison = getattr(audit.invoice, 'skip_ai_comparison', False)

        if skip_comparison:
            all_vins = set()
            brand_hints = {}
            for item in extracted.get('items', []):
                item_brand = (item.get('brand') or '').strip()
                for vin in item.get('vins', []):
                    vin_clean = vin.strip().upper()
                    if vin_clean:
                        all_vins.add(vin_clean)
                        if item_brand:
                            brand_hints[vin_clean] = item_brand
            found_cars = _find_cars_by_vins(all_vins, brand_hints)
            comparison = {
                'discrepancies': [],
                'cars_found': len(found_cars),
                'cars_missing': 0,
                'issues_count': 0,
                'found_cars': found_cars,
            }
            status = InvoiceAudit.STATUS_OK
        else:
            comparison = compare_with_db(extracted)
            create_supplier_costs(audit, extracted, comparison.get('found_cars', {}))
            if comparison['issues_count'] > 0 or comparison['cars_missing'] > 0:
                status = InvoiceAudit.STATUS_HAS_ISSUES
            else:
                status = InvoiceAudit.STATUS_OK

        # 7. Сохраняем результаты
        # LLM может вернуть null в любом поле JSON — принудительно заменяем
        # None на пустую строку / 'EUR', иначе str[:N] падает с
        # "NoneType object is not subscriptable".
        audit.counterparty_detected = (extracted.get('counterparty') or '')[:200]
        audit.invoice_number        = (extracted.get('invoice_number') or '')[:100]
        audit.invoice_date          = invoice_date
        audit.total_amount          = total_amount
        audit.currency              = (extracted.get('currency') or 'EUR')[:3]
        audit.raw_extracted         = extracted
        audit.discrepancies         = comparison['discrepancies']
        audit.cars_found            = comparison['cars_found']
        audit.cars_missing          = comparison['cars_missing']
        audit.issues_count          = comparison['issues_count']
        audit.status                = status
        audit.processed_at          = timezone.now()
        audit.save()

        logger.info(
            f"InvoiceAudit #{audit_id} обработан: "
            f"найдено={comparison['cars_found']}, "
            f"не найдено={comparison['cars_missing']}, "
            f"расхождений={comparison['issues_count']}"
        )

        # 8. Sync to linked NewInvoice (if exists).
        # В режиме «Без сверки с базой» (skip_ai_comparison=True) пользователь
        # сам задаёт сумму и позиции в админке — нам НЕЛЬЗЯ их перезаписывать
        # данными из PDF. AI в этом режиме работает как извлекатор: распарсит
        # контрагента / номер / VIN-ы для audit-записи, но инвойс оставит
        # в руках пользователя.
        if not skip_comparison:
            _sync_audit_to_newinvoice(audit, comparison.get('found_cars', {}), extracted)
        else:
            logger.info(
                "InvoiceAudit #%s: skip_ai_comparison=True → "
                "sync to NewInvoice пропущен, сумма/позиции остаются ручными",
                audit_id,
            )

    except Exception as e:
        logger.exception(f"Ошибка обработки InvoiceAudit #{audit_id}: {e}")
        audit.status        = InvoiceAudit.STATUS_ERROR
        audit.error_message = str(e)
        audit.processed_at  = timezone.now()
        audit.save(update_fields=['status', 'error_message', 'processed_at'])


def _get_short_name_for_service(provider_type: str, service_id: int) -> str | None:
    """Resolve short_name from CarService's underlying service model."""
    from core.models import CarService
    model_class = CarService.SERVICE_MODEL_MAP.get(provider_type)
    if not model_class:
        return None
    try:
        svc = model_class.objects.get(pk=service_id)
        return svc.short_name or svc.name[:10]
    except model_class.DoesNotExist:
        return None


def _get_client_price_for_car_service(car, provider_type: str, service_id: int) -> Decimal | None:
    """Get the client-facing price (with markup) from CarService for a given car + service."""
    cs = _find_car_service(car, provider_type, service_id)
    if not cs:
        return None
    price = cs.custom_price if cs.custom_price is not None else cs.get_default_price()
    markup = cs.markup_amount if cs.markup_amount is not None else Decimal('0')
    return (Decimal(str(price or 0)) + Decimal(str(markup))) * cs.quantity


def _get_client_storage_price(car) -> Decimal | None:
    """Get storage cost for the car as the client sees it."""
    if car.storage_cost and car.storage_cost > 0 and car.days and car.days > 0:
        daily_rate = car._get_storage_daily_rate() if car.warehouse else Decimal('0')
        return daily_rate * car.days
    return Decimal('0')


def _sync_audit_to_newinvoice(audit, found_cars: dict, extracted: dict):
    """
    Sync InvoiceAudit results to the linked NewInvoice:
    - Add found cars to M2M
    - Map extracted PDF items to CarService short_names (same columns as outgoing invoices)
    - Group by car + short_name, store both invoice price and client price
    - Update external_number, totals
    """
    if not audit.invoice_id:
        return

    try:
        from collections import OrderedDict

        from core.models_billing import InvoiceItem
        invoice = audit.invoice
        car_objects = [car for car in found_cars.values() if car is not None]

        if car_objects:
            existing_car_ids = set(invoice.cars.values_list('id', flat=True))
            new_cars = [c for c in car_objects if c.pk not in existing_car_ids]
            if new_cars:
                invoice.cars.add(*new_cars)
                logger.info(f"NewInvoice #{invoice.pk}: добавлено {len(new_cars)} машин из AI-анализа")
            for car_obj in car_objects:
                car_obj.update_days_and_storage()
                car_obj.save(update_fields=['days', 'storage_cost'])

        mapping = _load_service_mapping()
        counterparty = extracted.get('counterparty', '')
        counterparty_conf = _resolve_counterparty(counterparty, mapping)
        provider_type = counterparty_conf.get('provider_type') if counterparty_conf else None
        service_map = counterparty_conf.get('services', {}) if counterparty_conf else {}

        short_name_cache = {}
        for stype_key, sid in service_map.items():
            sn = _get_short_name_for_service(provider_type, sid)
            if sn:
                short_name_cache[stype_key] = sn

        # {vin: OrderedDict{short_name: {'invoice': Decimal, 'client': Decimal|None}}}
        car_groups: dict[str, OrderedDict] = {}
        unmatched_items = []

        for item in extracted.get('items', []):
            vins = [v.strip().upper() for v in item.get('vins', []) if v.strip()]
            stype = item.get('service_type', 'OTHER')
            descr = item.get('description', stype)
            unit_price = Decimal(str(item.get('unit_price', 0) or 0))
            total = Decimal(str(item.get('total', 0) or 0))
            mapped_sid = service_map.get(stype)
            storage_days = item.get('storage_days_per_vin', {})

            short_name = short_name_cache.get(stype)

            if not vins:
                if stype == 'COMPENSATION':
                    label = f"{short_name or 'Комп'}: {descr[:60]}"
                else:
                    label = short_name or descr[:50]
                unmatched_items.append((None, label, total, None))
                continue

            for vin in vins:
                car = found_cars.get(vin)

                # For STORAGE: actual cost = daily_rate × days for this VIN
                if stype == 'STORAGE' and vin in storage_days:
                    vin_amount = unit_price * int(storage_days[vin])
                else:
                    vin_amount = unit_price

                if not car:
                    label = short_name or descr[:50]
                    unmatched_items.append((None, label, vin_amount, None))
                    continue

                label = short_name or descr[:50]
                if vin not in car_groups:
                    car_groups[vin] = OrderedDict()
                groups = car_groups[vin]

                if label not in groups:
                    client_price = None
                    if car and provider_type and mapped_sid:
                        if stype == 'STORAGE':
                            client_price = _get_client_storage_price(car)
                        else:
                            client_price = _get_client_price_for_car_service(
                                car, provider_type, mapped_sid
                            )
                    groups[label] = {'invoice': Decimal('0'), 'client': client_price}

                groups[label]['invoice'] += vin_amount

        invoice.items.all().delete()

        items_to_create = []
        order = 0
        for vin, groups in car_groups.items():
            car = found_cars.get(vin)
            for short_name, data in groups.items():
                items_to_create.append(InvoiceItem(
                    invoice=invoice,
                    car=car,
                    description=short_name,
                    quantity=1,
                    unit_price=data['invoice'],
                    total_price=data['invoice'],
                    client_price=data['client'],
                    order=order,
                ))
                order += 1

        for car_obj, label, amount, client_price in unmatched_items:
            items_to_create.append(InvoiceItem(
                invoice=invoice,
                car=car_obj,
                description=label,
                quantity=1,
                unit_price=amount,
                total_price=amount,
                client_price=client_price,
                order=order,
            ))
            order += 1

        if items_to_create:
            InvoiceItem.objects.bulk_create(items_to_create)
        invoice.calculate_totals()
        if audit.total_amount and abs(invoice.total - audit.total_amount) < Decimal('1'):
            invoice.subtotal = audit.total_amount + invoice.discount - invoice.tax
            invoice.total = audit.total_amount
        invoice.save(update_fields=['subtotal', 'total'])
        logger.info(
            f"NewInvoice #{invoice.pk}: создано {order} позиций из PDF, total={invoice.total}"
        )

        ext_num = extracted.get('invoice_number', '')
        if ext_num and not invoice.external_number:
            invoice.external_number = str(ext_num)[:100]
            invoice.save(update_fields=['external_number'])

    except Exception as e:
        logger.exception(f"Error syncing InvoiceAudit #{audit.pk} to NewInvoice: {e}")
