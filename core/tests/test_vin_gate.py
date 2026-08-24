"""
Тесты единой проверки VIN на входе.

Покрывают:
- нормализацию введённого VIN (пробелы, регистр);
- находки: длина, запрещённые символы, контрольная цифра, похожий VIN
  в базе, расхождение с расшифровкой NHTSA;
- кэш VinCheck: повторная проверка не ходит в сеть, неудачная протухает;
- форму админки: сохранение блокируется до подтверждения оператора.

Запуск: pytest core/tests/test_vin_gate.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.forms import modelform_factory
from django.utils import timezone

from core.admin.car_forms import VinGuardForm
from core.models import Car, Container, VinCheck
from core.services.vin_gate import (
    ISSUE_CHECKSUM,
    ISSUE_FORBIDDEN_CHARS,
    ISSUE_LENGTH,
    ISSUE_NEAR_DUPLICATE,
    ISSUE_NHTSA_UNKNOWN,
    ISSUE_SPEC_MISMATCH,
    check_vin,
    get_vin_check,
    normalize_vin_input,
    refresh_vin_check,
)

pytestmark = pytest.mark.django_db

# Североамериканский VIN с корректной контрольной цифрой (позиция 9).
VALID_NA_VIN = "4T1BF1FK0CU511111"
# Он же с испорченной контрольной цифрой.
BROKEN_NA_VIN = "4T1BF1FK1CU511111"
# Отличается от VALID_NA_VIN одним последним символом.
NEAR_VIN = "4T1BF1FK0CU511112"
# Европейский VIN: контрольная цифра по ISO у него не сходится штатно.
EU_VIN = "WBA3A5C50DF111111"


def _codes(verdict):
    return [issue.code for issue in verdict.issues]


def _make_car(vin, **kwargs):
    defaults = {"year": 2020, "brand": "BMW X5", "status": "FLOATING"}
    defaults.update(kwargs)
    return Car.objects.create(vin=vin, **defaults)


def _nhtsa(vin, *, make="TOYOTA", model="CAMRY", year=2012, ok=True):
    return VinCheck.objects.create(
        vin=vin,
        length_ok=True,
        checksum_ok=True,
        is_north_american=vin[0] in "12345",
        nhtsa_ok=ok,
        nhtsa_make=make,
        nhtsa_model=model,
        nhtsa_year=year,
    )


# ── Нормализация ───────────────────────────────────────────────────────────


def test_normalize_strips_spaces_and_uppercases():
    assert normalize_vin_input(" 4t1bf1 fk0cu511111 ") == VALID_NA_VIN


def test_check_vin_normalizes_before_checking():
    _nhtsa(VALID_NA_VIN)
    verdict = check_vin(" 4t1bf1fk0cu511111 ", allow_network=False)
    assert verdict.vin == VALID_NA_VIN
    assert verdict.ok


# ── Находки ────────────────────────────────────────────────────────────────


def test_short_vin_reported_once():
    verdict = check_vin("ABC123", allow_network=False)
    assert _codes(verdict) == [ISSUE_LENGTH]
    assert "17" in verdict.issues[0].message


def test_forbidden_letters_suggest_visual_twins():
    # I, O и Q в VIN по ISO 3779 не используются вовсе.
    verdict = check_vin("4T1BF1FKOCU5I1111", allow_network=False)
    assert _codes(verdict) == [ISSUE_FORBIDDEN_CHARS]
    assert verdict.issues[0].suggestion == "4T1BF1FK0CU511111"


def test_broken_checksum_offers_correction():
    verdict = check_vin(BROKEN_NA_VIN, allow_network=False, check_duplicates=False)
    assert ISSUE_CHECKSUM in _codes(verdict)


def test_european_vin_not_flagged_by_checksum():
    _nhtsa(EU_VIN, make="BMW", model="335I", year=2013)
    verdict = check_vin(EU_VIN, allow_network=False, check_duplicates=False)
    # Контрольная цифра у европейских VIN не сходится штатно — это не находка.
    assert ISSUE_CHECKSUM not in _codes(verdict)


def test_similar_vin_in_database_is_reported():
    existing = _make_car(NEAR_VIN, brand="TOYOTA CAMRY")
    _nhtsa(VALID_NA_VIN)
    verdict = check_vin(VALID_NA_VIN, allow_network=False)

    assert ISSUE_NEAR_DUPLICATE in _codes(verdict)
    issue = next(i for i in verdict.issues if i.code == ISSUE_NEAR_DUPLICATE)
    assert issue.suggestion == existing.vin
    assert "1 символ" in issue.message


def test_own_vin_is_not_a_duplicate_of_itself():
    car = _make_car(NEAR_VIN)
    _nhtsa(car.vin)
    verdict = check_vin(car.vin, exclude_car_id=car.id, allow_network=False)
    assert ISSUE_NEAR_DUPLICATE not in _codes(verdict)


def test_brand_and_year_contradicting_vin_are_reported():
    _nhtsa(VALID_NA_VIN, make="TOYOTA", model="CAMRY", year=2012)
    verdict = check_vin(
        VALID_NA_VIN,
        brand="VOLKSWAGEN TAOS",
        year=2021,
        allow_network=False,
        check_duplicates=False,
    )
    assert _codes(verdict) == [ISSUE_SPEC_MISMATCH]
    assert "2012" in verdict.issues[0].message


def test_brand_written_differently_is_not_a_mismatch():
    _nhtsa(VALID_NA_VIN, make="TOYOTA", model="CAMRY", year=2012)
    verdict = check_vin(
        VALID_NA_VIN,
        brand="TOYOTA CAMRY SE",
        year=2012,
        allow_network=False,
        check_duplicates=False,
    )
    assert verdict.ok


def test_vin_unknown_to_nhtsa_is_reported():
    VinCheck.objects.create(
        vin=VALID_NA_VIN,
        length_ok=True,
        checksum_ok=True,
        is_north_american=True,
        nhtsa_ok=False,
        error_text="Invalid characters",
    )
    verdict = check_vin(VALID_NA_VIN, allow_network=False, check_duplicates=False)
    assert _codes(verdict) == [ISSUE_NHTSA_UNKNOWN]


def test_nhtsa_outage_does_not_produce_findings():
    VinCheck.objects.create(
        vin=VALID_NA_VIN,
        length_ok=True,
        checksum_ok=True,
        is_north_american=True,
        nhtsa_ok=False,
        error_text="NHTSA недоступен",
    )
    verdict = check_vin(VALID_NA_VIN, allow_network=False, check_duplicates=False)
    # Недоступность внешнего сервиса — не проблема введённого VIN.
    assert verdict.ok


# ── Кэш проверок ───────────────────────────────────────────────────────────


def test_refresh_vin_check_stores_snapshot(monkeypatch):
    monkeypatch.setattr(
        "core.services.vin_gate.decode_vin_nhtsa",
        lambda vin, timeout=5: {
            "ok": True,
            "make": "TOYOTA",
            "model": "CAMRY",
            "year": 2012,
            "error_code": "0",
            "error_text": "",
            "suggested_vin": "",
            "raw_failed": False,
        },
    )
    check = refresh_vin_check(VALID_NA_VIN)
    assert check.nhtsa_ok is True
    assert check.nhtsa_summary == "TOYOTA CAMRY 2012"
    assert check.checksum_ok is True


def test_successful_check_is_not_refetched(monkeypatch):
    _nhtsa(VALID_NA_VIN)
    calls = []
    monkeypatch.setattr(
        "core.services.vin_gate.decode_vin_nhtsa",
        lambda vin, timeout=5: calls.append(vin) or {"raw_failed": True},
    )
    get_vin_check(VALID_NA_VIN)
    assert calls == []


def test_stale_failed_check_is_refetched(monkeypatch):
    old = VinCheck.objects.create(
        vin=VALID_NA_VIN,
        length_ok=True,
        nhtsa_ok=False,
        error_text="NHTSA недоступен",
    )
    VinCheck.objects.filter(pk=old.pk).update(checked_at=timezone.now() - timedelta(days=VinCheck.STALE_AFTER_DAYS + 1))
    calls = []
    monkeypatch.setattr(
        "core.services.vin_gate.decode_vin_nhtsa",
        lambda vin, timeout=5: calls.append(vin)
        or {
            "ok": True,
            "make": "TOYOTA",
            "model": "CAMRY",
            "year": 2012,
            "error_code": "0",
            "error_text": "",
            "suggested_vin": "",
            "raw_failed": False,
        },
    )
    check = get_vin_check(VALID_NA_VIN)
    assert calls == [VALID_NA_VIN]
    assert check.nhtsa_ok is True


# ── Форма админки ──────────────────────────────────────────────────────────


# Админка строит форму через modelform_factory по полям своих fieldsets —
# повторяем то же, чтобы не тянуть в тест обязательные финансовые поля.
CarForm = modelform_factory(Car, form=VinGuardForm, fields=["vin", "year", "brand", "container", "vin_confirmed"])


def _form_data(vin, **extra):
    data = {"vin": vin, "year": 2012, "brand": "TOYOTA CAMRY"}
    data.update(extra)
    return data


def test_form_rejects_suspicious_vin_without_confirmation():
    _make_car(NEAR_VIN, brand="TOYOTA CAMRY")
    _nhtsa(VALID_NA_VIN)

    form = CarForm(data=_form_data(VALID_NA_VIN))
    assert not form.is_valid()
    assert "vin" in form.errors
    assert "похожий VIN" in " ".join(form.errors["vin"])


def test_form_accepts_suspicious_vin_after_confirmation():
    _make_car(NEAR_VIN, brand="TOYOTA CAMRY")
    _nhtsa(VALID_NA_VIN)

    form = CarForm(data=_form_data(VALID_NA_VIN, vin_confirmed="on"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["vin"] == VALID_NA_VIN


def test_form_normalizes_vin_without_complaining():
    _nhtsa(VALID_NA_VIN)
    form = CarForm(data=_form_data(" 4t1bf1fk0cu511111 "))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["vin"] == VALID_NA_VIN


def test_form_saves_car_and_schedules_check(monkeypatch):
    scheduled = []
    monkeypatch.setattr("core.admin.car_forms.schedule_vin_check", scheduled.append)
    _nhtsa(VALID_NA_VIN)

    container = Container.objects.create(number="MSDU7654321", status="FLOATING")
    form = CarForm(data=_form_data(VALID_NA_VIN, container=container.id))
    assert form.is_valid(), form.errors
    car = form.save()

    assert car.vin == VALID_NA_VIN
    assert scheduled == [VALID_NA_VIN]
