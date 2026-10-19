"""Массовое действие «Скопировать» в списке авто: «N. Год Марка VIN» по строке на авто."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.admin.car import car_copy_line, cars_copy_text
from core.models import Car

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    user = get_user_model().objects.create_user(
        username="copy-staff", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    return client


def test_copy_text_is_numbered_one_car_per_line():
    rows = [(2022, "Honda Accord", "1HGCV1F30NA000001"), (2019, " BMW 430I ", "WBA4J1C50KB000002")]

    assert cars_copy_text(rows) == "1. 2022 Honda Accord 1HGCV1F30NA000001\n2. 2019 BMW 430I WBA4J1C50KB000002"
    assert car_copy_line(None, "Toyota", "JT000000000000003") == "Toyota JT000000000000003"
    assert cars_copy_text([]) == ""


def test_changelist_rows_carry_copy_line(staff_client):
    Car.objects.create(year=2022, brand="Honda Accord", vin="1HGCV1F30NA000001", status="UNLOADED")

    body = staff_client.get(reverse("admin:core_car_changelist")).content.decode()

    assert 'data-copy-line="2022 Honda Accord 1HGCV1F30NA000001"' in body
    assert 'value="copy_selected_cars"' in body


def test_server_fallback_page_lists_selected_cars(staff_client):
    first = Car.objects.create(year=2022, brand="Honda Accord", vin="1HGCV1F30NA000001", status="UNLOADED")
    second = Car.objects.create(year=2019, brand="BMW 430I", vin="WBA4J1C50KB000002", status="UNLOADED")
    Car.objects.create(year=2020, brand="Audi A4", vin="WAUZZZF40LA000003", status="UNLOADED")

    response = staff_client.post(
        reverse("admin:core_car_changelist"),
        {"action": "copy_selected_cars", "_selected_action": [first.pk, second.pk], "index": 0},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "2022 Honda Accord 1HGCV1F30NA000001" in body
    assert "2019 BMW 430I WBA4J1C50KB000002" in body
    assert "Audi A4" not in body
