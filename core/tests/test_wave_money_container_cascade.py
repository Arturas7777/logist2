"""T2 (B3) + B11: каскады контейнера на авто уважают FSM и не трогают
переданные (TRANSFERRED) авто.

Запуск: pytest core/tests/test_wave_money_container_cascade.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, Container, Warehouse, WarehouseService
from core.services.container_lifecycle_service import (
    apply_post_save_cascades,
    bulk_update_car_statuses,
    status_cascade_messages,
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
    wh = Warehouse.objects.create(name="WH-CASCADE", free_days=0)
    WarehouseService.objects.create(
        warehouse=wh, name="Хранение", code="STORAGE", default_price=Decimal("5"), is_active=True
    )
    return wh


def _car(container, warehouse, vin, status, **extra):
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status=status,
        container=container,
        warehouse=warehouse,
        **extra,
    )


@pytest.mark.django_db
class TestMixedContainerStatusCascade:
    def test_rollback_to_in_port_keeps_transferred_cars(self, warehouse):
        today = timezone.now().date()
        container = Container.objects.create(
            number="CSC-MIX-1", status="UNLOADED", warehouse=warehouse, unload_date=today
        )
        unloaded = _car(container, warehouse, "CSCMIXUNLOADED001", "UNLOADED", unload_date=today)
        transferred = _car(
            container, warehouse, "CSCMIXTRANSFER001", "TRANSFERRED", unload_date=today, transfer_date=today
        )

        # Откат «разгрузки»: pre_save контейнера сам ставит UNLOADED при
        # заполненной дате, поэтому убираем и дату.
        container.status = "IN_PORT"
        container.unload_date = None
        container.save(update_fields=["status", "unload_date"])
        result = bulk_update_car_statuses(container)

        unloaded.refresh_from_db()
        transferred.refresh_from_db()
        assert unloaded.status == "IN_PORT"
        assert transferred.status == "TRANSFERRED"
        assert transferred.transfer_date == today
        assert result["updated"] == 1
        assert result["skipped_transferred"] == [transferred.vin]
        assert container._status_cascade_result is result

    def test_rollback_container_from_transferred_keeps_cars_transferred(self, warehouse):
        today = timezone.now().date()
        container = Container.objects.create(
            number="CSC-MIX-2", status="TRANSFERRED", warehouse=warehouse, unload_date=today
        )
        car = _car(container, warehouse, "CSCMIXTRANSFER002", "TRANSFERRED", unload_date=today, transfer_date=today)

        container.status = "UNLOADED"
        container.save(update_fields=["status"])
        result = bulk_update_car_statuses(container)

        car.refresh_from_db()
        assert car.status == "TRANSFERRED"
        assert result["updated"] == 0
        assert result["skipped_transferred"] == [car.vin]

    def test_forward_to_transferred_sets_transfer_date(self, warehouse):
        today = timezone.now().date()
        container = Container.objects.create(
            number="CSC-MIX-3", status="UNLOADED", warehouse=warehouse, unload_date=today
        )
        car = _car(container, warehouse, "CSCMIXFORWARD0001", "UNLOADED", unload_date=today)
        already = _car(
            container,
            warehouse,
            "CSCMIXFORWARD0002",
            "TRANSFERRED",
            unload_date=today,
            transfer_date=today - timezone.timedelta(days=3),
        )

        container.status = "TRANSFERRED"
        container.save(update_fields=["status"])
        result = bulk_update_car_statuses(container)

        car.refresh_from_db()
        already.refresh_from_db()
        assert car.status == "TRANSFERRED"
        assert car.transfer_date == today
        assert already.transfer_date == today - timezone.timedelta(days=3)
        assert result["updated"] == 1

    def test_important_cars_are_skipped(self, warehouse):
        container = Container.objects.create(number="CSC-MIX-4", status="FLOATING")
        normal = _car(container, warehouse, "CSCMIXIMPORTANT01", "FLOATING")
        important = _car(container, warehouse, "CSCMIXIMPORTANT02", "FLOATING", is_important=True)

        container.status = "IN_PORT"
        container.save(update_fields=["status"])
        result = apply_post_save_cascades(container, changed_data=["status"], is_change=True, status_auto_changed=False)

        normal.refresh_from_db()
        important.refresh_from_db()
        assert normal.status == "IN_PORT"
        assert important.status == "FLOATING"
        assert result["skipped_important"] == [important.vin]
        assert any("Важное" in m for m in status_cascade_messages(result))

    def test_messages_mention_transferred_cars(self):
        msgs = status_cascade_messages({"skipped_transferred": ["VIN1"], "skipped_fsm": [], "skipped_important": []})
        assert len(msgs) == 1
        assert "VIN1" in msgs[0]

    def test_sync_cars_action_path_respects_fsm(self, warehouse):
        """``Container.sync_cars`` (массовые admin-actions) идёт через тот же FSM-каскад."""
        today = timezone.now().date()
        container = Container.objects.create(
            number="CSC-SYNC-1", status="UNLOADED", warehouse=warehouse, unload_date=today
        )
        transferred = _car(
            container, warehouse, "CSCSYNCTRANSFER01", "TRANSFERRED", unload_date=today, transfer_date=today
        )
        Container.objects.filter(pk=container.pk).update(status="IN_PORT", unload_date=None)
        container.refresh_from_db()

        container.sync_cars()

        transferred.refresh_from_db()
        assert transferred.status == "TRANSFERRED"
        assert transferred.transfer_date == today


@pytest.mark.django_db
class TestTransferredFreeze:
    """B11: изменения контейнера задним числом не трогают переданные авто."""

    def _mixed_container(self, warehouse, number):
        today = timezone.now().date()
        container = Container.objects.create(
            number=number,
            status="UNLOADED",
            warehouse=warehouse,
            unload_date=today - timezone.timedelta(days=4),
        )
        active = _car(
            container,
            warehouse,
            f"{number[-6:]}ACTIVE0001"[:17].ljust(17, "A"),
            "UNLOADED",
            unload_date=container.unload_date,
        )
        frozen = _car(
            container,
            warehouse,
            f"{number[-6:]}FROZEN0001"[:17].ljust(17, "F"),
            "TRANSFERRED",
            unload_date=container.unload_date,
            transfer_date=today - timezone.timedelta(days=2),
        )
        for car in (active, frozen):
            car.update_days_and_storage()
            car.save(update_fields=["days", "storage_cost"])
        return container, active, frozen

    def test_warehouse_change_keeps_transferred_history(self, warehouse):
        container, active, frozen = self._mixed_container(warehouse, "CSC-FRZ-WH")
        other_wh = Warehouse.objects.create(name="WH-OTHER", free_days=0)
        WarehouseService.objects.create(
            warehouse=other_wh, name="Хранение", code="STORAGE", default_price=Decimal("50"), is_active=True
        )
        frozen_cost_before = frozen.storage_cost

        container.warehouse = other_wh
        container.save(update_fields=["warehouse"])
        apply_post_save_cascades(container, changed_data=["warehouse"], is_change=True, status_auto_changed=False)

        active.refresh_from_db()
        frozen.refresh_from_db()
        assert active.warehouse_id == other_wh.pk
        assert frozen.warehouse_id == warehouse.pk
        assert frozen.storage_cost == frozen_cost_before

    def test_warehouse_change_with_include_transferred(self, warehouse):
        container, active, frozen = self._mixed_container(warehouse, "CSC-FRZ-WH2")
        other_wh = Warehouse.objects.create(name="WH-OTHER-2", free_days=0)

        container.warehouse = other_wh
        container.save(update_fields=["warehouse"])
        apply_post_save_cascades(
            container,
            changed_data=["warehouse"],
            is_change=True,
            status_auto_changed=False,
            include_transferred=True,
        )

        frozen.refresh_from_db()
        assert frozen.warehouse_id == other_wh.pk

    def test_unload_date_cascade_service_keeps_transferred_days(self, warehouse):
        """Сервисный каскад даты разгрузки не трогает переданные авто.

        Контейнер обновляем через ``.update()``, минуя post_save-сигнал
        ``core.signals.container`` — он пока переписывает дату всем авто
        (см. xfail-тест ниже).
        """
        container, active, frozen = self._mixed_container(warehouse, "CSC-FRZ-UD")
        frozen_days_before = frozen.days
        frozen_unload_before = frozen.unload_date

        new_date = container.unload_date - timezone.timedelta(days=10)
        Container.objects.filter(pk=container.pk).update(unload_date=new_date)
        container.refresh_from_db()
        apply_post_save_cascades(container, changed_data=["unload_date"], is_change=True, status_auto_changed=False)

        active.refresh_from_db()
        frozen.refresh_from_db()
        assert active.unload_date == new_date
        assert frozen.unload_date == frozen_unload_before
        assert frozen.days == frozen_days_before

    @pytest.mark.xfail(
        strict=True,
        reason="core/signals/container.py::update_related_on_container_save обновляет unload_date всем авто, "
        "включая TRANSFERRED — нужен .exclude(status='TRANSFERRED') (вне зоны этого исполнителя)",
    )
    def test_unload_date_change_via_save_keeps_transferred_days(self, warehouse):
        container, active, frozen = self._mixed_container(warehouse, "CSC-FRZ-UD2")
        frozen_unload_before = frozen.unload_date

        container.unload_date = container.unload_date - timezone.timedelta(days=10)
        container.save(update_fields=["unload_date"])

        frozen.refresh_from_db()
        assert frozen.unload_date == frozen_unload_before

    def test_ths_change_skips_transferred_services(self, warehouse):
        from core.models import CarService, Line
        from core.service_codes import is_ths_service

        container, active, frozen = self._mixed_container(warehouse, "CSC-FRZ-THS")
        line = Line.objects.create(name="LINE-FRZ")
        Container.objects.filter(pk=container.pk).update(line=line, ths=Decimal("400"))
        container.refresh_from_db()

        apply_post_save_cascades(container, changed_data=["ths", "line"], is_change=True, status_auto_changed=False)

        active_ths = [cs for cs in CarService.objects.filter(car=active) if is_ths_service(cs)]
        frozen_ths = [cs for cs in CarService.objects.filter(car=frozen) if is_ths_service(cs)]
        assert len(active_ths) == 1
        assert frozen_ths == []
        # Доля считается по всему контейнеру: 400 / 2 авто = 200 активному.
        assert active_ths[0].custom_price == Decimal("200.00")
        frozen.refresh_from_db()
        assert frozen.line_id is None
