"""Tests for car lifecycle service and signal consolidation."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import (
    Car,
    Client,
    Company,
    Container,
    Line,
    Warehouse,
)
from core.services.car_lifecycle_service import (
    after_car_save,
    check_container_status,
    recalculate_car_price,
)


class RecalculateCarPriceTest(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='Test WH')
        self.container = Container.objects.create(number='CONT-LC-001', status='FLOATING')
        self.car = Car.objects.create(
            year=2023, brand='Toyota', vin='LIFECYCLE00000001',
            status='FLOATING', container=self.container,
        )

    def test_recalculate_updates_db(self):
        recalculate_car_price(self.car)
        self.car.refresh_from_db()
        self.assertIsNotNone(self.car.total_price)

    @patch('core.services.car_lifecycle_service.logger')
    def test_recalculate_handles_error(self, mock_logger):
        self.car.pk = 999999
        recalculate_car_price(self.car)
        # Should log error, not raise


class CheckContainerStatusTest(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='WH-CS')
        self.container = Container.objects.create(
            number='CONT-CS-001', status='UNLOADED',
            warehouse=self.warehouse, unload_date=timezone.now().date(),
        )

    def test_all_transferred_updates_container(self):
        car1 = Car.objects.create(
            year=2023, brand='BMW', vin='CONTSTAT000000001',
            status='TRANSFERRED', container=self.container,
            transfer_date=timezone.now().date(),
        )
        Car.objects.create(
            year=2023, brand='Audi', vin='CONTSTAT000000002',
            status='TRANSFERRED', container=self.container,
            transfer_date=timezone.now().date(),
        )
        check_container_status(car1)
        self.container.refresh_from_db()
        self.assertEqual(self.container.status, 'TRANSFERRED')

    def test_not_all_transferred_keeps_status(self):
        car1 = Car.objects.create(
            year=2023, brand='BMW', vin='CONTSTAT000000003',
            status='TRANSFERRED', container=self.container,
            transfer_date=timezone.now().date(),
        )
        Car.objects.create(
            year=2023, brand='Audi', vin='CONTSTAT000000004',
            status='UNLOADED', container=self.container,
        )
        check_container_status(car1)
        self.container.refresh_from_db()
        self.assertEqual(self.container.status, 'UNLOADED')


class AfterCarSaveTest(TestCase):
    def setUp(self):
        self.car = Car.objects.create(
            year=2023, brand='Honda', vin='AFTERSVTEST000001',
            status='FLOATING',
        )

    @patch('core.services.car_lifecycle_service.send_car_ws_notification')
    @patch('core.services.car_lifecycle_service.recalculate_car_price')
    def test_calls_recalculate_and_notify(self, mock_recalc, mock_ws):
        after_car_save(self.car, is_new=False)
        mock_recalc.assert_called_once_with(self.car)
        mock_ws.assert_called_once_with(self.car)


class FieldRenameTest(TestCase):
    """Verify that renamed fields work correctly."""

    def test_container_renamed_fields(self):
        c = Container.objects.create(
            number='RENAME-001', status='FLOATING',
            warehouse_fee=Decimal('200.00'),
            declaration_fee=Decimal('50.00'),
            markup=Decimal('30.00'),
        )
        c.refresh_from_db()
        self.assertEqual(c.warehouse_fee, Decimal('200.00'))
        self.assertEqual(c.declaration_fee, Decimal('50.00'))
        self.assertEqual(c.markup, Decimal('30.00'))

    def test_car_renamed_fields(self):
        car = Car.objects.create(
            year=2023, brand='Ford', vin='RENAME0000000001',
            status='FLOATING',
            declaration_fee=Decimal('100.00'),
            markup=Decimal('25.00'),
        )
        car.refresh_from_db()
        self.assertEqual(car.declaration_fee, Decimal('100.00'))
        self.assertEqual(car.markup, Decimal('25.00'))


class BalanceMixinTest(TestCase):
    """Verify that BalanceMethodsMixin works on all entity types."""

    def test_line_has_balance_methods(self):
        line = Line.objects.create(name='Test Line')
        info = line.get_balance_info()
        self.assertIn('status', info)
        self.assertIn('balance', info)
        self.assertEqual(info['status'], 'БАЛАНС')

    def test_client_balance_info(self):
        client = Client.objects.create(name='Test Client', balance=Decimal('-100.00'))
        info = client.get_balance_info()
        self.assertEqual(info['status'], 'ДОЛГ')
        self.assertEqual(info['color'], '#dc3545')

    def test_company_balance_breakdown(self):
        company = Company.objects.create(name='Caromoto Lithuania')
        breakdown = company.get_balance_breakdown()
        self.assertIn('cash', breakdown)
        self.assertIn('total', breakdown)


class NormalizationModelsTest(TestCase):
    """Test WarehouseSite and ClientEmail models."""

    def test_warehouse_site_creation(self):
        from core.models import WarehouseSite
        wh = Warehouse.objects.create(name='WH-Norm')
        site = WarehouseSite.objects.create(warehouse=wh, number=1, name='Main', address='123 Street')
        self.assertEqual(str(site), 'WH-Norm — Main')

    def test_warehouse_site_unique(self):
        from django.db import IntegrityError

        from core.models import WarehouseSite
        wh = Warehouse.objects.create(name='WH-Uniq')
        WarehouseSite.objects.create(warehouse=wh, number=1)
        with self.assertRaises(IntegrityError):
            WarehouseSite.objects.create(warehouse=wh, number=1)

    def test_client_email_creation(self):
        from core.models import ClientEmail
        client = Client.objects.create(name='Email Client')
        ce = ClientEmail.objects.create(client=client, email='test@example.com', is_primary=True)
        self.assertEqual(str(ce), 'Email Client — test@example.com')

    def test_client_email_unique(self):
        from django.db import IntegrityError

        from core.models import ClientEmail
        client = Client.objects.create(name='Uniq Client')
        ClientEmail.objects.create(client=client, email='dup@example.com')
        with self.assertRaises(IntegrityError):
            ClientEmail.objects.create(client=client, email='dup@example.com')
