"""B12: при LOADED автовоза transfer_date берётся из даты загрузки, а не «сегодня».

Запуск: pytest core/tests/test_wave_money_autotransport_transfer.py
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from core.models import AutoTransport, Car, Carrier, Client, Container, Warehouse
from core.signals.autotransport import resolve_transfer_date


@pytest.fixture
def carrier(db):
    return Carrier.objects.create(name="Carrier-B12")


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="WH-B12", free_days=0)


def _car(vin, warehouse, unload_date):
    client = Client.objects.create(name=f"Client {vin[-3:]}")
    container = Container.objects.create(number=f"B12-{vin[-4:]}", status="UNLOADED", warehouse=warehouse, unload_date=unload_date)
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status="UNLOADED",
        container=container,
        warehouse=warehouse,
        unload_date=unload_date,
        client=client,
    )


@pytest.mark.django_db
class TestResolveTransferDate:
    def test_override_wins(self, carrier):
        at = AutoTransport.objects.create(carrier=carrier, loading_date=datetime.date(2026, 1, 10))
        assert resolve_transfer_date(at, datetime.date(2026, 1, 5)) == datetime.date(2026, 1, 5)

    def test_loading_date_then_departure_then_today(self, carrier):
        today = timezone.now().date()
        at = AutoTransport.objects.create(
            carrier=carrier,
            loading_date=today - datetime.timedelta(days=3),
            departure_date=today - datetime.timedelta(days=2),
        )
        assert resolve_transfer_date(at) == today - datetime.timedelta(days=3)
        at.loading_date = None
        assert resolve_transfer_date(at) == today - datetime.timedelta(days=2)
        at.departure_date = None
        assert resolve_transfer_date(at) == today

    def test_future_date_falls_back_to_today(self, carrier):
        today = timezone.now().date()
        at = AutoTransport.objects.create(carrier=carrier, loading_date=today + datetime.timedelta(days=5))
        assert resolve_transfer_date(at) == today


@pytest.mark.django_db
class TestLoadedSetsTransferDateFromLoadingDate:
    def test_cars_get_loading_date(self, carrier, warehouse):
        today = timezone.now().date()
        loading = today - datetime.timedelta(days=4)
        car = _car("B12LOADINGDATE001", warehouse, unload_date=today - datetime.timedelta(days=10))
        at = AutoTransport.objects.create(carrier=carrier, status="FORMED", loading_date=loading)
        at.cars.add(car)

        at.status = "LOADED"
        at.save()

        car.refresh_from_db()
        assert car.status == "TRANSFERRED"
        assert car.transfer_date == loading

    def test_without_dates_uses_today(self, carrier, warehouse):
        today = timezone.now().date()
        car = _car("B12NODATES0000001", warehouse, unload_date=today - datetime.timedelta(days=10))
        at = AutoTransport.objects.create(carrier=carrier, status="FORMED")
        at.cars.add(car)

        at.status = "LOADED"
        at.save()

        car.refresh_from_db()
        assert car.transfer_date == today

    def test_transfer_not_before_unload_date(self, carrier, warehouse):
        """Загрузка «задним числом» до разгрузки — передача фиксируется днём разгрузки."""
        today = timezone.now().date()
        early_car = _car("B12EARLYUNLOAD001", warehouse, unload_date=today - datetime.timedelta(days=10))
        late_car = _car("B12LATEUNLOAD0001", warehouse, unload_date=today - datetime.timedelta(days=1))
        at = AutoTransport.objects.create(carrier=carrier, status="FORMED", loading_date=today - datetime.timedelta(days=3))
        at.cars.add(early_car, late_car)

        at.status = "LOADED"
        at.save()

        early_car.refresh_from_db()
        late_car.refresh_from_db()
        assert early_car.transfer_date == today - datetime.timedelta(days=3)
        assert late_car.transfer_date == today - datetime.timedelta(days=1)
        assert {early_car.status, late_car.status} == {"TRANSFERRED"}

    def test_already_transferred_cars_keep_their_date(self, carrier, warehouse):
        today = timezone.now().date()
        car = _car("B12ALREADYTRF0001", warehouse, unload_date=today - datetime.timedelta(days=10))
        Car.objects.filter(pk=car.pk).update(status="TRANSFERRED", transfer_date=today - datetime.timedelta(days=7))
        at = AutoTransport.objects.create(carrier=carrier, status="FORMED", loading_date=today - datetime.timedelta(days=2))
        at.cars.add(car)

        at.status = "LOADED"
        at.save()

        car.refresh_from_db()
        assert car.transfer_date == today - datetime.timedelta(days=7)
