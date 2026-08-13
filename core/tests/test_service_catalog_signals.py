"""Сигналы каталога услуг: сохранение склада не должно затирать наценки авто.

Раньше ``WarehouseService.save()`` (и то же для линии/перевозчика/компании)
массово писал ``default_markup`` во все существующие ``CarService``. Админский
formset save()'ит каждую строку, даже нетронутую — правка наценки у одной
услуги обнуляла наценки остальных машин.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import (
    Car,
    CarService,
    Container,
    Line,
    LineService,
    Warehouse,
    WarehouseService,
)


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="WH-Catalog", free_days=0)


@pytest.fixture
def car(db, warehouse):
    container = Container.objects.create(number="CATALOG-1", status="FLOATING")
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin="CATALOGSERVICE0001",
        status="UNLOADED",
        container=container,
        warehouse=warehouse,
        days=3,
    )


@pytest.mark.django_db
class TestWarehouseServiceCatalogSignal:
    def test_markup_only_change_does_not_touch_existing_cars(self, warehouse, car):
        svc = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Разгрузка",
            default_price=Decimal("160"),
            default_markup=Decimal("0"),
            is_active=True,
        )
        cs = CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=svc.id,
            custom_price=Decimal("160"),
            markup_amount=Decimal("25"),
        )

        svc.default_markup = Decimal("20")
        svc.save()

        cs.refresh_from_db()
        assert cs.markup_amount == Decimal("25")
        assert cs.custom_price == Decimal("160")

    def test_untouched_sibling_service_save_does_not_reset_markup(self, warehouse, car):
        unloading = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Разгрузка",
            default_price=Decimal("160"),
            default_markup=Decimal("0"),
            is_active=True,
        )
        ths = WarehouseService.objects.create(
            warehouse=warehouse,
            name="THS NETO",
            default_price=Decimal("85"),
            default_markup=Decimal("0"),
            is_active=True,
        )
        cs_unloading = CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=unloading.id,
            custom_price=Decimal("160"),
            markup_amount=Decimal("20"),
        )
        cs_ths = CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=ths.id,
            custom_price=Decimal("85"),
            markup_amount=Decimal("15"),
        )

        # Formset сохраняет и соседнюю нетронутую строку.
        ths.save()

        cs_unloading.refresh_from_db()
        cs_ths.refresh_from_db()
        assert cs_unloading.markup_amount == Decimal("20")
        assert cs_ths.markup_amount == Decimal("15")

    def test_price_change_updates_custom_price_but_keeps_markup(self, warehouse, car):
        svc = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Разгрузка",
            default_price=Decimal("160"),
            default_markup=Decimal("0"),
            is_active=True,
        )
        cs = CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=svc.id,
            custom_price=Decimal("160"),
            markup_amount=Decimal("25"),
        )

        svc.default_price = Decimal("180")
        svc.save()

        cs.refresh_from_db()
        assert cs.custom_price == Decimal("180")
        assert cs.markup_amount == Decimal("25")

    def test_deactivate_still_deletes_related_carservices(self, warehouse, car):
        svc = WarehouseService.objects.create(
            warehouse=warehouse,
            name="Разгрузка",
            default_price=Decimal("160"),
            is_active=True,
        )
        CarService.objects.create(
            car=car,
            service_type="WAREHOUSE",
            service_id=svc.id,
            custom_price=Decimal("160"),
        )

        svc.is_active = False
        svc.save()

        assert not CarService.objects.filter(car=car, service_id=svc.id).exists()


@pytest.mark.django_db
class TestLineServiceCatalogSignal:
    def test_markup_only_change_does_not_touch_existing_cars(self, db):
        line = Line.objects.create(name="LINE-Catalog")
        container = Container.objects.create(number="CATALOG-L", status="FLOATING", line=line)
        car = Car.objects.create(
            year=2023,
            brand="BMW",
            vin="CATALOGLINESVC0001",
            status="FLOATING",
            container=container,
            line=line,
        )
        svc = LineService.objects.create(
            line=line,
            name="THS",
            default_price=Decimal("100"),
            default_markup=Decimal("0"),
            is_active=True,
        )
        cs = CarService.objects.create(
            car=car,
            service_type="LINE",
            service_id=svc.id,
            custom_price=Decimal("100"),
            markup_amount=Decimal("12"),
        )

        svc.default_markup = Decimal("30")
        svc.save()

        cs.refresh_from_db()
        assert cs.markup_amount == Decimal("12")
        assert cs.custom_price == Decimal("100")
