"""L2: одна функция смены статуса авто — FSM, transfer_date, история, контейнер, автовоз."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import AutoTransport, Car, Carrier, CarStatusHistory, Client, Container, Warehouse
from core.services.car_lifecycle_service import transition_cars

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="WH-L2", free_days=0)


@pytest.fixture
def carrier():
    return Carrier.objects.create(name="Carrier-L2")


def _container(number, warehouse, status="UNLOADED"):
    return Container.objects.create(
        number=number, status=status, warehouse=warehouse, unload_date=TODAY - datetime.timedelta(days=10)
    )


def _car(vin, container, status="UNLOADED", **extra):
    extra.setdefault("client", Client.objects.create(name=f"Client {vin[-4:]}"))
    if status == "TRANSFERRED":
        extra.setdefault("transfer_date", TODAY - datetime.timedelta(days=2))
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status=status,
        container=container,
        warehouse=container.warehouse,
        unload_date=container.unload_date,
        **extra,
    )


def _history(car):
    return list(CarStatusHistory.objects.filter(car=car).order_by("id").values_list("previous_status", "status"))


# --- Откат передачи ----------------------------------------------------------


def test_car_save_rollback_clears_transfer_date_and_reopens_container(warehouse):
    container = _container("L2-ROLLBACK1", warehouse)
    car = _car("L2ROLLBACK0000001", container)
    car.status = "TRANSFERRED"
    car.save()
    container.refresh_from_db()
    assert container.status == "TRANSFERRED"

    car.status = "UNLOADED"
    car.save()

    car.refresh_from_db()
    container.refresh_from_db()
    assert car.status == "UNLOADED"
    assert car.transfer_date is None
    assert container.status == "UNLOADED"
    assert _history(car)[-1] == ("TRANSFERRED", "UNLOADED")


def test_rollback_with_update_fields_persists_cleared_transfer_date(warehouse):
    container = _container("L2-ROLLBACK2", warehouse)
    car = _car("L2ROLLBACK0000002", container, status="TRANSFERRED")
    car.status = "UNLOADED"
    car.save(update_fields=["status"])

    car.refresh_from_db()
    assert car.status == "UNLOADED"
    assert car.transfer_date is None
    car.notes = "после отката"
    car.save()
    car.refresh_from_db()
    assert car.status == "UNLOADED"


def test_bulk_rollback_via_transition(warehouse):
    container = _container("L2-ROLLBACK3", warehouse)
    car = _car("L2ROLLBACK0000003", container)
    transition_cars([car.pk], "TRANSFERRED")
    container.refresh_from_db()
    assert container.status == "TRANSFERRED"

    result = transition_cars([car.pk], "UNLOADED")

    car.refresh_from_db()
    container.refresh_from_db()
    assert result["updated"] == [car.pk]
    assert car.transfer_date is None
    assert container.status == "UNLOADED"
    assert _history(car) == [("", "UNLOADED"), ("UNLOADED", "TRANSFERRED"), ("TRANSFERRED", "UNLOADED")]


# --- transition_cars: правила -----------------------------------------------


def test_transition_skips_important_and_fsm(warehouse):
    container = _container("L2-RULES0001", warehouse)
    important = _car("L2IMPORTANT000001", container, is_important=True)
    transferred = _car("L2TRANSFERRED0001", container, status="TRANSFERRED")
    ok = _car("L2OKCAR0000000001", container)

    result = transition_cars([important.pk, transferred.pk, ok.pk], "IN_PORT")

    assert result["updated"] == [ok.pk]
    assert result["skipped_important"] == [important.vin]
    assert result["skipped_fsm"] == [transferred.vin]
    important.refresh_from_db()
    assert important.status == "UNLOADED"


def test_transition_to_transferred_sets_date_not_before_unload(warehouse):
    container = _container("L2-DATES0001", warehouse)
    car = _car("L2DATES0000000001", container)
    early = TODAY - datetime.timedelta(days=30)

    transition_cars([car.pk], "TRANSFERRED", transfer_date=early)

    car.refresh_from_db()
    assert car.status == "TRANSFERRED"
    assert car.transfer_date == container.unload_date


def test_require_unload_info(warehouse):
    container = Container.objects.create(number="L2-NOUNLOAD1", status="IN_PORT")
    car = Car.objects.create(year=2023, brand="Toyota", vin="L2NOUNLOAD0000001", status="IN_PORT", container=container)

    result = transition_cars([car.pk], "UNLOADED", require_unload_info=True)

    assert result["skipped_missing"] == [car.vin]
    car.refresh_from_db()
    assert car.status == "IN_PORT"


def test_transfer_notifies_client_after_commit(warehouse, django_capture_on_commit_callbacks):
    container = _container("L2-NOTIFY001", warehouse)
    car = _car("L2NOTIFY000000001", container)
    with (
        patch("core.tasks.send_car_transferred_notification_task.delay") as notify,
        patch("core.tasks.finalize_cars_transfer_task.delay") as finalize,
        django_capture_on_commit_callbacks(execute=True),
    ):
        transition_cars([car.pk], "TRANSFERRED")
    notify.assert_called_once_with(car.pk)
    finalize.assert_called_once_with([car.pk])


# --- Автовоз ---------------------------------------------------------------


def _loaded_autotransport(carrier, *cars, loading_date=None):
    at = AutoTransport.objects.create(carrier=carrier, status="FORMED", loading_date=loading_date)
    at.cars.add(*cars)
    at.status = "LOADED"
    at.save()
    return at


def test_autotransport_resave_does_not_retransfer_returned_car(warehouse, carrier):
    container = _container("L2-AT-RESAVE", warehouse)
    car = _car("L2ATRESAVE0000001", container)
    at = _loaded_autotransport(carrier, car)
    car.refresh_from_db()
    assert car.status == "TRANSFERRED"
    assert _history(car)[-1] == ("UNLOADED", "TRANSFERRED")

    car.status = "UNLOADED"
    car.save()
    at.status = "IN_TRANSIT"
    at.save()
    at.notes = "повторное сохранение"
    at.save()

    car.refresh_from_db()
    assert car.status == "UNLOADED"
    assert car.transfer_date is None


def test_autotransport_skips_important_car(warehouse, carrier):
    container = _container("L2-AT-IMPORT", warehouse)
    car = _car("L2ATIMPORTANT0001", container)
    other = _car("L2ATREGULAR000001", container)
    at = AutoTransport.objects.create(carrier=carrier, status="FORMED")
    at.cars.add(car, other)
    Car.objects.filter(pk=car.pk).update(is_important=True)

    at.status = "LOADED"
    at.save()

    car.refresh_from_db()
    other.refresh_from_db()
    assert car.status == "UNLOADED"
    assert other.status == "TRANSFERRED"


def test_car_added_to_loaded_autotransport_is_transferred(warehouse, carrier):
    container = _container("L2-AT-ADDED1", warehouse)
    first = _car("L2ATFIRST00000001", container)
    late = _car("L2ATLATE000000001", container)
    at = _loaded_autotransport(carrier, first, loading_date=TODAY - datetime.timedelta(days=1))

    at.cars.add(late)

    late.refresh_from_db()
    assert late.status == "TRANSFERRED"
    assert late.transfer_date == TODAY - datetime.timedelta(days=1)
