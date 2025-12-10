from django.db.models.signals import post_save, post_delete, pre_delete, pre_save
from django.dispatch import receiver
from .models import Car, Container, WarehouseService, LineService, CarrierService, CarService, DeletedCarService
from .models_billing import NewInvoice
from django.db.models import Sum
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction
from decimal import Decimal
import logging

logger = logging.getLogger('django')
@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, created, **kwargs):
    # При изменении контейнера — все машины внутри получают такой же статус и дату разгрузки
    # ОПТИМИЗИРОВАНО: Использует bulk_update вместо цикла
    if not instance.pk:
        return
    
    try:
        # Если есть дата разгрузки - обновляем её у всех автомобилей принудительно
        if instance.unload_date:
            # Подготавливаем данные для массового обновления
            cars_to_update = []
            for car in instance.container_cars.select_related('warehouse').all():
                # Обновляем дату разгрузки принудительно
                car.unload_date = instance.unload_date
                car.status = instance.status
                
                # Пересчитываем хранение и цены
                car.update_days_and_storage()
                car.calculate_total_price()
                cars_to_update.append(car)
            
            # Массовое обновление одним запросом
            if cars_to_update:
                Car.objects.bulk_update(
                    cars_to_update,
                    ['unload_date', 'status', 'days', 'storage_cost', 'current_price', 'total_price'],
                    batch_size=50
                )
                logger.info(f"✅ Container {instance.number}: bulk updated {len(cars_to_update)} cars (unload_date + status)")
        else:
            # Если нет даты разгрузки - обновляем только статус
            instance.container_cars.update(status=instance.status)
            logger.debug(f"Container {instance.number}: updated status for {instance.container_cars.count()} cars")
        
        # Отправляем batch WebSocket уведомление
        from core.utils import WebSocketBatcher
        for car in instance.container_cars.only('id', 'status'):
            WebSocketBatcher.add('Car', car.id, {'status': car.status})
        WebSocketBatcher.flush()
        
    except Exception as e:
        logger.error(f"Failed to update cars for container {instance.id}: {e}")

@receiver(post_save, sender=Car)
def update_related_on_car_save(sender, instance, **kwargs):
    # Обновляем total_amount инвойсов МАССОВО через bulk_update
    logger.debug(f"🔔 Signal post_save triggered for Car {instance.id} ({instance.vin})")
    
    # Проверяем, что у экземпляра есть первичный ключ
    if not instance.pk:
        logger.debug("Skipping - no PK")
        return
    
    # Обновляем новые инвойсы (NewInvoice)
    # Добавляем защиту от рекурсии
    logger.debug(f"Checking NewInvoice update for car {instance.id}, _updating_invoices={getattr(instance, '_updating_invoices', False)}")
    
    if not getattr(instance, '_updating_invoices', False):
        try:
            instance._updating_invoices = True
            
            # Получаем все новые инвойсы, связанные с этим автомобилем
            new_invoices = NewInvoice.objects.filter(cars=instance)
            logger.debug(f"Found {new_invoices.count()} NewInvoice(s) for car {instance.vin}")
            
            if new_invoices.exists():
                for invoice in new_invoices:
                    logger.info(f"Regenerating invoice {invoice.number} for car {instance.vin}...")
                    # Пересоздаем позиции инвойса на основе актуальных данных автомобиля
                    invoice.regenerate_items_from_cars()
                    logger.info(f"✅ Auto-regenerated invoice {invoice.number} for car {instance.vin}")
            else:
                logger.debug(f"No NewInvoice found for car {instance.vin}")
        except Exception as e:
            logger.error(f"❌ Failed to update new invoices for car {instance.id}: {e}", exc_info=True)
        finally:
            instance._updating_invoices = False
    else:
        logger.debug(f"Skipping NewInvoice update (recursion protection) for car {instance.id}")


# Сигналы для автоматического создания CarService при изменении контрагентов
# Сохраняем старые значения контрагентов перед сохранением
_old_contractors = {}

@receiver(pre_save, sender=Car)
def save_old_contractors(sender, instance, **kwargs):
    """Сохраняет старые значения контрагентов перед сохранением"""
    if instance.pk:
        try:
            old_instance = Car.objects.get(pk=instance.pk)
            _old_contractors[instance.pk] = {
                'warehouse_id': old_instance.warehouse_id,
                'line_id': old_instance.line_id,
                'carrier_id': old_instance.carrier_id
            }
        except Car.DoesNotExist:
            pass

@receiver(post_save, sender=Car)
def create_car_services_on_car_save(sender, instance, **kwargs):
    """Создает записи CarService при сохранении автомобиля с контрагентами"""
    if not instance.pk:
        return
    
    # Проверяем, изменились ли контрагенты (только при создании или смене контрагентов)
    created = kwargs.get('created', False)
    if not created:
        # Если это не создание, проверяем, изменились ли контрагенты
        old_contractors = _old_contractors.get(instance.pk, {})
        if old_contractors:
            warehouse_changed = old_contractors.get('warehouse_id') != instance.warehouse_id
            line_changed = old_contractors.get('line_id') != instance.line_id
            carrier_changed = old_contractors.get('carrier_id') != instance.carrier_id
            
            # Если контрагенты не изменились, не обновляем услуги
            if not (warehouse_changed or line_changed or carrier_changed):
                # Очищаем сохраненные значения
                _old_contractors.pop(instance.pk, None)
                return
        
        # Очищаем сохраненные значения
        _old_contractors.pop(instance.pk, None)
    
    try:
        # Получаем старые записи CarService для сравнения
        old_warehouse_services = set(instance.car_services.filter(service_type='WAREHOUSE').values_list('service_id', flat=True))
        old_line_services = set(instance.car_services.filter(service_type='LINE').values_list('service_id', flat=True))
        old_carrier_services = set(instance.car_services.filter(service_type='CARRIER').values_list('service_id', flat=True))
        
        # Обрабатываем услуги склада
        if instance.warehouse:
            warehouse_services = WarehouseService.objects.only('id', 'default_price').filter(
                warehouse=instance.warehouse, 
                is_active=True,
                default_price__gt=0
            )
            current_warehouse_service_ids = set()
            
            # Получаем черный список удаленных услуг
            deleted_warehouse_services = set(
                DeletedCarService.objects.filter(
                    car=instance,
                    service_type='WAREHOUSE'
                ).values_list('service_id', flat=True)
            )
            
            for service in warehouse_services:
                current_warehouse_service_ids.add(service.id)
                # Проверяем черный список
                if service.id not in deleted_warehouse_services:
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price}
                    )
            
            # Удаляем услуги склада, которые больше не актуальны
            services_to_remove = old_warehouse_services - current_warehouse_service_ids
            if services_to_remove:
                instance.car_services.filter(
                    service_type='WAREHOUSE',
                    service_id__in=services_to_remove
                ).delete()
        else:
            # Если склад не назначен, удаляем все услуги склада
            instance.car_services.filter(service_type='WAREHOUSE').delete()
        
        # Обрабатываем услуги линии
        if instance.line:
            line_services = LineService.objects.only('id', 'default_price').filter(
                line=instance.line, 
                is_active=True,
                default_price__gt=0
            )
            current_line_service_ids = set()
            
            # Получаем черный список удаленных услуг
            deleted_line_services = set(
                DeletedCarService.objects.filter(
                    car=instance,
                    service_type='LINE'
                ).values_list('service_id', flat=True)
            )
            
            for service in line_services:
                current_line_service_ids.add(service.id)
                # Проверяем черный список
                if service.id not in deleted_line_services:
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='LINE',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price}
                    )
            
            # Удаляем услуги линии, которые больше не актуальны
            services_to_remove = old_line_services - current_line_service_ids
            if services_to_remove:
                instance.car_services.filter(
                    service_type='LINE',
                    service_id__in=services_to_remove
                ).delete()
        else:
            # Если линия не назначена, удаляем все услуги линии
            instance.car_services.filter(service_type='LINE').delete()
        
        # Обрабатываем услуги перевозчика
        if instance.carrier:
            carrier_services = CarrierService.objects.only('id', 'default_price').filter(
                carrier=instance.carrier, 
                is_active=True,
                default_price__gt=0
            )
            current_carrier_service_ids = set()
            
            # Получаем черный список удаленных услуг
            deleted_carrier_services = set(
                DeletedCarService.objects.filter(
                    car=instance,
                    service_type='CARRIER'
                ).values_list('service_id', flat=True)
            )
            
            for service in carrier_services:
                current_carrier_service_ids.add(service.id)
                # Проверяем черный список
                if service.id not in deleted_carrier_services:
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='CARRIER',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price}
                    )
            
            # Удаляем услуги перевозчика, которые больше не актуальны
            services_to_remove = old_carrier_services - current_carrier_service_ids
            if services_to_remove:
                instance.car_services.filter(
                    service_type='CARRIER',
                    service_id__in=services_to_remove
                ).delete()
        else:
            # Если перевозчик не назначен, удаляем все услуги перевозчика
            instance.car_services.filter(service_type='CARRIER').delete()
                
    except Exception as e:
        logger.error(f"Error creating car services: {e}")

@receiver(post_save, sender=WarehouseService)
def update_cars_on_warehouse_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг склада"""
    try:
        # Находим все автомобили с этим складом
        cars = Car.objects.filter(warehouse=instance.warehouse)
        
        for car in cars:
            if instance.is_active and instance.default_price > 0:
                # Проверяем черный список перед созданием
                if not DeletedCarService.objects.filter(
                    car=car,
                    service_type='WAREHOUSE',
                    service_id=instance.id
                ).exists():
                    # Создаем или обновляем запись CarService
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='WAREHOUSE',
                        service_id=instance.id,
                        defaults={'custom_price': instance.default_price}
                    )
            else:
                # Удаляем запись CarService если услуга неактивна или цена = 0
                CarService.objects.filter(
                    car=car,
                    service_type='WAREHOUSE',
                    service_id=instance.id
                ).delete()
                
    except Exception as e:
        logger.error(f"Error updating cars on warehouse service change: {e}")

@receiver(post_save, sender=LineService)
def update_cars_on_line_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг линии"""
    try:
        # Находим все автомобили с этой линией
        cars = Car.objects.filter(line=instance.line)
        
        for car in cars:
            if instance.is_active and instance.default_price > 0:
                # Проверяем черный список перед созданием
                if not DeletedCarService.objects.filter(
                    car=car,
                    service_type='LINE',
                    service_id=instance.id
                ).exists():
                    # Создаем или обновляем запись CarService
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='LINE',
                        service_id=instance.id,
                        defaults={'custom_price': instance.default_price}
                    )
            else:
                # Удаляем запись CarService если услуга неактивна или цена = 0
                CarService.objects.filter(
                    car=car,
                    service_type='LINE',
                    service_id=instance.id
                ).delete()
                
    except Exception as e:
        logger.error(f"Error updating cars on line service change: {e}")

@receiver(post_save, sender=CarrierService)
def update_cars_on_carrier_service_change(sender, instance, **kwargs):
    """Обновляет записи CarService при изменении услуг перевозчика"""
    try:
        # Находим все автомобили с этим перевозчиком
        cars = Car.objects.filter(carrier=instance.carrier)
        
        for car in cars:
            if instance.is_active and instance.default_price > 0:
                # Проверяем черный список перед созданием
                if not DeletedCarService.objects.filter(
                    car=car,
                    service_type='CARRIER',
                    service_id=instance.id
                ).exists():
                    # Создаем или обновляем запись CarService
                    CarService.objects.get_or_create(
                        car=car,
                        service_type='CARRIER',
                        service_id=instance.id,
                        defaults={'custom_price': instance.default_price}
                    )
            else:
                # Удаляем запись CarService если услуга неактивна или цена = 0
                CarService.objects.filter(
                    car=car,
                    service_type='CARRIER',
                    service_id=instance.id
                ).delete()
                
    except Exception as e:
        logger.error(f"Error updating cars on carrier service change: {e}")


# ============================================================================
# СИГНАЛЫ ДЛЯ ПЕРЕСЧЕТА ИНВОЙСОВ ПРИ ИЗМЕНЕНИИ УСЛУГ АВТОМОБИЛЯ
# ============================================================================

@receiver(post_save, sender=CarService)
def recalculate_invoices_on_car_service_save(sender, instance, **kwargs):
    """Пересчитывает инвойсы при изменении услуги автомобиля"""
    try:
        car = instance.car
        if not car:
            return
        
        # Находим все инвойсы с этим автомобилем (кроме оплаченных и отмененных)
        invoices = NewInvoice.objects.filter(
            cars=car,
            status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE']
        )
        
        for invoice in invoices:
            logger.info(f"🔄 Пересчет инвойса {invoice.number} после изменения услуги авто {car.vin}")
            invoice.regenerate_items_from_cars()
            
    except Exception as e:
        logger.error(f"Error recalculating invoices on CarService save: {e}")


@receiver(post_delete, sender=CarService)
def recalculate_invoices_on_car_service_delete(sender, instance, **kwargs):
    """Пересчитывает инвойсы при удалении услуги автомобиля"""
    try:
        car = instance.car
        if not car:
            return
        
        # Находим все инвойсы с этим автомобилем (кроме оплаченных и отмененных)
        invoices = NewInvoice.objects.filter(
            cars=car,
            status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE']
        )
        
        for invoice in invoices:
            logger.info(f"🔄 Пересчет инвойса {invoice.number} после удаления услуги авто {car.vin}")
            invoice.regenerate_items_from_cars()
            
    except Exception as e:
        logger.error(f"Error recalculating invoices on CarService delete: {e}")