"""
Тесты финализации передачи авто и FSM в bulk-actions админки.

Покрывает изменения «Переделки Logist2»:

* ``storage_service_q`` — единый lookup услуги «Хранение» (код + legacy-имя);
* ``Car.get_warehouse_services_total`` — разовые услуги склада БЕЗ хранения;
* ``finalize_cars_transfer_task`` — фиксация days/storage_cost по
  transfer_date после bulk-перевода в TRANSFERRED (минуя ``Car.save()``);
* ``CarAdmin._bulk_set_status`` / ``ContainerAdmin._bulk_set_status`` —
  массовая смена статусов больше не обходит FSM и пропускает «Важное».

Запуск: pytest core/tests/test_transfer_finalize.py
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.contrib import admin
from django.utils import timezone

from core.admin.car import CarAdmin
from core.admin.container import ContainerAdmin
from core.models import Car, Container, Warehouse
from core.models.services import CarService, WarehouseService
from core.service_codes import ServiceCode, storage_service_q
from core.tasks import finalize_cars_transfer_task

pytestmark = pytest.mark.django_db


# ──────────────────────────────────────────────────────────────────────────
# Фикстуры
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="WH-TRF", free_days=0)


@pytest.fixture
def storage_service(warehouse):
    return WarehouseService.objects.create(
        warehouse=warehouse,
        name="Хранение",
        code=ServiceCode.STORAGE,
        default_price=Decimal("10.00"),
        is_active=True,
        add_by_default=False,
    )


def _car(vin, status, **extra):
    return Car.objects.create(year=2023, brand="Toyota", vin=vin, status=status, **extra)


class _FakeAdmin:
    """Собирает message_user-сообщения вместо messages framework."""

    def __init__(self):
        self.messages = []

    def __call__(self, request, message, level=None, **kwargs):
        self.messages.append((message, level))


def _make_admin(admin_class, model):
    ma = admin_class(model, admin.site)
    fake = _FakeAdmin()
    ma.message_user = fake
    return ma, fake


# ──────────────────────────────────────────────────────────────────────────
# storage_service_q / get_warehouse_services_total
# ──────────────────────────────────────────────────────────────────────────


class TestStorageLookup:
    def test_matches_by_code(self, warehouse):
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="Storage EN", code=ServiceCode.STORAGE, default_price=5
        )
        assert svc in WarehouseService.objects.filter(storage_service_q())

    def test_matches_by_legacy_name(self, warehouse):
        svc = WarehouseService.objects.create(warehouse=warehouse, name="Хранение", code="", default_price=5)
        assert svc in WarehouseService.objects.filter(storage_service_q())

    def test_other_services_not_matched(self, warehouse):
        svc = WarehouseService.objects.create(
            warehouse=warehouse, name="Разгрузка", code=ServiceCode.UNLOADING, default_price=5
        )
        assert svc not in WarehouseService.objects.filter(storage_service_q())


class TestWarehouseServicesTotal:
    def test_storage_excluded_from_total(self, warehouse, storage_service):
        unloading = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Цена за разгрузку",
            code=ServiceCode.UNLOADING,
            default_price=Decimal("50.00"),
        )
        car = _car("TRFTOTAL000000001", "UNLOADED", warehouse=warehouse, unload_date=timezone.now().date())
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=storage_service.pk, custom_price=Decimal("100.00")
        )
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=unloading.pk, custom_price=Decimal("50.00")
        )

        # Хранение (100) не входит; разовая разгрузка (50) входит.
        assert car.get_warehouse_services_total() == Decimal("50.00")


# ──────────────────────────────────────────────────────────────────────────
# finalize_cars_transfer_task — фиксация хранения по transfer_date
# ──────────────────────────────────────────────────────────────────────────


class TestFinalizeCarsTransfer:
    def test_recalculates_days_and_storage_after_bulk_transfer(self, warehouse, storage_service):
        today = timezone.now().date()
        unload_date = today - datetime.timedelta(days=9)
        transfer_date = unload_date + datetime.timedelta(days=3)  # 4 «платных» дня

        car = _car("TRFFINAL000000001", "UNLOADED", warehouse=warehouse, unload_date=unload_date)

        # Bulk-перевод минуя Car.save() — как в admin-action и сигнале автовоза.
        Car.objects.filter(pk=car.pk).update(status="TRANSFERRED", transfer_date=transfer_date)

        # Денормализованные поля устарели (посчитаны по «сегодня», 10 дней).
        stale = Car.objects.get(pk=car.pk)
        assert stale.status == "TRANSFERRED"

        finalize_cars_transfer_task(car_ids=[car.pk])

        fresh = Car.objects.get(pk=car.pk)
        expected_days = (transfer_date - unload_date).days + 1  # день разгрузки и передачи включаются
        assert fresh.days == expected_days
        assert fresh.storage_cost == Decimal(expected_days) * Decimal("10.00")

    def test_free_days_reduce_chargeable_days(self, storage_service, warehouse):
        warehouse.free_days = 2
        warehouse.save(update_fields=["free_days"])

        today = timezone.now().date()
        unload_date = today - datetime.timedelta(days=9)
        transfer_date = unload_date + datetime.timedelta(days=4)  # 5 всего, 3 платных

        car = _car("TRFFREED000000001", "UNLOADED", warehouse=warehouse, unload_date=unload_date)
        Car.objects.filter(pk=car.pk).update(status="TRANSFERRED", transfer_date=transfer_date)

        finalize_cars_transfer_task(car_ids=[car.pk])

        fresh = Car.objects.get(pk=car.pk)
        assert fresh.days == 3
        assert fresh.storage_cost == Decimal("30.00")


# ──────────────────────────────────────────────────────────────────────────
# FSM в bulk-actions CarAdmin
# ──────────────────────────────────────────────────────────────────────────


class TestCarAdminBulkFSM:
    def test_forbidden_transition_skipped(self):
        car = _car("TRFFSM00000000001", "TRANSFERRED", transfer_date=timezone.now().date())
        ma, fake = _make_admin(CarAdmin, Car)

        allowed = ma._bulk_set_status(None, Car.objects.filter(pk=car.pk), "IN_PORT", "В порту")

        assert allowed == []
        assert Car.objects.get(pk=car.pk).status == "TRANSFERRED"
        assert any("недопустимый переход" in msg for msg, _ in fake.messages)

    def test_important_car_skipped(self):
        car = _car("TRFFSM00000000002", "UNLOADED", is_important=True, unload_date=timezone.now().date())
        ma, fake = _make_admin(CarAdmin, Car)

        allowed = ma._bulk_set_status(None, Car.objects.filter(pk=car.pk), "TRANSFERRED", "Передан")

        assert allowed == []
        assert Car.objects.get(pk=car.pk).status == "UNLOADED"
        assert any("Важное" in msg for msg, _ in fake.messages)

    def test_valid_transition_applied(self):
        car = _car("TRFFSM00000000003", "FLOATING")
        ma, _ = _make_admin(CarAdmin, Car)

        allowed = ma._bulk_set_status(None, Car.objects.filter(pk=car.pk), "IN_PORT", "В порту")

        assert allowed == [car.pk]
        assert Car.objects.get(pk=car.pk).status == "IN_PORT"

    def test_mixed_queryset_partial_update(self):
        ok_car = _car("TRFFSM00000000004", "FLOATING")
        frozen = _car("TRFFSM00000000005", "TRANSFERRED", transfer_date=timezone.now().date())
        ma, _ = _make_admin(CarAdmin, Car)

        allowed = ma._bulk_set_status(None, Car.objects.filter(pk__in=[ok_car.pk, frozen.pk]), "IN_PORT", "В порту")

        assert allowed == [ok_car.pk]
        assert Car.objects.get(pk=ok_car.pk).status == "IN_PORT"
        assert Car.objects.get(pk=frozen.pk).status == "TRANSFERRED"


# ──────────────────────────────────────────────────────────────────────────
# FSM в bulk-actions ContainerAdmin
# ──────────────────────────────────────────────────────────────────────────


class TestContainerAdminBulkFSM:
    def test_transferred_rollback_to_floating_skipped(self, warehouse):
        cont = Container.objects.create(
            number="TRF-C-1",
            status="TRANSFERRED",
            warehouse=warehouse,
            unload_date=timezone.now().date(),
        )
        ma, fake = _make_admin(ContainerAdmin, Container)

        ma._bulk_set_status(None, Container.objects.filter(pk=cont.pk), "FLOATING", "В пути")

        assert Container.objects.get(pk=cont.pk).status == "TRANSFERRED"
        assert any("недопустимый переход" in msg for msg, _ in fake.messages)

    def test_forward_transition_applied(self):
        cont = Container.objects.create(number="TRF-C-2", status="FLOATING")
        ma, _ = _make_admin(ContainerAdmin, Container)

        ma._bulk_set_status(None, Container.objects.filter(pk=cont.pk), "IN_PORT", "В порту")

        assert Container.objects.get(pk=cont.pk).status == "IN_PORT"
