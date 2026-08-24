"""
Характеризующие тесты генерации позиций инвойса из услуг машины
(`NewInvoice.regenerate_items_from_cars` для выставителя-Компании).

Это сеть безопасности под фазу 1: оркестрация ``CarService`` переезжает
из админки в сервис, а именно состав ``CarService`` питает позиции
инвойса. Тесты фиксируют текущую логику:

- одна позиция на группу услуг по ``short_name``;
- услуги с одинаковым ``short_name`` суммируются;
- для Company цена позиции = (custom_price | default) + markup;
- хранение выносится в отдельную группу «Хран»;
- битые услуги (каталожная запись удалена) пропускаются.

Запуск: pytest core/tests/test_invoice_from_cars.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Company, Container, Warehouse, WarehouseService
from core.models_billing import NewInvoice


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
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="WH-INV", free_days=0)


@pytest.fixture
def car(db, warehouse):
    container = Container.objects.create(number="INV-CARS-1", status="FLOATING")
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin="INVFROMCARS000001",
        status="FLOATING",
        container=container,
        warehouse=warehouse,
    )


def _wh_service(warehouse, name, short_name, price):
    return WarehouseService.objects.create(
        warehouse=warehouse,
        name=name,
        short_name=short_name,
        default_price=Decimal(str(price)),
        is_active=True,
    )


def _add(car, svc, *, custom_price=None, markup=0):
    return CarService.objects.create(
        car=car,
        service_type="WAREHOUSE",
        service_id=svc.id,
        custom_price=None if custom_price is None else Decimal(str(custom_price)),
        markup_amount=Decimal(str(markup)),
        quantity=1,
    )


def _company_draft_invoice(company, car):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=None,
        recipient_warehouse=car.warehouse,
        date=timezone.now().date(),
        status="DRAFT",
    )
    inv.cars.add(car)
    return inv


@pytest.mark.django_db
class TestRegenerateItemsFromCars:
    def test_distinct_short_names_make_distinct_items(self, company, car, warehouse):
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50)
        _add(car, _wh_service(warehouse, "Доставка", "Дост", 30), custom_price=30)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        items = {i.description: i.unit_price for i in inv.items.all()}
        assert items == {"Порт": Decimal("50.00"), "Дост": Decimal("30.00")}

    def test_same_short_name_grouped_and_summed(self, company, car, warehouse):
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50)
        _add(car, _wh_service(warehouse, "Декларация", "Порт", 20), custom_price=20)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        items = list(inv.items.all())
        assert len(items) == 1
        assert items[0].description == "Порт"
        assert items[0].unit_price == Decimal("70.00")

    def test_company_price_includes_markup(self, company, car, warehouse):
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50, markup=15)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        item = inv.items.get()
        # Company: цена = (custom_price + markup) = 65
        assert item.unit_price == Decimal("65.00")

    def test_storage_is_separate_group(self, company, warehouse):
        container = Container.objects.create(number="INV-STORAGE-1", status="FLOATING")
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="INVSTORAGE0000001",
            status="FLOATING",
            container=container,
            warehouse=warehouse,
            unload_date=timezone.now().date() - timezone.timedelta(days=2),
        )
        _wh_service(warehouse, "Хранение", "Хран", 5)  # код по имени
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        descriptions = {i.description for i in inv.items.all()}
        assert "Порт" in descriptions
        assert "Хран" in descriptions

    def test_total_reflects_items(self, company, car, warehouse):
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50)
        _add(car, _wh_service(warehouse, "Доставка", "Дост", 30), custom_price=30)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()
        inv.refresh_from_db()
        assert inv.total == Decimal("80.00")


@pytest.mark.django_db
class TestStorageMarkupInInvoice:
    """Наценка на хранении: счёт клиенту должен сходиться с ``Car.total_price``.

    ``Car._update_storage_service_price`` пересчитывает у услуги хранения
    только ``custom_price`` и намеренно не трогает ``markup_amount``.
    Позиция «Хран» считается заново по актуальным дням, поэтому наценку
    нужно добавить к ней явно — иначе сумма счёта окажется меньше цены авто.
    """

    def _car_on_storage(self, warehouse, days_stored=3):
        container = Container.objects.create(number="INV-STOR-MK", status="FLOATING")
        return Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="INVSTORMARKUP0001",
            status="FLOATING",
            container=container,
            warehouse=warehouse,
            unload_date=timezone.now().date() - timezone.timedelta(days=days_stored - 1),
        )

    def test_company_invoice_includes_storage_markup(self, company, warehouse):
        car = self._car_on_storage(warehouse)
        storage = _wh_service(warehouse, "Хранение", "Хран", 5)
        _add(car, storage, custom_price=15, markup=20)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        item = inv.items.get(description="Хран")
        # 3 платных дня × 5 = 15 базы + 20 наценки
        assert item.unit_price == Decimal("35.00")

    def test_invoice_total_matches_car_total_price(self, company, warehouse):
        car = self._car_on_storage(warehouse)
        storage = _wh_service(warehouse, "Хранение", "Хран", 5)
        _add(car, storage, custom_price=15, markup=20)
        _add(car, _wh_service(warehouse, "Разгрузка", "Порт", 50), custom_price=50, markup=10)
        inv = _company_draft_invoice(company, car)

        inv.regenerate_items_from_cars()

        inv.refresh_from_db()
        # Регенерация считает дни и цену авто в памяти, не записывая их в БД,
        # поэтому для сравнения пересчитываем на свежем объекте.
        fresh = Car.objects.get(pk=car.pk)
        assert inv.total == fresh.calculate_total_price()

    def test_incoming_warehouse_invoice_excludes_markup(self, warehouse):
        """Входящий счёт склада — себестоимость, как и по остальным услугам."""
        car = self._car_on_storage(warehouse)
        storage = _wh_service(warehouse, "Хранение", "Хран", 5)
        _add(car, storage, custom_price=15, markup=20)
        company = Company.objects.create(name="Recipient Co")
        inv = NewInvoice.objects.create(
            issuer_warehouse=warehouse,
            recipient_company=company,
            date=timezone.now().date(),
            status="DRAFT",
        )
        inv.cars.add(car)

        inv.regenerate_items_from_cars()

        item = inv.items.get(description="Хран")
        assert item.unit_price == Decimal("15.00")
