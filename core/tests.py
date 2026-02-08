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


class CalculateTotalPriceTests(TestCase):
    """Тесты Car.calculate_total_price() — итоговая цена авто"""

    def setUp(self):
        CarService._service_obj_cache.clear()
        self.client_obj = Client.objects.create(name="TotalPrice Client")
        self.line = Line.objects.create(name="TP Line")
        self.warehouse = Warehouse.objects.create(name="WH-TP", free_days=0)
        self.container = Container.objects.create(
            number="CONT-TP", line=self.line, warehouse=self.warehouse,
        )
        self.car = Car.objects.create(
            year=2020, brand='TPBrand', vin='VIN-TP-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            line=self.line,
        )
        self.wh_svc = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Разгрузка', short_name='Разг',
            default_price=Decimal('50.00'), is_active=True,
        )
        self.line_svc = LineService.objects.create(
            line=self.line, name='Фрахт', short_name='Фрахт',
            default_price=Decimal('200.00'), is_active=True,
        )

    def test_sum_of_services(self):
        """total_price = сумма всех услуг + сумма всех markup"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('60.00'), markup_amount=Decimal('5.00'),
        )
        CarService.objects.create(
            car=self.car, service_type='LINE',
            service_id=self.line_svc.id, quantity=1,
            custom_price=Decimal('200.00'), markup_amount=Decimal('10.00'),
        )
        result = self.car.calculate_total_price()
        # final_price: 60 + 200 = 260 (услуги без markup)
        # markup: 5 + 10 = 15
        # total = 260 + 15 = 275
        self.assertEqual(result, Decimal('275.00'))

    def test_no_services_returns_zero(self):
        """Без услуг total_price = 0"""
        result = self.car.calculate_total_price()
        self.assertEqual(result, Decimal('0'))

    def test_uses_default_price_when_custom_is_none(self):
        """Если custom_price=None, используется default_price"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=None,  # → default_price = 50
        )
        result = self.car.calculate_total_price()
        self.assertEqual(result, Decimal('50.00'))

    def test_zero_custom_price_is_not_replaced_by_default(self):
        """custom_price=0 НЕ заменяется на default_price (0 — допустимое значение)"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('0.00'),
        )
        result = self.car.calculate_total_price()
        self.assertEqual(result, Decimal('0'))

    def test_quantity_multiplies_price(self):
        """quantity умножает цену"""
        CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=3,
            custom_price=Decimal('10.00'),
        )
        result = self.car.calculate_total_price()
        # final_price = 10 * 3 = 30
        self.assertEqual(result, Decimal('30.00'))

    def tearDown(self):
        CarService._service_obj_cache.clear()


class CarServicePriceTests(TestCase):
    """Тесты свойств final_price и invoice_price в CarService"""

    def setUp(self):
        CarService._service_obj_cache.clear()
        self.client_obj = Client.objects.create(name="Price Client")
        self.warehouse = Warehouse.objects.create(name="WH-Price", free_days=0)
        self.container = Container.objects.create(number="CONT-PRICE")
        self.car = Car.objects.create(
            year=2020, brand='PriceBrand', vin='VIN-PRICE-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
        )
        self.wh_svc = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Доставка', short_name='Дост',
            default_price=Decimal('100.00'), is_active=True,
        )

    def test_final_price_without_markup(self):
        """final_price = custom_price × quantity (БЕЗ markup)"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=2,
            custom_price=Decimal('80.00'), markup_amount=Decimal('15.00'),
        )
        self.assertEqual(cs.final_price, Decimal('160.00'))  # 80 * 2

    def test_invoice_price_with_markup(self):
        """invoice_price = (custom_price + markup) × quantity"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=2,
            custom_price=Decimal('80.00'), markup_amount=Decimal('15.00'),
        )
        self.assertEqual(cs.invoice_price, Decimal('190.00'))  # (80+15) * 2

    def test_invoice_price_zero_markup_not_treated_as_none(self):
        """markup_amount=0 — не заменяется на None, invoice_price = final_price"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=Decimal('50.00'), markup_amount=Decimal('0.00'),
        )
        self.assertEqual(cs.invoice_price, Decimal('50.00'))
        self.assertEqual(cs.final_price, Decimal('50.00'))

    def test_none_custom_price_uses_default(self):
        """custom_price=None → используется default_price"""
        cs = CarService.objects.create(
            car=self.car, service_type='WAREHOUSE',
            service_id=self.wh_svc.id, quantity=1,
            custom_price=None,
        )
        # default_price = 100
        self.assertEqual(cs.final_price, Decimal('100.00'))

    def tearDown(self):
        CarService._service_obj_cache.clear()


class CreateTHSServicesTests(TestCase):
    """Тесты create_ths_services_for_container() — создание CarService записей THS"""

    def setUp(self):
        CarService._service_obj_cache.clear()
        self.client_obj = Client.objects.create(name="CTS Client")
        self.line = Line.objects.create(name="CTS Line")
        self.warehouse = Warehouse.objects.create(name="WH-CTS", free_days=0)
        self.container = Container.objects.create(
            number="CONT-CTS", line=self.line, warehouse=self.warehouse,
            ths=Decimal('300.00'), ths_payer='LINE',
        )
        LineTHSCoefficient.objects.create(
            line=self.line, vehicle_type='SEDAN', coefficient=Decimal('1.00'),
        )
        self.car = Car.objects.create(
            year=2020, brand='CTSBrand', vin='VIN-CTS-1',
            client=self.client_obj, status='UNLOADED',
            container=self.container, vehicle_type='SEDAN',
            line=self.line,
        )

    def test_creates_car_services_for_line_payer(self):
        """При ths_payer=LINE создаются CarService с service_type=LINE"""
        from core.signals import create_ths_services_for_container
        count = create_ths_services_for_container(self.container)
        self.assertEqual(count, 1)
        cs = CarService.objects.filter(car=self.car, service_type='LINE')
        self.assertEqual(cs.count(), 1)
        self.assertEqual(cs.first().custom_price, Decimal('300'))

    def test_creates_car_services_for_warehouse_payer(self):
        """При ths_payer=WAREHOUSE создаются CarService с service_type=WAREHOUSE"""
        from core.signals import create_ths_services_for_container
        self.container.ths_payer = 'WAREHOUSE'
        self.container.save()
        count = create_ths_services_for_container(self.container)
        self.assertEqual(count, 1)
        cs = CarService.objects.filter(car=self.car, service_type='WAREHOUSE')
        self.assertEqual(cs.count(), 1)

    def test_deletes_old_ths_before_creating_new(self):
        """Повторный вызов удаляет старые THS и создаёт новые"""
        from core.signals import create_ths_services_for_container
        create_ths_services_for_container(self.container)
        # Меняем THS и пересоздаём
        self.container.ths = Decimal('500.00')
        self.container.save()
        create_ths_services_for_container(self.container)
        ths_services = CarService.objects.filter(
            car=self.car, service_type='LINE',
        ).filter(
            service_id__in=LineService.objects.filter(
                name__icontains='THS'
            ).values_list('id', flat=True)
        )
        self.assertEqual(ths_services.count(), 1)
        self.assertEqual(ths_services.first().custom_price, Decimal('500'))

    def test_no_container_returns_zero(self):
        from core.signals import create_ths_services_for_container
        self.assertEqual(create_ths_services_for_container(None), 0)

    def test_no_ths_returns_zero(self):
        from core.signals import create_ths_services_for_container
        self.container.ths = None
        self.container.save()
        self.assertEqual(create_ths_services_for_container(self.container), 0)

    def tearDown(self):
        CarService._service_obj_cache.clear()


class InvoiceCalculateTotalsTests(TestCase):
    """Тесты NewInvoice.calculate_totals() — пересчёт сумм инвойса"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Totals Client")
        self.warehouse = Warehouse.objects.create(name="WH-Totals", free_days=0)

    def test_totals_from_items(self):
        """subtotal = сумма total_price всех позиций, total = subtotal - discount + tax"""
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        InvoiceItem.objects.create(
            invoice=invoice, description='Услуга 1',
            quantity=1, unit_price=Decimal('100.00'),
        )
        InvoiceItem.objects.create(
            invoice=invoice, description='Услуга 2',
            quantity=2, unit_price=Decimal('50.00'),
        )
        invoice.calculate_totals()
        # subtotal = 100 + 100(2*50) = 200
        self.assertEqual(invoice.subtotal, Decimal('200.00'))
        self.assertEqual(invoice.total, Decimal('200.00'))

    def test_totals_with_discount(self):
        """total учитывает скидку"""
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
            discount=Decimal('30.00'),
        )
        InvoiceItem.objects.create(
            invoice=invoice, description='Услуга',
            quantity=1, unit_price=Decimal('200.00'),
        )
        invoice.calculate_totals()
        self.assertEqual(invoice.subtotal, Decimal('200.00'))
        self.assertEqual(invoice.total, Decimal('170.00'))  # 200 - 30

    def test_empty_invoice_totals_zero(self):
        """Инвойс без позиций → subtotal=0, total=0"""
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.calculate_totals()
        self.assertEqual(invoice.subtotal, Decimal('0'))
        self.assertEqual(invoice.total, Decimal('0'))


class InvoiceStatusTests(TestCase):
    """Тесты NewInvoice.update_status() — автоматические переходы статуса"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Status Client")
        self.warehouse = Warehouse.objects.create(name="WH-Status", free_days=0)

    def _make_invoice(self, **kwargs):
        defaults = dict(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        defaults.update(kwargs)
        return NewInvoice.objects.create(**defaults)

    def test_paid_when_full_payment(self):
        """Статус PAID при paid_amount >= total"""
        inv = self._make_invoice(total=Decimal('100'), paid_amount=Decimal('100'))
        inv.update_status()
        self.assertEqual(inv.status, 'PAID')

    def test_paid_when_overpaid(self):
        """Статус PAID при переплате"""
        inv = self._make_invoice(total=Decimal('100'), paid_amount=Decimal('150'))
        inv.update_status()
        self.assertEqual(inv.status, 'PAID')

    def test_partially_paid(self):
        """Статус PARTIALLY_PAID при частичной оплате"""
        inv = self._make_invoice(total=Decimal('100'), paid_amount=Decimal('30'))
        inv.update_status()
        self.assertEqual(inv.status, 'PARTIALLY_PAID')

    def test_overdue_when_past_due_date(self):
        """Статус OVERDUE если due_date в прошлом и не оплачен"""
        inv = self._make_invoice(
            total=Decimal('100'), paid_amount=Decimal('0'),
            due_date=timezone.now().date() - timedelta(days=5),
            status='ISSUED',
        )
        inv.update_status()
        self.assertEqual(inv.status, 'OVERDUE')

    def test_draft_stays_draft(self):
        """DRAFT остаётся DRAFT (не меняется автоматически)"""
        inv = self._make_invoice(
            total=Decimal('0'), paid_amount=Decimal('0'),
            status='DRAFT',
        )
        inv.update_status()
        self.assertEqual(inv.status, 'DRAFT')


class RegenerateStorageItemTests(TestCase):
    """Тесты генерации позиции 'Хран' в инвойсе"""

    def setUp(self):
        CarService._service_obj_cache.clear()
        self.client_obj = Client.objects.create(name="StorItem Client")
        self.warehouse = Warehouse.objects.create(name="WH-StorItem", free_days=0)
        self.container = Container.objects.create(
            number="CONT-STORITEM", warehouse=self.warehouse,
        )
        # Услуга "Хранение" = 5 EUR/день
        self.storage_svc = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Хранение',
            default_price=Decimal('5.00'), is_active=True,
        )
        # Услуга "Разгрузка" = 50 EUR
        self.unload_svc = WarehouseService.objects.create(
            warehouse=self.warehouse, name='Разгрузка', short_name='Разг',
            default_price=Decimal('50.00'), is_active=True,
        )

    def test_warehouse_invoice_includes_storage_group(self):
        """Инвойс от склада включает позицию 'Хран' если есть хранение"""
        unload = timezone.now().date() - timedelta(days=4)  # 5 total - 0 free = 5 дней
        car = Car.objects.create(
            year=2020, brand='StorItemBrand', vin='VIN-STORITM-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        # Добавляем услугу разгрузки
        CarService.objects.create(
            car=car, service_type='WAREHOUSE',
            service_id=self.unload_svc.id, quantity=1,
            custom_price=Decimal('50.00'),
        )
        # Добавляем услугу хранения (storage_cost пересчитается в regenerate)
        CarService.objects.create(
            car=car, service_type='WAREHOUSE',
            service_id=self.storage_svc.id, quantity=1,
            custom_price=Decimal('25.00'),  # будет пересчитана
        )
        invoice = NewInvoice.objects.create(
            issuer_warehouse=self.warehouse,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        descriptions = [i.description for i in items]
        # Должны быть: Разг + Хран
        self.assertIn('Разг', descriptions)
        self.assertIn('Хран', descriptions)

    def test_company_invoice_includes_storage_group(self):
        """Инвойс от компании тоже включает 'Хран'"""
        company = Company.objects.create(name="StorItem Company")
        unload = timezone.now().date() - timedelta(days=4)
        car = Car.objects.create(
            year=2020, brand='StorItemBrand', vin='VIN-STORITM-2',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload,
        )
        CarService.objects.create(
            car=car, service_type='WAREHOUSE',
            service_id=self.unload_svc.id, quantity=1,
            custom_price=Decimal('50.00'),
        )
        CarService.objects.create(
            car=car, service_type='WAREHOUSE',
            service_id=self.storage_svc.id, quantity=1,
            custom_price=Decimal('25.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_company=company,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        descriptions = [i.description for i in items]
        self.assertIn('Хран', descriptions)

    def test_line_invoice_excludes_storage(self):
        """Инвойс от линии НЕ включает 'Хран' (только Warehouse/Company)"""
        line = Line.objects.create(name="StorItem Line")
        line_svc = LineService.objects.create(
            line=line, name='Фрахт', short_name='Фрахт',
            default_price=Decimal('200.00'), is_active=True,
        )
        unload = timezone.now().date() - timedelta(days=4)
        car = Car.objects.create(
            year=2020, brand='StorItemBrand', vin='VIN-STORITM-3',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_date=unload, line=line,
        )
        CarService.objects.create(
            car=car, service_type='LINE',
            service_id=line_svc.id, quantity=1,
            custom_price=Decimal('200.00'),
        )
        invoice = NewInvoice.objects.create(
            issuer_line=line,
            recipient_client=self.client_obj,
        )
        invoice.cars.add(car)
        invoice.regenerate_items_from_cars()
        items = list(invoice.items.all())
        descriptions = [i.description for i in items]
        self.assertNotIn('Хран', descriptions)

    def tearDown(self):
        CarService._service_obj_cache.clear()


class ApplyWarehouseDefaultsTests(TestCase):
    """Тесты Car.apply_warehouse_defaults() — копирование дефолтов склада"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Defaults Client")
        self.warehouse = Warehouse.objects.create(name="WH-Defaults", free_days=0)
        self.container = Container.objects.create(number="CONT-DEFAULTS")
        # Услуги склада с дефолтными ценами
        WarehouseService.objects.create(
            warehouse=self.warehouse, name='Цена за разгрузку',
            default_price=Decimal('160.00'), is_active=True,
        )
        WarehouseService.objects.create(
            warehouse=self.warehouse, name='Доставка до склада',
            default_price=Decimal('80.00'), is_active=True,
        )

    def test_force_overwrites_existing_values(self):
        """force=True перезаписывает даже заполненные поля"""
        car = Car.objects.create(
            year=2020, brand='DefBrand', vin='VIN-DEF-1',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            unload_fee=Decimal('999.00'),  # уже задано
            delivery_fee=Decimal('888.00'),
        )
        car.apply_warehouse_defaults(force=True)
        self.assertEqual(car.unload_fee, Decimal('160'))
        self.assertEqual(car.delivery_fee, Decimal('80'))

    def test_no_force_keeps_nondefault_rate(self):
        """force=False не перезаписывает поле если значение != 0 и != model default"""
        # Поле rate имеет default=5 в модели. Ставим 7 — не должно перезаписаться.
        WarehouseService.objects.create(
            warehouse=self.warehouse, name='Ставка за сутки',
            default_price=Decimal('10.00'), is_active=True,
        )
        car = Car.objects.create(
            year=2020, brand='DefBrand', vin='VIN-DEF-2',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
            rate=Decimal('7.00'),  # != 0, != default(5)
        )
        car.apply_warehouse_defaults(force=False)
        self.assertEqual(car.rate, Decimal('7.00'))  # не перезаписано

    def test_no_force_fills_empty_fields(self):
        """force=False заполняет поля с None или 0"""
        car = Car.objects.create(
            year=2020, brand='DefBrand', vin='VIN-DEF-3',
            client=self.client_obj, status='UNLOADED',
            warehouse=self.warehouse, container=self.container,
        )
        car.unload_fee = None
        car.apply_warehouse_defaults(force=False)
        self.assertEqual(car.unload_fee, Decimal('160'))

    def test_no_warehouse_does_nothing(self):
        """Без склада apply_warehouse_defaults не падает"""
        car = Car.objects.create(
            year=2020, brand='DefBrand', vin='VIN-DEF-4',
            client=self.client_obj, status='UNLOADED',
            container=self.container,
        )
        car.apply_warehouse_defaults(force=True)  # не должно упасть
