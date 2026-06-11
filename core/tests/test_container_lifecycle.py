"""
Тесты сервиса жизненного цикла контейнера
(``core.services.container_lifecycle_service``), вынесенного из
``ContainerAdmin._save_model_inner``.

Драйвят каскады напрямую (без HTTP) и фиксируют поведение:
- смена статуса контейнера → статус проставляется всем авто;
- смена даты разгрузки → авто получают дату + пересчёт дней/хранения;
- THS-изменение → создаются THS-услуги линии для авто.

Запуск: pytest core/tests/test_container_lifecycle.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Container, Line, Warehouse, WarehouseService
from core.services.container_lifecycle_service import apply_post_save_cascades


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
    wh = Warehouse.objects.create(name="WH-CLC", free_days=0)
    WarehouseService.objects.create(
        warehouse=wh,
        name="Хранение",
        code="STORAGE",
        default_price=Decimal("5"),
        is_active=True,
    )
    return wh


def _make_cars(container, warehouse, n):
    cars = []
    for i in range(n):
        cars.append(
            Car.objects.create(
                year=2023,
                brand="Toyota",
                vin=f"CLCCAR{i:011d}",
                status=container.status,
                container=container,
                warehouse=warehouse,
            )
        )
    return cars


@pytest.mark.django_db
class TestStatusCascade:
    def test_status_change_propagates_to_all_cars(self, warehouse):
        container = Container.objects.create(number="CLC-ST-1", status="IN_PORT")
        _make_cars(container, warehouse, 3)
        container.status = "TRANSFERRED"
        container.save(update_fields=["status"])

        apply_post_save_cascades(
            container,
            changed_data=["status"],
            is_change=True,
            status_auto_changed=False,
        )
        statuses = set(container.container_cars.values_list("status", flat=True))
        assert statuses == {"TRANSFERRED"}

    def test_auto_status_change_also_propagates(self, warehouse):
        container = Container.objects.create(
            number="CLC-ST-2",
            status="UNLOADED",
            warehouse=warehouse,
            unload_date=timezone.now().date(),
        )
        _make_cars(container, warehouse, 2)
        apply_post_save_cascades(
            container,
            changed_data=[],
            is_change=True,
            status_auto_changed=True,
        )
        statuses = set(container.container_cars.values_list("status", flat=True))
        assert statuses == {"UNLOADED"}


@pytest.mark.django_db
class TestUnloadDateCascade:
    def test_unload_date_propagates_and_recalcs_storage(self, warehouse):
        container = Container.objects.create(
            number="CLC-UD-1",
            status="UNLOADED",
            warehouse=warehouse,
            unload_date=timezone.now().date() - timezone.timedelta(days=4),
        )
        _make_cars(container, warehouse, 2)

        apply_post_save_cascades(
            container,
            changed_data=["unload_date"],
            is_change=True,
            status_auto_changed=False,
        )
        for car in container.container_cars.all():
            assert car.unload_date == container.unload_date
            # (4 + 1) дней * 5 = 25
            assert car.days == 5
            assert car.storage_cost == Decimal("25.00")


@pytest.mark.django_db
class TestThsCascade:
    def test_ths_change_creates_line_services(self, warehouse):
        line = Line.objects.create(name="MAERSK-CLC")
        container = Container.objects.create(
            number="CLC-THS-1",
            status="FLOATING",
            line=line,
            ths=Decimal("300"),
        )
        Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="CLCTHSCAR0000001",
            status="FLOATING",
            container=container,
            warehouse=warehouse,
            vehicle_type="SEDAN",
        )
        apply_post_save_cascades(
            container,
            changed_data=["ths"],
            is_change=True,
            status_auto_changed=False,
        )
        assert CarService.objects.filter(service_type="LINE").count() >= 1
