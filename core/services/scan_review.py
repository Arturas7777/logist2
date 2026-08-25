"""
Сверка «что в документе» ↔ «что в системе» для AI-обработки сканов.

Оператор, глядя на job, должен за секунды понять три вещи: что AI прочитал
в скане, что сейчас лежит в карточке Car/Container и где именно они
расходятся. Раньше для этого приходилось читать сырой JSON, поэтому здесь
строится готовый отчёт с плоским списком полей, таблицей машин и списком
доступных действий.

Отчёт JSON-сериализуем (без объектов моделей) — его рендерит и панель в
карточке контейнера, и страница самой job, так что оба места показывают
одно и то же.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.urls import reverse

from core.models import Car
from core.models.scans import ScanProcessingJob
from core.services.scan_applier import (
    _resolve_weight_kg,  # та же формула веса, что и при применении
    find_similar_vins,
)
from core.services.vin_corrector import hamming_distance

logger = logging.getLogger(__name__)

SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"

# Состояния строки сравнения поля.
STATE_SAME = "same"
STATE_DIFF = "diff"
# Написание расходится, но речь явно об одном и том же (VW ↔ VOLKSWAGEN):
# показываем, не поднимая тревогу.
STATE_DIFF_SOFT = "diff_soft"
STATE_ONLY_DOC = "only_doc"
STATE_ONLY_SYS = "only_sys"
STATE_INFO = "info"

# Состояния машины из документа относительно контейнера.
VEHICLE_MATCHED = "matched"
VEHICLE_FUZZY = "fuzzy"
VEHICLE_ELSEWHERE = "elsewhere"
VEHICLE_NEW = "new"

VEHICLE_STATE_LABELS = {
    VEHICLE_MATCHED: "Есть в контейнере",
    VEHICLE_FUZZY: "Похожий VIN в контейнере",
    VEHICLE_ELSEWHERE: "Есть в базе, но в другом контейнере",
    VEHICLE_NEW: "Новая машина — будет создана",
}


# ── Мелкие утилиты ─────────────────────────────────────────────────────────


def _norm_vin(value) -> str:
    return (value or "").strip().upper()


def _clean(value) -> str:
    """Приводит значение любого типа к строке для показа в таблице."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, Decimal):
        return _fmt_number(value)
    if isinstance(value, float):
        return _fmt_number(Decimal(str(value)))
    return str(value).strip()


def _fmt_number(value: Decimal) -> str:
    """Убирает хвостовые нули: 1234.00 → 1234, 1234.50 → 1234.5."""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return str(normalized)


def _values_match(doc: str, sys_value: str) -> bool:
    return doc.strip().upper() == sys_value.strip().upper()


def _car_url(car_id) -> str:
    if not car_id:
        return ""
    return reverse("admin:core_car_change", args=[car_id])


def _field(label, doc, sys_value, *, hint: str = "", compare: bool = True, soft: bool = False) -> dict | None:
    """Строка сравнения одного поля. ``None``, если сравнивать нечего.

    ``compare=False`` — поле есть только в документе (номер тайтла, судно,
    порт): в системе для него места нет, поэтому расхождением считать
    нельзя, показываем как справку.

    ``soft=True`` — для текстовых полей, где разное написание нормально
    (марка, перевозчик): расхождение показываем, но не как ошибку.
    """
    doc_s = _clean(doc)
    sys_s = _clean(sys_value)
    if not doc_s and not sys_s:
        return None
    if not compare:
        state = STATE_INFO
    elif doc_s and sys_s:
        if _values_match(doc_s, sys_s) or (soft and _mentions(doc_s, sys_s)):
            state = STATE_SAME
        else:
            state = STATE_DIFF_SOFT if soft else STATE_DIFF
    elif doc_s:
        state = STATE_ONLY_DOC
    else:
        state = STATE_ONLY_SYS
    return {"label": label, "doc": doc_s, "sys": sys_s, "state": state, "hint": hint}


def _mentions(doc: str, sys_value: str) -> bool:
    """Одна строка встречается в другой — например «MSC» в «MSCU, MSC MAUREEN»."""
    left = doc.strip().upper()
    right = sys_value.strip().upper()
    return bool(left and right) and (left in right or right in left)


def _collect_warnings(data: dict) -> list[dict]:
    """Предупреждения VIN-валидатора (check digit + NHTSA) единым списком."""
    warnings: list[dict] = []
    for validation in data.get("vin_validations") or []:
        if validation.get("warnings"):
            warnings.append(
                {
                    "vin": validation.get("vin") or "",
                    "messages": list(validation.get("warnings") or []),
                    "nhtsa": validation.get("nhtsa") or {},
                    "suggested_vin": validation.get("suggested_vin") or "",
                }
            )
    for veh in data.get("vehicles") or []:
        validation = veh.get("vin_validation") or {}
        if validation.get("warnings"):
            warnings.append(
                {
                    "vin": veh.get("vin") or validation.get("vin") or "",
                    "messages": list(validation.get("warnings") or []),
                    "nhtsa": validation.get("nhtsa") or {},
                    "suggested_vin": validation.get("suggested_vin") or "",
                }
            )
    return warnings


def _confidence_level(data: dict, vin: str) -> str:
    for item in data.get("vin_confidences") or []:
        if _norm_vin(item.get("vin")) == vin:
            return item.get("level") or ""
    return ""


CONFIDENCE_LABELS = {
    "high": "высокая",
    "medium": "средняя",
    "low": "низкая",
}


# ── TITLE ──────────────────────────────────────────────────────────────────


def _title_target_car(job: ScanProcessingJob, data: dict, extracted_vin: str):
    """Машина, с которой имеет смысл сравнивать данные тайтла.

    После применения это ``linked_car``; до применения — точное совпадение
    по VIN, единственный кандидат VIN-конфликта или единственная похожая
    машина контейнера.
    """
    if job.linked_car_id:
        return job.linked_car
    if extracted_vin:
        car = Car.objects.filter(vin=extracted_vin).first()
        if car is not None:
            return car
    candidates = (data.get("vin_mismatch_review") or {}).get("candidates") or []
    if len(candidates) == 1:
        return Car.objects.filter(pk=candidates[0].get("car_id")).first()
    if extracted_vin and job.target_container_id:
        similar = find_similar_vins(extracted_vin, queryset=job.target_container.container_cars.all())
        if len(similar) == 1:
            return Car.objects.filter(pk=similar[0][1]).first()
    return None


def _title_fields(data: dict, car, extracted_vin: str) -> list[dict]:
    nhtsa_label = _nhtsa_label(data)
    # Когда NHTSA расшифровал VIN, написание марки в тайтле — справка, а не
    # сверка: «CHEV EQUINOX LT» против «CHEVROLET Equinox» не расхождение.
    brand_compare = not bool(nhtsa_label)
    rows = [
        _field("VIN", extracted_vin, car.vin if car else ""),
        _field("Год выпуска", data.get("year"), car.year if car and car.year else ""),
        _field(
            "Марка / модель",
            _brand_from(data),
            car.brand if car else "",
            soft=True,
            compare=brand_compare,
        ),
        _field("NHTSA", "", nhtsa_label, compare=False) if nhtsa_label else None,
        _field("Скан тайтла в карточке", "", "прикреплён" if car and car.title_scan else "", compare=False),
        _field("Отметка «тайтл есть»", "", "да" if car and car.has_title else "", compare=False),
        _field("Номер тайтла", data.get("title_number"), "", compare=False),
        _field("Штат выдачи", data.get("title_state"), "", compare=False),
        _field("Дата выдачи", data.get("title_issue_date"), "", compare=False),
        _field("Цвет", data.get("color"), "", compare=False),
        _field("Одометр", data.get("odometer"), "", compare=False),
        _field("Владелец", data.get("owner_name"), "", compare=False),
        _field(
            "Залогодержатель",
            data.get("lien_holder"),
            "",
            compare=False,
            hint="Если в тайтле указан залог — машину нельзя выдавать без снятия обременения.",
        ),
        _field("Примечания AI", data.get("notes"), "", compare=False),
    ]
    return [row for row in rows if row]


def _nhtsa_label(data: dict) -> str:
    for item in data.get("vin_validations") or []:
        nhtsa = (item or {}).get("nhtsa") or {}
        make = (nhtsa.get("make") or "").strip()
        model = (nhtsa.get("model") or "").strip()
        year = nhtsa.get("year")
        if make or model or year:
            return " ".join(str(part) for part in (make, model, f"({year})" if year else "") if part)
    return ""


def _brand_from(source: dict) -> str:
    make = (source.get("make") or "").strip()
    model = (source.get("model") or "").strip()
    return f"{make} {model}".strip()


def _container_car_rows(container, extracted_vin: str, *, can_apply: bool, target_car_id=None) -> list[dict]:
    """Машины контейнера, отсортированные по похожести VIN на скан.

    Главный ответ на вопрос «а к какой машине вообще относится этот тайтл»:
    оператор видит расстояние в символах и может прикрепить тайтл к нужной
    машине одним нажатием, не сверяя 17 символов глазами.
    """
    rows = []
    for car in container.container_cars.all().only("id", "vin", "brand", "year", "has_title"):
        distance = hamming_distance(extracted_vin, car.vin) if extracted_vin else None
        row = {
            "car_id": car.id,
            "car_url": _car_url(car.id),
            "vin": car.vin,
            "brand": car.brand,
            "year": car.year or "",
            "has_title": bool(car.has_title),
            "distance": distance,
            "is_target": car.id == target_car_id,
            "actions": [],
        }
        if can_apply and car.id != target_car_id:
            row["actions"].append(
                {
                    "action": "attach",
                    "car_id": car.id,
                    "vin": car.vin,
                    "label": "Тайтл от этой машины",
                    "kind": "success",
                    "confirm": f"Прикрепить тайтл к машине {car.vin}?",
                    "hint": "VIN в базе верен, AI ошибся при чтении тайтла.",
                }
            )
            if distance is not None and distance <= 2 and extracted_vin:
                row["actions"].append(
                    {
                        "action": "fix_car_vin",
                        "car_id": car.id,
                        "vin": extracted_vin,
                        "label": "Исправить VIN на тайтловый",
                        "kind": "warning",
                        "confirm": (f"Заменить VIN машины {car.vin} на {extracted_vin} из тайтла?"),
                        "hint": "VIN на тайтле верен, ошибка в карточке.",
                    }
                )
        rows.append(row)
    rows.sort(key=lambda r: (r["distance"] if r["distance"] is not None else 99, r["vin"]))
    return rows


def _build_title_report(job: ScanProcessingJob, data: dict, report: dict) -> None:
    vins = [v for v in (_norm_vin(x) for x in (data.get("vins") or [])) if v]
    extracted_vin = vins[0] if vins else ""
    car = _title_target_car(job, data, extracted_vin)

    report["doc_title"] = "Тайтл (US car title)"
    report["fields"] = _title_fields(data, car, extracted_vin)
    report["matched_car"] = (
        {
            "car_id": car.id,
            "car_url": _car_url(car.id),
            "vin": car.vin,
            "brand": car.brand,
            "year": car.year or "",
        }
        if car
        else None
    )
    report["extracted_vin"] = extracted_vin
    report["confidence"] = CONFIDENCE_LABELS.get(_confidence_level(data, extracted_vin), "")

    if len(vins) > 1:
        report["notes"].append(
            "В тайтле распознано несколько VIN — применение требует ручного разбора: " + ", ".join(vins)
        )

    container = job.target_container if job.target_container_id else None
    # При точном совпадении VIN перечислять остальные машины контейнера незачем:
    # список нужен, только пока непонятно, к какой машине относится тайтл.
    if container is not None and (car is None or car.vin != extracted_vin):
        report["container_cars"] = _container_car_rows(
            container,
            extracted_vin,
            can_apply=job.can_apply,
            target_car_id=car.id if car else None,
        )

    mismatch = data.get("vin_mismatch_review") or {}
    if mismatch:
        report["conflict"] = _build_conflict(mismatch, can_apply=job.can_apply, doc_label="тайтле")
        report["conflicts"] = [report["conflict"]]

    if job.status == ScanProcessingJob.STATUS_APPLIED:
        report["severity"] = SEVERITY_OK
        if job.created_new_car:
            report["headline"] = f"Создана новая машина {extracted_vin or ''} и к ней прикреплён тайтл".strip()
        else:
            report["headline"] = f"Тайтл прикреплён к машине {car.vin if car else extracted_vin}"
    elif job.status == ScanProcessingJob.STATUS_ERROR:
        report["severity"] = SEVERITY_ERROR
        report["headline"] = "AI не смог обработать документ"
    elif mismatch:
        report["severity"] = SEVERITY_ERROR
        report["headline"] = "VIN из тайтла почти совпадает с машиной в базе — нужно решить, где ошибка"
    elif not extracted_vin:
        report["severity"] = SEVERITY_ERROR
        report["headline"] = "AI не нашёл VIN в документе — выберите машину вручную или перечитайте скан"
    elif car is not None:
        # Предупреждения валидатора при найденной машине означают, что
        # сходится не всё: применять можно, но глазами сверить стоит.
        report["severity"] = SEVERITY_ERROR if report["warnings"] else SEVERITY_WARN
        report["headline"] = f"VIN совпал с машиной {car.vin}" + (
            " — но проверка VIN дала замечания" if report["warnings"] else " — можно применять"
        )
    elif container is not None:
        report["severity"] = SEVERITY_WARN
        report["headline"] = f"VIN {extracted_vin} не совпал ни с одной машиной контейнера"
    else:
        report["severity"] = SEVERITY_WARN
        report["headline"] = f"VIN {extracted_vin} в базе не найден"


def _build_conflict(mismatch: dict, *, can_apply: bool, doc_label: str = "документе") -> dict:
    """Нормализует описание спорного VIN в вид, удобный для шаблона.

    Один и тот же блок обслуживает тайтлы (там спорный VIN всегда один) и
    Dock Receipt (там их может быть несколько), поэтому в каждое действие
    кладётся ``doc_vin`` — какой именно VIN документа разрешаем.
    """
    extracted_vin = _norm_vin(mismatch.get("extracted_vin"))
    candidates = []
    for candidate in mismatch.get("candidates") or []:
        car_id = candidate.get("car_id")
        car = Car.objects.filter(pk=car_id).only("id", "vin", "brand", "year").first() if car_id else None
        validation = candidate.get("validation") or {}
        item = {
            "vin": _norm_vin(candidate.get("vin")),
            "car_id": car_id,
            "car_url": _car_url(car_id),
            "brand": car.brand if car else "",
            "year": (car.year or "") if car else "",
            "distance": candidate.get("hamming_distance"),
            "validation_ok": bool(validation) and not validation.get("warnings_count"),
            "has_validation": bool(validation),
            "nhtsa": " ".join(
                str(part)
                for part in (
                    validation.get("nhtsa_make"),
                    validation.get("nhtsa_model"),
                    f"({validation['nhtsa_year']})" if validation.get("nhtsa_year") else None,
                )
                if part
            ),
            "actions": [],
        }
        if can_apply and car_id:
            item["actions"] = [
                {
                    "action": "attach",
                    "car_id": car_id,
                    "vin": item["vin"],
                    "doc_vin": extracted_vin,
                    "label": "Верен VIN в базе",
                    "kind": "success",
                    "confirm": f"Считать, что документ относится к машине {item['vin']}, и не менять её VIN?",
                    "hint": "Документ относится к этой машине, VIN в карточке верен.",
                },
                {
                    "action": "fix_car_vin",
                    "car_id": car_id,
                    "vin": extracted_vin,
                    "doc_vin": extracted_vin,
                    "label": f"Верен VIN в {'тайтле' if doc_label == 'тайтле' else 'документе'}",
                    "kind": "warning",
                    "confirm": (f"Заменить VIN машины {item['vin']} на {extracted_vin} из документа?"),
                    "hint": "VIN в карточке будет исправлен по документу.",
                },
            ]
        candidates.append(item)
    candidates.sort(key=lambda c: (c["distance"] if c["distance"] is not None else 99))
    return {"extracted_vin": extracted_vin, "candidates": candidates, "doc_label": doc_label}


# ── DOCK RECEIPT ───────────────────────────────────────────────────────────


def _dock_fields(data: dict, container) -> list[dict]:
    detected_line = ""
    if container is not None and container.line_id:
        detected_line = container.line.name
    rows = [
        _field(
            "Номер контейнера",
            data.get("container_number"),
            container.number if container else "",
            hint="Расхождение номера — повод проверить, тот ли это документ.",
        ),
        _field("Номер букинга", data.get("booking_number"), container.booking_number if container else ""),
        _field("Морская линия", data.get("exporting_carrier"), detected_line, soft=True),
        _field("Судно", data.get("vessel_name"), "", compare=False),
        _field("Рейс", data.get("voyage_number"), "", compare=False),
        _field("Порт погрузки", data.get("port_of_loading"), "", compare=False),
        _field("Порт выгрузки", data.get("port_of_discharge"), "", compare=False),
        _field("Отправитель", data.get("shipper"), "", compare=False),
        _field("Получатель", data.get("consignee"), "", compare=False),
        _field("Номер пломбы", data.get("seal_number"), "", compare=False),
        _field("Дата документа", data.get("document_date"), "", compare=False),
        _field(
            "Скан Dock Receipt в карточке",
            "",
            "прикреплён" if container is not None and container.dock_receipt_scan else "",
            compare=False,
        ),
    ]
    return [row for row in rows if row]


def _dock_vehicle_rows(data: dict, container) -> tuple[list[dict], list[dict]]:
    """Машины документа и машины контейнера, которых в документе нет."""
    container_cars = (
        list(container.container_cars.all().only("id", "vin", "brand", "year", "weight_kg", "has_title"))
        if container
        else []
    )
    by_vin = {car.vin: car for car in container_cars}
    seen_car_ids: set[int] = set()

    rows = []
    for veh in data.get("vehicles") or []:
        vin = _norm_vin(veh.get("vin"))
        car = by_vin.get(vin)
        state = VEHICLE_MATCHED
        if car is None:
            fuzzy = [
                candidate
                for candidate in container_cars
                if (distance := hamming_distance(vin, candidate.vin)) is not None and 0 < distance <= 2
            ]
            if len(fuzzy) == 1:
                car = fuzzy[0]
                state = VEHICLE_FUZZY
            else:
                car = Car.objects.filter(vin=vin).only("id", "vin", "brand", "year", "weight_kg", "has_title").first()
                state = VEHICLE_ELSEWHERE if car is not None else VEHICLE_NEW
        if car is not None:
            seen_car_ids.add(car.id)

        doc_weight = _resolve_weight_kg(veh)
        confidence = (veh.get("vin_confidence") or {}).get("level") or ""
        notes = []
        if veh.get("vin_processing", {}).get("was_corrected"):
            original = veh.get("vin_processing", {}).get("original")
            if original:
                notes.append(f"AI поправил VIN при распознавании: {original} → {vin}")
        if state == VEHICLE_FUZZY and car is not None:
            notes.append(f"Отличие от VIN в контейнере: {hamming_distance(vin, car.vin)} симв.")

        rows.append(
            {
                "vin_doc": vin,
                "vin_sys": car.vin if car else "",
                "state": state,
                "state_label": VEHICLE_STATE_LABELS[state],
                "car_id": car.id if car else None,
                "car_url": _car_url(car.id) if car else "",
                "brand_doc": _brand_from(veh),
                "brand_sys": car.brand if car else "",
                "year_doc": _clean(veh.get("year")),
                "year_sys": _clean(car.year) if car and car.year else "",
                "weight_doc": _clean(doc_weight),
                "weight_sys": _clean(car.weight_kg) if car and car.weight_kg is not None else "",
                "weight_diff": bool(
                    car is not None
                    and car.weight_kg is not None
                    and doc_weight is not None
                    and car.weight_kg != doc_weight
                ),
                "has_title": bool(car.has_title) if car else False,
                "confidence": CONFIDENCE_LABELS.get(confidence, ""),
                "confidence_low": confidence == "low",
                "notes": notes,
            }
        )

    missing = [
        {
            "car_id": car.id,
            "car_url": _car_url(car.id),
            "vin": car.vin,
            "brand": car.brand,
            "year": car.year or "",
        }
        for car in container_cars
        if car.id not in seen_car_ids
    ]
    return rows, missing


def _build_dock_report(job: ScanProcessingJob, data: dict, report: dict) -> None:
    container = job.linked_container or job.target_container
    report["doc_title"] = "Dock Receipt"
    report["fields"] = _dock_fields(data, container)
    report["vehicles"], report["missing_in_doc"] = _dock_vehicle_rows(data, container)

    doc_number = (data.get("container_number") or "").strip().upper()
    number_mismatch = bool(container is not None and doc_number and doc_number != container.number)

    report["conflicts"] = [
        _build_conflict(conflict, can_apply=job.can_apply, doc_label="Dock Receipt")
        for conflict in (data.get("vin_conflicts") or [])
    ]
    if report["conflicts"]:
        report["conflict"] = report["conflicts"][0]

    if report["conflicts"] and job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW:
        report["severity"] = SEVERITY_ERROR
        count = len(report["conflicts"])
        report["headline"] = (
            "VIN из документа почти совпадает с машиной в базе — нужно решить, где ошибка"
            if count == 1
            else f"Спорных VIN: {count} — по каждому нужно решить, где ошибка"
        )
        return

    if job.status == ScanProcessingJob.STATUS_APPLIED:
        report["severity"] = SEVERITY_OK
        created = sum(1 for v in (job.applied_changes or {}).get("vehicles") or [] if v.get("created"))
        report["headline"] = "Документ применён" + (f", создано машин: {created}" if created else "")
    elif job.status == ScanProcessingJob.STATUS_ERROR:
        report["severity"] = SEVERITY_ERROR
        report["headline"] = "AI не смог обработать документ"
    elif number_mismatch:
        report["severity"] = SEVERITY_ERROR
        report["headline"] = f"Номер в документе ({doc_number}) не совпадает с карточкой ({container.number})"
    elif not report["vehicles"]:
        report["severity"] = SEVERITY_WARN
        report["headline"] = "AI не нашёл в документе ни одной машины"
    else:
        new_count = sum(1 for v in report["vehicles"] if v["state"] == VEHICLE_NEW)
        fuzzy_count = sum(1 for v in report["vehicles"] if v["state"] in (VEHICLE_FUZZY, VEHICLE_ELSEWHERE))
        if fuzzy_count:
            report["severity"] = SEVERITY_ERROR
            report["headline"] = f"Похожие, но не совпадающие VIN: {fuzzy_count} — проверьте, где опечатка"
        elif new_count:
            report["severity"] = SEVERITY_WARN
            report["headline"] = f"Будут добавлены новые машины: {new_count}"
        else:
            report["severity"] = SEVERITY_WARN
            report["headline"] = "Все машины документа уже есть в контейнере"


# ── Точка входа ────────────────────────────────────────────────────────────


def build_scan_review(job: ScanProcessingJob) -> dict:
    """Строит отчёт сверки по одной задаче обработки скана."""
    data = job.extracted_data or {}
    report: dict = {
        "job_id": job.pk,
        "scan_type": job.scan_type,
        "scan_type_label": job.get_scan_type_display(),
        "status": job.status,
        "status_label": job.get_status_display(),
        "severity": SEVERITY_WARN,
        "headline": "",
        "doc_title": "",
        "reason": job.error_message or data.get("auto_apply_skipped") or "",
        "auto_applied": bool((job.applied_changes or {}).get("auto_applied")),
        "auto_apply_reason": (job.applied_changes or {}).get("auto_apply_reason") or "",
        "file_url": job.original_file.url if job.original_file else "",
        "file_name": (job.original_file.name or "").rsplit("/", 1)[-1] if job.original_file else "",
        "created_at": job.created_at,
        "fields": [],
        "vehicles": [],
        "missing_in_doc": [],
        "container_cars": [],
        "conflict": None,
        "conflicts": [],
        "matched_car": None,
        "warnings": _collect_warnings(data),
        "notes": [],
        "actions": [],
        "admin_url": reverse("admin:core_scanprocessingjob_change", args=[job.pk]),
    }

    if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
        _build_title_report(job, data, report)
    elif job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        _build_dock_report(job, data, report)
    else:
        report["headline"] = "Неизвестный тип документа"
        report["severity"] = SEVERITY_ERROR

    report["actions"] = _global_actions(job, report)
    report["diff_count"] = _count_diffs(report)
    if report["status"] == ScanProcessingJob.STATUS_PENDING:
        report["headline"] = "Ожидает обработки"
    elif report["status"] == ScanProcessingJob.STATUS_PROCESSING:
        report["headline"] = "AI читает документ…"
    return report


def _count_diffs(report: dict) -> int:
    """Сколько расхождений показать в свёрнутой строке задачи."""
    # «Только в системе» расхождением не считаем: документ просто не обязан
    # содержать всё, что уже заполнено в карточке.
    count = sum(1 for field in report["fields"] if field["state"] == STATE_DIFF)
    count += sum(1 for veh in report["vehicles"] if veh["state"] != VEHICLE_MATCHED or veh["weight_diff"])
    count += len(report["missing_in_doc"])
    count += len(report["warnings"])
    count += len(report["conflicts"])
    return count


def _global_actions(job: ScanProcessingJob, report: dict) -> list[dict]:
    actions = []
    if job.can_apply:
        blocked = bool(report.get("conflicts"))
        actions.append(
            {
                "action": "apply",
                "label": "Применить как есть",
                "kind": "primary",
                "confirm": "",
                "hint": (
                    "Сначала разрешите конфликт VIN ниже."
                    if blocked
                    else "Данные документа будут перенесены в карточки."
                ),
                "disabled": blocked,
            }
        )
        if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
            actions.append(
                {
                    "action": "force_new",
                    "label": "Создать новую машину",
                    "kind": "danger",
                    "confirm": (
                        "Создать НОВУЮ карточку машины с VIN из тайтла? "
                        "Это уместно, только если похожие VIN в базе — действительно другие машины."
                    ),
                    "hint": "VIN на тайтле верен, такой машины в базе ещё нет.",
                    "disabled": False,
                }
            )
        elif blocked:
            actions.append(
                {
                    "action": "force_new",
                    "label": "Все VIN в документе верны",
                    "kind": "danger",
                    "confirm": (
                        "Создать новые карточки по всем спорным VIN? "
                        "Это уместно, только если похожие VIN в базе — действительно другие машины."
                    ),
                    "hint": "Похожие VIN относятся к другим машинам, совпадение случайное.",
                    "disabled": False,
                }
            )
    if job.status in (ScanProcessingJob.STATUS_NEEDS_REVIEW, ScanProcessingJob.STATUS_ERROR):
        actions.append(
            {
                "action": "retry",
                "label": "Распознать заново",
                "kind": "neutral",
                "confirm": "",
                "hint": "AI перечитает документ с нуля.",
                "disabled": False,
            }
        )
        actions.append(
            {
                "action": "ignore",
                "label": "Отложить",
                "kind": "neutral",
                "confirm": "Пометить документ как проигнорированный?",
                "hint": "Документ уйдёт из списка требующих внимания.",
                "disabled": False,
            }
        )
    return actions


# ── Разрешение VIN-конфликта ───────────────────────────────────────────────


RESOLVE_ACTIONS = ("attach", "fix_car_vin", "force_new")

# Старое имя действия со страницы job — оставляем ради ссылок и закладок.
_LEGACY_ACTION_ALIASES = {"fix_existing_car_vin": "fix_car_vin"}


def _allowed_car_ids(job: ScanProcessingJob, data: dict) -> set[int]:
    """Машины, к которым разрешено привязывать документ этой задачи.

    Это кандидаты VIN-конфликта плюс машины контейнера, из карточки
    которого скан загружали: только там оператор может осмысленно
    выбрать машину.
    """
    allowed: set = set()
    conflicts = [data.get("vin_mismatch_review") or {}, *(data.get("vin_conflicts") or [])]
    for conflict in conflicts:
        for candidate in conflict.get("candidates") or []:
            if candidate.get("car_id"):
                allowed.add(candidate["car_id"])
    if job.target_container_id:
        allowed.update(job.target_container.container_cars.values_list("id", flat=True))
    return {int(car_id) for car_id in allowed if car_id}


def resolve_vin_conflict(
    job: ScanProcessingJob,
    action: str,
    *,
    car_id=None,
    chosen_vin: str = "",
    doc_vin: str = "",
    user=None,
) -> tuple[bool, str]:
    """Применяет решение оператора по спорному VIN и сразу применяет job.

    Возвращает ``(ok, message)``; сообщение пригодно для показа
    пользователю как есть.

    Действия:
      * ``attach`` — VIN в базе верен: документ относится к выбранной
        машине, её VIN не меняется.
      * ``fix_car_vin`` — верен VIN в документе: правим VIN карточки.
      * ``force_new`` — обе стороны правы, машины в базе просто нет:
        создаём новую карточку, пропуская проверку похожих VIN.

    ``doc_vin`` нужен для Dock Receipt: в одном документе спорных VIN
    может быть несколько, и решение принимается по каждому отдельно.
    """
    action = _LEGACY_ACTION_ALIASES.get(action, action)
    if action not in RESOLVE_ACTIONS:
        return False, f"Неизвестное действие: {action}"
    if not job.can_apply:
        return False, f"Задача в статусе «{job.get_status_display()}» — применять нечего"

    data = job.extracted_data or {}

    if action == "force_new":
        data["skip_vin_check"] = True
        data.pop("vin_mismatch_review", None)
        data.pop("vin_conflicts", None)
        job.extracted_data = data
        job.save(update_fields=["extracted_data"])
        message = (
            "Созданы новые карточки по VIN документа."
            if job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT
            else "Создана новая карточка машины."
        )
        return _apply_and_report(job, user, message)

    if job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
        return _resolve_dock_conflict(job, action, data, car_id=car_id, doc_vin=doc_vin, user=user)

    allowed = _allowed_car_ids(job, data)
    car = None
    if car_id:
        try:
            car_id = int(car_id)
        except (TypeError, ValueError):
            return False, "Некорректный идентификатор машины"
        if car_id not in allowed:
            return False, "Эта машина не из списка доступных для выбора"
        car = Car.objects.filter(pk=car_id).first()
    elif chosen_vin:
        car = Car.objects.filter(vin=_norm_vin(chosen_vin)).first()
        if car is None or car.id not in allowed:
            return False, "Выбранный VIN не из списка доступных"
    if car is None:
        return False, "Машина не найдена"

    if action == "attach":
        vins = data.get("vins") or []
        if vins:
            vins[0] = car.vin
        else:
            vins = [car.vin]
        data["vins"] = vins
        data.pop("vin_mismatch_review", None)
        job.extracted_data = data
        job.save(update_fields=["extracted_data"])
        return _apply_and_report(job, user, f"Тайтл прикреплён к машине {car.vin}.")

    # action == "fix_car_vin"
    extracted_vin = _norm_vin((data.get("vin_mismatch_review") or {}).get("extracted_vin"))
    if not extracted_vin:
        vins = [v for v in (_norm_vin(x) for x in (data.get("vins") or [])) if v]
        extracted_vin = vins[0] if vins else ""
    if not extracted_vin:
        return False, "В документе нет распознанного VIN"
    if extracted_vin == car.vin:
        return False, "VIN в карточке уже совпадает с VIN из документа"
    collision = Car.objects.filter(vin=extracted_vin).exclude(pk=car.pk).first()
    if collision:
        return False, (
            f"VIN {extracted_vin} уже занят другой карточкой (Car #{collision.id}) — разберите конфликт вручную"
        )

    old_vin = car.vin
    car.vin = extracted_vin
    car.save(update_fields=["vin"])
    data.pop("vin_mismatch_review", None)
    data["vins"] = [extracted_vin]
    job.extracted_data = data
    job.save(update_fields=["extracted_data"])
    return _apply_and_report(job, user, f"VIN машины исправлен: {old_vin} → {extracted_vin}. Тайтл прикреплён.")


def _resolve_dock_conflict(
    job: ScanProcessingJob,
    action: str,
    data: dict,
    *,
    car_id,
    doc_vin: str,
    user,
) -> tuple[bool, str]:
    """Решение по одному спорному VIN из Dock Receipt.

    В документе может быть несколько машин, поэтому применяем задачу
    только когда разобраны все спорные VIN, а до тех пор запоминаем
    принятые решения в ``extracted_data``.
    """
    conflicts = data.get("vin_conflicts") or []
    if not conflicts:
        return False, "По этому документу спорных VIN нет"

    doc_vin = _norm_vin(doc_vin)
    if not doc_vin and len(conflicts) == 1:
        doc_vin = _norm_vin(conflicts[0].get("extracted_vin"))
    if not doc_vin:
        return False, "Не указано, какой VIN документа разрешаем"
    conflict = next((c for c in conflicts if _norm_vin(c.get("extracted_vin")) == doc_vin), None)
    if conflict is None:
        return False, f"VIN {doc_vin} не в списке спорных"

    allowed = _allowed_car_ids(job, data)
    try:
        car_id = int(car_id)
    except (TypeError, ValueError):
        return False, "Некорректный идентификатор машины"
    if car_id not in allowed:
        return False, "Эта машина не из списка доступных для выбора"
    car = Car.objects.filter(pk=car_id).first()
    if car is None:
        return False, "Машина не найдена"

    if action == "attach":
        overrides = dict(data.get("vin_overrides") or {})
        overrides[doc_vin] = car.vin
        data["vin_overrides"] = overrides
        message = f"VIN {doc_vin} из документа отнесён к машине {car.vin}."
    else:  # fix_car_vin
        if car.vin == doc_vin:
            return False, "VIN в карточке уже совпадает с VIN из документа"
        collision = Car.objects.filter(vin=doc_vin).exclude(pk=car.pk).first()
        if collision:
            return False, (
                f"VIN {doc_vin} уже занят другой карточкой (Car #{collision.id}) — разберите конфликт вручную"
            )
        old_vin = car.vin
        car.vin = doc_vin
        car.save(update_fields=["vin"])
        message = f"VIN машины исправлен: {old_vin} → {doc_vin}."

    remaining = [c for c in conflicts if _norm_vin(c.get("extracted_vin")) != doc_vin]
    data["vin_conflicts"] = remaining
    job.extracted_data = data
    job.save(update_fields=["extracted_data"])

    if remaining:
        return True, f"{message} Осталось разобрать спорных VIN: {len(remaining)}."
    return _apply_and_report(job, user, f"{message} Документ применён.")


def _apply_and_report(job: ScanProcessingJob, user, success_message: str) -> tuple[bool, str]:
    from core.services.scan_applier import apply_job

    try:
        apply_job(job, applied_by=user)
    except Exception:
        logger.exception("Не удалось применить job #%s после разрешения VIN-конфликта", job.pk)
        return False, "Ошибка при применении — см. логи сервера"
    job.refresh_from_db()
    if job.status != ScanProcessingJob.STATUS_APPLIED:
        return False, job.error_message or "Задача осталась на проверке"
    return True, success_message
