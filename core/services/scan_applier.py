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
    ("MSCU", "MSC"),
    ("MAEU", "MAERSK"),
    ("CMDU", "CMA"),
    ("MSC", "MSC"),
    ("MAERSK", "MAERSK"),
    # Сюда позже добавим ещё линии (ONE, OOCL, COSCO, HAPPAG, CMA CGM ...).
]

# Дефолты для автоматически создаваемого контейнера на основе Dock Receipt
# (Caromoto Lithuania workflow):
#   * Оплата THS — через склад (THS_PAYER_WAREHOUSE).
#   * Склад — NETO, площадка 1 (Klaipeda, Perkelos 10).
DEFAULT_NEW_CONTAINER_THS_PAYER = "WAREHOUSE"
DEFAULT_NEW_CONTAINER_WAREHOUSE_NAME = "NETO"
DEFAULT_NEW_CONTAINER_UNLOAD_SITE = 1  # site=1 → Perkelos 10 для NETO


def _attach_title_to_transport_requests(car) -> None:
    """Доложить свежий тайтл в пакеты активных заявок на автовоз.

    Тайтл обязателен в любом пакете, а заявка на авто может быть подана
    раньше, чем к нам придёт скан. Без этого бейдж полноты пакета на доске
    заявок висел бы «нет тайтла» до того, как сотрудник откроет карточку.
    """
    from core.models.website import TransportRequest
    from core.services import transport_package_actions

    requests = TransportRequest.objects.filter(cars=car).exclude(status__in=TransportRequest.INACTIVE_STATUSES)
    for transport_request in requests:
        transport_package_actions.sync_title_documents(transport_request)


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


def find_similar_vins(
    vin: str,
    *,
    max_distance: int = _VIN_FUZZY_MAX_DISTANCE,
    queryset=None,
) -> list[tuple[str, int, int]]:
    """Возвращает список ``(db_vin, car_id, hamming_distance)`` похожих VIN.

    Используется ТОЛЬКО для VIN длиной 17 (стандарт). Для нестандартных
    длин возвращает пустой список — там всё равно ничего хорошего не
    сравнить. Кандидат с distance=0 (точное совпадение) НЕ возвращается —
    его надо ловить через ``Car.objects.filter(vin=...)``.

    ``queryset`` — опциональное ограничение поиска (например, машины
    одного контейнера при загрузке скана из его карточки).

    Сортируем по возрастанию distance: ближайшие — первыми.
    """
    if not vin or len(vin) != 17:
        return []
    candidates: list[tuple[str, int, int]] = []
    qs = (queryset if queryset is not None else Car.objects.all()).exclude(vin="").values_list("vin", "id")
    for db_vin, car_id in qs.iterator():
        if not db_vin or len(db_vin) != 17:
            continue
        dist = sum(1 for a, b in zip(vin, db_vin, strict=False) if a != b)
        if 0 < dist <= max_distance:
            candidates.append((db_vin, car_id, dist))
    candidates.sort(key=lambda x: x[2])
    return candidates


def build_vin_candidate(vin: str, car_id, distance: int) -> dict:
    """Кандидат для разрешения спорного VIN, вместе с расшифровкой NHTSA.

    Оператору нужно понять, какой из двух почти одинаковых VIN настоящий.
    Самый надёжный признак — расшифровка: у верного VIN марка и год из
    NHTSA сходятся с карточкой, у ошибочного нет. Расшифровка берётся из
    кэша ``VinCheck``, поэтому список кандидатов строится без похода в сеть
    на каждый вызов.
    """
    candidate: dict = {"vin": vin, "car_id": car_id, "hamming_distance": distance}
    car = Car.objects.filter(pk=car_id).only("brand", "year").first() if car_id else None
    try:
        from core.services.vin_gate import check_vin

        verdict = check_vin(
            vin,
            brand=car.brand if car else None,
            year=car.year if car else None,
            exclude_car_id=car_id,
            check_duplicates=False,
        )
    except Exception:
        logger.warning("Не удалось проверить кандидата VIN %s", vin, exc_info=True)
        return candidate

    check = verdict.check
    candidate["validation"] = {
        "checksum_ok": bool(check.checksum_ok) if check else False,
        "warnings_count": len(verdict.issues),
        "nhtsa_make": check.nhtsa_make if check else "",
        "nhtsa_model": check.nhtsa_model if check else "",
        "nhtsa_year": check.nhtsa_year if check else None,
        "nhtsa_ok": bool(check.nhtsa_ok) if check else False,
    }
    return candidate


def dock_vin_overrides(data: dict) -> dict[str, str]:
    """Решения оператора «VIN из документа на самом деле относится к машине X».

    Хранятся в ``extracted_data`` как ``{vin_в_документе: vin_в_базе}``,
    чтобы повторное применение задачи вело себя так же, как первое.
    """
    raw = data.get("vin_overrides") or {}
    return {_normalize_vin(k): _normalize_vin(v) for k, v in raw.items() if k and v}


def detect_dock_vin_conflicts(data: dict, container=None) -> list[dict]:
    """VIN документа, почти совпадающие с уже заведёнными машинами.

    Ровно та же защита, что давно стоит на тайтлах, но для Dock Receipt.
    Без неё сценарий «машины завели руками с опечаткой, потом загрузили
    документ» приводил к тихому созданию второй карточки на ту же машину.

    Возвращает список конфликтов в том же виде, что ``vin_mismatch_review``
    у тайтлов, плюс ключ ``extracted_vin`` — какой именно VIN документа
    спорный (в одном документе их может быть несколько).
    """
    if data.get("skip_vin_check"):
        return []
    overrides = dock_vin_overrides(data)
    conflicts: list[dict] = []
    for veh in data.get("vehicles") or []:
        vin = _normalize_vin(veh.get("vin"))
        if not vin or len(vin) != 17 or vin in overrides:
            continue
        if Car.objects.filter(vin=vin).exists():
            continue  # точное совпадение — сомнений нет
        # Машины контейнера — более вероятный источник опечатки, поэтому
        # сначала ищем среди них и только потом по всей базе.
        similar = []
        if container is not None and container.pk:
            similar = find_similar_vins(vin, queryset=container.container_cars.all())
        if not similar:
            similar = find_similar_vins(vin)
        if not similar:
            continue
        conflicts.append(
            {
                "extracted_vin": vin,
                "candidates": [build_vin_candidate(v, cid, d) for v, cid, d in similar[:5]],
            }
        )
    return conflicts


# ── Утилиты ────────────────────────────────────────────────────────────────


def _file_basename(field_file) -> str:
    """Возвращает только имя файла из FileField (без пути)."""
    if not field_file:
        return "scan.pdf"
    return os.path.basename(field_file.name) or "scan.pdf"


def _copy_field_file(source_field, target_field) -> None:
    """Копирует содержимое одного FileField в другой (через storage).

    Не делает .save() модели; вызывающий код должен сам сохранять.
    """
    if not source_field:
        return
    source_field.open("rb")
    try:
        data = source_field.read()
    finally:
        source_field.close()
    target_field.save(_file_basename(source_field), ContentFile(data), save=False)


def _normalize_vin(vin) -> str:
    return (vin or "").strip().upper()


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
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
      2. Ищем Car по точному VIN. Если не найден и скан загружен из
         карточки контейнера (``target_container``) — fuzzy-поиск среди
         машин ЭТОГО контейнера: единственный кандидат с отличием ≤ 2
         символов почти наверняка и есть нужная машина (OCR-ошибка).
      3. Если и так не нашли — глобальный fuzzy-поиск → review; при
         отсутствии похожих создаём новый Car (FLOATING).
      4. Прикрепляем оригинальный скан в ``car.title_scan``,
         ставим ``has_title=True`` (``title_notes`` не трогаем — ручное поле).
      5. linked_car / created_new_car / status=APPLIED.
    """
    if job.scan_type != ScanProcessingJob.SCAN_TYPE_TITLE:
        raise ValueError(f"Job #{job.pk} is not a TITLE job")
    data = job.extracted_data or {}
    target = job.target_container if job.target_container_id else None

    vins = [v for v in (_normalize_vin(x) for x in (data.get("vins") or [])) if v]
    if not vins:
        _mark_error(job, "AI не нашёл VIN в титуле — нечего применять")
        return job

    primary_vin = vins[0]
    car = Car.objects.filter(vin=primary_vin).first()
    created_new = False
    context_match_note = ""
    if car is None and target is not None and not data.get("skip_vin_check"):
        # ── Контекст контейнера: ожидаемые VIN известны ──
        # Список машин контейнера мал (обычно 3-5), поэтому единственный
        # fuzzy-кандидат (≤ 2 символа разницы) — надёжный матч: даже если
        # AI ошибся в 1-2 символах, титул относится именно к этой машине.
        container_similar = find_similar_vins(primary_vin, queryset=target.container_cars.all())
        if len(container_similar) == 1:
            db_vin, car_id, dist = container_similar[0]
            car = Car.objects.filter(pk=car_id).first()
            if car is not None:
                context_match_note = (
                    f"VIN из титула {primary_vin} сматчен с {db_vin} "
                    f"(машина контейнера {target.number}, отличие {dist} симв.)"
                )
                data["vin_context_match"] = {
                    "extracted_vin": primary_vin,
                    "matched_vin": db_vin,
                    "container": target.number,
                    "hamming_distance": dist,
                }
                vins[0] = db_vin
                data["vins"] = vins
                primary_vin = db_vin
                logger.info("TITLE job #%s: %s", job.pk, context_match_note)
    if car is None:
        # ── Защита от OCR-ошибок при чтении VIN ──
        # Прежде чем создать новую карточку, проверим, нет ли в БД
        # похожего VIN (≤ 2 символа разницы). Если есть — велика
        # вероятность, что AI неправильно прочитал символ, и юзер
        # пытается прикрепить тайтл к УЖЕ существующей машине.
        # В этом случае откладываем job в review — пусть юзер сам решит:
        # привязать к существующему VIN или всё-таки создать новый Car.
        if not data.get("skip_vin_check"):
            similar = find_similar_vins(primary_vin)
            if similar:
                data["vin_mismatch_review"] = {
                    "extracted_vin": primary_vin,
                    "candidates": [build_vin_candidate(v, cid, d) for v, cid, d in similar[:5]],
                }
                job.extracted_data = data
                job.status = ScanProcessingJob.STATUS_NEEDS_REVIEW
                job.error_message = (
                    f"VIN {primary_vin} похож на существующий "
                    f"{similar[0][0]} (отличие {similar[0][2]} симв.). "
                    "Откройте job, чтобы выбрать действие."
                )
                job.save(update_fields=["extracted_data", "status", "error_message"])
                logger.warning(
                    "TITLE job #%s deferred: VIN %s ~ %s (dist=%d)",
                    job.pk,
                    primary_vin,
                    similar[0][0],
                    similar[0][2],
                )
                return job
        if target is not None and not data.get("skip_vin_check"):
            # Титул загружен в карточку контейнера, но VIN не совпал ни с
            # одной машиной (ни точно, ни fuzzy). Автосоздание новой машины
            # здесь почти наверняка ошибка (VIN прочитан неверно или титул
            # не от этого контейнера) — отправляем на review.
            job.extracted_data = data
            job.status = ScanProcessingJob.STATUS_NEEDS_REVIEW
            job.error_message = (
                f"VIN {primary_vin} не совпал ни с одной машиной контейнера "
                f"{target.number} и не найден в базе. Проверьте скан вручную."
            )
            job.save(update_fields=["extracted_data", "status", "error_message"])
            logger.warning(
                "TITLE job #%s deferred: VIN %s не найден среди машин контейнера %s",
                job.pk,
                primary_vin,
                target.number,
            )
            return job
        # Создаём новую карточку Car с минимальным набором полей.
        # Статус FLOATING — чтобы потом юзер привязал контейнер вручную
        # (или сразу в контейнер, если скан загружен из его карточки).
        # Марка и год — из NHTSA, если VIN расшифровался: OCR тайтла пишет
        # «CHEV EQUINOX LT», а каноническое имя одно — «CHEVROLET Equinox».
        year = _preferred_year(data)
        brand_full = _preferred_brand(data)
        create_kwargs = {
            "vin": primary_vin,
            "year": year or 0,
            "brand": brand_full or "Unknown",
            "status": "FLOATING",
        }
        if target is not None:
            create_kwargs["container"] = target
            create_kwargs["status"] = target.status
        car = Car.objects.create(**create_kwargs)
        created_new = True

    # Прикрепляем PDF (если уже был — перезаписываем).
    if job.original_file:
        _copy_field_file(job.original_file, car.title_scan)

    car.has_title = True
    # title_notes НЕ трогаем — это поле только для ручных заметок оператора.
    # Что именно AI прочитал (номер тайтла, штат, дата) — в applied_changes.
    update_fields = ["title_scan", "has_title"]
    if not created_new:
        filled = _fill_blank_spec_from_nhtsa(car, data)
        update_fields.extend(filled)
    car.save(update_fields=update_fields)

    _attach_title_to_transport_requests(car)

    # Если был флаг "подозрение VIN" — после успешного apply убираем,
    # чтобы не путал в админке.
    data.pop("vin_mismatch_review", None)
    data.pop("skip_vin_check", None)
    job.extracted_data = data

    job.linked_car = car
    job.created_new_car = created_new
    job.status = ScanProcessingJob.STATUS_APPLIED
    job.applied_at = timezone.now()
    job.applied_by = applied_by
    job.error_message = ""
    job.applied_changes = {
        "car_id": car.id,
        "car_vin": car.vin,
        "created_new_car": created_new,
        "title_scan_attached": bool(car.title_scan),
        "has_title_set": True,
        "title_info": _build_title_note(data),
    }
    if context_match_note:
        job.applied_changes["vin_context_match"] = context_match_note
    if created_new and target is not None:
        job.applied_changes["attached_to_container"] = target.number
    job.save(
        update_fields=[
            "linked_car",
            "created_new_car",
            "status",
            "applied_at",
            "applied_by",
            "applied_changes",
            "extracted_data",
            "error_message",
        ]
    )
    logger.info("Applied TITLE job #%s to Car #%s (VIN=%s, new=%s)", job.pk, car.id, car.vin, created_new)
    return job


def _build_brand(data: dict) -> str:
    """Собирает строку бренда из {make, model, year} извлечённого title."""
    make = (data.get("make") or "").strip()
    model = (data.get("model") or "").strip()
    if make and model:
        return f"{make} {model}"
    return make or model or ""


def _nhtsa_payload(source: dict) -> dict:
    """Расшифровка NHTSA из результата обработки VIN (тайтл или строка DR)."""
    direct = (source.get("vin_validation") or {}).get("nhtsa") or {}
    if direct.get("make") or direct.get("model"):
        return direct
    for item in source.get("vin_validations") or []:
        nhtsa = (item or {}).get("nhtsa") or {}
        if nhtsa.get("make") or nhtsa.get("model"):
            return nhtsa
    return {}


def _preferred_brand(source: dict) -> str:
    """Марка карточки: NHTSA важнее OCR документа."""
    from core.services.vin_gate import brand_from_nhtsa

    nhtsa = _nhtsa_payload(source)
    return brand_from_nhtsa(nhtsa.get("make"), nhtsa.get("model")) or _build_brand(source)


def _preferred_year(source: dict):
    nhtsa = _nhtsa_payload(source)
    year = nhtsa.get("year")
    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            pass
    return _safe_int(source.get("year"))


def _fill_blank_spec_from_nhtsa(car, source: dict) -> list[str]:
    """Подставляет марку и год из NHTSA, только если в карточке пусто."""
    changed: list[str] = []
    brand = _preferred_brand(source)
    if brand and (not (car.brand or "").strip() or car.brand.strip().lower() == "unknown"):
        car.brand = brand
        changed.append("brand")
    year = _preferred_year(source)
    if year and not car.year:
        car.year = year
        changed.append("year")
    return changed


def _build_title_note(data: dict) -> str:
    """Краткая сводка тайтла (номер/штат/дата) для applied_changes."""
    parts = []
    title_number = data.get("title_number")
    state = data.get("title_state")
    if title_number:
        parts.append(f"#{title_number}")
    if state:
        parts.append(state)
    issue = data.get("title_issue_date")
    if issue:
        parts.append(f"иссью {issue}")
    return " ".join(parts)


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
    target = job.target_container if job.target_container_id else None

    container_number = (data.get("container_number") or "").strip().upper()
    if not container_number and target is None:
        _mark_error(job, "AI не нашёл container_number в Dock Receipt")
        return job

    booking_number = (data.get("booking_number") or "").strip().upper()

    # ── Защита от дублей ──
    # Проверяем ДО любых изменений: если VIN документа почти совпадает с уже
    # заведённой машиной, применять нельзя — иначе на ту же машину появится
    # вторая карточка. Контейнер для поиска берём, не создавая его.
    lookup_container = target if target is not None else Container.objects.filter(number=container_number).first()
    conflicts = detect_dock_vin_conflicts(data, lookup_container)
    if conflicts:
        data["vin_conflicts"] = conflicts
        job.extracted_data = data
        job.status = ScanProcessingJob.STATUS_NEEDS_REVIEW
        first = conflicts[0]
        job.error_message = (
            f"VIN {first['extracted_vin']} похож на существующий "
            f"{first['candidates'][0]['vin']} (отличие {first['candidates'][0]['hamming_distance']} симв.). "
            "Выберите, где ошибка." + (f" Спорных VIN всего: {len(conflicts)}." if len(conflicts) > 1 else "")
        )
        job.save(update_fields=["extracted_data", "status", "error_message"])
        logger.warning("DOCK_RECEIPT job #%s отложен: спорных VIN %d", job.pk, len(conflicts))
        return job
    data.pop("vin_conflicts", None)

    # Авто-определение линии из "Exporting Carrier".
    detected_line = detect_line_from_carrier(data.get("exporting_carrier"))

    number_mismatch = ""
    if target is not None:
        # Скан загружен из карточки контейнера — применяем именно к нему,
        # НЕ создаём новый по распознанному номеру. Расхождение номера
        # фиксируем для аудита (в auto-режиме такой job до apply не дойдёт —
        # см. evaluate_auto_apply).
        container = target
        if container_number and container_number != container.number:
            number_mismatch = f"№ в документе: {container_number}, карточка: {container.number}"
            logger.warning("DOCK_RECEIPT job #%s: расхождение номера контейнера (%s)", job.pk, number_mismatch)
    else:
        container = Container.objects.filter(number=container_number).first()
    created_new_container = False
    auto_filled_fields: list[str] = []  # для applied_changes

    if container is None:
        # Новый контейнер: выставляем все дефолты Caromoto workflow.
        from core.models import Warehouse

        default_warehouse = Warehouse.objects.filter(name__iexact=DEFAULT_NEW_CONTAINER_WAREHOUSE_NAME).first()

        kwargs = {
            "number": container_number,
            "status": "FLOATING",
            "booking_number": booking_number or "",
            "ths_payer": DEFAULT_NEW_CONTAINER_THS_PAYER,
            "unload_site": DEFAULT_NEW_CONTAINER_UNLOAD_SITE,
        }
        if default_warehouse:
            kwargs["warehouse"] = default_warehouse
            auto_filled_fields.append(f"warehouse={default_warehouse.name}")
        if detected_line:
            kwargs["line"] = detected_line
            auto_filled_fields.append(f"line={detected_line.name}")
        container = Container.objects.create(**kwargs)
        created_new_container = True
        auto_filled_fields.extend(
            [
                f"ths_payer={DEFAULT_NEW_CONTAINER_THS_PAYER}",
                f"unload_site={DEFAULT_NEW_CONTAINER_UNLOAD_SITE}",
            ]
        )
    else:
        # Существующий контейнер: НЕ перетираем уже заполненные поля,
        # но добавляем недостающее (booking_number, line).
        update_fields = []
        if booking_number and not container.booking_number:
            container.booking_number = booking_number
            update_fields.append("booking_number")
        if detected_line and not container.line_id:
            container.line = detected_line
            update_fields.append("line")
            auto_filled_fields.append(f"line={detected_line.name} (was empty)")

    # Прикрепляем PDF.
    if job.original_file:
        _copy_field_file(job.original_file, container.dock_receipt_scan)
    # При создании всё уже сохранено в .create(); при update — сохраняем ровно
    # те поля, что меняли + dock_receipt_scan.
    if created_new_container:
        container.save(update_fields=["dock_receipt_scan"])
    else:
        container.save(update_fields=list({*update_fields, "dock_receipt_scan"}))

    vehicles = data.get("vehicles") or []
    overrides = dock_vin_overrides(data)
    affected = []
    created_vins = []
    for veh in vehicles:
        doc_vin = _normalize_vin(veh.get("vin"))
        if not doc_vin or len(doc_vin) != 17:
            # Невалидный VIN — пропускаем, но логируем.
            logger.warning("Job #%s: пропущен невалидный VIN %r", job.pk, doc_vin)
            continue
        # Оператор мог решить, что VIN документа относится к уже заведённой
        # машине (в документе опечатка) — тогда работаем с её VIN.
        vin = overrides.get(doc_vin, doc_vin)
        weight_kg = _resolve_weight_kg(veh)
        car = Car.objects.filter(vin=vin).first()
        car_created = False
        if car is None:
            year = _preferred_year(veh)
            brand_full = _preferred_brand(veh)
            create_kwargs = {
                "vin": vin,
                "year": year or 0,
                "brand": brand_full or "Unknown",
                "status": container.status,  # обычно FLOATING
                "container": container,
            }
            # Наследуем поля контейнера — так же, как это делает ручное
            # добавление машины в inline контейнера (save_formset).
            if container.warehouse_id:
                create_kwargs["warehouse_id"] = container.warehouse_id
            if container.client_id:
                create_kwargs["client_id"] = container.client_id
            if container.line_id:
                create_kwargs["line_id"] = container.line_id
            if container.unload_date:
                create_kwargs["unload_date"] = container.unload_date
            car = Car.objects.create(**create_kwargs)
            car_created = True
            created_vins.append(vin)
        else:
            if car.container_id != container.id:
                car.container = container
        if weight_kg is not None:
            car.weight_kg = weight_kg
        car.save(update_fields=["container", "weight_kg"])
        record = {
            "vin": vin,
            "car_id": car.id,
            "created": car_created,
            "weight_kg": float(weight_kg) if weight_kg is not None else None,
        }
        if vin != doc_vin:
            record["vin_in_document"] = doc_vin
        affected.append(record)

    data.pop("skip_vin_check", None)
    job.extracted_data = data
    job.linked_container = container
    # Если в Dock Receipt была одна машина — для удобства поставим её в linked_car.
    if len(affected) == 1:
        job.linked_car_id = affected[0]["car_id"]
    job.created_new_container = created_new_container
    job.created_new_car = bool(created_vins)
    job.status = ScanProcessingJob.STATUS_APPLIED
    job.applied_at = timezone.now()
    job.applied_by = applied_by
    job.error_message = ""
    job.applied_changes = {
        "container_id": container.id,
        "container_number": container.number,
        "created_new_container": created_new_container,
        "booking_number_set": container.booking_number,
        "auto_filled": auto_filled_fields,
        "detected_line": detected_line.name if detected_line else None,
        "exporting_carrier": data.get("exporting_carrier"),
        "vehicles": affected,
    }
    if number_mismatch:
        job.applied_changes["container_number_mismatch"] = number_mismatch
    job.save(
        update_fields=[
            "linked_container",
            "linked_car",
            "created_new_container",
            "created_new_car",
            "status",
            "applied_at",
            "applied_by",
            "applied_changes",
            "extracted_data",
            "error_message",
        ]
    )
    logger.info(
        "Applied DOCK_RECEIPT job #%s: container=%s (new=%s), %d vehicles (%d new)",
        job.pk,
        container.number,
        created_new_container,
        len(affected),
        len(created_vins),
    )
    # Номер контейнера и линия теперь известны — запрашиваем ETA у линии
    # (после коммита, чтобы не ходить в сеть внутри транзакции).
    transaction.on_commit(lambda: _schedule_eta_update(container.pk))
    return job


def _schedule_eta_update(container_id: int) -> None:
    """Ставит фоновое обновление ETA; без брокера выполняет синхронно."""
    from core.tasks import update_container_eta_task

    try:
        update_container_eta_task.delay(container_id)
    except Exception:
        try:
            update_container_eta_task(container_id)  # type: ignore[call-arg]
        except Exception:
            logger.exception("ETA update failed for container #%s", container_id)


def _resolve_weight_kg(veh: dict) -> Decimal | None:
    """Вернуть массу в кг для одной машины из dock receipt.

    В документах Caromoto масса всегда уже в килограммах, поэтому
    значение берётся как есть. Поле ``weight_lbs`` поддерживается
    только для совместимости со старыми job'ами (тогда конвертируется
    через ``lbs_to_kg``); в новых промптах AI его не возвращает.
    """
    raw_kg = veh.get("weight_kg")
    if raw_kg not in (None, "", 0):
        try:
            return Decimal(str(raw_kg)).quantize(Decimal("0.01"))
        except (TypeError, ValueError):
            pass
    # Backward compat для старых extracted_data.
    converted = lbs_to_kg(veh.get("weight_lbs"))
    if converted is None:
        return None
    return Decimal(str(converted)).quantize(Decimal("0.01"))


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mark_error(job: ScanProcessingJob, message: str) -> None:
    job.status = ScanProcessingJob.STATUS_ERROR
    job.error_message = message
    job.save(update_fields=["status", "error_message"])
    logger.warning("Job #%s marked ERROR: %s", job.pk, message)


# ── Универсальная точка входа ──────────────────────────────────────────────


def apply_job(job: ScanProcessingJob, *, applied_by=None) -> ScanProcessingJob:
    """Универсальный диспетчер: вызывает нужный applier по scan_type."""
    if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
        return apply_title_job(job, applied_by=applied_by)
    if job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        return apply_dock_receipt_job(job, applied_by=applied_by)
    raise ValueError(f"Unknown scan_type: {job.scan_type}")


# ── Авто-применение (без ручного review) ───────────────────────────────────

# Уровни уверенности распознавания VIN, достаточные для авто-применения,
# когда VIN подтверждён существующей записью в БД (точное совпадение).
_AUTO_APPLY_DB_MATCH_LEVELS = {"high", "medium"}


def _vin_confidence_level(data: dict, vin: str) -> str | None:
    """Достаёт уровень уверенности для VIN из extracted_data титула."""
    for item in data.get("vin_confidences") or []:
        if _normalize_vin(item.get("vin")) == vin:
            return item.get("level")
    return None


def _nhtsa_confirms_vin(data: dict, vin: str) -> bool:
    """NHTSA расшифровал этот VIN — марка и год известны, VIN настоящий."""
    vin = _normalize_vin(vin)
    for item in data.get("vin_validations") or []:
        if _normalize_vin(item.get("vin")) != vin:
            continue
        nhtsa = item.get("nhtsa") or {}
        if nhtsa.get("raw_failed"):
            return False
        return bool(nhtsa.get("make") and nhtsa.get("year"))
    return False


def evaluate_auto_apply(job: ScanProcessingJob) -> tuple[bool, str]:
    """Решает, можно ли применить job автоматически, без ручного review.

    Возвращает ``(ok, reason)`` — reason пишется в extracted_data /
    applied_changes для аудита.

    Принципы:
      * TITLE применяем сам только когда VIN однозначно сопоставлен
        СУЩЕСТВУЮЩЕЙ машине (точное совпадение или единственный
        fuzzy-кандидат среди машин контейнера контекста). Автосоздание
        новых Car из титула всегда требует подтверждения.
      * DOCK_RECEIPT применяем сам только при загрузке из карточки
        контейнера: номер в документе не противоречит карточке, и каждый
        VIN либо уже есть в базе, либо распознан с уверенностью high.
    """
    from core.services.vin_validator import is_north_american_vin, is_vin_checksum_valid

    if job.status != ScanProcessingJob.STATUS_NEEDS_REVIEW:
        return False, f"статус {job.status}, а не NEEDS_REVIEW"
    data = job.extracted_data or {}
    if data.get("vin_mismatch_review") or data.get("vin_conflicts"):
        return False, "есть неразрешённый VIN-конфликт"
    target = job.target_container if job.target_container_id else None

    if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
        vins = [v for v in (_normalize_vin(x) for x in (data.get("vins") or [])) if v]
        if not vins:
            return False, "AI не нашёл VIN в титуле"
        if len(vins) > 1:
            return False, "в титуле несколько VIN — нужен ручной разбор"
        vin = vins[0]
        level = _vin_confidence_level(data, vin)

        exact = Car.objects.filter(vin=vin).first()
        if exact is not None:
            nhtsa_ok = _nhtsa_confirms_vin(data, vin)
            if level not in _AUTO_APPLY_DB_MATCH_LEVELS and not nhtsa_ok:
                return False, f"уверенность распознавания VIN: {level or 'нет данных'}"
            if target is not None and exact.container_id != target.id:
                return False, (f"VIN {vin} принадлежит машине вне контейнера {target.number} — нужно подтверждение")
            return True, f"VIN {vin} точно совпал с существующей машиной (Car #{exact.id})"

        if target is not None:
            similar = find_similar_vins(vin, queryset=target.container_cars.all())
            if len(similar) == 1:
                db_vin, _car_id, dist = similar[0]
                # Матчим на VIN из БД — он должен сам быть консистентным
                # (для NA-VIN контрольная цифра обязана сходиться).
                if is_north_american_vin(db_vin) and not is_vin_checksum_valid(db_vin):
                    return False, f"VIN машины контейнера {db_vin} не проходит контрольную цифру"
                return True, (
                    f"единственный похожий VIN среди машин контейнера {target.number}: {db_vin} (отличие {dist} симв.)"
                )
            if len(similar) > 1:
                return False, "несколько похожих VIN среди машин контейнера"
        return False, "VIN не найден в базе — создание новой машины требует подтверждения"

    if job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        if target is None:
            return False, "dock receipt загружен без привязки к контейнеру"
        container_number = (data.get("container_number") or "").strip().upper()
        if container_number and container_number != target.number:
            return False, (
                f"номер контейнера в документе ({container_number}) не совпадает с карточкой ({target.number})"
            )
        vehicles = data.get("vehicles") or []
        if not vehicles:
            return False, "AI не нашёл ни одной машины в dock receipt"
        conflicts = detect_dock_vin_conflicts(data, target)
        if conflicts:
            first = conflicts[0]
            return False, (
                f"VIN {first['extracted_vin']} похож на существующий "
                f"{first['candidates'][0]['vin']} — нужно решение оператора"
            )
        for veh in vehicles:
            vin = _normalize_vin(veh.get("vin"))
            if not vin or len(vin) != 17:
                return False, f"невалидный VIN в списке машин: {vin or '—'}"
            level = (veh.get("vin_confidence") or {}).get("level")
            if Car.objects.filter(vin=vin).exists():
                # Машина уже в базе — VIN подтверждён записью; блокируем
                # только явное расхождение (low = второй проход разошёлся
                # или NHTSA противоречит данным документа).
                if level == "low":
                    return False, f"VIN {vin} распознан с низкой уверенностью"
                continue
            if level != "high":
                return False, f"новая машина {vin}: уверенность {level or 'нет данных'} (нужна high)"
        return True, f"все {len(vehicles)} VIN уверенно распознаны, контейнер {target.number} из контекста"

    return False, f"неизвестный scan_type {job.scan_type}"


def maybe_auto_apply(job: ScanProcessingJob) -> bool:
    """Применяет job автоматически, если evaluate_auto_apply разрешил.

    Вызывается из Celery-задачи после AI-извлечения. Возвращает True,
    если job был применён. Любая ошибка применения не роняет задачу —
    job остаётся в NEEDS_REVIEW для ручного разбора.
    """
    try:
        ok, reason = evaluate_auto_apply(job)
    except Exception:
        logger.exception("evaluate_auto_apply failed for job #%s", job.pk)
        return False

    data = job.extracted_data or {}
    if not ok:
        data["auto_apply_skipped"] = reason
        job.extracted_data = data
        job.save(update_fields=["extracted_data"])
        logger.info("Job #%s оставлен на ручную проверку: %s", job.pk, reason)
        return False

    data.pop("auto_apply_skipped", None)
    job.extracted_data = data
    try:
        apply_job(job, applied_by=None)
    except Exception:
        logger.exception("Auto-apply failed for job #%s", job.pk)
        return False

    job.refresh_from_db()
    if job.status != ScanProcessingJob.STATUS_APPLIED:
        # applier сам отложил в review (например, поймал fuzzy-конфликт).
        return False
    changes = job.applied_changes or {}
    changes["auto_applied"] = True
    changes["auto_apply_reason"] = reason
    job.applied_changes = changes
    job.save(update_fields=["applied_changes"])
    logger.info("Job #%s применён автоматически: %s", job.pk, reason)
    return True
