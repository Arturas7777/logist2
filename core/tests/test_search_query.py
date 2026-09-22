"""Поиск ломается, если в VIN из буфера обмена прилип невидимый символ."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Car, Client
from core.search_query import normalize_search_query

pytestmark = pytest.mark.django_db

VIN = "3VWEM7BU8RM057542"


def test_normalize_strips_zero_width_space():
    pasted = VIN + "\u200b"
    assert len(pasted) == 18
    assert normalize_search_query(pasted) == VIN


def test_normalize_strips_ltr_mark_and_bom():
    assert normalize_search_query("\ufeff" + VIN + "\u200e") == VIN


def test_normalize_nbsp_and_surrounding_spaces():
    assert normalize_search_query("  " + VIN + "\xa0") == VIN


@pytest.fixture
def staff_client(client):
    user = User.objects.create_user(username="staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def car(db):
    client_obj = Client.objects.create(name="Search Client")
    return Car.objects.create(year=2024, brand="Volkswagen Jetta", vin=VIN, status="UNLOADED", client=client_obj)


def test_global_search_finds_vin_with_zwsp(staff_client, car):
    url = reverse("global_search")
    dirty = staff_client.get(url, {"q": VIN + "\u200b"}).json()
    clean = staff_client.get(url, {"q": VIN}).json()

    dirty_vins = [item["label"] for group in dirty["groups"] for item in group["items"]]
    clean_vins = [item["label"] for group in clean["groups"] for item in group["items"]]
    assert any(VIN in label for label in dirty_vins)
    assert dirty_vins == clean_vins


def test_car_changelist_finds_vin_with_zwsp(staff_client, car):
    url = reverse("admin:core_car_changelist")
    response = staff_client.get(url, {"q": VIN + "\u200b"})
    assert response.status_code == 200
    assert VIN in response.content.decode()
    assert car.pk in {obj.pk for obj in response.context["cl"].queryset}
