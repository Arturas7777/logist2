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
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('django')


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
- VIN-номера: 17-значные коды (буквы и цифры). Извлекай ВСЕ VIN-номера точно, как написано.
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

    content = response.content[0].text

    # Claude может обернуть JSON в ```json ... ``` — убираем
    content = content.strip()
    if content.startswith('```'):
        lines = content.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        content = '\n'.join(lines)

    return json.loads(content)


def _find_cars_by_vins(vins: set) -> dict:
    """
    Find cars by VIN with fuzzy matching for padded VINs.
    Returns: dict {pdf_vin -> Car} (using the PDF's VIN as key).

    Matching strategy:
    1. Exact match (vin__in)
    2. For unmatched: DB VIN starts with the PDF VIN (handles padding like '12345---')
    3. For unmatched: PDF VIN starts with the DB VIN (handles truncation)
    """
    from core.models import Car

    if not vins:
        return {}

    result = {}

    # 1. Exact match
    exact = Car.objects.filter(vin__in=vins).select_related('client', 'container')
    for car in exact:
        result[car.vin] = car

    remaining = vins - set(result.keys())
    if not remaining:
        return result

    # 2. For each unmatched VIN, try startswith / contains
    from django.db.models import Q
    q = Q()
    for vin in remaining:
        q |= Q(vin__startswith=vin) | Q(vin__endswith=vin)
    candidates = Car.objects.filter(q).select_related('client', 'container')

    for vin in remaining:
        for car in candidates:
            if car.vin.startswith(vin) or car.vin.rstrip('-').upper() == vin:
                result[vin] = car
                break

    return result


def compare_with_db(extracted: dict) -> dict:
    """
    Сравнивает извлечённые данные из счёта с данными в БД.
    Возвращает dict с полями:
      - discrepancies: list — список расхождений
      - cars_found: int
      - cars_missing: int
      - issues_count: int
    """
    from core.models import Car, CarService

    # Маппинг service_type → id складских услуг в нашей системе
    # id=15/33/34/35/36/38 — "Разгрузка/ Погрузка / Декларация"
    # id=46 — "THS NETO"
    # id=32/39-45 — "Хранение"
    UNLOADING_SERVICE_IDS = {14, 15, 29, 33, 34, 35, 36, 37, 38, 48}
    THS_SERVICE_IDS       = {46, 47}
    STORAGE_SERVICE_IDS   = {32, 39, 40, 41, 42, 43, 44, 45}

    discrepancies = []
    cars_found    = 0
    cars_missing  = 0

    # Собираем все VIN из счёта
    all_vins_in_invoice = set()
    storage_days_map    = {}  # vin → paid_days

    for item in extracted.get('items', []):
        for vin in item.get('vins', []):
            vin_clean = vin.strip().upper()
            if vin_clean:
                all_vins_in_invoice.add(vin_clean)
        if item.get('service_type') == 'STORAGE':
            for vin, days in item.get('storage_days_per_vin', {}).items():
                storage_days_map[vin.strip().upper()] = int(days)

    # Загружаем машины из БД (с fuzzy-поиском для дополненных VIN)
    found_cars = _find_cars_by_vins(all_vins_in_invoice)

    # Загружаем CarService для всех найденных машин за один запрос
    car_services = {}  # car_id → list of CarService
    if found_cars:
        car_ids = [c.pk for c in found_cars.values()]
        for cs in CarService.objects.filter(car_id__in=car_ids):
            car_services.setdefault(cs.car_id, []).append(cs)

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
                our_unload_services = [s for s in services if s.service_id in UNLOADING_SERVICE_IDS]
                our_price = sum(float(s.custom_price or 0) for s in our_unload_services)
                if not our_unload_services:
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
                        'message':     f'Услуга разгрузки не найдена в системе (NETO: {unit_price:.2f} €)',
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
                        'message':     f'Разгрузка: NETO={unit_price:.2f}€, у нас={our_price:.2f}€ (разница {our_price - unit_price:+.2f}€)',
                    })

            # ── THS ──────────────────────────────────────────────────────────
            elif stype == 'THS':
                our_ths_services = [s for s in services if s.service_id in THS_SERVICE_IDS]
                our_ths = sum(float(s.custom_price or 0) for s in our_ths_services)
                diff = our_ths - unit_price

                if not our_ths_services and unit_price > 0:
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
                        'message':     f'THS не выставлен клиенту (NETO: {unit_price:.2f} €)',
                    })
                elif abs(diff) > 1.0:
                    severity = 'error' if diff < -5 else 'warning'
                    msg = (
                        f'THS: NETO={unit_price:.2f}€, клиенту={our_ths:.2f}€ — убыток {abs(diff):.2f}€'
                        if diff < 0 else
                        f'THS: NETO={unit_price:.2f}€, клиенту={our_ths:.2f}€ — наценка {diff:.2f}€'
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
                neto_paid_days   = storage_days_map.get(vin, 0)
                # Мы даём клиенту 5 бесплатных дней, NETO даёт 7 → разница 2
                expected_client_days = neto_paid_days + 2
                our_days         = car.days or 0
                our_storage_cost = float(car.storage_cost or 0)
                neto_cost        = neto_paid_days * 5.0

                day_diff = our_days - expected_client_days
                if day_diff != 0:
                    severity = 'info' if day_diff > 0 else 'warning'
                    msg = (
                        f'Хранение: NETO={neto_paid_days} пл. дней → ожидаем {expected_client_days} у клиента, у нас={our_days} (+{day_diff} дней)'
                        if day_diff > 0 else
                        f'Хранение: NETO={neto_paid_days} пл. дней → ожидаем {expected_client_days} у клиента, у нас={our_days} (на {abs(day_diff)} меньше!)'
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

    # ── Особые позиции: COMPENSATION / BDK / DOCS без VIN ───────────────────
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
                'message':     f'Компенсация от контрагента: {descr} ({total:.2f} €) — убедитесь, что учтена',
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
                'message':     f'Доп. позиция без VIN: {descr} ({total:.2f} €) — проверьте, выставлено ли клиенту',
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
        return data
    except FileNotFoundError:
        logger.warning(f"Файл маппинга не найден: {mapping_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга маппинга: {e}")
        return {}


def _resolve_counterparty(counterparty_name: str, mapping: dict) -> dict | None:
    """Находит конфиг контрагента по имени или алиасу (нечувствителен к регистру)."""
    name_upper = counterparty_name.strip().upper()
    for key, conf in mapping.items():
        if key.upper() == name_upper:
            return conf
        for alias in conf.get('aliases', []):
            if alias.upper() == name_upper:
                return conf
    # Fuzzy: если имя содержит ключ
    for key, conf in mapping.items():
        if key.upper() in name_upper or name_upper in key.upper():
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
    Использует маппинг из invoice_service_mapping.json для привязки к CarService.
    found_cars: dict vin -> Car (уже загруженные из БД).
    Returns: dict с метриками привязки {linked, unlinked, no_mapping}.
    """
    from core.models_invoice_audit import SupplierCost

    mapping = _load_service_mapping()
    counterparty = extracted.get('counterparty', '')[:200]
    counterparty_conf = _resolve_counterparty(counterparty, mapping)

    provider_type = counterparty_conf.get('provider_type') if counterparty_conf else None
    service_map   = counterparty_conf.get('services', {}) if counterparty_conf else {}

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
        descr      = item.get('description', '')[:300]
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

        mapped_service_id = service_map.get(stype)

        for vin in vins:
            if not vin:
                continue

            car = found_cars.get(vin)
            car_service = None

            if car and provider_type and mapped_service_id:
                car_service = _find_car_service(car, provider_type, mapped_service_id)
                if car_service:
                    stats['linked'] += 1
                else:
                    stats['unlinked'] += 1
            elif car:
                stats['no_mapping'] += 1

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

        if not text.strip():
            raise ValueError("PDF не содержит текста (возможно, отсканированное изображение)")

        # 2. LLM извлекает структурированные данные
        extracted = call_llm(text)

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

        # 5. Сравниваем с БД
        comparison = compare_with_db(extracted)

        # 5.5. Сохраняем фактические затраты по машинам
        create_supplier_costs(audit, extracted, comparison.get('found_cars', {}))

        # 6. Определяем итоговый статус
        if comparison['issues_count'] > 0 or comparison['cars_missing'] > 0:
            status = InvoiceAudit.STATUS_HAS_ISSUES
        else:
            status = InvoiceAudit.STATUS_OK

        # 7. Сохраняем результаты
        audit.counterparty_detected = extracted.get('counterparty', '')[:200]
        audit.invoice_number        = extracted.get('invoice_number', '')[:100] or ''
        audit.invoice_date          = invoice_date
        audit.total_amount          = total_amount
        audit.currency              = extracted.get('currency', 'EUR')[:3]
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

        # 8. Sync to linked NewInvoice (if exists)
        _sync_audit_to_newinvoice(audit, comparison.get('found_cars', {}), extracted)

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
