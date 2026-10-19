"""Тариф клиента: ставки по линиям, THS внутри тарифа, применение при любом пересчёте."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import (
    Car,
    CarService,
    Client,
    ClientTariffRate,
    Container,
    Line,
    LineService,
    Warehouse,
    WarehouseService,
)
from core.service_codes import is_storage_service


@pytest.fixture
def warehouse(db):
    wh = Warehouse.objects.create(name="NETO-T", free_days=30)
    WarehouseService.objects.create(
        warehouse=wh,
        name="Разгрузка/ Погрузка / Декларация",
        default_price=Decimal("140"),
        is_active=True,
        add_by_default=True,
    )
    return wh


@pytest.fixture
def maersk(db):
    return Line.objects.create(name="MAERSK-T")


@pytest.fixture
def msc(db):
    return Line.objects.create(name="MSC-T")


@pytest.fixture
def belarus(db, maersk):
    client = Client.objects.create(name="CAROMOTO BELARUS T", tariff_type="FLEXIBLE")
    ClientTariffRate.objects.create(client=client, vehicle_type="MOTO", min_cars=1, agreed_total_price=Decimal("195"))
    ClientTariffRate.objects.create(
        client=client, vehicle_type="SEDAN", line=maersk, min_cars=1, agreed_total_price=Decimal("290")
    )
    ClientTariffRate.objects.create(client=client, vehicle_type="SEDAN", min_cars=1, agreed_total_price=Decimal("260"))
    return client


def _make_car(container, warehouse, client, vin, vehicle_type="SEDAN", status="FLOATING"):
    return Car.objects.create(
        year=2022,
        brand="Honda",
        vin=vin,
        status=status,
        container=container,
        warehouse=warehouse,
        line=container.line,
        client=client,
        vehicle_type=vehicle_type,
    )


def _price_without_storage(car):
    return sum(
        (cs.invoice_price for cs in CarService.objects.filter(car=car) if not is_storage_service(cs)),
        Decimal("0"),
    )


@pytest.mark.django_db
class TestTariffRateLookup:
    def test_line_specific_rate_wins_over_any_line(self, warehouse, belarus, maersk, msc):
        from core.services.car_service_manager import apply_client_tariff_for_car

        c1 = Container.objects.create(number="TAR-MAERSK", status="FLOATING", line=maersk, warehouse=warehouse)
        c2 = Container.objects.create(number="TAR-MSC", status="FLOATING", line=msc, warehouse=warehouse)
        car_maersk = _make_car(c1, warehouse, belarus, "TARIFFLINE0000001")
        car_msc = _make_car(c2, warehouse, belarus, "TARIFFLINE0000002")

        apply_client_tariff_for_car(car_maersk)
        apply_client_tariff_for_car(car_msc)

        assert _price_without_storage(car_maersk) == Decimal("290")
        assert _price_without_storage(car_msc) == Decimal("260")

    def test_crossover_falls_back_to_sedan_rate(self, warehouse, belarus, msc):
        from core.services.car_service_manager import _get_agreed_total

        assert _get_agreed_total(belarus, "CROSSOVER", 1, line_id=msc.pk) == Decimal("260")
        assert _get_agreed_total(belarus, "MOTO", 1, line_id=msc.pk) == Decimal("195")
        assert _get_agreed_total(belarus, "BIG_MOTO", 1, line_id=msc.pk) is None

    def test_own_vehicle_type_rate_beats_sedan_fallback(self, belarus, maersk):
        from core.services.car_service_manager import _get_agreed_total

        ClientTariffRate.objects.create(
            client=belarus, vehicle_type="CROSSOVER", min_cars=1, agreed_total_price=Decimal("310")
        )
        assert _get_agreed_total(belarus, "CROSSOVER", 1, line_id=maersk.pk) == Decimal("310")


@pytest.mark.django_db
class TestThsInsideTariff:
    def test_line_ths_is_included_in_tariff(self, warehouse, belarus, maersk):
        from core.services.car_service_manager import (
            apply_client_tariffs_for_container,
            create_ths_services_for_container,
        )

        container = Container.objects.create(
            number="TAR-THS-LINE",
            status="FLOATING",
            line=maersk,
            warehouse=warehouse,
            ths=Decimal("90"),
            ths_payer="LINE",
        )
        car = _make_car(container, warehouse, belarus, "TARIFFTHS00000001")

        create_ths_services_for_container(container)
        apply_client_tariffs_for_container(container)

        ths = CarService.objects.get(car=car, service_type="LINE")
        assert ths.invoice_price == Decimal("90")
        assert _price_without_storage(car) == Decimal("290")

    def test_price_stays_equal_to_tariff_when_ths_changes(self, warehouse, belarus, maersk):
        from core.services.car_service_manager import (
            apply_client_tariffs_for_container,
            create_ths_services_for_container,
        )

        container = Container.objects.create(
            number="TAR-THS-CHG", status="FLOATING", line=maersk, warehouse=warehouse, ths=Decimal("90")
        )
        car = _make_car(container, warehouse, belarus, "TARIFFTHS00000002")
        create_ths_services_for_container(container)
        apply_client_tariffs_for_container(container)

        container.ths = Decimal("175")
        container.save(update_fields=["ths"])
        create_ths_services_for_container(container)
        apply_client_tariffs_for_container(container)

        assert _price_without_storage(car) == Decimal("290")


@pytest.mark.django_db
class TestTariffAppliedOnRecalc:
    def test_recalc_task_applies_tariff_to_programmatic_edit(self, warehouse, belarus, msc):
        from core.tasks import recalculate_cars_total_price_task

        container = Container.objects.create(number="TAR-RECALC", status="FLOATING", line=msc, warehouse=warehouse)
        car = _make_car(container, warehouse, None, "TARIFFRECALC00001")
        Car.objects.filter(pk=car.pk).update(client=belarus)

        recalculate_cars_total_price_task(car_ids=[car.pk])

        car.refresh_from_db()
        assert car.total_price == Decimal("260")

    def test_recalc_skips_transferred_cars(self, warehouse, belarus, msc):
        from core.tasks import recalculate_cars_total_price_task

        container = Container.objects.create(number="TAR-TRANSF", status="FLOATING", line=msc, warehouse=warehouse)
        car = _make_car(container, warehouse, None, "TARIFFTRANSF00001")
        Car.objects.filter(pk=car.pk).update(client=belarus, status="TRANSFERRED")

        recalculate_cars_total_price_task(car_ids=[car.pk])

        car.refresh_from_db()
        assert car.total_price == Decimal("140")

    def test_programmatic_create_gets_tariff_price(self, warehouse, belarus, msc, django_capture_on_commit_callbacks):
        container = Container.objects.create(number="TAR-CREATE", status="FLOATING", line=msc, warehouse=warehouse)
        with django_capture_on_commit_callbacks(execute=True):
            car = _make_car(container, warehouse, belarus, "TARIFFCREATE00001")

        car.refresh_from_db()
        assert car.total_price == Decimal("260")


@pytest.mark.django_db
class TestCatalogPriceChange:
    def test_price_change_skips_transferred_and_keeps_tariff(
        self, warehouse, belarus, msc, django_capture_on_commit_callbacks
    ):
        container = Container.objects.create(number="TAR-CATALOG", status="FLOATING", line=msc, warehouse=warehouse)
        with django_capture_on_commit_callbacks(execute=True):
            active = _make_car(container, warehouse, belarus, "TARIFFCATALOG0001")
            transferred = _make_car(container, warehouse, None, "TARIFFCATALOG0002")
        Car.objects.filter(pk=transferred.pk).update(status="TRANSFERRED")

        svc = WarehouseService.objects.get(warehouse=warehouse, add_by_default=True)
        with django_capture_on_commit_callbacks(execute=True):
            svc.default_price = Decimal("120")
            svc.save()

        assert CarService.objects.get(car=transferred, service_id=svc.id).custom_price == Decimal("140")
        assert CarService.objects.get(car=active, service_id=svc.id).custom_price == Decimal("120")
        active.refresh_from_db()
        assert active.total_price == Decimal("260")

    def test_saving_ths_catalog_service_keeps_car_ths(self, warehouse, belarus, maersk):
        from core.services.car_service_manager import create_ths_services_for_container

        container = Container.objects.create(
            number="TAR-THS-CAT", status="FLOATING", line=maersk, warehouse=warehouse, ths=Decimal("90")
        )
        car = _make_car(container, warehouse, belarus, "TARIFFTHSCAT00001")
        create_ths_services_for_container(container)
        ths_service = LineService.objects.get(line=maersk, name__icontains="THS")

        ths_service.save()

        assert CarService.objects.filter(car=car, service_type="LINE", service_id=ths_service.id).exists()
