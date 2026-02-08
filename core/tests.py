from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model

from .models import Client, Warehouse, Container, Car


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
