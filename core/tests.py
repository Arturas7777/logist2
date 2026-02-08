from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model

from .models import Client, Warehouse, Container, Car
from .models_billing import NewInvoice as Invoice, Transaction as Payment


class BillingTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Test Client")
        self.warehouse = Warehouse.objects.create(name="WH1", rate=0, free_days=0)
        self.container = Container.objects.create(number="CONT-001")

    def _make_car(self, price: Decimal = Decimal('100.00'), status: str = 'UNLOADED', transferred: bool = False):
        car = Car.objects.create(
            year=2020,
            brand='Brand',
            vin=f"VIN{timezone.now().timestamp()}".replace('.', ''),
            client=self.client_obj,
            status=status,
            warehouse=self.warehouse,
            container=self.container,
            price=price,
            rate=Decimal('0.00'),
            free_days=0,
            proft=Decimal('0.00'),
        )
        if transferred:
            car.status = 'TRANSFERRED'
            car.transfer_date = timezone.now().date()
            car.save()
        return car

    def test_payment_flow_and_overpayment(self):
        car = self._make_car(price=Decimal('100.00'))
        inv = Invoice.objects.create(number="INV-001", client=self.client_obj)
        inv.cars.set([car])
        inv.save()

        self.client_obj.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, Decimal('100.00'))
        self.assertEqual(self.client_obj.debt, Decimal('100.00'))

        p1 = Payment.objects.create(
            invoice=inv,
            amount=Decimal('100.00'),
            payment_type='CASH',
            payer=self.client_obj,
            recipient='WH',
        )
        inv.refresh_from_db()
        self.client_obj.refresh_from_db()
        self.assertTrue(inv.paid)
        self.assertEqual(self.client_obj.debt, Decimal('0.00'))

        p2 = Payment.objects.create(
            invoice=inv,
            amount=Decimal('10.00'),
            payment_type='CASH',
            payer=self.client_obj,
            recipient='WH',
        )
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.cash_balance, Decimal('10.00'))

    def test_payment_from_balance_validation(self):
        self.client_obj.cash_balance = Decimal('5.00')
        self.client_obj.save()
        with self.assertRaises(ValueError):
            Payment.objects.create(
                amount=Decimal('10.00'),
                payment_type='BALANCE',
                from_balance=True,
                from_cash_balance=True,
                payer=self.client_obj,
                recipient='Any',
            )

    def test_edit_payment_reverts_old_effects(self):
        car = self._make_car(price=Decimal('100.00'))
        inv = Invoice.objects.create(number="INV-002", client=self.client_obj)
        inv.cars.set([car])
        inv.save()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('100.00'))

        p = Payment.objects.create(invoice=inv, amount=Decimal('40.00'), payment_type='CASH', payer=self.client_obj, recipient='WH')
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('60.00'))

        p.amount = Decimal('70.00')
        p.save()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('30.00'))


class InvoiceCalculationTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Client A")
        self.warehouse = Warehouse.objects.create(name="WH1", rate=0, free_days=0)
        self.container = Container.objects.create(number="CONT-100")

    def test_update_total_amount_uses_current_and_total_correctly(self):
        car1 = Car.objects.create(
            year=2020, brand='B1', vin='VIN-A', client=self.client_obj,
            status='UNLOADED', warehouse=self.warehouse, container=self.container,
            price=Decimal('50.00'), rate=Decimal('0.00'), free_days=0, proft=Decimal('0.00')
        )
        car2 = Car.objects.create(
            year=2020, brand='B2', vin='VIN-B', client=self.client_obj,
            status='TRANSFERRED', warehouse=self.warehouse, container=self.container,
            price=Decimal('30.00'), rate=Decimal('0.00'), free_days=0, transfer_date=timezone.now().date(), proft=Decimal('0.00')
        )

        inv = Invoice.objects.create(number="INV-003", client=self.client_obj)
        inv.cars.set([car1, car2])
        inv.save()
        inv.refresh_from_db()

        self.assertEqual(inv.total_amount, Decimal('80.00'))


class APIPermissionsTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def test_api_requires_staff(self):
        resp_anon = self.client.get('/api/v1/cars/')
        self.assertIn(resp_anon.status_code, (401, 403))

        user = self.User.objects.create_user(username='admin', password='pass', is_staff=True)
        self.client.login(username='admin', password='pass')
        resp_auth = self.client.get('/api/v1/cars/')
        self.assertEqual(resp_auth.status_code, 200)


class RoundUpTo5Tests(SimpleTestCase):
    """Тесты для утилиты round_up_to_5 (без БД)"""

    def test_exact_multiple_of_5(self):
        from core.utils import round_up_to_5
        self.assertEqual(round_up_to_5(Decimal('70')), Decimal('70'))
        self.assertEqual(round_up_to_5(Decimal('0')), Decimal('0'))
        self.assertEqual(round_up_to_5(Decimal('5')), Decimal('5'))
        self.assertEqual(round_up_to_5(Decimal('100')), Decimal('100'))

    def test_rounds_up(self):
        from core.utils import round_up_to_5
        self.assertEqual(round_up_to_5(Decimal('73.12')), Decimal('75'))
        self.assertEqual(round_up_to_5(Decimal('71')), Decimal('75'))
        self.assertEqual(round_up_to_5(Decimal('1')), Decimal('5'))
        self.assertEqual(round_up_to_5(Decimal('76')), Decimal('80'))

    def test_decimal_precision_preserved(self):
        """round_up_to_5 не должен терять точность через float"""
        from core.utils import round_up_to_5
        # Большое число, которое может потерять точность при float конвертации
        big = Decimal('99999999999999.99')
        result = round_up_to_5(big)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result % 5, 0)
        self.assertGreaterEqual(result, big)


class StorageCostCalculationTests(TestCase):
    """Тесты расчёта стоимости хранения"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Storage Test Client")
        self.warehouse = Warehouse.objects.create(name="WH-Storage", free_days=3)
        self.container = Container.objects.create(number="CONT-STOR")

    def test_no_warehouse_returns_zero(self):
        car = Car.objects.create(
            year=2020, brand='TestBrand', vin='VIN-STOR-1',
            client=self.client_obj, status='UNLOADED',
            container=self.container,
            unload_date=timezone.now().date(),
        )
        self.assertEqual(car.calculate_storage_cost(), Decimal('0.00'))

    def test_no_unload_date_returns_zero(self):
        car = Car.objects.create(
            year=2020, brand='TestBrand', vin='VIN-STOR-2',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse,
            container=self.container,
        )
        self.assertEqual(car.calculate_storage_cost(), Decimal('0.00'))


class ServiceCacheTests(TestCase):
    """Тесты кэширования объектов услуг в CarService"""

    def setUp(self):
        from core.models import CarService, Warehouse, WarehouseService
        # Очищаем кэш перед каждым тестом
        CarService._service_obj_cache.clear()

        self.client_obj = Client.objects.create(name="Cache Test Client")
        self.warehouse = Warehouse.objects.create(name="WH-Cache", free_days=0)
        self.container = Container.objects.create(number="CONT-CACHE")
        self.wh_service = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Разгрузка',
            default_price=Decimal('50.00'), is_active=True,
        )
        self.car = Car.objects.create(
            year=2020, brand='CacheBrand', vin='VIN-CACHE-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
        )

    def test_get_service_name_returns_correct_name(self):
        from core.models import CarService
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_service.id, quantity=1,
        )
        self.assertEqual(cs.get_service_name(), 'Разгрузка')

    def test_cache_avoids_repeated_queries(self):
        from core.models import CarService
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_service.id, quantity=1,
        )
        # Первый вызов — кэширует
        cs.get_service_name()
        cache_key = ('WAREHOUSE', self.wh_service.id)
        self.assertIn(cache_key, CarService._service_obj_cache)

        # Второй вызов — берёт из кэша (не делает запрос)
        from django.test.utils import override_settings
        from django.db import connection, reset_queries
        reset_queries()
        name = cs.get_service_name()
        # Не должно быть запросов к WarehouseService
        service_queries = [q for q in connection.queries if 'warehouse' in q['sql'].lower() and 'service' in q['sql'].lower()]
        self.assertEqual(len(service_queries), 0)
        self.assertEqual(name, 'Разгрузка')

    def test_nonexistent_service_returns_not_found(self):
        from core.models import CarService
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=99999, quantity=1,
        )
        self.assertEqual(cs.get_service_name(), 'Услуга не найдена')

    def tearDown(self):
        from core.models import CarService
        CarService._service_obj_cache.clear()
