# -*- coding: utf-8 -*-
"""
Команда для исправления услуг линий THS.

Проблема: При добавлении мотоциклов в контейнеры, всем ТС была добавлена услуга 
"THS Линия 4 АВТО" вместо правильных услуг:
- Для автомобилей: "THS Линия 3 АВТО" (если в контейнере есть мотоциклы)
- Для мотоциклов: "THS Линия MOTO"

Исправляем для контейнеров со статусами "Разгружен" (UNLOADED) и "В порту" (IN_PORT).
"""

from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.db.models import Count, Q
from django.db.models.signals import post_save, pre_save
from core.models import Car, Container, CarService, LineService, Line
from core import signals as core_signals
from decimal import Decimal
import logging

logger = logging.getLogger('django')


class Command(BaseCommand):
    help = 'Исправляет услуги линий THS для контейнеров с мотоциклами'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет изменено без реальных изменений',
        )
        parser.add_argument(
            '--containers',
            type=str,
            help='ID контейнеров через запятую (например: 107,111,120)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        containers_filter = options.get('containers')
        
        self.stdout.write(self.style.NOTICE('=' * 70))
        self.stdout.write(self.style.NOTICE('ANALIZ USLUG LINIJ THS'))
        self.stdout.write(self.style.NOTICE('=' * 70))
        
        # 1. Показываем все доступные услуги линий
        self.stdout.write(self.style.HTTP_INFO('\n[LIST] Vse uslugi linij v sisteme:'))
        all_line_services = LineService.objects.select_related('line').filter(is_active=True).order_by('line__name', 'name')
        
        current_line = None
        for service in all_line_services:
            if current_line != service.line.name:
                current_line = service.line.name
                self.stdout.write(f'\n  [LINE] Liniya: {current_line}')
            self.stdout.write(f'      ID={service.id}: {service.name} - cena: {service.default_price} EUR')
        
        # 2. Находим контейнеры с мотоциклами и нужными статусами
        self.stdout.write(self.style.HTTP_INFO('\n\n[CONTAINERS] Kontejnery s motociklami (status: IN_PORT ili UNLOADED):'))
        
        # Базовый queryset - контейнеры с мотоциклами
        containers_qs = Container.objects.filter(
            status__in=['IN_PORT', 'UNLOADED'],
            container_cars__vehicle_type='MOTO'
        ).distinct().prefetch_related('container_cars')
        
        # Фильтр по конкретным контейнерам если указано
        if containers_filter:
            try:
                container_ids = [int(x.strip()) for x in containers_filter.split(',')]
                containers_qs = containers_qs.filter(id__in=container_ids)
            except ValueError:
                self.stdout.write(self.style.ERROR('Nevernyi format --containers. Ispolzujte: --containers=107,111,120'))
                return
        
        containers_with_moto = containers_qs
        
        if not containers_with_moto.exists():
            self.stdout.write(self.style.WARNING('  Ne najdeno kontejnerov s motociklami'))
            return
        
        # Словарь для хранения изменений
        changes_to_make = []
        
        for container in containers_with_moto:
            cars = container.container_cars.all()
            car_count = cars.exclude(vehicle_type='MOTO').count()
            moto_count = cars.filter(vehicle_type='MOTO').count()
            
            self.stdout.write(f'\n  [BOX] {container.number} (ID={container.id}, status={container.status})')
            self.stdout.write(f'      Avtomobilej: {car_count}, Motociklov: {moto_count}')
            
            # Анализируем каждый автомобиль в контейнере
            self.stdout.write(f'\n      [CARS] Transportnye sredstva:')
            for car in cars:
                # Получаем линию из автомобиля (не из контейнера!)
                line = car.line
                line_name = line.name if line else "NE UKAZANA"
                
                car_service = CarService.objects.filter(car=car, service_type='LINE').first()
                
                current_service_name = "NET USLUGI"
                current_service_id = None
                current_price = Decimal('0')
                service_exists = True
                
                if car_service:
                    try:
                        current_ls = LineService.objects.get(id=car_service.service_id)
                        current_service_name = current_ls.name
                        current_service_id = current_ls.id
                        current_price = car_service.custom_price or current_ls.default_price
                    except LineService.DoesNotExist:
                        current_service_name = f"USLUGA NE NAJDENA (ID={car_service.service_id})"
                        service_exists = False
                
                # Определяем нужное изменение
                needs_change = False
                new_service = None
                reason = ""
                
                if not line:
                    self.stdout.write(f'        [!] [{car.vehicle_type}] {car.brand} (ID={car.id}): {current_service_name}')
                    self.stdout.write(self.style.WARNING(f'           Liniya ne ukazana na avto!'))
                    continue
                
                # Ищем услуги для этой линии
                line_services = LineService.objects.filter(line=line, is_active=True)
                
                # Ищем услуги по паттернам
                service_4_avto = None
                service_3_avto = None
                service_moto = None
                
                for service in line_services:
                    name_upper = service.name.upper()
                    if '4 АВТО' in name_upper or '4 AUTO' in name_upper:
                        service_4_avto = service
                    elif '3 АВТО' in name_upper or '3 AUTO' in name_upper:
                        service_3_avto = service
                    elif 'MOTO' in name_upper:
                        service_moto = service
                
                # Определяем правильную услугу по количеству авто (без мотоциклов)
                correct_car_service = None
                for service in line_services:
                    name_upper = service.name.upper()
                    if f'{car_count} АВТО' in name_upper or f'{car_count} AUTO' in name_upper:
                        correct_car_service = service
                        break
                
                if car.vehicle_type == 'MOTO':
                    # Мотоцикл должен иметь услугу MOTO
                    if service_moto and current_service_id != service_moto.id:
                        needs_change = True
                        new_service = service_moto
                        reason = "MOTO -> usluga MOTO"
                    elif not service_moto:
                        reason = "[!] Usluga MOTO ne najdena dlya linii!"
                    elif not service_exists and service_moto:
                        # Если услуга не существует, нужно создать новую
                        needs_change = True
                        new_service = service_moto
                        reason = "Orphan service -> MOTO"
                else:
                    # Автомобиль - определяем правильную услугу
                    target_service = correct_car_service or service_3_avto
                    
                    if not service_exists and target_service:
                        # Услуга-сирота, нужно заменить
                        needs_change = True
                        new_service = target_service
                        reason = f"Orphan service -> {car_count} AVTO"
                    elif target_service and current_service_id != target_service.id:
                        needs_change = True
                        new_service = target_service
                        reason = f"4 AVTO -> {car_count} AVTO (est motocikly)"
                    elif service_4_avto and current_service_id == service_4_avto.id and service_3_avto:
                        # Если установлена 4 АВТО, но должна быть другая (есть мотоциклы)
                        needs_change = True
                        new_service = service_3_avto
                        reason = f"4 AVTO -> 3 AVTO (est motocikly)"
                
                # Выводим информацию
                status_icon = "[OK]" if not needs_change and service_exists else "[!!]"
                type_label = "MOTO" if car.vehicle_type == 'MOTO' else "CAR"
                
                self.stdout.write(f'        {status_icon} [{type_label}] {car.brand} (ID={car.id}), Line={line_name}: {current_service_name}')
                
                if needs_change and new_service:
                    self.stdout.write(self.style.WARNING(f'           -> NUZHNO IZMENIT: {new_service.name} ({new_service.default_price} EUR)'))
                    changes_to_make.append({
                        'car': car,
                        'car_service': car_service,
                        'new_service': new_service,
                        'old_service_name': current_service_name,
                        'reason': reason
                    })
                elif reason and not new_service:
                    self.stdout.write(self.style.ERROR(f'           {reason}'))
        
        # 3. Итог
        self.stdout.write(self.style.NOTICE('\n' + '=' * 70))
        self.stdout.write(self.style.NOTICE(f'ITOGO: Zapisej dlya izmeneniya: {len(changes_to_make)}'))
        self.stdout.write(self.style.NOTICE('=' * 70))
        
        if not changes_to_make:
            self.stdout.write(self.style.SUCCESS('\n[OK] Vse uslugi nastroeny pravilno!'))
            return
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n[!] DRY-RUN rezhim - izmeneniya NE primeneny'))
            self.stdout.write(self.style.HTTP_INFO('\nDlya primeneniya izmenenij zapustite komandu bez --dry-run'))
            return
        
        # Применяем изменения НАПРЯМУЮ через SQL, без триггерирования сигналов
        self.stdout.write(self.style.HTTP_INFO('\n[FIX] Primenenie izmenenij (bez signalov)...'))
        
        updated_count = 0
        created_count = 0
        cars_to_recalc = set()
        
        with transaction.atomic():
            for change in changes_to_make:
                car = change['car']
                car_service = change['car_service']
                new_service = change['new_service']
                
                try:
                    if car_service:
                        # Обновляем напрямую через QuerySet.update() - это НЕ вызывает сигналы
                        old_price = car_service.custom_price
                        
                        CarService.objects.filter(id=car_service.id).update(
                            service_id=new_service.id,
                            custom_price=new_service.default_price
                        )
                        
                        self.stdout.write(
                            f'  [OK] {car.brand} (ID={car.id}): '
                            f'{change["old_service_name"]} -> {new_service.name} '
                            f'(cena: {old_price} EUR -> {new_service.default_price} EUR)'
                        )
                        updated_count += 1
                    else:
                        # Создаем новую запись CarService напрямую через SQL
                        with connection.cursor() as cursor:
                            cursor.execute("""
                                INSERT INTO core_carservice (car_id, service_type, service_id, custom_price, quantity, notes, created_at, updated_at)
                                VALUES (%s, 'LINE', %s, %s, 1, '', NOW(), NOW())
                            """, [car.id, new_service.id, float(new_service.default_price)])
                        
                        self.stdout.write(
                            f'  [OK] {car.brand} (ID={car.id}): '
                            f'SOZDANO {new_service.name} ({new_service.default_price} EUR)'
                        )
                        created_count += 1
                    
                    cars_to_recalc.add(car.id)
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  [ERR] Oshibka dlya {car.brand} (ID={car.id}): {e}'))
        
        # Пересчитываем цены для всех затронутых автомобилей
        self.stdout.write(self.style.HTTP_INFO(f'\n[RECALC] Pereschityvaem ceny dlya {len(cars_to_recalc)} avto...'))
        
        for car_id in cars_to_recalc:
            try:
                car = Car.objects.get(id=car_id)
                car.calculate_total_price()
                # Обновляем только поля цен напрямую через update()
                Car.objects.filter(id=car_id).update(
                    current_price=car.current_price,
                    total_price=car.total_price
                )
                self.stdout.write(f'  [OK] Car ID={car_id}: current_price={car.current_price}, total_price={car.total_price}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  [ERR] Oshibka peresceta dlya Car ID={car_id}: {e}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n[OK] Gotovo!'))
        self.stdout.write(f'  Obnovleno: {updated_count}')
        self.stdout.write(f'  Sozdano: {created_count}')
        self.stdout.write(f'  Pereschitano cen: {len(cars_to_recalc)}')
