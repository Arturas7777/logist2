"""Сигналы ``Car`` для кабинета клиента: «авто передано» (C5) и история статусов (C2).

Не трогаем ``signals/car.py`` — здесь только тонкая обвязка:

* ``pre_save`` — снимок старого статуса (один ``values_list`` запрос; при
  ``save(update_fields=...)`` без ``status`` запрос не делается);
* ``post_save`` — если статус изменился: запись в ``CarStatusHistory`` и, при
  переходе в TRANSFERRED, уведомление клиенту после commit.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models import Car
from core.models.status_history import CarStatusHistory

logger = logging.getLogger(__name__)

_ATTR = "_client_notify_old_status"


@receiver(pre_save, sender=Car, dispatch_uid="client_notify_car_pre_save")
def snapshot_car_status(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "status" not in update_fields:
        setattr(instance, _ATTR, instance.status)
        return
    if not instance.pk:
        setattr(instance, _ATTR, None)
        return
    setattr(instance, _ATTR, Car.objects.filter(pk=instance.pk).values_list("status", flat=True).first())


@receiver(post_save, sender=Car, dispatch_uid="client_notify_car_post_save")
def on_car_status_changed(sender, instance, created, **kwargs):
    old_status = getattr(instance, _ATTR, None)
    if hasattr(instance, _ATTR):
        delattr(instance, _ATTR)

    if not created and old_status == instance.status:
        return

    try:
        CarStatusHistory.objects.create(
            car=instance,
            status=instance.status,
            previous_status=old_status or "",
            source=CarStatusHistory.SOURCE_SIGNAL,
        )
    except Exception as exc:  # журнал — вспомогательный, сохранение авто не ломаем
        logger.warning("Не удалось записать историю статуса авто %s: %s", instance.pk, exc)

    if created or instance.status != "TRANSFERRED" or not instance.client_id:
        return

    car_id = instance.pk

    def _queue():
        try:
            from core.tasks import send_car_transferred_notification_task

            send_car_transferred_notification_task.delay(car_id)
        except Exception as exc:
            logger.warning("[client_notify] Celery недоступен для авто %s: %s", car_id, exc)

    transaction.on_commit(_queue)
