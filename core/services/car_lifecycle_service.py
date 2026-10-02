"""
Car lifecycle service — orchestrates side-effects that were previously
embedded in ``Car.save()``.

Call ``after_car_save()`` from model ``save()`` or admin ``save_model()``
instead of scattering logic across signals and the model itself.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
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
        logger.error("Failed to calculate total price for car %s: %s", car.vin, e)


def check_container_status(car) -> None:
    """If all cars in the container are TRANSFERRED, update container status."""
    if not car.container_id:
        return
    try:
        car.container.check_and_update_status_from_cars()
    except Exception as e:
        logger.error("Failed to check container status for car %s: %s", car.pk, e)


# Денормализованные поля Car: их пишут фоновые пересчёты
# (recalculate_cars_total_price_task, refresh_unloaded_storage_daily,
# apply_car_service_edits). Сохранение ТОЛЬКО этих полей — не событие для
# UI-рассылки: иначе ежедневный пересчёт 40+ машин = 40+ group_send подряд (Q8).
CAR_WS_DENORM_FIELDS = frozenset({"total_price", "days", "storage_cost", "current_price", "updated_at"})

# Окно дебаунса рассылки по одной машине, сек. Сохранение карточки из админки
# даёт несколько Car.save подряд (форма → тариф → финальный пересчёт) — в
# WebSocket уходит одно сообщение.
CAR_WS_DEBOUNCE_SECONDS = 2


def should_send_car_ws(car, *, update_fields=None, raw=False) -> bool:
    """Нужно ли слать WS-уведомление для этого сохранения Car."""
    if raw or getattr(car, "_bulk_updating", False) or getattr(car, "_creating_services", False):
        return False
    if update_fields is not None and set(update_fields) <= CAR_WS_DENORM_FIELDS:
        return False
    return True


def send_car_ws_notification(car, *, update_fields=None, raw=False) -> None:
    """Enqueue a WebSocket notification after commit.

    Q8: пропускаем «тихие» сохранения (только денормализованные поля,
    ``_bulk_updating``, ``raw``) и дебаунсим по ``car_id`` через
    ``cache.add`` — повторное сохранение той же машины в течение
    :data:`CAR_WS_DEBOUNCE_SECONDS` сообщение не шлёт.
    """
    if not should_send_car_ws(car, update_fields=update_fields, raw=raw):
        return

    car_id = car.pk
    payload = {
        "type": "data_update",
        "data": {
            "model": "Car",
            "id": car_id,
            "status": car.status,
            "storage_cost": str(car.storage_cost),
            "days": car.days,
            "price": str(car.total_price),
        },
    }

    def _notify():
        try:
            # add() атомарен: True только у первого за окно дебаунса.
            if not cache.add(f"ws:car:{car_id}", 1, CAR_WS_DEBOUNCE_SECONDS):
                return
        except Exception:
            # Кэш недоступен — лучше лишнее сообщение, чем потерянное.
            logger.debug("WS debounce cache unavailable for car %s", car_id, exc_info=True)
        try:
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)("updates", payload)
        except Exception as e:
            logger.error("Failed to send WebSocket notification for car %s: %s", car_id, e)

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
