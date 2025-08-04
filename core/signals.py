from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Car, Payment, Invoice, Container
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from decimal import Decimal
import logging

logger = logging.getLogger('django')
@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, **kwargs):
    # При изменении контейнера — все машины внутри получают такой же статус
    instance.cars.update(status=instance.status)

@receiver(post_save, sender=Car)
def update_related_on_car_save(sender, instance, **kwargs):
    # Обновляем total_amount инвойсов без вызова save
    for invoice in instance.invoice_set.all():
        invoice.update_total_amount()
        # Сохраняем только total_amount и paid
        Invoice.objects.filter(pk=invoice.pk).update(
            total_amount=invoice.total_amount,
            paid=invoice.paid_amount >= invoice.total_amount
        )
        logger.debug(f"Updated invoice {invoice.number} total_amount: {invoice.total_amount}")

@receiver(post_save, sender=Payment)
def update_client_balance_on_payment_save(sender, instance, **kwargs):
    if instance.payer:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {
                    "model": "Client",
                    "id": instance.payer.id,
                    "debt": str(instance.payer.debt),
                    "cash_balance": str(instance.payer.cash_balance),
                    "card_balance": str(instance.payer.card_balance)
                }
            }
        )

@receiver(post_delete, sender=Payment)
def update_client_balance_on_payment_delete(sender, instance, **kwargs):
    if instance.payer:
        logger.info(f"Reverting deleted payment ID={instance.id}, Amount={instance.amount}, Type={instance.payment_type}, From Balance={instance.from_balance}, From Cash Balance={instance.from_cash_balance}, Payer={instance.payer.name}")
        amount = Decimal(str(instance.amount))

        # Откат основного платежа
        if instance.from_balance:
            if instance.from_cash_balance:
                instance.payer.cash_balance += amount
            else:
                instance.payer.card_balance += amount
        else:
            if instance.invoice:  # Откат платежа по инвойсу
                instance.payer.debt += amount  # Увеличиваем долг
                # Откат переплаты, если была
                excess = instance.invoice.balance if instance.invoice else 0
                if excess > 0:
                    if instance.payment_type == 'CASH' or (instance.payment_type == 'BALANCE' and instance.from_cash_balance):
                        instance.payer.cash_balance -= excess
                    elif instance.payment_type == 'CARD' or (instance.payment_type == 'BALANCE' and not instance.from_cash_balance):
                        instance.payer.card_balance -= excess
            else:  # Откат платежа без инвойса
                if instance.payment_type == 'CASH' or (instance.payment_type == 'BALANCE' and instance.from_cash_balance):
                    instance.payer.cash_balance -= amount
                elif instance.payment_type == 'CARD' or (instance.payment_type == 'BALANCE' and not instance.from_cash_balance):
                    instance.payer.card_balance -= amount

        instance.payer.save()
        logger.info(f"Client {instance.payer.name} updated after payment deletion: debt={instance.payer.debt}, cash_balance={instance.payer.cash_balance}, card_balance={instance.payer.card_balance}")

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {
                    "model": "Client",
                    "id": instance.payer.id,
                    "debt": str(instance.payer.debt),
                    "cash_balance": str(instance.payer.cash_balance),
                    "card_balance": str(instance.payer.card_balance)
                }
            }
        )