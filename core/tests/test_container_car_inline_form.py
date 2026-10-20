"""
Инлайн машин в карточке контейнера не затирает данные, записанные AI в фоне.

Сценарий из жизни: оператор открыл карточку контейнера, перетащил dock
receipt в панель документов, AI в фоне записал машинам вес, а оператор
нажал «Сохранить» на странице, где поля веса ещё пустые.

Запуск: pytest core/tests/test_container_car_inline_form.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.forms import modelform_factory

from core.admin.car_forms import ContainerCarInlineForm
from core.models import Car, VinCheck

pytestmark = pytest.mark.django_db

VIN = "4T1BF1FK0CU511111"

InlineForm = modelform_factory(
    Car,
    form=ContainerCarInlineForm,
    fields=["year", "brand", "vehicle_type", "vin", "vin_confirmed", "weight_kg", "has_title"],
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr("core.admin.car_forms.schedule_vin_check", lambda vin: None)
    VinCheck.objects.create(
        vin=VIN,
        length_ok=True,
        checksum_ok=True,
        is_north_american=True,
        nhtsa_ok=True,
        nhtsa_make="TOYOTA",
        nhtsa_model="CAMRY",
        nhtsa_year=2012,
        nhtsa_vehicle_type="PASSENGER CAR",
    )


def _car(**kwargs):
    defaults = {"vin": VIN, "year": 2012, "brand": "TOYOTA Camry", "status": "IN_PORT"}
    defaults.update(kwargs)
    return Car.objects.create(**defaults)


def _post(page: dict, **submitted) -> dict:
    """POST инлайна: ``page`` — значения на момент открытия страницы."""
    data = {"vin": VIN}
    for name, value in page.items():
        data[f"initial-{name}"] = value
        data[name] = value
    data.update(submitted)
    if data.get("has_title") in ("False", ""):
        data.pop("has_title")
    return data


PAGE_BEFORE_AI = {
    "year": "2012",
    "brand": "TOYOTA Camry",
    "vehicle_type": "SEDAN",
    "weight_kg": "",
    "has_title": "False",
}


def _ai_writes_in_background(car):
    Car.objects.filter(pk=car.pk).update(weight_kg=Decimal("1508.00"), has_title=True)
    return Car.objects.get(pk=car.pk)


def test_stale_form_without_edits_is_not_saved():
    car = _ai_writes_in_background(_car())

    form = InlineForm(data=_post(PAGE_BEFORE_AI), instance=car)

    assert form.is_valid(), form.errors
    assert not form.has_changed()


def test_editing_other_field_keeps_ai_weight_and_title():
    car = _ai_writes_in_background(_car())

    form = InlineForm(data=_post(PAGE_BEFORE_AI, brand="TOYOTA Camry LE"), instance=car)
    assert form.is_valid(), form.errors
    form.save()

    car.refresh_from_db()
    assert car.brand == "TOYOTA Camry LE"
    assert car.weight_kg == Decimal("1508.00")
    assert car.has_title is True


def test_operator_can_clear_weight_explicitly():
    car = _car(weight_kg=Decimal("1508.00"))
    page = {**PAGE_BEFORE_AI, "weight_kg": "1508.00"}

    form = InlineForm(data=_post(page, weight_kg=""), instance=car)
    assert form.is_valid(), form.errors
    form.save()

    car.refresh_from_db()
    assert car.weight_kg is None


def test_operator_weight_wins_over_background_value():
    car = _ai_writes_in_background(_car())

    form = InlineForm(data=_post(PAGE_BEFORE_AI, weight_kg="1600"), instance=car)
    assert form.is_valid(), form.errors
    form.save()

    car.refresh_from_db()
    assert car.weight_kg == Decimal("1600.00")
