"""
Unit-тесты сервиса оркестрации услуг авто (``core.services.car_admin_service``).

Сервис вынесен из ``CarAdmin._save_model_inner`` и декаплен от ``request``:
на вход подаётся обычный ``dict`` POST-данных. Эти тесты драйвят его без
HTTP-слоя и фиксируют ключевые сценарии:

- автодобавление дефолтных услуг склада при создании авто;
- обновление custom_price/markup существующих услуг из POST;
- удаление услуги по ``remove_*=1`` + запись в blacklist DeletedCarService;
- финальный пересчёт ``Car.total_price``.

Запуск: pytest core/tests/test_car_admin_service.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import (
    Car,
    CarService,
    Container,
    DeletedCarService,
    Warehouse,
    WarehouseService,
)
from core.services.car_admin_service import (
    apply_car_service_edits,
    process_removed_services,
    services_touched,
)


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
def warehouse(db):
    return Warehouse.objects.create(name="WH-Admin", free_days=0)


@pytest.fixture
def car(db, warehouse):
    container = Container.objects.create(number="ADMSVC-1", status="FLOATING")
    car = Car.objects.create(
        year=2023, brand="Toyota", vin="ADMINSERVICE00001",
        status="FLOATING", container=container, warehouse=warehouse,
    )
    car._bulk_updating = True  # как делает админка до super().save_model
    return car


# ---------------------------------------------------------------------------
# pure-хелперы
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_services_touched_detects_fields(self):
        assert services_touched({"warehouse_service_5": "10"})
        assert services_touched({"markup_line_service_3": "2"})
        assert services_touched({"remove_carrier_service_7": "1"})

    def test_services_touched_ignores_unrelated(self):
        assert not services_touched({"status": "FLOATING", "vin": "X"})


# ---------------------------------------------------------------------------
# apply_car_service_edits — интеграция
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyCarServiceEdits:
    def test_auto_adds_default_warehouse_services_on_create(self, car, warehouse):
        WarehouseService.objects.create(
            warehouse=warehouse, name="Разгрузка", default_price=Decimal("50"),
            is_active=True, add_by_default=True,
        )
        WarehouseService.objects.create(
            warehouse=warehouse, name="Опция", default_price=Decimal("99"),
            is_active=True, add_by_default=False,  # НЕ должна добавиться
        )
        apply_car_service_edits(car, post={}, changed_data=[], is_change=False)

        services = CarService.objects.filter(car=car, service_type="WAREHOUSE")
        assert services.count() == 1
        assert services.first().custom_price == Decimal("50.00")
        car.refresh_from_db()
        assert car.total_price == Decimal("50.00")

    def test_updates_existing_price_and_markup_from_post(self, car, warehouse):
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="Разгрузка", default_price=Decimal("50"),
            is_active=True, add_by_default=False,
        )
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=svc.id,
            custom_price=Decimal("50"), markup_amount=Decimal("0"),
        )
        post = {
            f"warehouse_service_{svc.id}": "70",
            f"markup_warehouse_service_{svc.id}": "5",
        }
        apply_car_service_edits(car, post=post, changed_data=[], is_change=True)

        cs = CarService.objects.get(car=car, service_id=svc.id)
        assert cs.custom_price == Decimal("70")
        assert cs.markup_amount == Decimal("5")
        car.refresh_from_db()
        # invoice_price = 70 + 5 = 75
        assert car.total_price == Decimal("75.00")

    def test_removes_service_and_blacklists(self, car, warehouse):
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="Разгрузка", default_price=Decimal("50"),
            is_active=True, add_by_default=True,
        )
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=svc.id,
            custom_price=Decimal("50"),
        )
        post = {f"remove_warehouse_service_{svc.id}": "1"}

        # is_change=True и warehouse НЕ в changed_data → auto-add не сработает,
        # услуга должна остаться удалённой.
        apply_car_service_edits(car, post=post, changed_data=[], is_change=True)

        assert not CarService.objects.filter(car=car, service_id=svc.id).exists()
        assert DeletedCarService.objects.filter(
            car=car, service_type="WAREHOUSE", service_id=svc.id
        ).exists()
        car.refresh_from_db()
        assert car.total_price == Decimal("0.00")

    def test_blacklist_prevents_readd_on_provider_change(self, car, warehouse):
        # Услуга add_by_default, но занесена в blacklist → не добавляется
        # повторно при смене склада.
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="Разгрузка", default_price=Decimal("50"),
            is_active=True, add_by_default=True,
        )
        DeletedCarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=svc.id,
        )
        apply_car_service_edits(
            car, post={}, changed_data=["warehouse"], is_change=True,
        )
        assert not CarService.objects.filter(car=car, service_id=svc.id).exists()


@pytest.mark.django_db
class TestProcessRemovedServices:
    def test_returns_prefixed_keys(self, car, warehouse):
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="X", default_price=Decimal("5"), is_active=True,
        )
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=svc.id, custom_price=Decimal("5"),
        )
        removed = process_removed_services(
            car, {f"remove_warehouse_service_{svc.id}": "1"}
        )
        assert removed == {f"warehouse_{svc.id}"}
        assert not CarService.objects.filter(car=car, service_id=svc.id).exists()
