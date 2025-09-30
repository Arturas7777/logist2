from django.db.models.signals import post_save, post_delete, pre_delete, pre_save
from django.dispatch import receiver
from .models import Car, PaymentOLD, InvoiceOLD, Container, WarehouseService, LineService, CarrierService, CarService, DeletedCarService
from django.db.models import Sum
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction
from decimal import Decimal
import logging

logger = logging.getLogger('django')
@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, **kwargs):
    # При изменении контейнера — все машины внутри получают такой же статус
    # ОПТИМИЗИРОВАНО: Использует bulk_update вместо цикла
    if not instance.pk:
        return
    
    try:
        # Массовое обновление одним запросом вместо N
        instance.container_cars.update(status=instance.status)
        
        # Отправляем batch WebSocket уведомление
        from core.utils import WebSocketBatcher
        for car in instance.container_cars.only('id', 'status'):
            WebSocketBatcher.add('Car', car.id, {'status': car.status})
        WebSocketBatcher.flush()
        
    except Exception as e:
        logger.error(f"Failed to update cars status for container {instance.id}: {e}")

@receiver(post_save, sender=Car)
def update_related_on_car_save(sender, instance, **kwargs):
    # Обновляем total_amount инвойсов МАССОВО через bulk_update
    # Проверяем, что у экземпляра есть первичный ключ
    if not instance.pk:
        return
    
    try:
        # Получаем все связанные инвойсы одним запросом
        invoices = list(instance.invoiceold_set.all())
        
        if not invoices:
            return
        
        # Обновляем все инвойсы в памяти
        invoices_to_update = []
        for invoice in invoices:
            invoice.update_total_amount()
            invoices_to_update.append(invoice)
        
        # Одно массовое обновление вместо N отдельных
        if invoices_to_update:
            InvoiceOLD.objects.bulk_update(
                invoices_to_update,
                ['total_amount', 'paid'],
                batch_size=50
            )
            logger.debug(f"Bulk updated {len(invoices_to_update)} invoices for car {instance.id}")
    except Exception as e:
        logger.error(f"Failed to update invoices for car {instance.id}: {e}")


@receiver(post_save, sender=InvoiceOLD)
def update_balances_on_invoice_save(sender, instance, created, **kwargs):
    """Обновляет балансы отправителя и получателя при создании/изменении инвойса"""
    try:
        if created:
            # При создании нового инвойса обновляем сумму и балансы
            logger.info(f"New invoice created: {instance.number}, amount: {instance.total_amount}")
            # Обновляем сумму инвойса на основе автомобилей
            instance.update_total_amount()
        
        # Обновляем балансы отправителя и получателя
        if instance.from_entity:
            instance.from_entity.update_balance_from_invoices()
            logger.debug(f"Updated from_entity balance: {instance.from_entity}")
        
        if instance.to_entity:
            instance.to_entity.update_balance_from_invoices()
            logger.debug(f"Updated to_entity balance: {instance.to_entity}")
            
    except Exception as e:
        logger.error(f"Error updating balances on invoice save: {e}")


@receiver(post_delete, sender=InvoiceOLD)
def update_balances_on_invoice_delete(sender, instance, **kwargs):
    """Обновляет балансы отправителя и получателя при удалении инвойса"""
    try:
        # Обновляем балансы отправителя и получателя
        if instance.from_entity:
            instance.from_entity.update_balance_from_invoices()
            logger.debug(f"Updated from_entity balance after delete: {instance.from_entity}")
        
        if instance.to_entity:
            instance.to_entity.update_balance_from_invoices()
            logger.debug(f"Updated to_entity balance after delete: {instance.to_entity}")
            
    except Exception as e:
        logger.error(f"Error updating balances on invoice delete: {e}")

@receiver(post_save, sender=PaymentOLD)
def update_balance_on_payment_save(sender, instance, **kwargs):
    """Обрабатывает сохранение платежа с новой системой балансов"""
    try:
        # Проверяем, является ли это пополнением собственного баланса
        is_self_payment = (instance.sender == instance.recipient and 
                          instance.sender is not None and 
                          instance.payment_type in ['CASH', 'CARD'])
        
        if is_self_payment:
            # Пополнение собственного баланса - только увеличиваем
            if hasattr(instance.sender, 'cash_balance') and hasattr(instance.sender, 'card_balance'):
                if instance.payment_type == 'CASH':
                    instance.sender.cash_balance += instance.amount
                    # Проверяем, что баланс не стал отрицательным
                    if instance.sender.cash_balance < 0:
                        instance.sender.cash_balance = Decimal('0.00')
                        logger.warning(f"Наличный баланс {instance.sender} не может быть отрицательным. Установлен в 0.")
                elif instance.payment_type == 'CARD':
                    instance.sender.card_balance += instance.amount
                    # Проверяем, что баланс не стал отрицательным
                    if instance.sender.card_balance < 0:
                        instance.sender.card_balance = Decimal('0.00')
                        logger.warning(f"Безналичный баланс {instance.sender} не может быть отрицательным. Установлен в 0.")
                instance.sender.save()
                logger.info(f"Пополнен {instance.payment_type} баланс для {instance.sender}: +{instance.amount}")
        else:
            # Обычный перевод между разными участниками
            # Обрабатываем отправителя (списание с баланса)
            if instance.sender:
                if hasattr(instance.sender, 'cash_balance') and hasattr(instance.sender, 'card_balance'):
                    if instance.payment_type == 'CASH':
                        # Списание с наличного баланса
                        if instance.sender.cash_balance >= instance.amount:
                            instance.sender.cash_balance -= instance.amount
                        else:
                            # Если наличного не хватает, списываем все что есть
                            instance.sender.cash_balance = Decimal('0.00')
                            logger.warning(f"Недостаточно наличного баланса для списания {instance.amount}. Баланс обнулен.")
                        instance.sender.save()
                    elif instance.payment_type == 'CARD':
                        # Списание с безналичного баланса
                        if instance.sender.card_balance >= instance.amount:
                            instance.sender.card_balance -= instance.amount
                        else:
                            # Если безналичного не хватает, списываем все что есть
                            instance.sender.card_balance = Decimal('0.00')
                            logger.warning(f"Недостаточно безналичного баланса для списания {instance.amount}. Баланс обнулен.")
                        instance.sender.save()
                    elif instance.payment_type == 'FROM_BALANCE':
                        # Списание с соответствующего баланса (определяем по описанию или другим параметрам)
                        # Сначала пытаемся списать с наличного, затем с безналичного
                        remaining_amount = instance.amount
                        
                        # Списание с наличного баланса
                        if instance.sender.cash_balance > 0:
                            if instance.sender.cash_balance >= remaining_amount:
                                instance.sender.cash_balance -= remaining_amount
                                remaining_amount = Decimal('0.00')
                            else:
                                remaining_amount -= instance.sender.cash_balance
                                instance.sender.cash_balance = Decimal('0.00')
                        
                        # Если наличного не хватило, списываем с безналичного
                        if remaining_amount > 0 and instance.sender.card_balance > 0:
                            if instance.sender.card_balance >= remaining_amount:
                                instance.sender.card_balance -= remaining_amount
                                remaining_amount = Decimal('0.00')
                            else:
                                remaining_amount -= instance.sender.card_balance
                                instance.sender.card_balance = Decimal('0.00')
                        
                        # Проверяем, что балансы не стали отрицательными
                        if instance.sender.cash_balance < 0:
                            instance.sender.cash_balance = Decimal('0.00')
                            logger.warning(f"Наличный баланс {instance.sender} не может быть отрицательным. Установлен в 0.")
                        if instance.sender.card_balance < 0:
                            instance.sender.card_balance = Decimal('0.00')
                            logger.warning(f"Безналичный баланс {instance.sender} не может быть отрицательным. Установлен в 0.")
                        
                        # Проверяем, что списали всю сумму
                        if remaining_amount > 0:
                            logger.warning(f"Недостаточно средств для списания {instance.amount}. Осталось: {remaining_amount}")
                        
                        instance.sender.save()
                    logger.info(f"Списан {instance.payment_type} баланс для {instance.sender}: -{instance.amount}")
            
            # Обрабатываем получателя (пополнение баланса)
            if instance.recipient:
                if hasattr(instance.recipient, 'cash_balance') and hasattr(instance.recipient, 'card_balance'):
                    if instance.payment_type == 'CASH':
                        # Пополнение наличного баланса
                        instance.recipient.cash_balance += instance.amount
                        # Проверяем, что баланс не стал отрицательным
                        if instance.recipient.cash_balance < 0:
                            instance.recipient.cash_balance = Decimal('0.00')
                            logger.warning(f"Наличный баланс получателя {instance.recipient} не может быть отрицательным. Установлен в 0.")
                        instance.recipient.save()
                    elif instance.payment_type == 'CARD':
                        # Пополнение безналичного баланса
                        instance.recipient.card_balance += instance.amount
                        # Проверяем, что баланс не стал отрицательным
                        if instance.recipient.card_balance < 0:
                            instance.recipient.card_balance = Decimal('0.00')
                            logger.warning(f"Безналичный баланс получателя {instance.recipient} не может быть отрицательным. Установлен в 0.")
                        instance.recipient.save()
                    elif instance.payment_type == 'BALANCE':
                        # Пополнение баланса (по умолчанию наличный)
                        instance.recipient.cash_balance += instance.amount
                        # Проверяем, что баланс не стал отрицательным
                        if instance.recipient.cash_balance < 0:
                            instance.recipient.cash_balance = Decimal('0.00')
                            logger.warning(f"Баланс получателя {instance.recipient} не может быть отрицательным. Установлен в 0.")
                        instance.recipient.save()
                    logger.info(f"Зачислен {instance.payment_type} баланс для {instance.recipient}: +{instance.amount}")
        
        # Обновляем инвойс-балансы на основе реальных данных
        if instance.sender:
            instance.sender.update_balance_from_invoices()
        if instance.recipient:
            instance.recipient.update_balance_from_invoices()
        
        # Отправляем уведомления через WebSocket
        if instance.sender and hasattr(instance.sender, 'invoice_balance'):
            def _notify():
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    "updates",
                    {
                        "type": "data_update",
                        "model": "Client",
                        "id": instance.sender.id,
                        "invoice_balance": str(instance.sender.invoice_balance),
                        "cash_balance": str(instance.sender.cash_balance),
                        "card_balance": str(instance.sender.card_balance)
                    }
                )
            try:
                _notify()
            except Exception as e:
                logger.error(f"Failed to send WebSocket notification: {e}")
        
    except Exception as e:
        logger.error(f"Error updating balances on payment save: {e}")


@receiver(pre_delete, sender=InvoiceOLD)
def adjust_client_on_invoice_delete(sender, instance, **kwargs):
    """При удалении входящего инвойса откатываем влияние на долг клиента.
    Считаем net-долг как total_amount - paid_amount и уменьшаем клиентский долг на эту величину.
    Используем pre_delete, чтобы успеть посчитать paid_amount до обнуления FK у платежей.
    """
    try:
        if not instance.client or instance.is_outgoing:
            return
        total = Decimal(str(instance.total_amount or 0))
        paid = PaymentOLD.objects.filter(invoice=instance).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        paid = Decimal(str(paid))
        delta = total - paid
        if delta != 0:
            instance.client.debt = Decimal(str(instance.client.debt or 0)) - delta
            instance.client.save()
    except Exception as e:
        logger.error(f"Failed to adjust client debt on invoice delete {instance.id}: {e}")


def _maybe_zero_client(client):
    try:
        if not client:
            return
        has_invoices = client.invoiceold_set.exists()

        has_payments = PaymentOLD.objects.filter(
            from_client=client
        ).exists()
        if not has_invoices and not has_payments:
            client.debt = Decimal('0.00')
            client.cash_balance = Decimal('0.00')
            client.card_balance = Decimal('0.00')
            client.save()
    except Exception:
        pass


def _recalculate_client_debt(client):
    """Пересчитывает долг клиента на основе инвойс-баланса"""
    try:
        if not client:
            return
            
        # Сумма всех входящих инвойсов клиента
        total_invoiced = client.invoiceold_set.filter(
            is_outgoing=False
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Сумма всех платежей клиента по инвойсам (включая списания с баланса)
        total_paid = PaymentOLD.objects.filter(
            from_client=client,
            invoice__isnull=False  # Только платежи по инвойсам
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Инвойс-баланс = инвойсы - платежи
        real_debt = total_invoiced - total_paid
        
        # Обновляем поле debt если нужно
        if client.debt != real_debt:
            logger.info(f"Updating client {client.name} debt from {client.debt} to {real_debt}")
            client.debt = real_debt
            client.save(update_fields=['debt'])
            
    except Exception as e:
        logger.error(f"Error recalculating client debt: {e}")


@receiver(post_delete, sender=InvoiceOLD)
def maybe_zero_after_invoice_delete(sender, instance, **kwargs):
    if instance.client:
        _recalculate_client_debt(instance.client)
        _maybe_zero_client(instance.client)

@receiver(post_delete, sender=PaymentOLD)
def update_client_balance_on_payment_delete(sender, instance, **kwargs):
    """Обрабатывает удаление платежа с новой системой балансов"""
    try:
        # Обрабатываем отправителя (возврат на баланс)
        if instance.sender:
            if hasattr(instance.sender, 'cash_balance') and hasattr(instance.sender, 'card_balance'):
                if instance.payment_type == 'CASH':
                    # Возврат на наличный баланс
                    instance.sender.cash_balance += instance.amount
                    instance.sender.save()
                elif instance.payment_type == 'CARD':
                    # Возврат на безналичный баланс
                    instance.sender.card_balance += instance.amount
                    instance.sender.save()
                elif instance.payment_type == 'FROM_BALANCE':
                    # Возврат на баланс (по умолчанию наличный)
                    instance.sender.cash_balance += instance.amount
                    instance.sender.save()
        
        # Обрабатываем получателя (списание с баланса)
        if instance.recipient:
            if hasattr(instance.recipient, 'cash_balance') and hasattr(instance.recipient, 'card_balance'):
                if instance.payment_type == 'CASH':
                    # Списание с наличного баланса
                    instance.recipient.cash_balance -= instance.amount
                    instance.recipient.save()
                elif instance.payment_type == 'CARD':
                    # Списание с безналичного баланса
                    instance.recipient.card_balance -= instance.amount
                    instance.recipient.save()
                elif instance.payment_type == 'BALANCE':
                    # Списание с баланса (по умолчанию наличный)
                    instance.recipient.cash_balance -= instance.amount
                    instance.recipient.save()
        
        # Обновляем инвойс-балансы на основе реальных данных
        if instance.sender:
            instance.sender.update_balance_from_invoices()
        if instance.recipient:
            instance.recipient.update_balance_from_invoices()
        
        # Отправляем уведомления через WebSocket
        if instance.sender and hasattr(instance.sender, 'invoice_balance'):
            def _notify():
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    "updates",
                    {
                        "type": "data_update",
                        "model": "Client",
                        "id": instance.sender.id,
                        "invoice_balance": str(instance.sender.invoice_balance),
                        "cash_balance": str(instance.sender.cash_balance),
                        "card_balance": str(instance.sender.card_balance)
                    }
                )
            try:
                _notify()
            except Exception as e:
                logger.error(f"Failed to send WebSocket notification: {e}")
        
    except Exception as e:
        logger.error(f"Error updating balances on payment delete: {e}")


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