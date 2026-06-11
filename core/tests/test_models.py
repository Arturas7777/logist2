"""
Тесты моделей core приложения.

Используется стандартный Django TestCase с тестовой БД (SQLite).
Запуск: python manage.py test core.tests.test_models
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    VEHICLE_TYPE_CHOICES,
    Car,
    Company,
    Container,
    Line,
    Warehouse,
)


class CompanyModelTest(TestCase):
    """Тесты модели Company"""

    def setUp(self):
        # Используем имя из settings.COMPANY_NAME, чтобы get_default() нашёл компанию
        # независимо от точного написания («Caromoto Lithuania» vs «Caromoto Lithuania, MB»).
        self.company = Company.objects.create(name=settings.COMPANY_NAME)

    def test_get_default_returns_company(self):
        """get_default() возвращает компанию из settings.COMPANY_NAME"""
        result = Company.get_default()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, settings.COMPANY_NAME)

    def test_get_default_returns_none_when_missing(self):
        """get_default() возвращает None если компания не существует"""
        Company.objects.all().delete()
        result = Company.get_default()
        self.assertIsNone(result)

    def test_str_representation(self):
        self.assertEqual(str(self.company), settings.COMPANY_NAME)


class ContainerModelTest(TestCase):
    """Тесты модели Container"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name="Test WH")
        self.line = Line.objects.create(name="Test Line")

    def test_create_floating_container(self):
        """Создание контейнера со статусом В пути"""
        container = Container.objects.create(
            number="TEST-001",
            status="FLOATING",
            line=self.line,
        )
        self.assertEqual(container.status, "FLOATING")
        self.assertEqual(str(container), "TEST-001")

    def test_unloaded_requires_warehouse_and_date(self):
        """Статус UNLOADED требует склад и дату разгрузки.

        Раньше тест ожидал ``ValueError``, но валидация делается через
        ``Container.clean()`` и поднимает ``ValidationError``. Поскольку
        ``Container.save()`` теперь сам зовёт ``self.clean()``
        (см. core/models.py), ошибка приходит при `objects.create`.
        """
        with self.assertRaises(ValidationError):
            Container.objects.create(
                number="TEST-002",
                status="UNLOADED",
            )

    def test_clean_validation_unloaded_no_warehouse(self):
        """clean() проверяет обязательные поля для UNLOADED"""
        container = Container(
            number="TEST-003",
            status="UNLOADED",
            unload_date=timezone.now().date(),
        )
        with self.assertRaises(ValidationError) as ctx:
            container.clean()
        self.assertIn('warehouse', ctx.exception.message_dict)

    def test_clean_validation_unload_before_eta(self):
        """clean() не позволяет дате разгрузки быть раньше ETA"""
        container = Container(
            number="TEST-004",
            status="UNLOADED",
            warehouse=self.warehouse,
            eta=timezone.now().date() + timezone.timedelta(days=5),
            unload_date=timezone.now().date(),
        )
        with self.assertRaises(ValidationError) as ctx:
            container.clean()
        self.assertIn('unload_date', ctx.exception.message_dict)

    def test_storage_is_aggregate_of_cars(self):
        """Хранение контейнера — агрегат хранения его машин (read-only)."""
        container = Container(number="TEST-005", status="FLOATING")
        # Несохранённый контейнер — нули.
        self.assertEqual(container.days, 0)
        self.assertEqual(container.storage_cost, 0)

        container.save()
        self.assertEqual(container.days, 0)
        self.assertEqual(container.storage_cost, 0)


class CarModelTest(TestCase):
    """Тесты модели Car"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name="Test WH", free_days=3)
        self.container = Container.objects.create(
            number="CONT-001",
            status="FLOATING",
        )

    def test_create_car(self):
        """Создание автомобиля"""
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="12345678901234567",
            status="FLOATING",
            container=self.container,
        )
        self.assertEqual(str(car), "Toyota (12345678901234567)")

    def test_inherit_warehouse_from_container(self):
        """Автомобиль наследует склад из контейнера"""
        self.container.warehouse = self.warehouse
        self.container.save()
        car = Car(
            year=2023,
            brand="Honda",
            vin="ABCDE12345678901A",
            status="FLOATING",
            container=self.container,
        )
        car._inherit_from_container()
        self.assertEqual(car.warehouse, self.warehouse)

    def test_sync_status_and_dates_transferred(self):
        """Если transfer_date установлен, статус меняется на TRANSFERRED"""
        car = Car(
            year=2023,
            brand="BMW",
            vin="BMWVN12345678901A",
            status="UNLOADED",
            transfer_date=timezone.now().date(),
        )
        car._sync_status_and_dates()
        self.assertEqual(car.status, "TRANSFERRED")

    def test_sync_status_sets_transfer_date(self):
        """Если статус TRANSFERRED, но нет даты - проставляется автоматически"""
        car = Car(
            year=2023,
            brand="Audi",
            vin="AUDIV12345678901A",
            status="TRANSFERRED",
        )
        car._sync_status_and_dates()
        self.assertIsNotNone(car.transfer_date)

    def test_clean_validation_vin_length(self):
        """clean() проверяет длину VIN"""
        car = Car(
            year=2023,
            brand="Tesla",
            vin="SHORT",
            status="FLOATING",
        )
        with self.assertRaises(ValidationError) as ctx:
            car.clean()
        self.assertIn('vin', ctx.exception.message_dict)

    def test_clean_validation_year_range(self):
        """clean() проверяет диапазон года"""
        car = Car(
            year=1800,
            brand="Ford",
            vin="12345678901234567",
            status="FLOATING",
        )
        with self.assertRaises(ValidationError) as ctx:
            car.clean()
        self.assertIn('year', ctx.exception.message_dict)

    def test_vehicle_type_choices_not_duplicated(self):
        """VEHICLE_TYPE_CHOICES в Car ссылается на модульный уровень"""
        self.assertIs(Car.VEHICLE_TYPE_CHOICES, VEHICLE_TYPE_CHOICES)


class VehicleTypeChoicesTest(TestCase):
    """Проверяет единый набор типов ТС"""

    def test_all_choices_present(self):
        """Все 11 типов ТС присутствуют"""
        self.assertEqual(len(VEHICLE_TYPE_CHOICES), 11)
        codes = [code for code, _ in VEHICLE_TYPE_CHOICES]
        self.assertIn('SEDAN', codes)
        self.assertIn('MOTO', codes)
        self.assertIn('SUV', codes)
        self.assertIn('CONSTRUCTION', codes)
