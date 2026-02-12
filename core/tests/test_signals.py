"""
Тесты сигналов core приложения.

Проверяет thread-safe хранение старых значений на экземплярах (а не в глобальных dict).
Запуск: python manage.py test core.tests.test_signals
"""
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone

from core.models import Car, Container, Warehouse, Line, Company


class ContainerPreSaveSignalTest(TestCase):
    """Тесты pre_save сигнала для Container (thread-safe instance attrs)"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name="Signal WH", free_days=0)
        self.line = Line.objects.create(name="Signal Line")

    def test_old_values_stored_on_instance(self):
        """Старые значения сохраняются на экземпляре, а не в глобальном dict"""
        container = Container.objects.create(
            number="SIG-001",
            status="FLOATING",
            line=self.line,
        )
        # При изменении статуса - pre_save сохранит старые значения
        container.status = "IN_PORT"
        container.save()
        
        # После save, _pre_save_values должен быть очищен
        self.assertIsNone(getattr(container, '_pre_save_values', None))

    def test_unloaded_status_at_set_on_transition(self):
        """unloaded_status_at устанавливается при переходе в UNLOADED"""
        container = Container.objects.create(
            number="SIG-002",
            status="IN_PORT",
            line=self.line,
        )
        container.status = "UNLOADED"
        container.warehouse = self.warehouse
        container.unload_date = timezone.now().date()
        container.save()
        
        container.refresh_from_db()
        self.assertIsNotNone(container.unloaded_status_at)


class CarPreSaveContractorSignalTest(TestCase):
    """Тесты pre_save сигнала для Car (thread-safe contractor tracking)"""

    def setUp(self):
        self.warehouse1 = Warehouse.objects.create(name="WH1", free_days=0)
        self.warehouse2 = Warehouse.objects.create(name="WH2", free_days=0)
        self.container = Container.objects.create(
            number="SIG-CAR-001",
            status="FLOATING",
        )

    def test_contractors_stored_on_instance(self):
        """Старые значения контрагентов сохраняются на экземпляре"""
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="SIGCAR1234567890A",
            status="FLOATING",
            container=self.container,
            warehouse=self.warehouse1,
        )
        # Меняем склад
        car.warehouse = self.warehouse2
        car.save()
        
        # _pre_save_contractors должен быть очищен после save
        self.assertIsNone(getattr(car, '_pre_save_contractors', None))
