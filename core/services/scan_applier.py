"""
Применение результатов AI-извлечения к карточкам Car / Container.

Принципы:
  * Атомарно (transaction.atomic).
  * Идемпотентно для повторного применения (re-apply одной и той же job
    не создаст дубликат — карточка обновится).
  * Любые автосозданные сущности помечаются на job (created_new_car/_container).
  * Сам PDF копируется в Car.title_scan / Container.dock_receipt_scan
    через ContentFile (без копирования файла на диске — переиспользуем тот
    же storage entry).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.models import Car, Container
from core.models_scans import ScanProcessingJob
from core.services.scan_extractor import lbs_to_kg

logger = logging.getLogger(__name__)


# ── Авто-настройка нового Container из Dock Receipt ─────────────────────────

# Mapping ключевого слова в "Exporting Carrier" -> имя морской линии в БД.
# Проверка идёт регистронезависимо, по подстроке. Порядок важен:
# первое совпадение выигрывает (поэтому MSCU/MSC до MAEU/MAERSK не критично,
# но всё равно: специфичные коды-префиксы первыми).
LINE_KEYWORD_MAP: list[tuple[str, str]] = [
    ('MSCU', 'MSC'),
    ('MAEU', 'MAERSK'),
    ('CMDU', 'CMA'),
    ('MSC',  'MSC'),
    ('MAERSK', 'MAERSK'),
    # Сюда позже добавим ещё линии (ONE, OOCL, COSCO, HAPPAG, CMA CGM ...).
]

# Дефолты для автоматически создаваемого контейнера на основе Dock Receipt
# (Caromoto Lithuania workflow):
#   * Оплата THS — через склад (THS_PAYER_WAREHOUSE).
#   * Склад — NETO, площадка 1 (Klaipeda, Perkelos 10).
DEFAULT_NEW_CONTAINER_THS_PAYER = 'WAREHOUSE'
DEFAULT_NEW_CONTAINER_WAREHOUSE_NAME = 'NETO'
DEFAULT_NEW_CONTAINER_UNLOAD_SITE = 1  # site=1 → Perkelos 10 для NETO


def detect_line_from_carrier(exporting_carrier: str | None):
    """По полю 'Exporting Carrier' из dock receipt вернуть Line (или None).

    Совпадение по подстроке, без учёта регистра. Стопится на первом
    найденном ключевом слове (см. LINE_KEYWORD_MAP). Возвращает Line-объект
    из БД — если он там есть; иначе None.
    """
    if not exporting_carrier:
        return None
    text = exporting_carrier.upper()
    for keyword, line_name in LINE_KEYWORD_MAP:
        if keyword in text:
            from core.models import Line
            return Line.objects.filter(name__iexact=line_name).first()
    return None


# ── Защита от mismatch'а VIN при OCR-ошибках ───────────────────────────────

# Максимальное расстояние Хэмминга (число несовпадающих символов в VIN
# одинаковой длины), при котором считаем кандидата подозрительно похожим.
# Типичные OCR-ошибки на тайтлах: 0/O, 1/I, 8/B, 5/S, 2/Z, 6/G — обычно
# дают 1-2 символа разницы. Поэтому 2 — разумный порог.
_VIN_FUZZY_MAX_DISTANCE = 2


def find_similar_vins(vin: str, *, max_distance: int = _VIN_FUZZY_MAX_DISTANCE) -> list[tuple[str, int, int]]:
    """Возвращает список ``(db_vin, car_id, hamming_distance)`` похожих VIN.

    Используется ТОЛЬКО для VIN длиной 17 (стандарт). Для нестандартных
    длин возвращает пустой список — там всё равно ничего хорошего не
    сравнить. Кандидат с distance=0 (точное совпадение) НЕ возвращается —
    его надо ловить через ``Car.objects.filter(vin=...)``.

    Сортируем по возрастанию distance: ближайшие — первыми.
    """
    if not vin or len(vin) != 17:
        return []
    candidates: list[tuple[str, int, int]] = []
    qs = Car.objects.exclude(vin='').values_list('vin', 'id')
    for db_vin, car_id in qs.iterator():
        if not db_vin or len(db_vin) != 17:
            continue
        dist = sum(1 for a, b in zip(vin, db_vin) if a != b)
        if 0 < dist <= max_distance:
            candidates.append((db_vin, car_id, dist))
    candidates.sort(key=lambda x: x[2])
    return candidates


# ── Утилиты ────────────────────────────────────────────────────────────────


def _file_basename(field_file) -> str:
    """Возвращает только имя файла из FileField (без пути)."""
    if not field_file:
        return 'scan.pdf'
    return os.path.basename(field_file.name) or 'scan.pdf'


def _copy_field_file(source_field, target_field) -> None:
    """Копирует содержимое одного FileField в другой (через storage).

    Не делает .save() модели; вызывающий код должен сам сохранять.
    """
    if not source_field:
        return
    source_field.open('rb')
    try:
        data = source_field.read()
    finally:
        source_field.close()
    target_field.save(_file_basename(source_field), ContentFile(data), save=False)


def _normalize_vin(vin) -> str:
    return (vin or '').strip().upper()


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


# ── TITLE: применение ──────────────────────────────────────────────────────


@transaction.atomic
def apply_title_job(job: ScanProcessingJob, *, applied_by=None) -> ScanProcessingJob:
    """Применить результат AI-обработки US car title к карточке Car.

    Логика:
      1. Берём первый VIN из ``extracted_data.vins``.
      2. Ищем Car по точному VIN. Если не найден — создаём новый Car
         со статусом FLOATING и заполненными year/brand (если AI извлёк).
      3. Прикрепляем оригинальный PDF в ``car.title_scan``.
      4. Ставим ``car.has_title=True``. Если есть ``title_notes`` —
         накладываем (не затирая существующие).
      5. linked_car / created_new_car / status=APPLIED.
    """
    if job.scan_type != ScanProcessingJob.SCAN_TYPE_TITLE:
        raise ValueError(f"Job #{job.pk} is not a TITLE job")
    data = job.extracted_data or {}

    vins = [v for v in (_normalize_vin(x) for x in (data.get('vins') or [])) if v]
    if not vins:
        _mark_error(job, "AI не нашёл VIN в титуле — нечего применять")
        return job

    primary_vin = vins[0]
    car = Car.objects.filter(vin=primary_vin).first()
    created_new = False
    if car is None:
        # ── Защита от OCR-ошибок при чтении VIN ──
        # Прежде чем создать новую карточку, проверим, нет ли в БД
        # похожего VIN (≤ 2 символа разницы). Если есть — велика
        # вероятность, что AI неправильно прочитал символ, и юзер
        # пытается прикрепить тайтл к УЖЕ существующей машине.
        # В этом случае откладываем job в review — пусть юзер сам решит:
        # привязать к существующему VIN или всё-таки создать новый Car.
        if not data.get('skip_vin_check'):
            similar = find_similar_vins(primary_vin)
            if similar:
                # Валидируем каждого кандидата через NHTSA, чтобы оператор
                # сразу видел, какой из двух VIN правильный (тот, у кого
                # ✓ NHTSA + сходится год с make).
                from core.services.vin_validator import cross_check_with_ai_data
                candidates_payload = []
                for v, cid, d in similar[:5]:
                    candidate = {'vin': v, 'car_id': cid, 'hamming_distance': d}
                    try:
                        cand_car = Car.objects.filter(pk=cid).only('brand', 'year').first()
                        if cand_car:
                            val = cross_check_with_ai_data(
                                v,
                                ai_make=(cand_car.brand or '').split()[0] if cand_car.brand else None,
                                ai_year=cand_car.year,
                            )
                            nhtsa = val.get('nhtsa') or {}
                            candidate['validation'] = {
                                'checksum_ok': val.get('checksum_ok'),
                                'warnings_count': len(val.get('warnings') or []),
                                'nhtsa_make': nhtsa.get('make'),
                                'nhtsa_model': nhtsa.get('model'),
                                'nhtsa_year': nhtsa.get('year'),
                                'nhtsa_ok': nhtsa.get('ok'),
                            }
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "Candidate VIN validation failed for %s: %s", v, e,
                        )
                    candidates_payload.append(candidate)
                data['vin_mismatch_review'] = {
                    'extracted_vin': primary_vin,
                    'candidates': candidates_payload,
                }
                job.extracted_data = data
                job.status = ScanProcessingJob.STATUS_NEEDS_REVIEW
                job.error_message = (
                    f"VIN {primary_vin} похож на существующий "
                    f"{similar[0][0]} (отличие {similar[0][2]} симв.). "
                    "Откройте job, чтобы выбрать действие."
                )
                job.save(update_fields=['extracted_data', 'status', 'error_message'])
                logger.warning(
                    "TITLE job #%s deferred: VIN %s ~ %s (dist=%d)",
                    job.pk, primary_vin, similar[0][0], similar[0][2],
                )
                return job
        # Создаём новую карточку Car с минимальным набором полей.
        # Статус FLOATING — чтобы потом юзер привязал контейнер вручную.
        year = _safe_int(data.get('year'))
        brand_full = _build_brand(data)
        car = Car.objects.create(
            vin=primary_vin,
            year=year or 0,
            brand=brand_full or 'Unknown',
            status='FLOATING',
        )
        created_new = True

    # Прикрепляем PDF (если уже был — перезаписываем).
    if job.original_file:
        _copy_field_file(job.original_file, car.title_scan)

    car.has_title = True
    # Минимальная пометка в title_notes — что именно AI прочитал.
    auto_note = _build_title_note(data)
    if auto_note and auto_note not in (car.title_notes or ''):
        sep = ' | ' if car.title_notes else ''
        car.title_notes = (car.title_notes or '') + sep + auto_note
        # Подрезаем под лимит CharField(200).
        car.title_notes = car.title_notes[:200]
    car.save(update_fields=['title_scan', 'has_title', 'title_notes'])

    # Если был флаг "подозрение VIN" — после успешного apply убираем,
    # чтобы не путал в админке.
    if data.pop('vin_mismatch_review', None) or data.pop('skip_vin_check', None):
        job.extracted_data = data

    job.linked_car = car
    job.created_new_car = created_new
    job.status = ScanProcessingJob.STATUS_APPLIED
    job.applied_at = timezone.now()
    job.applied_by = applied_by
    job.error_message = ''
    job.applied_changes = {
        'car_id': car.id,
        'car_vin': car.vin,
        'created_new_car': created_new,
        'title_scan_attached': bool(car.title_scan),
        'has_title_set': True,
        'title_notes_appended': auto_note,
    }
    job.save(update_fields=[
        'linked_car', 'created_new_car', 'status',
        'applied_at', 'applied_by', 'applied_changes',
        'extracted_data', 'error_message',
    ])
    logger.info("Applied TITLE job #%s to Car #%s (VIN=%s, new=%s)",
                job.pk, car.id, car.vin, created_new)
    return job


def _build_brand(data: dict) -> str:
    """Собирает строку бренда из {make, model, year} извлечённого title."""
    make = (data.get('make') or '').strip()
    model = (data.get('model') or '').strip()
    if make and model:
        return f"{make} {model}"
    return make or model or ''


def _build_title_note(data: dict) -> str:
    """Краткая авто-аннотация для title_notes."""
    parts = []
    title_number = data.get('title_number')
    state = data.get('title_state')
    if title_number:
        parts.append(f"#{title_number}")
    if state:
        parts.append(state)
    issue = data.get('title_issue_date')
    if issue:
        parts.append(f"иссью {issue}")
    return ' '.join(parts)


# ── DOCK RECEIPT: применение ───────────────────────────────────────────────


@transaction.atomic
def apply_dock_receipt_job(job: ScanProcessingJob, *, applied_by=None) -> ScanProcessingJob:
    """Применить результат AI-обработки Dock Receipt к Container и связанным Car.

    Логика:
      1. Находим Container по ``container_number``. Если нет — создаём
         новый со статусом FLOATING + booking_number.
      2. Прикрепляем PDF в ``container.dock_receipt_scan``.
      3. Для каждой машины из ``vehicles``:
           * Ищем Car по VIN. Если нет — создаём (со статусом FLOATING,
             year/brand из dock receipt).
           * Привязываем Car к контейнеру.
           * Записываем weight_kg (конвертируем из lbs если нужно).
      4. status=APPLIED, в applied_changes — список затронутых VIN.
    """
    if job.scan_type != ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        raise ValueError(f"Job #{job.pk} is not a DOCK_RECEIPT job")
    data = job.extracted_data or {}

    container_number = (data.get('container_number') or '').strip().upper()
    if not container_number:
        _mark_error(job, "AI не нашёл container_number в Dock Receipt")
        return job

    booking_number = (data.get('booking_number') or '').strip().upper()

    # Авто-определение линии из "Exporting Carrier".
    detected_line = detect_line_from_carrier(data.get('exporting_carrier'))

    container = Container.objects.filter(number=container_number).first()
    created_new_container = False
    auto_filled_fields: list[str] = []  # для applied_changes

    if container is None:
        # Новый контейнер: выставляем все дефолты Caromoto workflow.
        from core.models import Warehouse
        default_warehouse = Warehouse.objects.filter(
            name__iexact=DEFAULT_NEW_CONTAINER_WAREHOUSE_NAME
        ).first()

        kwargs = {
            'number': container_number,
            'status': 'FLOATING',
            'booking_number': booking_number or '',
            'ths_payer': DEFAULT_NEW_CONTAINER_THS_PAYER,
            'unload_site': DEFAULT_NEW_CONTAINER_UNLOAD_SITE,
        }
        if default_warehouse:
            kwargs['warehouse'] = default_warehouse
            auto_filled_fields.append(f'warehouse={default_warehouse.name}')
        if detected_line:
            kwargs['line'] = detected_line
            auto_filled_fields.append(f'line={detected_line.name}')
        container = Container.objects.create(**kwargs)
        created_new_container = True
        auto_filled_fields.extend([
            f'ths_payer={DEFAULT_NEW_CONTAINER_THS_PAYER}',
            f'unload_site={DEFAULT_NEW_CONTAINER_UNLOAD_SITE}',
        ])
    else:
        # Существующий контейнер: НЕ перетираем уже заполненные поля,
        # но добавляем недостающее (booking_number, line).
        update_fields = []
        if booking_number and not container.booking_number:
            container.booking_number = booking_number
            update_fields.append('booking_number')
        if detected_line and not container.line_id:
            container.line = detected_line
            update_fields.append('line')
            auto_filled_fields.append(f'line={detected_line.name} (was empty)')

    # Прикрепляем PDF.
    if job.original_file:
        _copy_field_file(job.original_file, container.dock_receipt_scan)
    # При создании всё уже сохранено в .create(); при update — сохраняем ровно
    # те поля, что меняли + dock_receipt_scan.
    if created_new_container:
        container.save(update_fields=['dock_receipt_scan'])
    else:
        container.save(update_fields=list(set(update_fields + ['dock_receipt_scan'])))

    vehicles = data.get('vehicles') or []
    affected = []
    created_vins = []
    for veh in vehicles:
        vin = _normalize_vin(veh.get('vin'))
        if not vin or len(vin) != 17:
            # Невалидный VIN — пропускаем, но логируем.
            logger.warning("Job #%s: пропущен невалидный VIN %r", job.pk, vin)
            continue
        weight_kg = _resolve_weight_kg(veh)
        car = Car.objects.filter(vin=vin).first()
        car_created = False
        if car is None:
            year = _safe_int(veh.get('year'))
            brand_full = _build_brand(veh)
            car = Car.objects.create(
                vin=vin,
                year=year or 0,
                brand=brand_full or 'Unknown',
                status=container.status,  # обычно FLOATING
                container=container,
            )
            car_created = True
            created_vins.append(vin)
        else:
            if car.container_id != container.id:
                car.container = container
        if weight_kg is not None:
            car.weight_kg = weight_kg
        car.save(update_fields=['container', 'weight_kg'])
        affected.append({
            'vin': vin,
            'car_id': car.id,
            'created': car_created,
            'weight_kg': float(weight_kg) if weight_kg is not None else None,
        })

    job.linked_container = container
    # Если в Dock Receipt была одна машина — для удобства поставим её в linked_car.
    if len(affected) == 1:
        job.linked_car_id = affected[0]['car_id']
    job.created_new_container = created_new_container
    job.created_new_car = bool(created_vins)
    job.status = ScanProcessingJob.STATUS_APPLIED
    job.applied_at = timezone.now()
    job.applied_by = applied_by
    job.applied_changes = {
        'container_id': container.id,
        'container_number': container.number,
        'created_new_container': created_new_container,
        'booking_number_set': container.booking_number,
        'auto_filled': auto_filled_fields,
        'detected_line': detected_line.name if detected_line else None,
        'exporting_carrier': data.get('exporting_carrier'),
        'vehicles': affected,
    }
    job.save(update_fields=[
        'linked_container', 'linked_car', 'created_new_container', 'created_new_car',
        'status', 'applied_at', 'applied_by', 'applied_changes',
    ])
    logger.info(
        "Applied DOCK_RECEIPT job #%s: container=%s (new=%s), %d vehicles (%d new)",
        job.pk, container.number, created_new_container, len(affected), len(created_vins),
    )
    return job


def _resolve_weight_kg(veh: dict) -> Decimal | None:
    """Вернуть массу в кг для одной машины из dock receipt.

    В документах Caromoto масса всегда уже в килограммах, поэтому
    значение берётся как есть. Поле ``weight_lbs`` поддерживается
    только для совместимости со старыми job'ами (тогда конвертируется
    через ``lbs_to_kg``); в новых промптах AI его не возвращает.
    """
    raw_kg = veh.get('weight_kg')
    if raw_kg not in (None, '', 0):
        try:
            return Decimal(str(raw_kg)).quantize(Decimal('0.01'))
        except (TypeError, ValueError):
            pass
    # Backward compat для старых extracted_data.
    converted = lbs_to_kg(veh.get('weight_lbs'))
    if converted is None:
        return None
    return Decimal(str(converted)).quantize(Decimal('0.01'))


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mark_error(job: ScanProcessingJob, message: str) -> None:
    job.status = ScanProcessingJob.STATUS_ERROR
    job.error_message = message
    job.save(update_fields=['status', 'error_message'])
    logger.warning("Job #%s marked ERROR: %s", job.pk, message)


# ── Универсальная точка входа ──────────────────────────────────────────────


def apply_job(job: ScanProcessingJob, *, applied_by=None) -> ScanProcessingJob:
    """Универсальный диспетчер: вызывает нужный applier по scan_type."""
    if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
        return apply_title_job(job, applied_by=applied_by)
    if job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        return apply_dock_receipt_job(job, applied_by=applied_by)
    raise ValueError(f"Unknown scan_type: {job.scan_type}")
