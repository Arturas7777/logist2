"""
Характеризующие тесты каскада цены автомобиля.

Фиксируют поведение ПЕРЕД рефакторингом (сеть безопасности для фазы 1,
где оркестрация услуг переезжает из админки в сервис):

- ``Car.calculate_total_price()`` = Σ ``CarService.invoice_price``
  (базовая цена + скрытая наценка) × quantity.
- Сигнал ``recalculate_car_price_on_service_save`` после коммита
  пересчитывает ``Car.total_price`` и пишет его в БД (UPDATE).
- Удаление услуги уменьшает ``total_price``.
- Хранение: ``update_days_and_storage`` берёт ставку из услуги склада
  «Хранение» и считает ``storage_cost = платные_дни × ставка``.

Запуск: pytest core/tests/test_price_cascade.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Container, Warehouse, WarehouseService


@pytest.fixture(autouse=True)
def _clear_pricing_thread_locals():
    """Сигналы ``CarService`` дедуплицируют пересчёт через process-global
    thread-local множества и чистят их в ``on_commit``. В обычных
    (``@django_db`` без ``transaction``) тестах ``on_commit`` не срабатывает,
    поэтому car_id «протекает» между тестами и ломает изоляцию. Чистим
    наборы до и после каждого теста.
    """
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
    return Warehouse.objects.create(name="WH-Cascade", free_days=0)


@pytest.fixture
def container(db):
    return Container.objects.create(number="CASCADE-001", status="FLOATING")


@pytest.fixture
def car(db, container, warehouse):
    return Car.objects.create(
        year=2023, brand="Toyota", vin="CASCADE1234567890",
        status="FLOATING", container=container, warehouse=warehouse,
    )


def _wh_service(warehouse, name, price, *, code="", add_by_default=False):
    return WarehouseService.objects.create(
        warehouse=warehouse, name=name, code=code,
        default_price=Decimal(str(price)), is_active=True,
        add_by_default=add_by_default,
    )


def _add_car_service(car, wh_service, *, custom_price=None, markup=0, qty=1):
    return CarService.objects.create(
        car=car,
        service_type="WAREHOUSE",
        service_id=wh_service.id,
        custom_price=None if custom_price is None else Decimal(str(custom_price)),
        markup_amount=Decimal(str(markup)),
        quantity=qty,
    )


# ---------------------------------------------------------------------------
# calculate_total_price: математика (детерминированно, без on_commit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCalculateTotalPrice:
    def test_sum_of_services(self, car, warehouse):
        svc1 = _wh_service(warehouse, "Разгрузка", 50)
        svc2 = _wh_service(warehouse, "Погрузка", 30)
        _add_car_service(car, svc1, custom_price=50)
        _add_car_service(car, svc2, custom_price=30)
        total = car.calculate_total_price()
        assert total == Decimal("80.00")

    def test_markup_included(self, car, warehouse):
        svc = _wh_service(warehouse, "Разгрузка", 50)
        _add_car_service(car, svc, custom_price=50, markup=10)
        # invoice_price = (base + markup) * qty = (50 + 10) * 1
        assert car.calculate_total_price() == Decimal("60.00")

    def test_quantity_multiplies(self, car, warehouse):
        svc = _wh_service(warehouse, "Услуга", 25)
        _add_car_service(car, svc, custom_price=25, qty=3)
        assert car.calculate_total_price() == Decimal("75.00")

    def test_custom_price_overrides_default(self, car, warehouse):
        svc = _wh_service(warehouse, "Услуга", 100)
        # custom_price=70 имеет приоритет над default_price=100
        _add_car_service(car, svc, custom_price=70)
        assert car.calculate_total_price() == Decimal("70.00")

    def test_falls_back_to_default_price(self, car, warehouse):
        svc = _wh_service(warehouse, "Услуга", 42)
        # custom_price=None → берётся default_price каталога
        _add_car_service(car, svc, custom_price=None)
        assert car.calculate_total_price() == Decimal("42.00")

    def test_no_services_is_zero(self, car):
        assert car.calculate_total_price() == Decimal("0.00")


# ---------------------------------------------------------------------------
# Хранение: дни и storage_cost
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStorageCascade:
    def test_storage_cost_from_warehouse_rate(self, car, warehouse):
        _wh_service(warehouse, "Хранение", 5, code="STORAGE")
        car.unload_date = timezone.now().date() - timezone.timedelta(days=4)
        # free_days=0 → платных дней = (4 + 1) = 5; ставка 5 → 25
        car.update_days_and_storage()
        assert car.days == 5
        assert car.storage_cost == Decimal("25.00")

    def test_free_days_reduce_paid_days(self, container):
        wh = Warehouse.objects.create(name="WH-Free", free_days=3)
        _wh_service(wh, "Хранение", 10, code="STORAGE")
        car = Car.objects.create(
            year=2023, brand="Honda", vin="STORAGE123456789A",
            status="FLOATING", container=container, warehouse=wh,
        )
        car.unload_date = timezone.now().date() - timezone.timedelta(days=4)
        # (4 + 1) - 3 free = 2 платных дня; 2 * 10 = 20
        car.update_days_and_storage()
        assert car.days == 2
        assert car.storage_cost == Decimal("20.00")

    def test_no_unload_date_zero_storage(self, car, warehouse):
        _wh_service(warehouse, "Хранение", 5, code="STORAGE")
        car.unload_date = None
        car.update_days_and_storage()
        assert car.days == 0
        assert car.storage_cost == Decimal("0.00")


# ---------------------------------------------------------------------------
# Сигнальный каскад: CarService.save/delete → Car.total_price в БД
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSignalCascade:
    """``transaction=True`` нужен, чтобы ``transaction.on_commit`` коллбэки
    сигнала ``recalculate_car_price_on_service_save`` реально срабатывали."""

    def test_adding_service_updates_total_price_in_db(self):
        wh = Warehouse.objects.create(name="WH-Sig", free_days=0)
        svc = _wh_service(wh, "Разгрузка", 50)
        container = Container.objects.create(number="SIG-CASCADE-1", status="FLOATING")
        car = Car.objects.create(
            year=2023, brand="Toyota", vin="SIGCASCADE0000001",
            status="FLOATING", container=container, warehouse=wh,
        )
        _add_car_service(car, svc, custom_price=50)
        # После commit сигнал должен пересчитать и записать total_price в БД.
        car.refresh_from_db()
        assert car.total_price == Decimal("50.00")

    def test_deleting_service_decreases_total_price(self):
        wh = Warehouse.objects.create(name="WH-Sig2", free_days=0)
        svc1 = _wh_service(wh, "Разгрузка", 50)
        svc2 = _wh_service(wh, "Погрузка", 30)
        container = Container.objects.create(number="SIG-CASCADE-2", status="FLOATING")
        car = Car.objects.create(
            year=2023, brand="Toyota", vin="SIGCASCADE0000002",
            status="FLOATING", container=container, warehouse=wh,
        )
        _add_car_service(car, svc1, custom_price=50)
        cs2 = _add_car_service(car, svc2, custom_price=30)
        car.refresh_from_db()
        assert car.total_price == Decimal("80.00")

        cs2.delete()
        car.refresh_from_db()
        assert car.total_price == Decimal("50.00")
