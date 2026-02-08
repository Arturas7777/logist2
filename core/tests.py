from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model
from datetime import timedelta

from .models import (
    Client, Warehouse, Container, Car, Line,
    LineTHSCoefficient, LineService, WarehouseService, CarService, Company,
)
from .models_billing import NewInvoice, InvoiceItem


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


class THSCalculationTests(TestCase):
    """Тесты расчёта THS (пропорциональное распределение по коэффициентам)"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="THS Test Client")
        self.line = Line.objects.create(name="Test Line")
        self.warehouse = Warehouse.objects.create(name="WH-THS", free_days=0)
        self.container = Container.objects.create(
            number="CONT-THS", line=self.line, warehouse=self.warehouse,
            ths=Decimal('500.00'), ths_payer='LINE',
        )
        # Коэффициенты: легковой=1.0, джип=2.0, мото=0.5
        LineTHSCoefficient.objects.create(line=self.line, vehicle_type='SEDAN', coefficient=Decimal('1.00'))
        LineTHSCoefficient.objects.create(line=self.line, vehicle_type='SUV', coefficient=Decimal('2.00'))
        LineTHSCoefficient.objects.create(line=self.line, vehicle_type='MOTO', coefficient=Decimal('0.50'))

    def test_proportional_distribution(self):
        """THS распределяется пропорционально коэффициентам типов ТС"""
        from core.signals import calculate_ths_for_container
        car1 = Car.objects.create(
            year=2020, brand='Sedan', vin='VIN-THS-1',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SEDAN',
        )
        car2 = Car.objects.create(
            year=2020, brand='SUV', vin='VIN-THS-2',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SUV',
        )
        car3 = Car.objects.create(
            year=2020, brand='Moto', vin='VIN-THS-3',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='MOTO',
        )
        result = calculate_ths_for_container(self.container)
        # Сумма коэфф = 1.0 + 2.0 + 0.5 = 3.5
        # Sedan: 500 * 1.0/3.5 = 142.86 → round_up_to_5 → 145
        # SUV: 500 * 2.0/3.5 = 285.71 → round_up_to_5 → 290
        # Moto: 500 * 0.5/3.5 = 71.43 → round_up_to_5 → 75
        self.assertEqual(result[car1.id], Decimal('145'))
        self.assertEqual(result[car2.id], Decimal('290'))
        self.assertEqual(result[car3.id], Decimal('75'))

    def test_all_results_rounded_to_5(self):
        """Все суммы THS округлены вверх до кратного 5"""
        from core.signals import calculate_ths_for_container
        Car.objects.create(
            year=2020, brand='S1', vin='VIN-THS-R1',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SEDAN',
        )
        Car.objects.create(
            year=2020, brand='S2', vin='VIN-THS-R2',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SUV',
        )
        result = calculate_ths_for_container(self.container)
        for ths_amount in result.values():
            self.assertEqual(ths_amount % 5, 0, f"THS {ths_amount} не кратно 5")

    def test_no_container_returns_empty(self):
        from core.signals import calculate_ths_for_container
        self.assertEqual(calculate_ths_for_container(None), {})

    def test_no_line_returns_empty(self):
        from core.signals import calculate_ths_for_container
        container = Container.objects.create(number="CONT-NOLINE", ths=Decimal('100'))
        self.assertEqual(calculate_ths_for_container(container), {})

    def test_zero_ths_returns_empty(self):
        from core.signals import calculate_ths_for_container
        self.container.ths = Decimal('0')
        self.container.save()
        self.assertEqual(calculate_ths_for_container(self.container), {})

    def test_no_cars_returns_empty(self):
        from core.signals import calculate_ths_for_container
        self.assertEqual(calculate_ths_for_container(self.container), {})

    def test_default_coefficient_when_not_specified(self):
        """Если для типа ТС нет коэффициента, используется 1.0"""
        from core.signals import calculate_ths_for_container
        # PICKUP не имеет коэффициента → по умолчанию 1.0
        car1 = Car.objects.create(
            year=2020, brand='Pickup', vin='VIN-THS-DEF1',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='PICKUP',
        )
        car2 = Car.objects.create(
            year=2020, brand='Sedan', vin='VIN-THS-DEF2',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SEDAN',
        )
        result = calculate_ths_for_container(self.container)
        # PICKUP=1.0(default), SEDAN=1.0 → сумма 2.0
        # Каждый получает 500 * 1.0/2.0 = 250 → 250 (уже кратно 5)
        self.assertEqual(result[car1.id], Decimal('250'))
        self.assertEqual(result[car2.id], Decimal('250'))

    def test_single_car_gets_full_ths(self):
        """Одна машина получает весь THS (округлённый)"""
        from core.signals import calculate_ths_for_container
        car = Car.objects.create(
            year=2020, brand='Solo', vin='VIN-THS-SOLO',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SEDAN',
        )
        result = calculate_ths_for_container(self.container)
        # 500 * 1.0/1.0 = 500 → 500
        self.assertEqual(result[car.id], Decimal('500'))


class StorageCostFullTests(TestCase):
    """Полные тесты расчёта стоимости хранения (дни × ставка)"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Storage Full Client")
        self.warehouse = Warehouse.objects.create(name="WH-StorFull", free_days=3)
        self.container = Container.objects.create(number="CONT-STORF")
        # Создаём услугу "Хранение" со ставкой 5 EUR/день
        self.storage_service = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Хранение',
            default_price=Decimal('5.00'), is_active=True,
        )

    def test_storage_cost_with_chargeable_days(self):
        """Стоимость = платные дни × ставка из услуги 'Хранение'"""
        # 10 дней назад разгружен, free_days=3 → 10-3=7 платных дней
        unload = timezone.now().date() - timedelta(days=9)  # total_days = 9+1(incl) = 10
        car = Car.objects.create(
            year=2020, brand='StorBrand', vin='VIN-STORF-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        cost = car.calculate_storage_cost()
        # total_days = (today - unload).days + 1 = 10
        # chargeable = max(0, 10 - 3) = 7
        # cost = 5 * 7 = 35
        self.assertEqual(cost, Decimal('35'))

    def test_within_free_days_returns_zero(self):
        """В пределах бесплатных дней стоимость = 0"""
        # 2 дня назад, free_days=3 → 0 платных дней
        unload = timezone.now().date() - timedelta(days=1)  # total_days = 2
        car = Car.objects.create(
            year=2020, brand='StorBrand', vin='VIN-STORF-2',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        cost = car.calculate_storage_cost()
        self.assertEqual(cost, Decimal('0'))

    def test_transferred_uses_transfer_date(self):
        """Для переданного авто используется transfer_date, а не сегодня"""
        unload = timezone.now().date() - timedelta(days=20)
        transfer = unload + timedelta(days=10)  # 10 дней хранения
        car = Car.objects.create(
            year=2020, brand='StorBrand', vin='VIN-STORF-3',
            client=self.client_obj, status='TRANSFERRED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload, transfer_date=transfer,
        )
        cost = car.calculate_storage_cost()
        # total_days = (transfer - unload).days + 1 = 11
        # chargeable = max(0, 11 - 3) = 8
        # cost = 5 * 8 = 40
        self.assertEqual(cost, Decimal('40'))

    def test_no_storage_service_returns_zero(self):
        """Если нет услуги 'Хранение', стоимость = 0"""
        self.storage_service.delete()
        unload = timezone.now().date() - timedelta(days=9)
        car = Car.objects.create(
            year=2020, brand='StorBrand', vin='VIN-STORF-4',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        cost = car.calculate_storage_cost()
        self.assertEqual(cost, Decimal('0'))

    def test_update_days_and_storage(self):
        """update_days_and_storage корректно обновляет days и storage_cost"""
        unload = timezone.now().date() - timedelta(days=9)
        car = Car.objects.create(
            year=2020, brand='StorBrand', vin='VIN-STORF-5',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        car.update_days_and_storage()
        self.assertEqual(car.days, 7)  # 10 total - 3 free
        self.assertEqual(car.storage_cost, Decimal('35'))  # 7 * 5


class RegenerateItemsTests(TestCase):
    """Тесты генерации позиций инвойса из услуг автомобилей"""

    def setUp(self):
        CarService._service_obj_cache.clear()
        self.client_obj = Client.objects.create(name="Regen Test Client")
        self.warehouse = Warehouse.objects.create(name="WH-Regen", free_days=0)
        self.line = Line.objects.create(name="Regen Line")
        self.container = Container.objects.create(
            number="CONT-REGEN", line=self.line, warehouse=self.warehouse,
        )
        self.car = Car.objects.create(
            year=2020, brand='RegenBrand', vin='VIN-REGEN-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            line=self.line,
        )
        # Услуга склада: Разгрузка (short_name='Разг')
        self.wh_svc = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Разгрузка', short_name='Разг',
            default_price=Decimal('50.00'), is_active=True,
        )

    def test_warehouse_issuer_creates_items(self):
        """Инвойс от склада создаёт позиции из warehouse-услуг"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        # Должна быть 1 позиция: Разг
        descriptions = [i.description for i in items]
        self.assertIn('Разг', descriptions)

    def test_company_issuer_includes_markup(self):
        """Инвойс от компании включает markup_amount в цену"""
        company = Company.objects.create(name="Test Company")
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'),
            markup_amount=Decimal('10.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_company=company,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        # Для компании: price = custom_price + markup = 60 + 10 = 70
        regen_item = [i for i in items if i.description == 'Разг']
        self.assertEqual(len(regen_item), 1)
        self.assertEqual(regen_item[0].unit_price, Decimal('70.00'))

    def test_warehouse_issuer_excludes_markup(self):
        """Инвойс от склада НЕ включает markup в цену"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'),
            markup_amount=Decimal('10.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        regen_item = [i for i in items if i.description == 'Разг']
        self.assertEqual(len(regen_item), 1)
        # Для склада: price = custom_price = 60 (без markup)
        self.assertEqual(regen_item[0].unit_price, Decimal('60.00'))

    def test_grouping_by_short_name(self):
        """Услуги с одинаковым short_name группируются (суммируются)"""
        # Вторая услуга с тем же short_name
        wh_svc2 = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Разгрузка спец', short_name='Разг',
            default_price=Decimal('30.00'), is_active=True,
        )
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'),
        )
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=wh_svc2.id, quantity=1,
            custom_price=Decimal('40.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        regen_items = [i for i in items if i.description == 'Разг']
        self.assertEqual(len(regen_items), 1)
        # Группировка: 60 + 40 = 100
        self.assertEqual(regen_items[0].unit_price, Decimal('100.00'))

    def test_no_issuer_creates_no_items(self):
        """Инвойс без выставителя не создаёт позиций"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
        )
        invoice = NewInvoice.objects.create(
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 0)

    def test_regenerate_deletes_old_items(self):
        """Повторный вызов regenerate удаляет старые позиции"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(self.car)
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 1)
        # Повторный вызов — старые удаляются, новые создаются
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 1)

    def tearDown(self):
        CarService._service_obj_cache.clear()
