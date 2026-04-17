"""
Car lifecycle service — orchestrates side-effects that were previously
embedded in ``Car.save()``.

Call ``after_car_save()`` from model ``save()`` or admin ``save_model()``
instead of scattering logic across signals and the model itself.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)


def recalculate_car_price(car) -> None:
    """Recalculate total_price, days, storage_cost and persist via UPDATE."""
    from core.models import Car

    try:
        car.calculate_total_price()
        Car.objects.filter(pk=car.pk).update(
            total_price=car.total_price,
            days=car.days,
            storage_cost=car.storage_cost,
        )
    except Exception as e:
        logger.error('Failed to calculate total price for car %s: %s', car.vin, e)


def check_container_status(car) -> None:
    """If all cars in the container are TRANSFERRED, update container status."""
    if not car.container_id:
        return
    try:
        car.container.check_and_update_status_from_cars()
    except Exception as e:
        logger.error('Failed to check container status for car %s: %s', car.pk, e)


def send_car_ws_notification(car) -> None:
    """Enqueue a WebSocket notification after commit."""
    car_id = car.pk
    payload = {
        'type': 'data_update',
        'data': {
            'model': 'Car',
            'id': car_id,
            'status': car.status,
            'storage_cost': str(car.storage_cost),
            'days': car.days,
            'price': str(car.total_price),
        },
    }

    def _notify():
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)('updates', payload)
        except Exception as e:
            logger.error('Failed to send WebSocket notification for car %s: %s', car_id, e)

    transaction.on_commit(_notify)


def after_car_save(car, *, is_new: bool = False) -> None:
    """
    Central entry-point for post-save side-effects.

    Called from ``Car.save()`` after ``super().save()``.
    """
    if not car.pk:
        return

    recalculate_car_price(car)
    check_container_status(car)
    send_car_ws_notification(car)
