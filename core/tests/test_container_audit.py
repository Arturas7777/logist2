"""
Тесты постоянной сверки данных контейнера.

Главное, что здесь проверяется: результат зависит только от текущего
состояния, а не от того, что было раньше — сканы или ручной ввод. Поэтому
сценарии «машину добавили после применения Dock Receipt» и «Dock Receipt
пришёл после машин» должны давать одинаковые находки.

Запуск: pytest core/tests/test_container_audit.py
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import Car, Container, VinCheck
from core.models.scans import ScanProcessingJob
from core.services.container_audit import (
    CAR_NOT_IN_DOCK_RECEIPT,
    DOCK_RECEIPT_CAR_MISSING,
    NO_DOCK_RECEIPT,
    TITLE_DATA_MISMATCH,
    VIN_CHECKSUM_FAILED,
    VIN_NEAR_DUPLICATE,
    VIN_NHTSA_UNKNOWN,
    VIN_SPEC_MISMATCH,
    WEIGHT_MISSING,
    audit_container,
    build_audit_report,
    refresh_container_audit,
)

pytestmark = pytest.mark.django_db

VIN_A = "4T1BF1FK0CU511111"
VIN_B = "WBA3A5C50DF222222"
# Отличается от VIN_A одним символом — типичная опечатка.
VIN_A_TYPO = "4T1BF1FK0CU511112"


@pytest.fixture
def container():
    return Container.objects.create(number="MSDU1234567", status="FLOATING")


def _make_car(vin, container, **kwargs):
    defaults = {"year": 2012, "brand": "TOYOTA CAMRY", "status": "FLOATING", "weight_kg": 1500}
    defaults.update(kwargs)
    return Car.objects.create(vin=vin, container=container, **defaults)


def _applied_dock_receipt(container, vins, *, overrides=None):
    data = {
        "container_number": container.number,
        "vehicles": [{"vin": vin, "make": "TOYOTA", "model": "CAMRY", "year": 2012} for vin in vins],
    }
    if overrides:
        data["vin_overrides"] = overrides
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT,
        status=ScanProcessingJob.STATUS_APPLIED,
        extracted_data=data,
        linked_container=container,
        applied_at=timezone.now(),
    )


def _applied_title(car, **data):
    payload = {"vins": [car.vin], "make": "TOYOTA", "model": "CAMRY", "year": 2012}
    payload.update(data)
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_TITLE,
        status=ScanProcessingJob.STATUS_APPLIED,
        extracted_data=payload,
        linked_car=car,
        applied_at=timezone.now(),
    )


def _ok_check(vin, *, make="TOYOTA", model="CAMRY", year=2012):
    return VinCheck.objects.create(
        vin=vin,
        length_ok=True,
        checksum_ok=True,
        is_north_american=vin[0] in "12345",
        nhtsa_ok=True,
        nhtsa_make=make,
        nhtsa_model=model,
        nhtsa_year=year,
    )


def _codes(container):
    return [f.code for f in audit_container(container)]


# ── Состав контейнера против документа ─────────────────────────────────────


def test_container_without_dock_receipt_is_flagged(container):
    _make_car(VIN_A, container)
    _ok_check(VIN_A)
    assert NO_DOCK_RECEIPT in _codes(container)


def test_matching_container_and_document_have_no_findings(container):
    car = _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [car.vin])

    report = build_audit_report(container)
    assert report["errors"] == 0
    assert report["level"] == "ok"


def test_car_added_after_dock_receipt_is_found(container):
    """Ключевой сценарий: сверка смотрит на состояние, а не на событие."""
    first = _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [first.vin])

    # Машину заводят руками уже после применения документа — на момент
    # применения её не существовало, и старая проверка её бы не заметила.
    _make_car(VIN_B, container, brand="BMW 335I")
    _ok_check(VIN_B, make="BMW", model="335I", year=2013)

    findings = audit_container(container)
    codes = [f.code for f in findings]
    assert CAR_NOT_IN_DOCK_RECEIPT in codes
    assert next(f for f in findings if f.code == CAR_NOT_IN_DOCK_RECEIPT).car_vin == VIN_B


def test_document_car_missing_from_container_is_found(container):
    _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [VIN_A, VIN_B])

    findings = audit_container(container)
    missing = [f for f in findings if f.code == DOCK_RECEIPT_CAR_MISSING]
    assert len(missing) == 1
    assert missing[0].car_vin == VIN_B


def test_typo_in_manual_vin_shows_closest_document_vin(container):
    _make_car(VIN_A_TYPO, container)
    _ok_check(VIN_A_TYPO)
    _applied_dock_receipt(container, [VIN_A])

    finding = next(f for f in audit_container(container) if f.code == CAR_NOT_IN_DOCK_RECEIPT)
    assert finding.details["closest"] == VIN_A
    assert finding.details["distance"] == 1


def test_operator_decision_about_document_vin_is_respected(container):
    """Если оператор решил, что VIN документа относится к машине из базы."""
    car = _make_car(VIN_A_TYPO, container)
    _ok_check(VIN_A_TYPO)
    _applied_dock_receipt(container, [VIN_A], overrides={VIN_A: car.vin})

    codes = _codes(container)
    assert CAR_NOT_IN_DOCK_RECEIPT not in codes
    assert DOCK_RECEIPT_CAR_MISSING not in codes


def test_missing_weight_is_a_warning(container):
    car = _make_car(VIN_A, container, weight_kg=None)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [car.vin])

    report = build_audit_report(container)
    assert WEIGHT_MISSING in [f["code"] for f in report["findings"]]
    assert report["level"] == "warn"


# ── Проверки VIN ───────────────────────────────────────────────────────────


def test_two_similar_vins_in_one_container_are_reported_once(container):
    _make_car(VIN_A, container)
    _make_car(VIN_A_TYPO, container)
    _ok_check(VIN_A)
    _ok_check(VIN_A_TYPO)
    _applied_dock_receipt(container, [VIN_A, VIN_A_TYPO])

    duplicates = [f for f in audit_container(container) if f.code == VIN_NEAR_DUPLICATE]
    assert len(duplicates) == 1
    assert duplicates[0].details["distance"] == 1


def test_broken_checksum_is_reported(container):
    car = _make_car("4T1BF1FK1CU511111", container)
    VinCheck.objects.create(
        vin=car.vin,
        length_ok=True,
        checksum_ok=False,
        is_north_american=True,
        nhtsa_ok=False,
    )
    _applied_dock_receipt(container, [car.vin])
    assert VIN_CHECKSUM_FAILED in _codes(container)


def test_vin_unknown_to_nhtsa_is_a_warning(container):
    car = _make_car(VIN_B, container)
    VinCheck.objects.create(
        vin=car.vin,
        length_ok=True,
        checksum_ok=True,
        is_north_american=False,
        nhtsa_ok=False,
        error_text="Invalid characters",
    )
    _applied_dock_receipt(container, [car.vin])
    assert VIN_NHTSA_UNKNOWN in _codes(container)


def test_card_contradicting_vin_decoding_is_reported(container):
    car = _make_car(VIN_A, container, brand="VOLKSWAGEN TAOS", year=2021)
    _ok_check(car.vin, make="TOYOTA", model="CAMRY", year=2012)
    _applied_dock_receipt(container, [car.vin])
    assert VIN_SPEC_MISMATCH in _codes(container)


def test_nhtsa_outage_does_not_produce_findings(container):
    car = _make_car(VIN_A, container)
    VinCheck.objects.create(
        vin=car.vin,
        length_ok=True,
        checksum_ok=True,
        is_north_american=True,
        nhtsa_ok=False,
        error_text="NHTSA недоступен",
    )
    _applied_dock_receipt(container, [car.vin])
    assert build_audit_report(container)["errors"] == 0


# ── Тайтлы ─────────────────────────────────────────────────────────────────


def test_title_data_mismatch_is_reported(container):
    car = _make_car(VIN_A, container, has_title=True, year=2012)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [car.vin])
    _applied_title(car, year=2015)

    assert TITLE_DATA_MISMATCH in _codes(container)


def test_title_matching_card_produces_no_finding(container):
    car = _make_car(VIN_A, container, has_title=True)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [car.vin])
    _applied_title(car)

    assert TITLE_DATA_MISMATCH not in _codes(container)


@pytest.mark.parametrize(
    ("card_brand", "title_make"),
    [
        ("CHEVROLET MALIBU", "CHEV"),
        ("CHEVROLET EQUINOX", "CHEVY"),
        ("TOYOTA C-HR LE", "TOYOTA"),
        ("VOLKSWAGEN TAOS", "VOLKSWAGEN"),
    ],
)
def test_abbreviated_make_in_title_is_not_a_mismatch(container, card_brand, title_make):
    """В тайтлах марка сокращена — придираться к этому значит завалить панель шумом."""
    car = _make_car(VIN_A, container, has_title=True, brand=card_brand)
    _ok_check(VIN_A, make=title_make)
    _applied_dock_receipt(container, [car.vin])
    _applied_title(car, make=title_make, model="")

    codes = _codes(container)
    assert TITLE_DATA_MISMATCH not in codes
    assert VIN_SPEC_MISMATCH not in codes


def test_title_model_written_as_make_is_not_a_mismatch(container):
    """OCR тайтла часто ставит модель в поле марки — NHTSA это не опровергает."""
    car = _make_car(VIN_A, container, has_title=True, brand="CHEVROLET EQUINOX")
    _ok_check(VIN_A, make="CHEVROLET", model="Equinox")
    _applied_dock_receipt(container, [car.vin])
    _applied_title(car, make="EQUINOX", model="LT")

    assert TITLE_DATA_MISMATCH not in _codes(container)


def test_different_make_in_title_is_still_a_mismatch(container):
    car = _make_car(VIN_A, container, has_title=True, brand="TOYOTA CAMRY")
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [car.vin])
    _applied_title(car, make="VOLKSWAGEN", model="TAOS")

    assert TITLE_DATA_MISMATCH in _codes(container)


# ── Денормализованный итог ─────────────────────────────────────────────────


def test_refresh_stores_level_and_count(container):
    _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [VIN_A, VIN_B])

    level = refresh_container_audit(container)
    container.refresh_from_db()

    assert level == Container.AUDIT_LEVEL_ERROR
    assert container.data_audit_level == Container.AUDIT_LEVEL_ERROR
    assert container.data_audit_count >= 1
    assert container.data_audit_checked_at is not None


def test_clean_container_gets_ok_level(container):
    _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [VIN_A])

    assert refresh_container_audit(container) == Container.AUDIT_LEVEL_OK


def test_saving_car_updates_container_badge(container, django_capture_on_commit_callbacks):
    _make_car(VIN_A, container)
    _ok_check(VIN_A)
    _applied_dock_receipt(container, [VIN_A])
    refresh_container_audit(container)

    # Машина, добавленная руками, должна сама поднять уровень контейнера.
    # Пересчёт отложен до конца транзакции — при сохранении инлайна машин
    # сигнал срабатывает на каждой строке, а контейнер у них общий.
    with django_capture_on_commit_callbacks(execute=True):
        _make_car(VIN_B, container, brand="BMW 335I")

    container.refresh_from_db()
    assert container.data_audit_level == Container.AUDIT_LEVEL_ERROR
