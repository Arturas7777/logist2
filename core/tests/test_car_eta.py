"""Тесты ETA машины: наследование от контейнера и синхронизация."""

import datetime

import pytest

from core.models import Car, Client, Container, Warehouse


@pytest.mark.django_db
class TestCarETA:
    """Тесты property Car.eta и синхронизации с Container."""

    def test_eta_inherits_from_container(self):
        """ETA машины наследуется от контейнера, если не задана явно."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST001",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )
        car = Car.objects.create(
            vin="1HGBH41JXMN109186",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
        )

        # ETA наследуется от контейнера
        assert car.eta == datetime.date(2026, 12, 25)
        assert car._eta_override is None

    def test_eta_override_takes_precedence(self):
        """Явная ETA машины переопределяет ETA контейнера."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST002",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )
        car = Car.objects.create(
            vin="2HGBH41JXMN109187",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
            _eta_override=datetime.date(2026, 12, 30),
        )

        # Явная ETA машины переопределяет контейнер
        assert car.eta == datetime.date(2026, 12, 30)
        assert car._eta_override == datetime.date(2026, 12, 30)

    def test_eta_without_container_uses_foreign_eta(self):
        """Машина без контейнера использует foreign_eta как fallback."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        car = Car.objects.create(
            vin="3HGBH41JXMN109188",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            foreign_container_number="FOREIGN123",
            foreign_eta=datetime.date(2027, 1, 10),
        )

        # ETA берётся из foreign_eta
        assert car.eta == datetime.date(2027, 1, 10)
        assert car._eta_override is None
        assert car.container_id is None

    def test_container_eta_change_syncs_to_cars(self, django_capture_on_commit_callbacks):
        """Изменение ETA контейнера синхронизируется на машины без переопределения."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST003",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )

        # Машина 1: без переопределения (должна синхронизироваться)
        car1 = Car.objects.create(
            vin="4HGBH41JXMN109189",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
        )

        # Машина 2: с явным переопределением (НЕ должна синхронизироваться)
        car2 = Car.objects.create(
            vin="5HGBH41JXMN109190",
            brand="Toyota",
            year=2021,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
            _eta_override=datetime.date(2027, 1, 1),
        )

        # Меняем ETA контейнера. Синхронизация идёт через on_commit, а тест
        # работает внутри транзакции — колбэки выполняем явно.
        container.eta = datetime.date(2027, 1, 15)
        with django_capture_on_commit_callbacks(execute=True):
            container.save()

        # Обновляем объекты из БД
        car1.refresh_from_db()
        car2.refresh_from_db()

        # car1: foreign_eta синхронизировалась, property eta возвращает новую дату
        assert car1.foreign_eta == datetime.date(2027, 1, 15)
        assert car1.eta == datetime.date(2027, 1, 15)

        # car2: foreign_eta НЕ синхронизировалась (_eta_override != NULL),
        # property eta возвращает переопределённую дату
        assert car2._eta_override == datetime.date(2027, 1, 1)
        assert car2.eta == datetime.date(2027, 1, 1)

    def test_eta_setter(self):
        """Setter car.eta устанавливает _eta_override."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST004",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )
        car = Car.objects.create(
            vin="6HGBH41JXMN109191",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
        )

        # Изначально наследуется от контейнера
        assert car.eta == datetime.date(2026, 12, 25)

        # Устанавливаем явную ETA через setter
        car.eta = datetime.date(2027, 2, 1)
        car.save()

        # Теперь используется переопределённая дата
        car.refresh_from_db()
        assert car._eta_override == datetime.date(2027, 2, 1)
        assert car.eta == datetime.date(2027, 2, 1)


@pytest.mark.django_db
def test_eta_display_matches_admin_date_widget():
    """В карточке ETA — то же поле даты, что разгрузка и передача (дд.мм.гггг)."""
    from django.contrib import admin as django_admin

    from core.admin.car import CarAdmin

    client = Client.objects.create(name="Test Client")
    warehouse = Warehouse.objects.create(name="Test Warehouse")
    container = Container.objects.create(
        number="TESTDATE01",
        status="FLOATING",
        eta=datetime.date(2026, 12, 25),
    )
    car = Car.objects.create(
        vin="7HGBH41JXMN109192",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="FLOATING",
        container=container,
    )

    html = str(CarAdmin(Car, django_admin.site).eta_display(car))
    assert 'class="vDateField"' in html
    assert 'value="25.12.2026"' in html
    assert "readonly" in html
    assert "🚢" not in html


@pytest.mark.django_db
def test_car_changelist_puts_earlier_eta_first():
    """Список всех авто: разгруженные выше, внутри группы более ранняя ETA выше."""
    from django.test import RequestFactory
    from django.urls import resolve

    from core.admin.car import CarAdmin
    from logist2.admin_site import admin_site

    client = Client.objects.create(name="Sort Client")
    warehouse = Warehouse.objects.create(name="Sort Warehouse")
    early = Container.objects.create(number="ETAEARLY0001", status="FLOATING", eta=datetime.date(2026, 9, 1))
    mid = Container.objects.create(number="ETAMID000001", status="FLOATING", eta=datetime.date(2026, 11, 1))
    late = Container.objects.create(number="ETALATE00001", status="FLOATING", eta=datetime.date(2026, 12, 20))
    overridden = Container.objects.create(number="ETAOVER00001", status="FLOATING", eta=datetime.date(2026, 12, 1))

    Car.objects.create(
        vin="ETAUNLDVIN0000001",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="UNLOADED",
        container=early,
    )
    Car.objects.create(
        vin="ETAMIDVIN00000001",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="FLOATING",
        container=mid,
    )
    Car.objects.create(
        vin="ETALATEVIN0000001",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="FLOATING",
        container=late,
    )
    Car.objects.create(
        vin="ETAOVERVIN0000001",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="FLOATING",
        container=overridden,
        _eta_override=datetime.date(2026, 8, 1),
    )
    Car.objects.create(
        vin="ETAUNLDVIN0000002",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="UNLOADED",
        container=mid,
    )
    Car.objects.create(
        vin="ETANONEVIN0000001",
        brand="Honda",
        year=2020,
        client=client,
        warehouse=warehouse,
        status="FLOATING",
    )

    request = RequestFactory().get("/admin/core/car/")
    request.resolver_match = resolve("/admin/core/car/")
    car_admin = CarAdmin(Car, admin_site)
    vins = list(
        car_admin.get_queryset(request).order_by(*car_admin.get_ordering(request)).values_list("vin", flat=True)
    )
    assert vins == [
        "ETAUNLDVIN0000001",
        "ETAUNLDVIN0000002",
        "ETAOVERVIN0000001",
        "ETAMIDVIN00000001",
        "ETALATEVIN0000001",
        "ETANONEVIN0000001",
    ]
