"""Условия хранения клиента: себестоимость — по складу, клиенту — по его условиям."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client as DjangoClient
from django.urls import reverse
from django.utils import timezone

from core.models import Car, CarService, Client, ClientStorageTerms, Company, Container, Warehouse, WarehouseService
from core.models_billing import NewInvoice


@pytest.fixture
def warehouse(db):
    wh = Warehouse.objects.create(name="NETO-S", free_days=7)
    WarehouseService.objects.create(
        warehouse=wh,
        name="Хранение",
        code="storage",
        default_price=Decimal("5"),
        is_active=True,
        add_by_default=True,
    )
    return wh


@pytest.fixture
def client(db):
    return Client.objects.create(name="Storage Client")


def _car(warehouse, client, vin, days_on_storage=10):
    return Car.objects.create(
        year=2022,
        brand="Honda",
        vin=vin,
        status="UNLOADED",
        container=Container.objects.create(number=f"C-{vin[-6:]}", status="FLOATING"),
        warehouse=warehouse,
        client=client,
        unload_date=timezone.localdate() - timedelta(days=days_on_storage - 1),
    )


def _storage_service(car):
    return CarService.objects.get(car=car, service_type="WAREHOUSE")


@pytest.mark.django_db
class TestClientStorage:
    def test_without_terms_client_pays_warehouse_cost(self, warehouse, client):
        car = _car(warehouse, client, "STORAGETERMS00001")

        car.calculate_total_price()

        assert car.days == 3
        assert car.total_price == Decimal("15")
        assert _storage_service(car).markup_amount == Decimal("0")

    def test_client_terms_turn_into_storage_markup(self, warehouse, client):
        ClientStorageTerms.objects.create(client=client, free_days=5, daily_rate=Decimal("6"))
        car = _car(warehouse, client, "STORAGETERMS00002")

        car.calculate_total_price()

        svc = _storage_service(car)
        # Склад: 10 дней − 7 бесплатных = 3 × 5 € = 15 (себестоимость).
        # Клиенту: 10 − 5 = 5 × 6 € = 30 → наценка 15.
        assert car.days == 3
        assert car.storage_cost == Decimal("15")
        assert svc.custom_price == Decimal("15")
        assert svc.markup_amount == Decimal("15")
        assert car.total_price == Decimal("30")
        assert car.get_client_storage()["paid_days"] == 5

    def test_warehouse_specific_terms_beat_any_warehouse(self, warehouse, client):
        ClientStorageTerms.objects.create(client=client, free_days=5, daily_rate=Decimal("6"))
        ClientStorageTerms.objects.create(client=client, warehouse=warehouse, daily_rate=Decimal("8"))
        car = _car(warehouse, client, "STORAGETERMS00003")

        storage = car.get_client_storage()

        # Строка склада: бесплатные дни пусто → как у склада (7).
        assert storage["free_days"] == 7
        assert storage["amount"] == Decimal("24")

    def test_empty_rate_follows_warehouse(self, warehouse, client):
        ClientStorageTerms.objects.create(client=client, free_days=5)
        car = _car(warehouse, client, "STORAGETERMS00004")

        assert car.get_client_storage()["amount"] == Decimal("25")

    def test_client_pays_before_warehouse_starts_charging(self, warehouse, client):
        ClientStorageTerms.objects.create(client=client, free_days=5, daily_rate=Decimal("6"))
        car = _car(warehouse, client, "STORAGETERMS00005", days_on_storage=6)
        company = Company.objects.create(name="Caromoto Lithuania, MB")
        inv = NewInvoice.objects.create(
            issuer_company=company, recipient_client=client, date=timezone.localdate(), status="DRAFT"
        )
        inv.cars.add(car)

        inv.regenerate_items_from_cars()

        assert inv.items.get(description="Хран").unit_price == Decimal("6")
        svc = _storage_service(car)
        assert svc.custom_price == Decimal("0")
        assert svc.markup_amount == Decimal("6")


@pytest.mark.django_db(transaction=True)
class TestTermsChangeReprices:
    """Реальные коммиты: on_commit-пересчёт срабатывает как в проде."""

    def test_saving_terms_reprices_only_active_cars(self, warehouse, client):
        active = _car(warehouse, client, "STORAGETERMS00006")
        transferred = _car(warehouse, client, "STORAGETERMS00007")
        Car.objects.filter(pk=transferred.pk).update(status="TRANSFERRED", transfer_date=timezone.localdate())
        active.refresh_from_db()
        assert active.total_price == Decimal("15")

        ClientStorageTerms.objects.create(client=client, free_days=5, daily_rate=Decimal("6"))

        active.refresh_from_db()
        transferred.refresh_from_db()
        assert active.total_price == Decimal("30")
        assert transferred.total_price == Decimal("15")
        assert _storage_service(transferred).markup_amount == Decimal("0")

    def test_deleting_terms_returns_to_warehouse_cost(self, warehouse, client):
        terms = ClientStorageTerms.objects.create(client=client, free_days=5, daily_rate=Decimal("6"))
        car = _car(warehouse, client, "STORAGETERMS00008")
        car.refresh_from_db()
        assert car.total_price == Decimal("30")

        terms.delete()

        car.refresh_from_db()
        assert car.total_price == Decimal("15")
        assert _storage_service(car).markup_amount == Decimal("0")


def _admin_post_data(response):
    """Значения формы клиента и инлайнов так, как их отдала страница."""
    data = {}
    form = response.context["adminform"].form
    for name in form.fields:
        value = form[name].value()
        data[name] = "" if value is None else value
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        for name in formset.management_form.fields:
            value = formset.management_form[name].value()
            data[formset.management_form.add_prefix(name)] = "" if value is None else value
        for inline_form in formset.forms:
            for name in inline_form.fields:
                value = inline_form[name].value()
                data[inline_form.add_prefix(name)] = "" if value is None else value
    return data


@pytest.mark.django_db
def test_admin_saves_new_client_storage_terms(warehouse):
    """Новая строка с индексом 0 сохраняется. Шаблон __prefix__ — нет."""
    user = get_user_model().objects.create_user(
        username="storage-terms-staff", password="secret123", is_staff=True, is_superuser=True
    )
    http = DjangoClient()
    http.force_login(user)
    customer = Client.objects.create(name="Terms Admin")
    url = reverse("admin:core_client_change", args=[customer.pk])

    page = http.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="storage_terms-empty"' in html
    assert 'name="storage_terms-__prefix__-warehouse"' in html
    css = Path("core/static/css/dashboard_admin.css").read_text(encoding="utf-8")
    assert ".cm-card-inline.module tbody tr.form-row.empty-form" in css

    ignored = _admin_post_data(page)
    ignored["storage_terms-__prefix__-warehouse"] = str(warehouse.pk)
    ignored["storage_terms-__prefix__-free_days"] = "5"
    ignored["storage_terms-__prefix__-daily_rate"] = "8.50"
    ignored["_save"] = "Save"
    http.post(url, ignored)
    assert not customer.storage_terms.exists()

    saved = _admin_post_data(page)
    saved["storage_terms-TOTAL_FORMS"] = "1"
    saved["storage_terms-0-warehouse"] = str(warehouse.pk)
    saved["storage_terms-0-free_days"] = "5"
    saved["storage_terms-0-daily_rate"] = "8.50"
    saved["storage_terms-0-id"] = ""
    saved["storage_terms-0-client"] = str(customer.pk)
    saved["_save"] = "Save"
    response = http.post(url, saved)
    assert response.status_code == 302, response.content.decode()[:2000]
    terms = customer.storage_terms.get()
    assert terms.warehouse_id == warehouse.pk
    assert terms.free_days == 5
    assert terms.daily_rate == Decimal("8.50")
