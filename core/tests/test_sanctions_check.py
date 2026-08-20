"""Проверка на санкции: правила по памятке таможни и доступ к разделу.

Правила взяты из ``LENTELĖ SANKCIJOS / REIKALINGI DOKUMENTAI`` — см.
docstring :mod:`core.services.sanctions_check`. Тесты фиксируют именно
поведение таблицы: код КН, роль просвета 165 мм и возраста 5 лет.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Client
from core.models.website import ClientUser
from core.services import sanctions_check as sc
from core.views_website.errors import csrf_failure

TODAY = date(2026, 8, 20)


def check(**kwargs):
    return sc.check(sc.CheckInput(**kwargs), today=TODAY)


# ---------------------------------------------------------------------------
# Легковые: код КН по двигателю и объёму
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "cc", "code"),
    [
        ("PETROL", 900, "8703 21"),
        ("PETROL", 1400, "8703 22"),
        ("PETROL", 1800, "8703 23"),
        ("PETROL", 2500, "8703 23"),
        ("PETROL", 4000, "8703 24"),
        ("DIESEL", 1600, "8703 31/32"),
        ("DIESEL", 2200, "8703 32"),
        ("DIESEL", 3000, "8703 33"),
    ],
)
def test_cn_code_by_engine_and_volume(engine, cc, code):
    result = check(engine_type=engine, displacement_cc=cc, clearance="NO", year=2015)
    assert result.cn_code == code


@pytest.mark.parametrize(
    ("engine", "code"),
    [
        ("HYBRID_PETROL", "8703 40"),
        ("HYBRID_DIESEL", "8703 50"),
        ("PLUGIN_PETROL", "8703 60"),
        ("PLUGIN_DIESEL", "8703 70"),
        ("ELECTRIC", "8703 80"),
    ],
)
def test_cn_code_for_hybrids_and_electric(engine, code):
    """Гибридам и электро объём не нужен — код определяется типом привода."""
    result = check(engine_type=engine, clearance="NO", year=2024)
    assert result.cn_code == code
    assert result.verdict == sc.ALLOWED


# ---------------------------------------------------------------------------
# Просвет 165 мм и возраст 5 лет — главная развилка
# ---------------------------------------------------------------------------


def test_small_engine_is_never_sanctioned():
    """Бензин до 1900 см³ проходит даже у нового внедорожника."""
    result = check(engine_type="PETROL", displacement_cc=1800, clearance="YES", year=2025)
    assert result.verdict == sc.ALLOWED
    assert list(result.documents) == list(sc.BASE_DOCUMENTS)


def test_low_clearance_car_is_not_in_sanctioned_position():
    result = check(engine_type="PETROL", displacement_cc=3500, clearance="NO", year=2024)
    assert result.verdict == sc.ALLOWED
    assert "просвет" in " ".join(result.reasons).lower()


def test_high_clearance_older_than_five_years_needs_clearance_paper():
    result = check(engine_type="PETROL", displacement_cc=2500, clearance="YES", year=2018)
    assert result.verdict == sc.ALLOWED_WITH_EXTRA
    assert sc.CLEARANCE_DOCUMENT in result.documents


def test_high_clearance_newer_than_five_years_is_sanctioned():
    result = check(engine_type="PETROL", displacement_cc=2500, clearance="YES", year=2023)
    assert result.verdict == sc.SANCTIONED
    assert result.is_blocked
    assert not result.documents


def test_exactly_five_years_old_is_still_sanctioned():
    """«Старше 5 лет» — строго больше: ровно пять лет не проходит."""
    result = check(engine_type="DIESEL", displacement_cc=3000, clearance="YES", year=TODAY.year - 5)
    assert result.verdict == sc.SANCTIONED


def test_electric_suv_older_than_five_years_passes_with_paper():
    result = check(engine_type="ELECTRIC", clearance="YES", year=2015)
    assert result.verdict == sc.ALLOWED_WITH_EXTRA


# ---------------------------------------------------------------------------
# Нехватка данных
# ---------------------------------------------------------------------------


def test_unknown_clearance_asks_for_it():
    result = check(engine_type="PETROL", displacement_cc=2500, clearance="UNKNOWN", year=2018)
    assert result.verdict == sc.NEED_DATA
    assert any("росвет" in item for item in result.missing)


def test_missing_volume_asks_for_it():
    result = check(engine_type="PETROL", clearance="YES", year=2018)
    assert result.verdict == sc.NEED_DATA
    assert any("Объём" in item for item in result.missing)


def test_missing_year_asks_for_it():
    result = check(engine_type="PETROL", displacement_cc=2500, clearance="YES")
    assert result.verdict == sc.NEED_DATA
    assert "Год выпуска" in result.missing


# ---------------------------------------------------------------------------
# Остальные категории
# ---------------------------------------------------------------------------


def test_cargo_up_to_1900_passes_and_bigger_is_sanctioned():
    assert check(category="CARGO", displacement_cc=1800).verdict == sc.ALLOWED
    assert check(category="CARGO", displacement_cc=2500).verdict == sc.SANCTIONED


def test_motorcycle_is_not_sanctioned():
    result = check(category="MOTO", year=2024)
    assert result.verdict == sc.ALLOWED
    assert result.cn_code == "8711"


def test_collectible_needs_thirty_years():
    assert check(category="COLLECTIBLE", year=1980).verdict == sc.ALLOWED
    young = check(category="COLLECTIBLE", year=2010)
    assert young.verdict == sc.NEED_DATA


def test_price_above_limit_only_warns():
    result = check(engine_type="PETROL", displacement_cc=1400, clearance="NO", year=2018, price_eur=Decimal("70000"))
    assert result.verdict == sc.ALLOWED
    assert any("50 000 EUR" in w for w in result.warnings)


def test_moto_price_limit_is_lower():
    result = check(category="MOTO", year=2020, price_eur=Decimal("6000"))
    assert result.verdict == sc.ALLOWED
    assert any("5 000 EUR" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Данные NHTSA → поля формы
# ---------------------------------------------------------------------------


def test_guess_marks_suv_as_high_clearance():
    guess = sc.guess_from_nhtsa(
        {
            "body_class": "Sport Utility Vehicle (SUV)/Multi-Purpose Vehicle (MPV)",
            "fuel_primary": "Gasoline",
            "displacement_cc": 3500,
            "year": 2021,
            "vehicle_type": "MULTIPURPOSE PASSENGER VEHICLE (MPV)",
        }
    )
    assert guess["clearance"] == "YES"
    assert guess["engine_type"] == "PETROL"
    assert guess["category"] == "CAR"
    assert guess["displacement_cc"] == 3500


def test_guess_marks_sedan_as_low_clearance():
    guess = sc.guess_from_nhtsa({"body_class": "Sedan/Saloon", "fuel_primary": "Diesel", "displacement_cc": 2000})
    assert guess["clearance"] == "NO"
    assert guess["engine_type"] == "DIESEL"


def test_guess_detects_plugin_hybrid_and_electric():
    plugin = sc.guess_from_nhtsa({"fuel_primary": "Gasoline", "electrification": "PHEV (Plug-in Hybrid)"})
    assert plugin["engine_type"] == "PLUGIN_PETROL"

    hybrid = sc.guess_from_nhtsa({"fuel_primary": "Gasoline", "electrification": "Strong HEV"})
    assert hybrid["engine_type"] == "HYBRID_PETROL"

    electric = sc.guess_from_nhtsa({"fuel_primary": "Electric", "electrification": "BEV (Battery Electric Vehicle)"})
    assert electric["engine_type"] == "ELECTRIC"


def test_guess_marks_motorcycle():
    guess = sc.guess_from_nhtsa({"vehicle_type": "MOTORCYCLE", "body_class": "Street"})
    assert guess["category"] == "MOTO"


# ---------------------------------------------------------------------------
# Доступ к разделу в кабинете
# ---------------------------------------------------------------------------


@pytest.fixture
def by_client(db):
    return Client.objects.create(name="Клиент из Беларуси", country="BY")


@pytest.fixture
def kz_client(db):
    return Client.objects.create(name="Клиент из Казахстана", country="KZ")


def _portal_client(django_client, client_obj, username):
    user = User.objects.create_user(username=username, password="secret123")
    ClientUser.objects.create(user=user, client=client_obj, is_verified=True)
    django_client.force_login(user)
    return django_client


def test_page_open_for_belarusian_client(client, by_client):
    portal = _portal_client(client, by_client, "by-user")
    response = portal.get(reverse("website:sanctions_check"))
    assert response.status_code == 200
    assert "Проверка на санкции" in response.content.decode()


def test_page_closed_for_other_countries(client, kz_client):
    portal = _portal_client(client, kz_client, "kz-user")
    assert portal.get(reverse("website:sanctions_check")).status_code == 403


def test_menu_item_only_for_belarus(client, by_client, kz_client):
    by_portal = _portal_client(client, by_client, "by-menu")
    body = by_portal.get(reverse("website:dashboard")).content.decode()
    assert reverse("website:sanctions_check") in body

    kz_portal = _portal_client(client, kz_client, "kz-menu")
    body = kz_portal.get(reverse("website:dashboard")).content.decode()
    assert reverse("website:sanctions_check") not in body


def test_form_returns_verdict(client, by_client):
    portal = _portal_client(client, by_client, "by-post")
    response = portal.post(
        reverse("website:sanctions_check"),
        {
            "vin": "1HGCM82633A004352",
            "category": "CAR",
            "engine_type": "PETROL",
            "displacement_cc": "2500",
            "clearance": "YES",
            "year": "2024",
        },
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert sc.VERDICT_LABELS[sc.SANCTIONED] in body


def test_vin_lookup_uses_nhtsa(client, by_client, monkeypatch):
    portal = _portal_client(client, by_client, "by-vin")

    def fake_details(vin, **kwargs):
        assert vin == "1HGCM82633A004352"
        return {
            "ok": True,
            "raw_failed": False,
            "make": "HONDA",
            "model": "Accord",
            "year": 2019,
            "displacement_cc": 2400,
            "fuel_primary": "Gasoline",
            "electrification": "",
            "body_class": "Sedan/Saloon",
            "vehicle_type": "PASSENGER CAR",
        }

    monkeypatch.setattr("core.views_website.portal_sanctions.decode_vin_details", fake_details)
    response = portal.get(reverse("website:sanctions_vin_lookup"), {"vin": "1hgcm82633a004352"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["fields"]["engine_type"] == "PETROL"
    assert data["fields"]["clearance"] == "NO"
    assert data["fields"]["displacement_cc"] == 2400


def test_vin_lookup_reports_nhtsa_outage(client, by_client, monkeypatch):
    portal = _portal_client(client, by_client, "by-vin-down")
    monkeypatch.setattr(
        "core.views_website.portal_sanctions.decode_vin_details",
        lambda vin, **kwargs: {"ok": False, "raw_failed": True},
    )
    response = portal.get(reverse("website:sanctions_vin_lookup"), {"vin": "1HGCM82633A004352"})
    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_vin_lookup_closed_for_other_countries(client, kz_client):
    portal = _portal_client(client, kz_client, "kz-vin")
    response = portal.get(reverse("website:sanctions_vin_lookup"), {"vin": "1HGCM82633A004352"})
    assert response.status_code == 403


def test_vin_lookup_needs_no_csrf_token(client, by_client, monkeypatch):
    """Подстановка по VIN — запрос читающий, устаревший токен ей не мешает.

    Из-за CSRF на POST кнопка «Заполнить по VIN» отвечала HTML-страницей
    403, и скрипт падал на разборе JSON.
    """
    portal = _portal_client(client, by_client, "by-vin-nocsrf")
    monkeypatch.setattr(
        "core.views_website.portal_sanctions.decode_vin_details",
        lambda vin, **kwargs: {"ok": True, "raw_failed": False, "make": "FORD", "body_class": "Sedan/Saloon"},
    )
    response = portal.get(reverse("website:sanctions_vin_lookup"), {"vin": "1HGCM82633A004352"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


def test_csrf_failure_answers_json_to_ajax(rf):
    """AJAX должен получать JSON: на HTML-странице скрипты падали с SyntaxError."""
    ajax = csrf_failure(rf.post("/x/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
    assert ajax.status_code == 403
    assert ajax["Content-Type"].startswith("application/json")

    page = csrf_failure(rf.post("/x/"))
    assert page.status_code == 403
    assert "text/html" in page["Content-Type"]
