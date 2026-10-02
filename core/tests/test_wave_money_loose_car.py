"""B13: авто без контейнера (loose car) — тарифы клиента + хранение, без THS;
инвойс автовоза FORMED содержит всё это.

Запуск: pytest core/tests/test_wave_money_loose_car.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import (
    AutoTransport,
    Car,
    Carrier,
    CarService,
    Client,
    ClientTariffRate,
    Company,
    Warehouse,
    WarehouseService,
)
from core.models_billing import NewInvoice
from core.service_codes import is_storage_service, is_ths_service
from core.services.car_admin_service import apply_car_service_edits
from core.services.car_service_manager import ensure_ths_and_tariffs_for_car, sync_car_services_for_car


@pytest.fixture(autouse=True)
def _clear_pricing_thread_locals():
    from core.signals import car_service as cs_signals

    def _reset():
        for attr in ("_pricing_local", "_regen_local"):
            local = getattr(cs_signals, attr, None)
            if local is not None and getattr(local, "cars", None) is not None:
                local.cars.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def company(db, settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def warehouse(db):
    wh = Warehouse.objects.create(name="WH-LOOSE", free_days=0)
    WarehouseService.objects.create(
        warehouse=wh,
        name="Разгрузка/Погрузка",
        short_name="Порт",
        default_price=Decimal("160"),
        is_active=True,
        add_by_default=True,
    )
    WarehouseService.objects.create(
        warehouse=wh,
        name="Хранение",
        short_name="Хран",
        code="STORAGE",
        default_price=Decimal("5"),
        is_active=True,
        add_by_default=True,
    )
    return wh


@pytest.fixture
def fixed_client(db):
    client = Client.objects.create(name="Loose Client", tariff_type="FIXED")
    ClientTariffRate.objects.create(client=client, vehicle_type="SEDAN", min_cars=1, agreed_total_price=Decimal("300"))
    return client


def _loose_car(warehouse, client, days_stored=5):
    car = Car.objects.create(
        year=2023,
        brand="Toyota",
        vin="LOOSECAR000000001",
        status="UNLOADED",
        warehouse=warehouse,
        client=client,
        vehicle_type="SEDAN",
        unload_date=timezone.now().date() - timezone.timedelta(days=days_stored - 1),
    )
    # Как делает CarAdmin при создании: услуги по умолчанию + тариф клиента.
    car._bulk_updating = True
    apply_car_service_edits(car, post={}, changed_data=[], is_change=False)
    car.refresh_from_db()
    return car


@pytest.mark.django_db
class TestLooseCarServices:
    def test_sync_services_without_container_has_no_ths(self, warehouse, fixed_client):
        car = _loose_car(warehouse, fixed_client)
        services = list(CarService.objects.filter(car=car))
        assert services, "услуги склада по умолчанию должны создаться"
        assert not any(is_ths_service(s) for s in services)
        assert any(is_storage_service(s) for s in services)
        # Повторный sync без контейнера не падает и не плодит дубли.
        sync_car_services_for_car(car, created=False, warehouse_changed=True, line_changed=False, carrier_changed=False)
        assert CarService.objects.filter(car=car, service_type="WAREHOUSE").count() == 2

    def test_ensure_ths_is_noop_for_loose_car(self, warehouse, fixed_client):
        car = _loose_car(warehouse, fixed_client)
        assert ensure_ths_and_tariffs_for_car(car) is False

    def test_tariff_and_storage_in_total_price(self, warehouse, fixed_client):
        car = _loose_car(warehouse, fixed_client)
        # Пакет склада приведён к тарифу 300 (Порт 160 + наценка 140), хранение 5 дн × 5 = 25 сверху.
        assert car.days == 5
        assert car.storage_cost == Decimal("25.00")
        assert car.total_price == Decimal("325.00")


@pytest.mark.django_db
class TestLooseCarAutotransportInvoice:
    def test_formed_autotransport_invoice_has_storage_and_tariff(self, company, warehouse, fixed_client):
        car = _loose_car(warehouse, fixed_client)
        carrier = Carrier.objects.create(name="Carrier-Loose")
        at = AutoTransport.objects.create(carrier=carrier, status="FORMED")
        at.cars.add(car)

        invoices = at.generate_invoices()

        assert len(invoices) == 1
        inv = NewInvoice.objects.get(pk=invoices[0].pk)
        assert inv.recipient_client_id == fixed_client.pk
        assert inv.document_type == "PROFORMA_BLC"
        items = {i.description: i.unit_price for i in inv.items.all()}
        assert items == {"Порт": Decimal("300.00"), "Хран": Decimal("25.00")}
        assert inv.total == Decimal("325.00")
        assert "THS" not in " ".join(items)
