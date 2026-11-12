"""Тесты бот-команды car-set-eta."""

import datetime

import pytest

from core.models import Car, Client, Container, Warehouse
from core.services.bot_ops import car_set_eta
from core.services.bot_runtime import BotError


@pytest.mark.django_db
class TestCarSetETA:
    """Тесты бот-команды car-set-eta."""

    def test_car_set_eta_by_vin(self):
        """Установка ETA машины по VIN."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        car = Car.objects.create(
            vin="1HGBH41JXMN109186",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            foreign_container_number="FOREIGN123",
        )

        # Устанавливаем ETA
        result = car_set_eta(
            vin="1HGBH41JXMN109186",
            eta=datetime.date(2027, 3, 15),
            apply=True,
        )

        assert result["result"]["outcome"] == "updated"
        assert result["after"]["_eta_override"] == "2027-03-15"

        car.refresh_from_db()
        assert car._eta_override == datetime.date(2027, 3, 15)
        assert car.foreign_eta == datetime.date(2027, 3, 15)
        assert car.eta == datetime.date(2027, 3, 15)

    def test_car_set_eta_by_id(self):
        """Установка ETA машины по car_id."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        car = Car.objects.create(
            vin="2HGBH41JXMN109187",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
        )

        result = car_set_eta(
            car_id=car.pk,
            eta=datetime.date(2027, 4, 20),
            apply=True,
        )

        assert result["result"]["outcome"] == "updated"
        car.refresh_from_db()
        assert car._eta_override == datetime.date(2027, 4, 20)
        assert car.eta == datetime.date(2027, 4, 20)

    def test_car_set_eta_overrides_container(self):
        """ETA машины переопределяет ETA контейнера."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST001",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )
        car = Car.objects.create(
            vin="3HGBH41JXMN109188",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
        )

        # Изначально наследуется от контейнера
        assert car.eta == datetime.date(2026, 12, 25)

        # Переопределяем ETA для машины
        result = car_set_eta(
            vin="3HGBH41JXMN109188",
            eta=datetime.date(2027, 1, 10),
            apply=True,
        )

        assert result["result"]["outcome"] == "updated"
        car.refresh_from_db()
        assert car._eta_override == datetime.date(2027, 1, 10)
        assert car.eta == datetime.date(2027, 1, 10)
        # Контейнер не изменился
        container.refresh_from_db()
        assert container.eta == datetime.date(2026, 12, 25)

    def test_car_set_eta_clear_override(self):
        """Очистка переопределения ETA (машина снова наследует от контейнера)."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        container = Container.objects.create(
            number="TEST002",
            status="FLOATING",
            eta=datetime.date(2026, 12, 25),
        )
        car = Car.objects.create(
            vin="4HGBH41JXMN109189",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
            container=container,
            _eta_override=datetime.date(2027, 2, 1),
        )

        # Изначально переопределена
        assert car.eta == datetime.date(2027, 2, 1)

        # Очищаем переопределение (eta=None)
        result = car_set_eta(
            vin="4HGBH41JXMN109189",
            eta=None,
            apply=True,
        )

        assert result["result"]["outcome"] == "updated"
        car.refresh_from_db()
        assert car._eta_override is None
        # Теперь наследуется от контейнера
        assert car.eta == datetime.date(2026, 12, 25)

    def test_car_set_eta_dry_run(self):
        """Dry-run не изменяет данные."""
        client = Client.objects.create(name="Test Client")
        warehouse = Warehouse.objects.create(name="Test Warehouse")
        car = Car.objects.create(
            vin="5HGBH41JXMN109190",
            brand="Honda",
            year=2020,
            client=client,
            warehouse=warehouse,
            status="FLOATING",
        )

        result = car_set_eta(
            vin="5HGBH41JXMN109190",
            eta=datetime.date(2027, 5, 1),
            apply=False,
        )

        assert result["result"]["outcome"] == "would_update"
        assert result["after"]["_eta_override"] == "2027-05-01"

        car.refresh_from_db()
        # Данные не изменились
        assert car._eta_override is None
        assert car.eta is None

    def test_car_set_eta_not_found(self):
        """Ошибка если машина не найдена."""
        # VIN ровно 17 символов: более короткий отсекается раньше, проверкой длины.
        with pytest.raises(BotError, match="Машина не найдена"):
            car_set_eta(
                vin="9HGBH41JXMN109199",
                eta=datetime.date(2027, 6, 1),
                apply=True,
            )

    def test_car_set_eta_requires_vin_or_id(self):
        """Ошибка если не указан ни VIN, ни car_id."""
        with pytest.raises(BotError, match="Нужен --vin или --car-id"):
            car_set_eta(
                eta=datetime.date(2027, 6, 1),
                apply=True,
            )
