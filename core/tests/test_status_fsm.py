"""
Тесты FSM статусов Car/Container (``ALLOWED_STATUS_TRANSITIONS`` в
``core.models.containers``).

Правила:
- вперёд — свободно, включая пропуск шагов;
- назад — свободно (исправление ошибок), кроме выхода из TRANSFERRED;
- из TRANSFERRED — только в UNLOADED (осознанный откат передачи);
- bulk ``queryset.update()`` проверку обходит (escape hatch).

Запуск: pytest core/tests/test_status_fsm.py
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Car, Container, Warehouse


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="WH-FSM", free_days=0)


def _container(number, status, warehouse=None):
    # Container.clean() для UNLOADED/TRANSFERRED требует склад и дату разгрузки.
    extra = {}
    if status in ("UNLOADED", "TRANSFERRED"):
        extra = {"warehouse": warehouse, "unload_date": timezone.now().date()}
    return Container.objects.create(number=number, status=status, **extra)


def _car(status, **extra):
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=f"FSMCAR{status[:5]:>05}000000"[:17].replace(" ", "0"),
        status=status,
        **extra,
    )


@pytest.mark.django_db
class TestContainerStatusFSM:
    def test_forward_transitions_allowed(self, warehouse):
        c = _container("FSM-C-1", "FLOATING")
        c.warehouse = warehouse
        c.unload_date = timezone.now().date()
        c.save(update_fields=["warehouse", "unload_date"])
        for status in ("IN_PORT", "UNLOADED", "TRANSFERRED"):
            c.status = status
            c.save(update_fields=["status"])
        assert Container.objects.get(pk=c.pk).status == "TRANSFERRED"

    def test_skip_forward_allowed(self):
        c = _container("FSM-C-2", "FLOATING")
        c.status = "TRANSFERRED"
        c.save(update_fields=["status"])

    def test_rollback_before_transfer_allowed(self, warehouse):
        c = _container("FSM-C-3", "UNLOADED", warehouse)
        c.status = "FLOATING"
        c.save(update_fields=["status"])

    def test_transferred_to_floating_forbidden(self, warehouse):
        c = _container("FSM-C-4", "TRANSFERRED", warehouse)
        c.status = "FLOATING"
        with pytest.raises(ValidationError):
            c.save(update_fields=["status"])

    def test_transferred_to_unloaded_allowed(self, warehouse):
        c = _container("FSM-C-5", "TRANSFERRED", warehouse)
        c.status = "UNLOADED"
        c.save(update_fields=["status"])

    def test_update_fields_without_status_not_checked(self, warehouse):
        c = _container("FSM-C-6", "TRANSFERRED", warehouse)
        # Объект в памяти «испорчен», но сохраняем другое поле — ок.
        c.status = "FLOATING"
        c.notes = "x"
        c.save(update_fields=["notes"])

    def test_bulk_update_bypasses_fsm(self, warehouse):
        c = _container("FSM-C-7", "TRANSFERRED", warehouse)
        Container.objects.filter(pk=c.pk).update(status="FLOATING")
        assert Container.objects.get(pk=c.pk).status == "FLOATING"


@pytest.mark.django_db
class TestCarStatusFSM:
    def test_forward_allowed(self):
        car = _car("FLOATING")
        car.status = "IN_PORT"
        car.save()

    def test_transferred_to_in_port_forbidden(self):
        car = _car("TRANSFERRED", transfer_date=timezone.now().date())
        car.status = "IN_PORT"
        car.transfer_date = None  # иначе _sync_status_and_dates вернёт TRANSFERRED
        with pytest.raises(ValidationError):
            car.save()

    def test_transfer_undo_via_unloaded_allowed(self):
        car = _car("TRANSFERRED", transfer_date=timezone.now().date())
        car.status = "UNLOADED"
        car.transfer_date = None
        car.save()
        assert Car.objects.get(pk=car.pk).status == "UNLOADED"

    def test_sync_sets_transferred_by_transfer_date(self):
        # transfer_date → _sync_status_and_dates сам ставит TRANSFERRED;
        # это форвард-переход, FSM не мешает.
        car = _car("UNLOADED", unload_date=timezone.now().date())
        car.transfer_date = timezone.now().date()
        car.save()
        assert Car.objects.get(pk=car.pk).status == "TRANSFERRED"
