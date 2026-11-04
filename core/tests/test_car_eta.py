"""Тесты ETA машины: наследование от контейнера и синхронизация."""

import datetime

import pytest
from django.utils import timezone

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

    def test_container_eta_change_syncs_to_cars(self):
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

        # Меняем ETA контейнера
        container.eta = datetime.date(2027, 1, 15)
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
