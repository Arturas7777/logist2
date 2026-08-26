"""Редактор CMR: префилл из заявки, сохранение, доступ только сотруднику."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Car, Client
from core.models.carriers import Carrier
from core.models.company import Company
from core.models.warehouses import Warehouse
from core.models.website import ClientUser, TransportCmr, TransportRequest
from core.services.cmr import AUTO_KEYS, apply_prefill, parse_cmr_post, prefill_cmr

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(
        name="Klaipeda Terminal",
        address_name="Pagrindinė",
        address="Minijos g. 180, Klaipėda",
        general_email="terminal@example.lt",
    )


@pytest.fixture
def portal_client(db):
    return Client.objects.create(
        name="UAB Test Receiver",
        country="BY",
        physical_address="Minsk, Nezavisimosti 1",
        imones_kodas="123456789",
    )


@pytest.fixture
def company(db):
    return Company.objects.create(
        name=settings.COMPANY_NAME,
        physical_address="Vilniaus g. 1, Vilnius",
        registration_country="Lietuva",
        imones_kodas="305000000",
        vat_code="LT305000000",
    )


@pytest.fixture
def car(warehouse, portal_client):
    return Car.objects.create(
        year=2023,
        brand="Chevrolet Malibu",
        vin="1G1ZD5ST0PF171248",
        status="UNLOADED",
        client=portal_client,
        warehouse=warehouse,
        weight_kg=Decimal("1850.00"),
        has_title=True,
    )


@pytest.fixture
def transport_request(car, portal_client):
    tr = TransportRequest.objects.create(
        client=portal_client,
        carrier_name="MAXER TRANSPORT Sp. z.o.o.",
        carrier_eori="PL123456789",
        truck_number="ABC123",
        trailer_number="XYZ789",
        driver_name="Jonas Petraitis",
        driver_phone="+37060000000",
        border_crossing="Medininkai",
        planned_loading_date=datetime.date(2026, 9, 1),
        destination_country="BY",
        declaration_type="TRANSIT",
        status="SUBMITTED",
    )
    tr.cars.add(car)
    return tr


@pytest.fixture
def staff_client(client):
    user = User.objects.create_user(username="staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def portal_user(portal_client):
    user = User.objects.create_user(username="portal", password="secret123")
    ClientUser.objects.create(user=user, client=portal_client, is_verified=True)
    return user


def test_prefill_maps_request_and_car(transport_request, car, company):
    data = prefill_cmr(transport_request, car)

    assert company.name in data["sender"]
    assert "Vilniaus g. 1" in data["sender"]
    assert "UAB Test Receiver" in data["consignee"]
    assert "Minsk" in data["consignee"]
    assert data["delivery_country"] == "Baltarusija"
    assert "Klaipeda Terminal" in data["takeover_place"]
    assert data["takeover_country"] == "Lietuva"
    assert data["takeover_date"] == "01.09.2026"
    assert "T1" in data["annexed_docs"]
    assert "Title" in data["annexed_docs"]
    assert data["marks"] == car.vin
    assert data["packages"] == "1"
    assert car.vin in data["goods_nature"]
    assert "Chevrolet Malibu" in data["goods_nature"]
    assert data["weight_kg"] == "1850"
    assert "Medininkai" in data["sender_instructions"]
    assert "Транзит" in data["sender_instructions"]
    assert data["carrier"] == "MAXER TRANSPORT Sp. z.o.o."
    assert data["drivers"] == "Jonas Petraitis"
    assert data["truck_reg"] == "ABC123"
    assert data["trailer_reg"] == "XYZ789"


def test_prefill_uses_carrier_from_directory(transport_request, car, company):
    Carrier.objects.create(
        name="MAXER TRANSPORT Sp. z.o.o.",
        eori_code="PL123456789",
        physical_address="ul. Testowa 5, Warszawa",
        registration_country="Lenkija",
        imones_kodas="PL000",
    )
    data = prefill_cmr(transport_request, car)
    assert "ul. Testowa 5" in data["carrier"]
    assert "Lenkija" in data["carrier"]


def test_prefill_prefers_declaration_buyer(transport_request, car, company):
    from core.models.website import DeclarationRequest

    DeclarationRequest.objects.create(
        client=transport_request.client,
        car=car,
        transport_request=transport_request,
        buyer_name="Ivan Buyer",
        buyer_code="AB123",
        buyer_country="Belarus",
        buyer_address="Grodno, Sovetskaya 10",
        destination_country="Belarus",
        destination_city="Grodno",
        declaration_type="TRANSIT",
    )
    data = prefill_cmr(transport_request, car)
    assert "Ivan Buyer" in data["consignee"]
    assert "Grodno, Sovetskaya 10" in data["consignee"]
    assert data["delivery_place"] == "Grodno"
    assert data["delivery_country"] == "Belarus"


def test_apply_prefill_keeps_manual_fields():
    existing = {
        "sender": "old sender",
        "carrier_reservations": "glass damaged",
        "cod": "100 EUR",
        "following_carrier": "Second Ltd",
    }
    fresh = {key: f"new-{key}" for key in AUTO_KEYS}
    merged = apply_prefill(existing, fresh)
    assert merged["sender"] == "new-sender"
    assert merged["carrier_reservations"] == "glass damaged"
    assert merged["cod"] == "100 EUR"
    assert merged["following_carrier"] == "Second Ltd"


def test_editor_has_standard_bw_blank(staff_client, transport_request, car, company):
    from core.services.cmr import CMR_KEYS

    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    body = staff_client.get(url).content.decode()
    assert "TARPTAUTINIS KROVINIŲ TRANSPORTAVIMO" in body
    assert "VAŽTARAŠTIS" in body
    assert "INTERNATIONAL CONSIGNMENT NOTE" in body
    assert "LINAVA" not in body
    assert "cmr_blank.png" not in body
    for key in CMR_KEYS:
        assert f'name="{key}"' in body


def test_editor_renders_all_29_boxes(staff_client, transport_request, car, company):
    """Каждая графа бланка 1-29 присутствует со своим номером."""
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    body = staff_client.get(url).content.decode()
    for number in range(1, 30):
        assert f'class="n">{number}<' in body, f"нет графы {number}"


def test_editor_labels_are_bilingual(staff_client, transport_request, car, company):
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    body = staff_client.get(url).content.decode()
    pairs = [
        ("Siuntėjas (pavadinimas, adresas, šalis)", "Sender (name, address, country)"),
        ("Vežėjas (pavadinimas, adresas, šalis)", "Carrier (name, address, country)"),
        ("Krovinio pavadinimas*", "Nature of the goods*"),
        ("Apmokėjimui", "To be paid by:"),
        ("Ypatingos suderintos sąlygos", "Special agreements"),
        ("Registracinis nr.", "Vehicle reg. no."),
        ("Pareikštoji krovinio vertė", "Stated value of the goods"),
        ("Vilkikas/Tow", "Puspriek./Semitrailer"),
        ("Sutartis", "(ADR)"),
    ]
    for lt, en in pairs:
        assert lt in body, lt
        assert en in body, en


# Сколько строк письма в линованных графах — по эталонному скану бланка.
# В графах 1, 2, 16, 17, 18 всего шесть строк: строка подписи плюс пять линованных.
RULED_ROWS = {
    "sender": 5,
    "consignee": 5,
    "annexed_docs": 3,
    "carrier": 5,
    "following_carrier": 5,
    "carrier_reservations": 5,
    "marks": 10,
    "packages": 10,
    "packing": 10,
    "goods_nature": 10,
    "stat_no": 10,
    "weight_kg": 10,
    "volume_m3": 10,
    "sender_instructions": 5,
    "freight_payment": 2,
    "special_agreements": 2,
    "truck_reg": 4,
    "trailer_reg": 4,
    "truck_type": 4,
    "trailer_type": 4,
    "t1_rate_km": 4,
    "t1_total": 4,
}


def test_ruled_lines_are_elements_and_match_blank(staff_client, transport_request, car, company):
    """Линовка нарисована элементами: фоновые картинки браузер не печатает."""
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    body = staff_client.get(url).content.decode()
    assert "repeating-linear-gradient" not in body
    # Каждый кусок — одна линованная область: от её тега до начала следующей.
    blocks = body.split('<div class="ruled"')[1:]
    for name, rows in RULED_ROWS.items():
        found = [b for b in blocks if f'name="{name}"' in b]
        assert found, f"нет линовки в графе {name}"
        assert found[0].count("<i></i>") == rows, name


def test_blank_has_exactly_three_line_weights(staff_client, transport_request, car, company):
    """В бланке ровно три толщины линий: фоновая, базовая и жирная.

    Экранные значения целочисленные, печатные — точные по эталону; при 96 dpi
    точные схлопнулись бы в один пиксель, и градаций осталось бы две.
    """
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    body = staff_client.get(url).content.decode()
    weights = {"rule", "base", "bold"}
    # Любая другая переменная в px или pt — забытая четвёртая градация.
    assert set(re.findall(r"--([a-z-]+): \d+px;", body)) == weights
    assert set(re.findall(r"--([a-z-]+): [\d.]+pt;", body)) == weights
    assert set(re.findall(r"var\(--(rule|base|bold)\)", body)) == weights


def test_parse_cmr_post_keeps_known_keys_only():
    class FakePost(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    post = FakePost(sender="A", unknown="hack", marks="VIN")
    data = parse_cmr_post(post)
    assert data["sender"] == "A"
    assert data["marks"] == "VIN"
    assert "unknown" not in data
    assert data["cod"] == ""


def test_list_and_editor_staff_only(client, portal_user, transport_request, car):
    client.force_login(portal_user)
    list_url = reverse("admin_request_cmr_list", args=[transport_request.pk])
    edit_url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    assert client.get(list_url).status_code == 302
    assert client.get(edit_url).status_code == 302


def test_editor_creates_prefilled_draft(staff_client, transport_request, car, company):
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    response = staff_client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert car.vin in body
    assert "TARPTAUTINIS KROVINIŲ TRANSPORTAVIMO" in body
    assert company.name in body
    cmr = TransportCmr.objects.get(request=transport_request, car=car)
    assert cmr.data["marks"] == car.vin
    assert cmr.data["truck_reg"] == "ABC123"


def test_editor_saves_edits_and_prefill_preserves_manual(staff_client, transport_request, car, company):
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, car.pk])
    staff_client.get(url)
    response = staff_client.post(
        url,
        {
            "action": "save",
            "sender": "Custom sender",
            "marks": car.vin,
            "carrier_reservations": "scratch on door",
            "cod": "50 EUR",
        },
    )
    assert response.status_code == 302
    cmr = TransportCmr.objects.get(request=transport_request, car=car)
    assert cmr.data["sender"] == "Custom sender"
    assert cmr.data["carrier_reservations"] == "scratch on door"
    assert cmr.data["cod"] == "50 EUR"

    staff_client.post(
        url,
        {
            "action": "prefill",
            "sender": "Custom sender",
            "carrier_reservations": "scratch on door",
            "cod": "50 EUR",
        },
    )
    cmr.refresh_from_db()
    assert company.name in cmr.data["sender"]
    assert cmr.data["carrier_reservations"] == "scratch on door"
    assert cmr.data["cod"] == "50 EUR"


def test_editor_404_for_car_not_in_request(staff_client, transport_request, warehouse, portal_client):
    other = Car.objects.create(
        year=2020,
        brand="BMW",
        vin="WBA12345678901234",
        status="UNLOADED",
        client=portal_client,
        warehouse=warehouse,
    )
    url = reverse("admin_request_cmr_editor", args=[transport_request.pk, other.pk])
    assert staff_client.get(url).status_code == 404


def test_list_shows_cars_and_button_on_card(staff_client, transport_request, car):
    list_url = reverse("admin_request_cmr_list", args=[transport_request.pk])
    response = staff_client.get(list_url)
    assert response.status_code == 200
    assert car.vin in response.content.decode()

    card = staff_client.get(reverse("admin_request_card", args=[transport_request.pk]))
    body = card.content.decode()
    assert "Редактор CMR" in body
    assert reverse("admin_request_cmr_list", args=[transport_request.pk]) in body


def test_unique_one_cmr_per_request_car(transport_request, car, company):
    from django.db import IntegrityError, transaction

    TransportCmr.objects.create(request=transport_request, car=car, data={"marks": car.vin})
    with pytest.raises(IntegrityError), transaction.atomic():
        TransportCmr.objects.create(request=transport_request, car=car, data={})
