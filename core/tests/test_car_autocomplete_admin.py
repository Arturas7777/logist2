"""Поиск машин в админке автовоза и инвойса — включая уже переданные.

Оформление рейса и счетов часто делают постфактум: машины уже TRANSFERRED,
но их нужно найти по VIN и добавить в новый автовоз / инвойс.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import Car, Carrier, Client
from core.models.auto_transport import AutoTransport

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    user = User.objects.create_user(username="staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def portal_client(db):
    return Client.objects.create(name="ACME Logistics")


def _car(*, vin, status, client, is_important=False):
    return Car.objects.create(
        year=2023,
        brand="Toyota Camry",
        vin=vin,
        status=status,
        client=client,
        is_important=is_important,
        transfer_date=timezone.now().date() if status == "TRANSFERRED" else None,
    )


def test_invoice_autocomplete_finds_transferred_car(staff_client, portal_client):
    car = _car(vin="JTDBL40E799000001", status="TRANSFERRED", client=portal_client)
    url = reverse("admin:newinvoice_cars_autocomplete")

    response = staff_client.get(url, {"term": "JTDBL40E799000001"})

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert car.pk in ids
    transferred = next(row for row in response.json()["results"] if row["id"] == car.pk)
    assert transferred["status"] == "TRANSFERRED"


def test_invoice_autocomplete_ranks_active_before_transferred(staff_client, portal_client):
    transferred = _car(vin="JTDBL40E799000011", status="TRANSFERRED", client=portal_client)
    active = _car(vin="JTDBL40E799000012", status="UNLOADED", client=portal_client)
    url = reverse("admin:newinvoice_cars_autocomplete")

    response = staff_client.get(url, {"term": "JTDBL40E799"})
    ids = [row["id"] for row in response.json()["results"]]

    assert ids.index(active.pk) < ids.index(transferred.pk)


def test_autotransport_autocomplete_finds_transferred_car(staff_client, portal_client):
    car = _car(vin="JTDBL40E799000021", status="TRANSFERRED", client=portal_client)
    url = reverse("admin:core_autotransport_cars_autocomplete")

    response = staff_client.get(url, {"term": "JTDBL40E799000021"})

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert car.pk in ids


def test_autotransport_autocomplete_hides_important_car(staff_client, portal_client):
    car = _car(vin="JTDBL40E799000031", status="UNLOADED", client=portal_client, is_important=True)
    url = reverse("admin:core_autotransport_cars_autocomplete")

    response = staff_client.get(url, {"term": "JTDBL40E799000031"})

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert car.pk not in ids


def test_autotransport_change_form_keeps_selected_transferred_car(staff_client, portal_client):
    car = _car(vin="JTDBL40E799000041", status="TRANSFERRED", client=portal_client)
    carrier = Carrier.objects.create(name="Maxer Transport")
    trip = AutoTransport.objects.create(carrier=carrier, status="DRAFT")
    trip.cars.add(car)

    url = reverse("admin:core_autotransport_change", args=[trip.pk])
    response = staff_client.get(url)
    body = response.content.decode()

    assert response.status_code == 200
    assert f'value="{car.pk}"' in body
    assert "selected" in body
    assert car.vin in body
