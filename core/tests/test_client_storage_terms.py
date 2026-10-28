"""Условия хранения клиента: себестоимость — по складу, клиенту — по его условиям."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
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
