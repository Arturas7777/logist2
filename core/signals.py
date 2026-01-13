from django.db.models.signals import post_save, post_delete, pre_delete, pre_save
from django.dispatch import receiver
from .models import Car, Container, WarehouseService, LineService, CarrierService, CarService, DeletedCarService
from .models_billing import NewInvoice
from django.db.models import Sum
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction, OperationalError
from decimal import Decimal
import logging

logger = logging.getLogger('django')

# Сохраняем старые значения контейнера для определения что изменилось
_old_container_values = {}

@receiver(pre_save, sender=Container)
def save_old_container_values(sender, instance, **kwargs):
    """Сохраняем старые значения контейнера до сохранения"""
    print(f"[PRE_SAVE] Container {instance.number} pk={instance.pk}", flush=True)
    if instance.pk:
        try:
            old = Container.objects.filter(pk=instance.pk).values('status', 'unload_date').first()
            if old:
                _old_container_values[instance.pk] = old
                print(f"[PRE_SAVE] Saved old values: {old}", flush=True)
        except Exception as e:
            print(f"[PRE_SAVE] Error: {e}", flush=True)

@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, created, **kwargs):
    """
    ОТКЛЮЧЕНО - обновление автомобилей теперь происходит в ContainerAdmin.save_model()
    Это позволяет избежать дублирования работы и таймаутов
    """
    # Просто очищаем сохранённые значения, ничего больше не делаем
    _old_container_values.pop(instance.pk, None)
    logger.debug(f"Container {instance.number} post_save: signal disabled, handled in admin")

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
            # Используем select_for_update(nowait=True) чтобы не ждать блокировку
            new_invoices = list(NewInvoice.objects.filter(cars=instance).values_list('id', flat=True))
            logger.debug(f"Found {len(new_invoices)} NewInvoice(s) for car {instance.vin}")
            
            if new_invoices:
                for invoice_id in new_invoices:
                    try:
                        # Каждый инвойс обрабатываем в отдельной транзакции
                        with transaction.atomic():
                            invoice = NewInvoice.objects.select_for_update(nowait=True).get(id=invoice_id)
                            logger.info(f"Regenerating invoice {invoice.number} for car {instance.vin}...")
                            invoice.regenerate_items_from_cars()
                            logger.info(f"✅ Auto-regenerated invoice {invoice.number} for car {instance.vin}")
                    except OperationalError:
                        # Инвойс заблокирован другой транзакцией - пропускаем
                        logger.warning(f"⏭️ Skipping invoice {invoice_id} - locked by another transaction")
                    except NewInvoice.DoesNotExist:
                        logger.warning(f"⏭️ Invoice {invoice_id} was deleted")
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

def find_line_service_by_container_count(line, container, vehicle_type):
    """
    Находит подходящую услугу линии на основе количества авто в контейнере и типа ТС.
    
    Логика выбора:
    - Для мотоциклов: ищем "THS {ЛИНИЯ} MOTO" или "MOTO" в названии
    - Для авто: ищем "THS {ЛИНИЯ} {КОЛ-ВО} АВТО" или "{КОЛ-ВО} АВТО" в названии
    
    ВАЖНО: Мотоциклы НЕ учитываются при подсчёте количества авто!
    """
    if not line or not container:
        return None
    
    line_name_upper = line.name.upper()
    
    # Считаем количество ТОЛЬКО автомобилей в контейнере (мотоциклы не учитываются!)
    car_count = container.container_cars.exclude(vehicle_type='MOTO').count()
    
    # Получаем все активные услуги линии
    services = LineService.objects.filter(line=line, is_active=True)
    
    if vehicle_type == 'MOTO':
        # Для мотоциклов ищем услугу с MOTO в названии
        for service in services:
            service_name_upper = service.name.upper()
            if 'MOTO' in service_name_upper:
                # Проверяем что это услуга для этой линии
                if line_name_upper in service_name_upper or 'THS' in service_name_upper:
                    return service
        # Если не нашли специфичную, ищем любую с MOTO
        for service in services:
            if 'MOTO' in service.name.upper():
                return service
    else:
        # Для авто ищем услугу по количеству
        # Формат: "THS MAERSK 3 АВТО" или "3 АВТО"
        search_patterns = [
            f'{car_count} АВТО',
            f'{car_count} AUTO',
            f'{car_count}АВТО',
            f'{car_count}AUTO',
        ]
        
        for service in services:
            service_name_upper = service.name.upper()
            for pattern in search_patterns:
                if pattern in service_name_upper:
                    return service
    
    return None


def find_warehouse_services_for_car(warehouse):
    """
    Находит услуги склада для автомобиля, которые должны добавляться по умолчанию.
    Возвращает только услуги с флагом add_by_default=True.
    """
    if not warehouse:
        return []
    
    # Возвращаем только услуги с флагом add_by_default=True
    return list(WarehouseService.objects.filter(
        warehouse=warehouse, 
        is_active=True,
        add_by_default=True
    ))


@receiver(post_save, sender=Car)
def create_car_services_on_car_save(sender, instance, **kwargs):
    """
    Создает записи CarService при сохранении автомобиля с контрагентами.
    
    Умный выбор услуг:
    - Услуги линий: выбираются по количеству авто в контейнере (THS MAERSK 3 АВТО)
    - Для мотоциклов: выбирается услуга с MOTO (THS CMA MOTO)
    - Услуги складов: добавляются "Разгрузка/Погрузка/Декларация" и "Хранение"
    """
    if not instance.pk:
        return
    
    # Защита от рекурсии - пропускаем если уже создаем услуги для этого авто
    if getattr(instance, '_creating_services', False):
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
        else:
            # Нет сохранённых значений - значит контрагенты не менялись
            return
        
        # Очищаем сохраненные значения
        _old_contractors.pop(instance.pk, None)
    
    # Устанавливаем флаг для защиты от рекурсии
    instance._creating_services = True
    
    try:
        # Получаем черные списки удаленных услуг
        deleted_warehouse_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='WAREHOUSE').values_list('service_id', flat=True)
        )
        deleted_line_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='LINE').values_list('service_id', flat=True)
        )
        deleted_carrier_services = set(
            DeletedCarService.objects.filter(car=instance, service_type='CARRIER').values_list('service_id', flat=True)
        )
        
        # ========== УСЛУГИ СКЛАДА ==========
        # Удаляем старые услуги склада если склад изменился
        instance.car_services.filter(service_type='WAREHOUSE').delete()
        
        if instance.warehouse:
            # Находим стандартные услуги склада (Разгрузка/Декларация и Хранение)
            warehouse_services = find_warehouse_services_for_car(instance.warehouse)
            
            for service in warehouse_services:
                if service.id not in deleted_warehouse_services:
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price}
                    )
                    logger.info(f"🏭 Добавлена услуга склада '{service.name}' для {instance.vin}")
        
        # ========== УСЛУГИ ЛИНИИ ==========
        # Удаляем старые услуги линии если линия изменилась
        instance.car_services.filter(service_type='LINE').delete()
        
        if instance.line and instance.container:
            # Определяем тип ТС (по умолчанию CAR если поле не существует)
            vehicle_type = getattr(instance, 'vehicle_type', 'CAR')
            
            # Находим подходящую услугу линии по количеству авто в контейнере
            line_service = find_line_service_by_container_count(
                instance.line, 
                instance.container, 
                vehicle_type
            )
            
            if line_service and line_service.id not in deleted_line_services:
                CarService.objects.get_or_create(
                    car=instance,
                    service_type='LINE',
                    service_id=line_service.id,
                    defaults={'custom_price': line_service.default_price}
                )
                logger.info(f"🚢 Добавлена услуга линии '{line_service.name}' для {instance.vin} (контейнер: {instance.container.number})")
        
        # ========== УСЛУГИ ПЕРЕВОЗЧИКА ==========
        # Удаляем старые услуги перевозчика если перевозчик изменился
        instance.car_services.filter(service_type='CARRIER').delete()
        
        if instance.carrier:
            carrier_services = CarrierService.objects.filter(
                carrier=instance.carrier, 
                is_active=True,
                default_price__gt=0
            )
            
            for service in carrier_services:
                if service.id not in deleted_carrier_services:
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type='CARRIER',
                        service_id=service.id,
                        defaults={'custom_price': service.default_price}
                    )
                
    except Exception as e:
        logger.error(f"Error creating car services: {e}")
    finally:
        # Сбрасываем флаг защиты от рекурсии
        instance._creating_services = False

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


# ============================================================================
# КАСКАДНОЕ УДАЛЕНИЕ CarService ПРИ УДАЛЕНИИ УСЛУГ ИЗ СПРАВОЧНИКОВ
# ============================================================================

@receiver(pre_delete, sender=LineService)
def delete_car_services_on_line_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги линии.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='LINE',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении LineService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on LineService delete: {e}")


@receiver(pre_delete, sender=WarehouseService)
def delete_car_services_on_warehouse_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги склада.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='WAREHOUSE',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении WarehouseService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on WarehouseService delete: {e}")


@receiver(pre_delete, sender=CarrierService)
def delete_car_services_on_carrier_service_delete(sender, instance, **kwargs):
    """
    Удаляет связанные CarService записи при удалении услуги перевозчика.
    Это предотвращает появление 'битых' записей с несуществующими service_id.
    """
    try:
        deleted_count = CarService.objects.filter(
            service_type='CARRIER',
            service_id=instance.id
        ).delete()[0]
        
        if deleted_count > 0:
            logger.info(f"🗑️ Удалено {deleted_count} CarService записей при удалении CarrierService '{instance.name}' (id={instance.id})")
    except Exception as e:
        logger.error(f"Error deleting CarService on CarrierService delete: {e}")


# ============================================================================
# СИГНАЛЫ ДЛЯ EMAIL-УВЕДОМЛЕНИЙ КЛИЕНТОВ
# ============================================================================

# Храним старые значения для определения изменений
_old_notification_values = {}

@receiver(pre_save, sender=Container)
def save_old_notification_values(sender, instance, **kwargs):
    """Сохраняем старые значения planned_unload_date и unload_date перед сохранением"""
    if instance.pk:
        try:
            old = Container.objects.filter(pk=instance.pk).values('planned_unload_date', 'unload_date').first()
            if old:
                _old_notification_values[instance.pk] = {
                    'planned_unload_date': old.get('planned_unload_date'),
                    'unload_date': old.get('unload_date')
                }
        except Exception:
            pass


@receiver(post_save, sender=Container)
def send_container_notifications_on_save(sender, instance, created, **kwargs):
    """
    Автоматически отправляет уведомления клиентам:
    - При установке planned_unload_date -> уведомление о планируемой разгрузке
    - При установке unload_date -> уведомление о фактической разгрузке
    """
    if not instance.pk:
        return
    
    # Получаем старые значения
    old_values = _old_notification_values.pop(instance.pk, {})
    old_planned_unload_date = old_values.get('planned_unload_date')
    old_unload_date = old_values.get('unload_date')
    
    # Проверяем нужно ли отправить уведомление о планируемой разгрузке
    should_notify_planned = False
    if instance.planned_unload_date:
        if created:
            should_notify_planned = True
        elif old_planned_unload_date is None:
            # Планируемая дата разгрузки была установлена впервые
            should_notify_planned = True
    
    # Проверяем нужно ли отправить уведомление о фактической разгрузке
    should_notify_unload = False
    if instance.unload_date:
        if created:
            should_notify_unload = True
        elif old_unload_date is None:
            # Дата разгрузки была установлена впервые
            should_notify_unload = True
    
    # Отправляем уведомления асинхронно после коммита транзакции
    if should_notify_planned:
        def send_planned_notifications():
            try:
                from core.services.email_service import ContainerNotificationService
                
                if not ContainerNotificationService.was_planned_notification_sent(instance):
                    sent, failed = ContainerNotificationService.send_planned_to_all_clients(instance)
                    if sent > 0:
                        logger.info(f"📧 Auto-sent planned unload notifications for {instance.number}: {sent} sent, {failed} failed")
                else:
                    logger.debug(f"Planned unload notifications already sent for {instance.number}")
            except Exception as e:
                logger.error(f"Failed to send planned unload notifications for {instance.number}: {e}")
        
        transaction.on_commit(send_planned_notifications)
    
    if should_notify_unload:
        def send_unload_notifications():
            try:
                from core.services.email_service import ContainerNotificationService
                
                if not ContainerNotificationService.was_unload_notification_sent(instance):
                    sent, failed = ContainerNotificationService.send_unload_to_all_clients(instance)
                    if sent > 0:
                        logger.info(f"📧 Auto-sent unload notifications for {instance.number}: {sent} sent, {failed} failed")
                else:
                    logger.debug(f"Unload notifications already sent for {instance.number}")
            except Exception as e:
                logger.error(f"Failed to send unload notifications for {instance.number}: {e}")
        
        transaction.on_commit(send_unload_notifications)