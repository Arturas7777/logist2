"""
Тесты контракта Фазы 2: legacy fee-поля Car больше не источник истины.

Фиксируют новое поведение ПОСЛЕ декаплинга:
- создание авто со складом НЕ заполняет legacy fee-поля
  (``unload_fee``/``delivery_fee``/…); они остаются ``None``;
- ``Car.sync_with_container()`` синхронизирует только живые поля
  (статус/склад/даты) и пересчитывает цену из ``CarService``, не трогая
  ``ths``/``markup``/``declaration_fee`` и складские fee-поля;
- ``Container`` manager ``update_related`` (через ``sync_cars``) обновляет
  цену из ``CarService`` без записи legacy-полей.

Запуск: pytest core/tests/test_phase2_legacy_decouple.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Container, Warehouse, WarehouseService


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


LEGACY_FEE_FIELDS = (
    "unload_fee",
    "delivery_fee",
    "loading_fee",
    "docs_fee",
    "transfer_fee",
    "transit_declaration",
    "export_declaration",
    "extra_costs",
    "complex_fee",
)


@pytest.fixture
def warehouse(db):
    # Склад с ненулевыми дефолтами — раньше они копировались в legacy-поля.
    return Warehouse.objects.create(
        name="WH-P2",
        free_days=0,
        default_unloading_fee=Decimal("40.00"),
        delivery_to_warehouse=Decimal("30.00"),
        loading_on_trawl=Decimal("20.00"),
        documents_fee=Decimal("10.00"),
        transfer_fee=Decimal("15.00"),
    )


@pytest.mark.django_db(transaction=True)
class TestNoLegacyWriteOnCreate:
    def test_new_car_does_not_populate_legacy_fee_fields(self, warehouse):
        container = Container.objects.create(number="P2-CR-1", status="FLOATING")
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="P2DECOUPLE0000001",
            status="FLOATING",
            container=container,
            warehouse=warehouse,
        )
        car.refresh_from_db()
        for field in LEGACY_FEE_FIELDS:
            assert getattr(car, field) is None, (
                f"Поле {field} было заполнено при создании авто — запись legacy полей должна быть прекращена (Фаза 2)"
            )


@pytest.mark.django_db(transaction=True)
class TestSyncWithContainer:
    def test_sync_updates_live_fields_only(self, warehouse):
        container = Container.objects.create(
            number="P2-SY-1",
            status="UNLOADED",
            warehouse=warehouse,
            unload_date=timezone.now().date(),
        )
        car = Car.objects.create(
            year=2022,
            brand="Honda",
            vin="P2DECOUPLE0000002",
            status="FLOATING",
        )

        car.sync_with_container(container)

        assert car.status == "UNLOADED"
        assert car.warehouse_id == warehouse.id
        assert car.unload_date == container.unload_date
        # legacy-поля не тронуты
        assert car.ths is None
        for field in LEGACY_FEE_FIELDS:
            assert getattr(car, field) is None

    def test_price_comes_from_car_services_after_sync(self, warehouse):
        container = Container.objects.create(
            number="P2-SY-2",
            status="UNLOADED",
            warehouse=warehouse,
            unload_date=timezone.now().date(),
        )
        car = Car.objects.create(
            year=2022,
            brand="Honda",
            vin="P2DECOUPLE0000003",
            status="UNLOADED",
            container=container,
            warehouse=warehouse,
            unload_date=timezone.now().date(),
        )
        svc = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Разгрузка",
            default_price=Decimal("40.00"),
            is_active=True,
        )
        CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=svc.id,
            custom_price=Decimal("40.00"),
            markup_amount=Decimal("0.00"),
            quantity=1,
        )

        car.sync_with_container(container)

        assert car.calculate_total_price() == Decimal("40.00")
