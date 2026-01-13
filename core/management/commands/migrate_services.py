from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import (
    Car, Line, Carrier, Warehouse, ServiceType, 
    LineService, CarrierService, WarehouseService, CarService
)
from decimal import Decimal


class Command(BaseCommand):
    help = 'Мигрирует старые услуги в новую систему услуг'

    def handle(self, *args, **options):
        self.stdout.write('Начинаем миграцию услуг...')
        
        with transaction.atomic():
            # 1. Создаем типы услуг если их нет
            self.create_service_types()
            
            # 2. Конвертируем услуги линий
            self.migrate_line_services()
            
            # 3. Конвертируем услуги перевозчиков
            self.migrate_carrier_services()
            
            # 4. Конвертируем услуги складов
            self.migrate_warehouse_services()
            
            # 5. Конвертируем услуги автомобилей
            self.migrate_car_services()
        
        self.stdout.write(
            self.style.SUCCESS('Миграция услуг завершена успешно!')
        )

    def create_service_types(self):
        """Создает типы услуг"""
        service_types = [
            ('Перевозка', 'Услуги по перевозке'),
            ('Документооборот', 'Оформление документов'),
            ('Хранение', 'Складские услуги'),
            ('Погрузка/Разгрузка', 'Услуги погрузки и разгрузки'),
            ('Таможенное оформление', 'Таможенные услуги'),
            ('Дополнительные услуги', 'Прочие услуги'),
        ]
        
        for name, description in service_types:
            ServiceType.objects.get_or_create(
                name=name,
                defaults={'description': description}
            )
        
        self.stdout.write('✓ Типы услуг созданы')

    def migrate_line_services(self):
        """Конвертирует услуги линий из старых полей"""
        transport_type = ServiceType.objects.get(name='Перевозка')
        documents_type = ServiceType.objects.get(name='Документооборот')
        additional_type = ServiceType.objects.get(name='Дополнительные услуги')
        
        for line in Line.objects.all():
            # Океанский фрахт
            if line.ocean_freight_rate and line.ocean_freight_rate > 0:
                LineService.objects.get_or_create(
                    line=line,
                    name='Океанский фрахт',
                    defaults={
                        'service_type': transport_type,
                        'default_price': line.ocean_freight_rate,
                        'description': 'Перевозка автомобиля морем'
                    }
                )
            
            # Документооборот
            if line.documentation_fee and line.documentation_fee > 0:
                LineService.objects.get_or_create(
                    line=line,
                    name='Оформление документов',
                    defaults={
                        'service_type': documents_type,
                        'default_price': line.documentation_fee,
                        'description': 'Оформление экспортных документов'
                    }
                )
            
            # Обработка груза
            if line.handling_fee and line.handling_fee > 0:
                LineService.objects.get_or_create(
                    line=line,
                    name='Обработка груза',
                    defaults={
                        'service_type': additional_type,
                        'default_price': line.handling_fee,
                        'description': 'Обработка груза в порту'
                    }
                )
            
            # THS сбор
            if line.ths_fee and line.ths_fee > 0:
                LineService.objects.get_or_create(
                    line=line,
                    name='THS сбор',
                    defaults={
                        'service_type': additional_type,
                        'default_price': line.ths_fee,
                        'description': 'Terminal Handling Surcharge'
                    }
                )
            
            # Дополнительные сборы
            if line.additional_fees and line.additional_fees > 0:
                LineService.objects.get_or_create(
                    line=line,
                    name='Дополнительные сборы',
                    defaults={
                        'service_type': additional_type,
                        'default_price': line.additional_fees,
                        'description': 'Прочие сборы линии'
                    }
                )
        
        self.stdout.write('✓ Услуги линий сконвертированы')

    def migrate_carrier_services(self):
        """Конвертирует услуги перевозчиков из старых полей"""
        transport_type = ServiceType.objects.get(name='Перевозка')
        handling_type = ServiceType.objects.get(name='Погрузка/Разгрузка')
        additional_type = ServiceType.objects.get(name='Дополнительные услуги')
        
        for carrier in Carrier.objects.all():
            # Перевозка
            if carrier.transport_rate and carrier.transport_rate > 0:
                CarrierService.objects.get_or_create(
                    carrier=carrier,
                    name='Перевозка',
                    defaults={
                        'service_type': transport_type,
                        'default_price': carrier.transport_rate,
                        'description': 'Стоимость перевозки за км'
                    }
                )
            
            # Погрузка
            if carrier.loading_fee and carrier.loading_fee > 0:
                CarrierService.objects.get_or_create(
                    carrier=carrier,
                    name='Погрузка',
                    defaults={
                        'service_type': handling_type,
                        'default_price': carrier.loading_fee,
                        'description': 'Стоимость погрузки'
                    }
                )
            
            # Разгрузка
            if carrier.unloading_fee and carrier.unloading_fee > 0:
                CarrierService.objects.get_or_create(
                    carrier=carrier,
                    name='Разгрузка',
                    defaults={
                        'service_type': handling_type,
                        'default_price': carrier.unloading_fee,
                        'description': 'Стоимость разгрузки'
                    }
                )
            
            # Топливная надбавка
            if carrier.fuel_surcharge and carrier.fuel_surcharge > 0:
                CarrierService.objects.get_or_create(
                    carrier=carrier,
                    name='Топливная надбавка',
                    defaults={
                        'service_type': additional_type,
                        'default_price': carrier.fuel_surcharge,
                        'description': 'Топливная надбавка'
                    }
                )
            
            # Дополнительные сборы
            if carrier.additional_fees and carrier.additional_fees > 0:
                CarrierService.objects.get_or_create(
                    carrier=carrier,
                    name='Дополнительные сборы',
                    defaults={
                        'service_type': additional_type,
                        'default_price': carrier.additional_fees,
                        'description': 'Прочие сборы перевозчика'
                    }
                )
        
        self.stdout.write('✓ Услуги перевозчиков сконвертированы')

    def migrate_warehouse_services(self):
        """Конвертирует услуги складов из старых полей"""
        storage_type = ServiceType.objects.get(name='Хранение')
        handling_type = ServiceType.objects.get(name='Погрузка/Разгрузка')
        documents_type = ServiceType.objects.get(name='Документооборот')
        customs_type = ServiceType.objects.get(name='Таможенное оформление')
        additional_type = ServiceType.objects.get(name='Дополнительные услуги')
        
        for warehouse in Warehouse.objects.all():
            # Разгрузка
            if warehouse.default_unloading_fee and warehouse.default_unloading_fee > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Разгрузка',
                    defaults={
                        'service_type': handling_type,
                        'default_price': warehouse.default_unloading_fee,
                        'description': 'Разгрузка автомобиля'
                    }
                )
            
            # Доставка до склада
            if warehouse.delivery_to_warehouse and warehouse.delivery_to_warehouse > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Доставка до склада',
                    defaults={
                        'service_type': handling_type,
                        'default_price': warehouse.delivery_to_warehouse,
                        'description': 'Доставка автомобиля до склада'
                    }
                )
            
            # Погрузка на трал
            if warehouse.loading_on_trawl and warehouse.loading_on_trawl > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Погрузка на трал',
                    defaults={
                        'service_type': handling_type,
                        'default_price': warehouse.loading_on_trawl,
                        'description': 'Погрузка автомобиля на трал'
                    }
                )
            
            # Оформление документов
            if warehouse.documents_fee and warehouse.documents_fee > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Оформление документов',
                    defaults={
                        'service_type': documents_type,
                        'default_price': warehouse.documents_fee,
                        'description': 'Оформление документов'
                    }
                )
            
            # Плата за передачу
            if warehouse.transfer_fee and warehouse.transfer_fee > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Плата за передачу',
                    defaults={
                        'service_type': additional_type,
                        'default_price': warehouse.transfer_fee,
                        'description': 'Плата за передачу автомобиля'
                    }
                )
            
            # Транзитная декларация
            if warehouse.transit_declaration and warehouse.transit_declaration > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Транзитная декларация',
                    defaults={
                        'service_type': customs_type,
                        'default_price': warehouse.transit_declaration,
                        'description': 'Оформление транзитной декларации'
                    }
                )
            
            # Экспортная декларация
            if warehouse.export_declaration and warehouse.export_declaration > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Экспортная декларация',
                    defaults={
                        'service_type': customs_type,
                        'default_price': warehouse.export_declaration,
                        'description': 'Оформление экспортной декларации'
                    }
                )
            
            # Дополнительные расходы
            if warehouse.additional_expenses and warehouse.additional_expenses > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Дополнительные расходы',
                    defaults={
                        'service_type': additional_type,
                        'default_price': warehouse.additional_expenses,
                        'description': 'Прочие расходы склада'
                    }
                )
            
            # Комплекс
            if warehouse.complex_fee and warehouse.complex_fee > 0:
                WarehouseService.objects.get_or_create(
                    warehouse=warehouse,
                    name='Комплекс',
                    defaults={
                        'service_type': additional_type,
                        'default_price': warehouse.complex_fee,
                        'description': 'Комплексные услуги склада'
                    }
                )
        
        self.stdout.write('✓ Услуги складов сконвертированы')

    def migrate_car_services(self):
        """Конвертирует услуги автомобилей из старых полей"""
        transport_type = ServiceType.objects.get(name='Перевозка')
        documents_type = ServiceType.objects.get(name='Документооборот')
        customs_type = ServiceType.objects.get(name='Таможенное оформление')
        additional_type = ServiceType.objects.get(name='Дополнительные услуги')
        
        for car in Car.objects.all():
            # Океанский фрахт
            if car.ocean_freight and car.ocean_freight > 0 and car.line:
                line_service = LineService.objects.filter(
                    line=car.line,
                    name='Океанский фрахт'
                ).first()
                if line_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='LINE',
                        service_id=line_service.id,
                        defaults={
                            'custom_price': car.ocean_freight,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля ocean_freight'
                        }
                    )
            
            # Транспорт КЗ
            if car.transport_kz and car.transport_kz > 0 and car.carrier:
                carrier_service = CarrierService.objects.filter(
                    carrier=car.carrier,
                    name='Перевозка'
                ).first()
                if carrier_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='CARRIER',
                        service_id=carrier_service.id,
                        defaults={
                            'custom_price': car.transport_kz,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля transport_kz'
                        }
                    )
            
            # Разгрузка
            if car.unload_fee and car.unload_fee > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Разгрузка'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.unload_fee,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля unload_fee'
                        }
                    )
            
            # Документы
            if car.docs_fee and car.docs_fee > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Оформление документов'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.docs_fee,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля docs_fee'
                        }
                    )
            
            # Транзитная декларация
            if car.transit_declaration and car.transit_declaration > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Транзитная декларация'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.transit_declaration,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля transit_declaration'
                        }
                    )
            
            # Экспортная декларация
            if car.export_declaration and car.export_declaration > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Экспортная декларация'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.export_declaration,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля export_declaration'
                        }
                    )
            
            # Дополнительные расходы
            if car.extra_costs and car.extra_costs > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Дополнительные расходы'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.extra_costs,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля extra_costs'
                        }
                    )
            
            # Комплекс
            if car.complex_fee and car.complex_fee > 0 and car.warehouse:
                warehouse_service = WarehouseService.objects.filter(
                    warehouse=car.warehouse,
                    name='Комплекс'
                ).first()
                if warehouse_service:
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=warehouse_service.id,
                        defaults={
                            'custom_price': car.complex_fee,
                            'quantity': 1,
                            'notes': 'Мигрировано из старого поля complex_fee'
                        }
                    )
        
        self.stdout.write('✓ Услуги автомобилей сконвертированы')

