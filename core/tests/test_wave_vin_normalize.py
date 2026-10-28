"""L5: VIN нормализуется в одном месте и уникален без учёта регистра."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import Car, Container
from core.services.email_matcher import _VIN_RE
from core.services.scan_applier import _normalize_vin
from core.services.scan_review import _norm_vin
from core.services.vin import find_vins, normalize_vin
from core.services.vin_corrector import normalize_vin as ocr_normalize_vin
from core.services.vin_gate import normalize_vin_input


def test_normalize_vin_strips_all_whitespace_and_uppercases():
    assert normalize_vin("  wdb 1234\t5678\n901234 ") == "WDB12345678901234"
    assert normalize_vin(None) == ""


def test_all_callers_share_normalization():
    raw = " jt2ae 92e0k3 123456 "
    expected = "JT2AE92E0K3123456"
    assert normalize_vin_input(raw) == expected
    assert _normalize_vin(raw) == expected
    assert _norm_vin(raw) == expected
    assert ocr_normalize_vin(raw) == (expected, [])


def test_find_vins_keeps_order_and_dedupes_case_insensitive():
    text = "VINs: jt2ae92e0k3123456, WDB12345678901234 и снова JT2AE92E0K3123456"
    assert find_vins(text) == ["JT2AE92E0K3123456", "WDB12345678901234"]
    assert find_vins("") == []


def test_email_matcher_regex_has_no_capture_group():
    assert _VIN_RE.findall("x JT2AE92E0K3123456 y") == ["JT2AE92E0K3123456"]


@pytest.mark.django_db
def test_car_save_normalizes_vin():
    container = Container.objects.create(number="L5VN1234567", status="IN_PORT")
    car = Car.objects.create(year=2023, brand="Toyota", vin=" jt2ae92e0k3123456 ", container=container)

    car.refresh_from_db()
    assert car.vin == "JT2AE92E0K3123456"


@pytest.mark.django_db
def test_vin_unique_case_insensitive():
    container = Container.objects.create(number="L5VN7654321", status="IN_PORT")
    Car.objects.create(year=2023, brand="Toyota", vin="JT2AE92E0K3123456", container=container)

    clone = Car(year=2023, brand="Toyota", vin="jt2ae92e0k3123456", container=container)
    with pytest.raises(ValidationError):
        clone.validate_constraints()
    with pytest.raises(IntegrityError), transaction.atomic():
        Car.objects.bulk_create([clone])
