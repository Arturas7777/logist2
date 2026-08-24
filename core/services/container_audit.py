"""Сверка данных контейнера — постоянная, а не в момент загрузки документа.

Проверки в пайплайне сканов срабатывают ровно один раз: когда документ
применяется. Из-за этого порядок действий влиял на надёжность — машина,
добавленная руками уже после применённого Dock Receipt, не сверялась с ним
никогда.

Здесь сверка устроена иначе: это функция от текущего состояния контейнера.
Её можно вызвать в любой момент, и она даст один и тот же ответ независимо
от того, что было раньше — сканы или ручной ввод.

В сеть модуль не ходит: расшифровка VIN берётся из кэша
:class:`core.models.VinCheck`, который наполняют форма ввода, applier
сканов и ежедневная фоновая задача.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.urls import reverse

from core.models.scans import ScanProcessingJob
from core.models.vin_checks import VinCheck
from core.services.vin_corrector import hamming_distance
from core.services.vin_gate import makes_match

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"
LEVEL_INFO = "info"

# Порядок важен: по нему считается итоговый уровень контейнера.
_LEVEL_ORDER = {LEVEL_INFO: 0, LEVEL_WARN: 1, LEVEL_ERROR: 2}

# Коды находок.
NO_DOCK_RECEIPT = "no_dock_receipt"
CAR_NOT_IN_DOCK_RECEIPT = "car_not_in_dock_receipt"
DOCK_RECEIPT_CAR_MISSING = "dock_receipt_car_missing"
VIN_NEAR_DUPLICATE = "vin_near_duplicate"
VIN_CHECKSUM_FAILED = "vin_checksum_failed"
VIN_NHTSA_UNKNOWN = "vin_nhtsa_unknown"
VIN_SPEC_MISMATCH = "vin_spec_mismatch"
VIN_NOT_CHECKED = "vin_not_checked"
TITLE_MISSING = "title_missing"
TITLE_DATA_MISMATCH = "title_data_mismatch"
WEIGHT_MISSING = "weight_missing"
SCAN_NEEDS_REVIEW = "scan_needs_review"

# Отличие в 1-2 символа из 17 — почти всегда опечатка, а не две разные машины.
_NEAR_DUPLICATE_MAX_DISTANCE = 2


@dataclass
class Finding:
    """Одно расхождение в данных контейнера."""

    code: str
    level: str
    message: str
    hint: str = ""
    car_id: int | None = None
    car_vin: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def car_url(self) -> str:
        return reverse("admin:core_car_change", args=[self.car_id]) if self.car_id else ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "hint": self.hint,
            "car_id": self.car_id,
            "car_vin": self.car_vin,
            "car_url": self.car_url,
            "details": self.details,
        }


def audit_container(container) -> list[Finding]:
    """Все расхождения в данных одного контейнера, от важных к второстепенным."""
    if container is None or not container.pk:
        return []

    cars = list(container.container_cars.all().only("id", "vin", "brand", "year", "weight_kg", "has_title"))
    doc_vins, doc_by_vin = _dock_receipt_vins(container)
    checks = _vin_checks_for(cars)

    findings: list[Finding] = []
    findings += _document_findings(container, cars, doc_vins, doc_by_vin)
    findings += _vin_findings(cars, checks)
    findings += _duplicate_findings(cars)
    findings += _title_findings(container, cars)
    findings += _pending_scan_findings(container)

    findings.sort(key=lambda f: -_LEVEL_ORDER.get(f.level, 0))
    return findings


def audit_level(findings: list[Finding]) -> str:
    """Итоговый уровень контейнера: худшая из находок.

    Справочные находки (нет тайтла, VIN ещё не проверен) уровень не
    поднимают — это не расхождения, а нормальное состояние процесса.
    """
    if not findings:
        return "ok"
    worst = max(_LEVEL_ORDER.get(f.level, 0) for f in findings)
    if worst >= _LEVEL_ORDER[LEVEL_ERROR]:
        return LEVEL_ERROR
    if worst >= _LEVEL_ORDER[LEVEL_WARN]:
        return LEVEL_WARN
    return "ok"


def build_audit_report(container) -> dict[str, Any]:
    """Готовый к отрисовке отчёт: находки, уровень и счётчики по важности.

    Справочные находки («тайтла ещё нет», «VIN пока не проверен») выносятся
    в отдельный список: их много и они есть почти всегда, поэтому в основном
    списке они бы утопили то, что действительно требует разбора.
    """
    all_findings = audit_container(container)
    findings = [f for f in all_findings if f.level != LEVEL_INFO]
    notes = [f for f in all_findings if f.level == LEVEL_INFO]
    level = audit_level(all_findings)
    errors = sum(1 for f in findings if f.level == LEVEL_ERROR)
    warns = sum(1 for f in findings if f.level == LEVEL_WARN)
    return {
        "level": level,
        "findings": [f.as_dict() for f in findings],
        "notes": [f.as_dict() for f in notes],
        "count": len(findings),
        "errors": errors,
        "warnings": warns,
        "headline": _headline(level, errors, warns),
    }


def _headline(level: str, errors: int, warns: int) -> str:
    if level == "ok":
        return "Данные контейнера сходятся"
    if errors and warns:
        return f"Расхождений: {errors}, замечаний: {warns}"
    if errors:
        return f"Расхождений: {errors}"
    return f"Замечаний: {warns}"


def refresh_container_audit(container, report: dict[str, Any] | None = None) -> str:
    """Пересчитывает и сохраняет денормализованный итог сверки.

    Нужен списку контейнеров: считать полную сверку для каждой строки на
    каждой отрисовке слишком дорого, поэтому уровень и число находок
    хранятся в самом контейнере. ``report`` можно передать готовым, если
    сверка уже посчитана рядом.
    """
    from django.utils import timezone

    if container is None or not container.pk:
        return "ok"
    if report is None:
        report = build_audit_report(container)
    level = report["level"]
    count = min(report["errors"] + report["warnings"], 32767)
    container.data_audit_level = level
    container.data_audit_count = count
    container.data_audit_checked_at = timezone.now()
    # update() вместо save(): пересчёт вызывается из сигнала сохранения
    # машины, и повторный save() контейнера утянул бы за собой sync_cars.
    type(container).objects.filter(pk=container.pk).update(
        data_audit_level=level,
        data_audit_count=count,
        data_audit_checked_at=container.data_audit_checked_at,
    )
    return level


# ── Источники данных ──────────────────────────────────────────────────────


def _dock_receipt_vins(container) -> tuple[set[str], dict[str, dict]]:
    """VIN из последнего применённого Dock Receipt этого контейнера.

    Берём именно применённый документ: пока job на проверке, её содержимое
    ещё может измениться, и строить на нём выводы рано.
    """
    job = (
        ScanProcessingJob.objects.filter(
            linked_container_id=container.pk,
            scan_type=ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT,
            status=ScanProcessingJob.STATUS_APPLIED,
        )
        .order_by("-applied_at", "-id")
        .first()
    )
    if job is None:
        return set(), {}
    overrides = {
        str(k).strip().upper(): str(v).strip().upper()
        for k, v in ((job.extracted_data or {}).get("vin_overrides") or {}).items()
    }
    by_vin: dict[str, dict] = {}
    for veh in (job.extracted_data or {}).get("vehicles") or []:
        vin = (veh.get("vin") or "").strip().upper()
        if not vin:
            continue
        # Оператор мог решить, что VIN документа относится к другой машине —
        # тогда в контейнере ожидается именно её VIN.
        by_vin[overrides.get(vin, vin)] = veh
    return set(by_vin), by_vin


def _vin_checks_for(cars) -> dict[str, VinCheck]:
    vins = [car.vin for car in cars if car.vin]
    if not vins:
        return {}
    return {check.vin: check for check in VinCheck.objects.filter(vin__in=vins)}


# ── Группы проверок ───────────────────────────────────────────────────────


def _document_findings(container, cars, doc_vins: set[str], doc_by_vin: dict[str, dict]) -> list[Finding]:
    findings: list[Finding] = []

    if not doc_vins:
        if not container.dock_receipt_scan:
            findings.append(
                Finding(
                    code=NO_DOCK_RECEIPT,
                    level=LEVEL_WARN if cars else LEVEL_INFO,
                    message="Dock Receipt не загружен",
                    hint="Пока документа нет, состав контейнера не с чем сверять.",
                )
            )
        return findings

    car_vins = {car.vin for car in cars if car.vin}

    for car in cars:
        if not car.vin or car.vin in doc_vins:
            continue
        closest = _closest(car.vin, doc_vins)
        hint = "Проверьте, та ли машина добавлена в контейнер."
        if closest:
            hint = (
                f"В документе есть похожий VIN {closest[0]} (отличие {closest[1]} симв.) — "
                "скорее всего, где-то опечатка."
            )
        findings.append(
            Finding(
                code=CAR_NOT_IN_DOCK_RECEIPT,
                level=LEVEL_ERROR,
                message=f"Машина {car.vin} есть в контейнере, но её нет в Dock Receipt",
                hint=hint,
                car_id=car.id,
                car_vin=car.vin,
                details={"closest": closest[0] if closest else "", "distance": closest[1] if closest else None},
            )
        )

    for vin in sorted(doc_vins - car_vins):
        veh = doc_by_vin.get(vin) or {}
        brand = " ".join(str(part) for part in (veh.get("make"), veh.get("model")) if part)
        findings.append(
            Finding(
                code=DOCK_RECEIPT_CAR_MISSING,
                level=LEVEL_ERROR,
                message=f"В Dock Receipt есть {vin}{f' ({brand})' if brand else ''}, но такой машины в контейнере нет",
                hint="Машину либо не добавили, либо позже перенесли в другой контейнер.",
                car_vin=vin,
            )
        )

    for car in cars:
        if car.vin in doc_vins and car.weight_kg in (None, 0):
            findings.append(
                Finding(
                    code=WEIGHT_MISSING,
                    level=LEVEL_WARN,
                    message=f"У машины {car.vin} не заполнена масса",
                    hint="Масса есть в Dock Receipt — стоит перенести её в карточку.",
                    car_id=car.id,
                    car_vin=car.vin,
                )
            )

    return findings


def _vin_findings(cars, checks: dict[str, VinCheck]) -> list[Finding]:
    findings: list[Finding] = []
    for car in cars:
        if not car.vin:
            continue
        check = checks.get(car.vin)
        if check is None:
            findings.append(
                Finding(
                    code=VIN_NOT_CHECKED,
                    level=LEVEL_INFO,
                    message=f"VIN {car.vin} ещё не проверен по базе NHTSA",
                    hint="Проверка выполнится фоновой задачей.",
                    car_id=car.id,
                    car_vin=car.vin,
                )
            )
            continue

        if check.is_north_american and not check.checksum_ok:
            findings.append(
                Finding(
                    code=VIN_CHECKSUM_FAILED,
                    level=LEVEL_ERROR,
                    message=f"VIN {car.vin} не проходит контрольную цифру",
                    hint="Для VIN из США и Канады это почти всегда ошибка в одном символе.",
                    car_id=car.id,
                    car_vin=car.vin,
                )
            )
        elif not check.nhtsa_ok and check.error_text and check.error_text != "NHTSA недоступен":
            findings.append(
                Finding(
                    code=VIN_NHTSA_UNKNOWN,
                    level=LEVEL_WARN,
                    message=f"NHTSA не распознаёт VIN {car.vin}",
                    hint=check.error_text,
                    car_id=car.id,
                    car_vin=car.vin,
                )
            )

        if check.nhtsa_ok:
            mismatch = _spec_mismatch(car, check)
            if mismatch:
                findings.append(mismatch)
    return findings


def _spec_mismatch(car, check: VinCheck) -> Finding | None:
    parts: list[str] = []
    if car.year and check.nhtsa_year and int(car.year) != int(check.nhtsa_year):
        parts.append(f"год {car.year} вместо {check.nhtsa_year}")
    if not makes_match(car.brand, check.nhtsa_make):
        parts.append(f"марка {car.brand} вместо {check.nhtsa_make}")
    if not parts:
        return None
    return Finding(
        code=VIN_SPEC_MISMATCH,
        level=LEVEL_WARN,
        message=f"Карточка {car.vin} расходится с расшифровкой VIN: {', '.join(parts)}",
        hint=f"NHTSA по этому VIN: {check.nhtsa_summary}. Проверьте, тот ли VIN введён.",
        car_id=car.id,
        car_vin=car.vin,
    )


def _duplicate_findings(cars) -> list[Finding]:
    """Два почти одинаковых VIN внутри одного контейнера — верный признак опечатки."""
    findings: list[Finding] = []
    reported: set[tuple[int, int]] = set()
    for i, car in enumerate(cars):
        for other in cars[i + 1 :]:
            if not car.vin or not other.vin:
                continue
            distance = hamming_distance(car.vin, other.vin)
            if distance is None or not 0 < distance <= _NEAR_DUPLICATE_MAX_DISTANCE:
                continue
            key = (min(car.id, other.id), max(car.id, other.id))
            if key in reported:
                continue
            reported.add(key)
            word = "символ" if distance == 1 else "символа"
            findings.append(
                Finding(
                    code=VIN_NEAR_DUPLICATE,
                    level=LEVEL_ERROR,
                    message=(
                        f"В контейнере два почти одинаковых VIN: {car.vin} и {other.vin} (отличие {distance} {word})"
                    ),
                    hint="Скорее всего это одна машина, заведённая дважды с опечаткой.",
                    car_id=car.id,
                    car_vin=car.vin,
                    details={"other_car_id": other.id, "other_vin": other.vin, "distance": distance},
                )
            )
    return findings


def _title_findings(container, cars) -> list[Finding]:
    findings: list[Finding] = []
    titles = _applied_title_data(container)
    for car in cars:
        if not car.has_title:
            findings.append(
                Finding(
                    code=TITLE_MISSING,
                    level=LEVEL_INFO,
                    message=f"У машины {car.vin} нет тайтла",
                    hint="Пока тайтл не пришёл — это нормально.",
                    car_id=car.id,
                    car_vin=car.vin,
                )
            )
            continue
        data = titles.get(car.vin)
        if not data:
            continue
        mismatch = _title_mismatch(car, data)
        if mismatch:
            findings.append(mismatch)
    return findings


def _applied_title_data(container) -> dict[str, dict]:
    """Что AI прочитал в применённых тайтлах машин этого контейнера."""
    jobs = ScanProcessingJob.objects.filter(
        scan_type=ScanProcessingJob.SCAN_TYPE_TITLE,
        status=ScanProcessingJob.STATUS_APPLIED,
        linked_car__container_id=container.pk,
    ).select_related("linked_car")
    result: dict[str, dict] = {}
    for job in jobs:
        if job.linked_car_id and job.linked_car and job.linked_car.vin:
            result[job.linked_car.vin] = job.extracted_data or {}
    return result


def _title_mismatch(car, data: dict) -> Finding | None:
    parts: list[str] = []
    doc_year = data.get("year")
    try:
        doc_year = int(doc_year) if doc_year else None
    except (TypeError, ValueError):
        doc_year = None
    if car.year and doc_year and int(car.year) != doc_year:
        parts.append(f"год {car.year} против {doc_year} в тайтле")

    # Сравниваем только марку: модель в тайтлах сокращается как попало
    # («CHEV CORVETTE Z» вместо «CHEVROLET CORVETTE Z06»), и придираться к
    # ней значит сделать панель бесполезной от шума.
    doc_make = data.get("make")
    if doc_make and not makes_match(car.brand, doc_make):
        parts.append(f"марка {car.brand} против {doc_make} в тайтле")

    if not parts:
        return None
    return Finding(
        code=TITLE_DATA_MISMATCH,
        level=LEVEL_WARN,
        message=f"Карточка {car.vin} расходится с тайтлом: {', '.join(parts)}",
        hint="Сверьте карточку со сканом тайтла.",
        car_id=car.id,
        car_vin=car.vin,
    )


def _pending_scan_findings(container) -> list[Finding]:
    """Документы, которые уже загружены, но ещё ждут решения оператора."""
    from django.db.models import Q

    pending = ScanProcessingJob.objects.filter(
        Q(target_container_id=container.pk) | Q(linked_container_id=container.pk),
        status__in=[ScanProcessingJob.STATUS_NEEDS_REVIEW, ScanProcessingJob.STATUS_ERROR],
    ).count()
    if not pending:
        return []
    return [
        Finding(
            code=SCAN_NEEDS_REVIEW,
            level=LEVEL_WARN,
            message=f"Документов ждут решения: {pending}",
            hint="Разберите их в панели «Документы» ниже — часть расхождений закроется сама.",
        )
    ]


def _closest(vin: str, candidates) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    for candidate in candidates:
        distance = hamming_distance(vin, candidate)
        if distance is None:
            continue
        if best is None or distance < best[1]:
            best = (candidate, distance)
    if best is None or best[1] > _NEAR_DUPLICATE_MAX_DISTANCE:
        return None
    return best
