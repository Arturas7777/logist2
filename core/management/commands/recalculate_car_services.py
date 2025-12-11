"""
Команда для пересчета услуг всех автомобилей.

Заменяет:
- Услуги линий в соответствии с количеством авто в контейнере
- Услуги складов только на "Разгрузка/Погрузка/Декларация" и "Хранение"
- Пересчитывает итоговые и текущие цены
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Car, CarService, LineService, WarehouseService, Container
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Пересчитывает услуги всех автомобилей и цены'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать что будет изменено, без сохранения',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДПРОСМОТРА (dry-run) ===\n'))
        
        self.stdout.write(self.style.SUCCESS('Начинаем пересчет услуг автомобилей...\n'))
        
        # Статистика
        stats = {
            'cars_processed': 0,
            'line_services_updated': 0,
            'warehouse_services_updated': 0,
            'prices_recalculated': 0,
            'errors': 0,
        }
        
        # Получаем все автомобили с контейнерами
        cars = Car.objects.select_related(
            'container', 'container__line', 'warehouse', 'line'
        ).prefetch_related('car_services').all()
        
        total_cars = cars.count()
        self.stdout.write(f'Найдено автомобилей: {total_cars}\n')
        
        for i, car in enumerate(cars, 1):
            try:
                with transaction.atomic():
                    changes = self.process_car(car, dry_run)
                    
                    if changes['line_updated']:
                        stats['line_services_updated'] += 1
                    if changes['warehouse_updated']:
                        stats['warehouse_services_updated'] += 1
                    if changes['price_updated']:
                        stats['prices_recalculated'] += 1
                    
                    stats['cars_processed'] += 1
                    
                    if i % 50 == 0:
                        self.stdout.write(f'Обработано: {i}/{total_cars}')
                        
            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(f'Ошибка для {car.vin}: {e}'))
                logger.error(f'Error processing car {car.vin}: {e}')
        
        # Итоги
        self.stdout.write('\n' + '='*50)
        self.stdout.write(self.style.SUCCESS('ИТОГИ:'))
        self.stdout.write(f'  Обработано автомобилей: {stats["cars_processed"]}')
        self.stdout.write(f'  Обновлено услуг линий: {stats["line_services_updated"]}')
        self.stdout.write(f'  Обновлено услуг складов: {stats["warehouse_services_updated"]}')
        self.stdout.write(f'  Пересчитано цен: {stats["prices_recalculated"]}')
        self.stdout.write(f'  Ошибок: {stats["errors"]}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n[!] Eto byl predprosmotr. Zapustite bez --dry-run dlya primeneniya izmeneniy.'))

    def process_car(self, car, dry_run):
        """Обрабатывает один автомобиль"""
        changes = {
            'line_updated': False,
            'warehouse_updated': False,
            'price_updated': False,
        }
        
        # === 1. УСЛУГИ ЛИНИЙ ===
        if car.container and car.container.line:
            line = car.container.line
            car_count = car.container.container_cars.count()
            vehicle_type = getattr(car, 'vehicle_type', 'CAR')
            
            # Находим подходящую услугу линии
            new_line_service = self.find_line_service(line, car_count, vehicle_type)
            
            if new_line_service:
                # Удаляем старые услуги линий
                old_services = list(car.car_services.filter(service_type='LINE').values_list('service_id', flat=True))
                
                if not dry_run:
                    car.car_services.filter(service_type='LINE').delete()
                    CarService.objects.create(
                        car=car,
                        service_type='LINE',
                        service_id=new_line_service.id,
                        custom_price=new_line_service.default_price
                    )
                
                if old_services != [new_line_service.id]:
                    changes['line_updated'] = True
                    self.stdout.write(f'  [LINE] {car.vin}: услуга линии -> {new_line_service.name} ({new_line_service.default_price})')
        
        # === 2. УСЛУГИ СКЛАДА ===
        if car.warehouse:
            # Находим нужные услуги склада
            warehouse_services = self.find_warehouse_services(car.warehouse)
            
            if warehouse_services:
                old_services = set(car.car_services.filter(service_type='WAREHOUSE').values_list('service_id', flat=True))
                new_services = set(s.id for s in warehouse_services)
                
                if old_services != new_services:
                    if not dry_run:
                        car.car_services.filter(service_type='WAREHOUSE').delete()
                        for service in warehouse_services:
                            CarService.objects.create(
                                car=car,
                                service_type='WAREHOUSE',
                                service_id=service.id,
                                custom_price=service.default_price
                            )
                    
                    changes['warehouse_updated'] = True
                    service_names = ', '.join([s.name for s in warehouse_services])
                    self.stdout.write(f'  [WAREHOUSE] {car.vin}: услуги склада -> {service_names}')
        
        # === 3. ПЕРЕСЧЕТ ЦЕН ===
        old_total = car.total_price
        old_current = car.current_price
        
        if not dry_run:
            car.update_days_and_storage()
            car.calculate_total_price()
            car.save(update_fields=['days', 'storage_cost', 'current_price', 'total_price'])
        
        if car.total_price != old_total or car.current_price != old_current:
            changes['price_updated'] = True
            self.stdout.write(f'  [PRICE] {car.vin}: cena {old_total} -> {car.total_price}')
        
        return changes

    def find_line_service(self, line, car_count, vehicle_type):
        """Находит подходящую услугу линии"""
        services = LineService.objects.filter(line=line, is_active=True)
        
        if vehicle_type == 'MOTO':
            # Для мотоциклов ищем услугу с MOTO
            for service in services:
                if 'MOTO' in service.name.upper():
                    return service
        else:
            # Для авто ищем по количеству
            search_patterns = [
                f'{car_count} АВТО',
                f'{car_count} AUTO',
                f'{car_count}АВТО',
            ]
            
            for service in services:
                service_name_upper = service.name.upper()
                for pattern in search_patterns:
                    if pattern in service_name_upper:
                        return service
        
        return None

    def find_warehouse_services(self, warehouse):
        """Находит услуги склада: Разгрузка/Декларация и Хранение"""
        services = []
        all_services = WarehouseService.objects.filter(warehouse=warehouse, is_active=True)
        
        unload_keywords = ['РАЗГРУЗКА', 'ПОГРУЗКА', 'ДЕКЛАРАЦИЯ']
        storage_keywords = ['ХРАНЕНИЕ', 'STORAGE']
        
        unload_service = None
        storage_service = None
        
        for service in all_services:
            service_name_upper = service.name.upper()
            
            # Услуга разгрузки/декларации
            if not unload_service and any(kw in service_name_upper for kw in unload_keywords):
                unload_service = service
            
            # Услуга хранения
            if not storage_service and any(kw in service_name_upper for kw in storage_keywords):
                storage_service = service
        
        if unload_service:
            services.append(unload_service)
        if storage_service:
            services.append(storage_service)
        
        return services

