"""Сигналы для ``Container``: pre/post save + email-нотификации + GDrive-note.

Ранее эти три блока жили в разных секциях монолитного ``signals.py``,
но все они слушают ``Container.save()`` и работают со снимком
``_pre_save_*`` из единого ``pre_save``-обработчика, поэтому собраны в
один модуль.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import Container

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Container)
def save_old_container_values(sender, instance, **kwargs):
    if instance.unload_date and instance.status in ("FLOATING", "IN_PORT"):
        instance.status = "UNLOADED"
        logger.info("[PRE_SAVE] Auto-set status to UNLOADED for container %s", instance.number)

    update_fields = kwargs.get("update_fields")

    if instance.pk:
        try:
            old = (
                Container.objects.filter(pk=instance.pk).values("status", "unload_date", "planned_unload_date").first()
            )
            if old:
                # При update_fields подгружаем старые значения тоже, иначе
                # post_save воспринимает повторное сохранение как «первичную
                # установку даты» и ставит в очередь рассылку уведомлений
                # ещё раз. Сохранение container.save(update_fields=['status'])
                # из _update_container_status_if_all_transferred — типичный
                # путь, где это ломалось.
                instance._pre_save_values = old
                instance._pre_save_notification = {
                    "planned_unload_date": old.get("planned_unload_date"),
                    "unload_date": old.get("unload_date"),
                }
                old_status = old.get("status")
                if (
                    update_fields is None
                    and instance.status == "UNLOADED"
                    and old_status != "UNLOADED"
                    and not instance.unloaded_status_at
                ):
                    instance.unloaded_status_at = timezone.now()
            else:
                instance._pre_save_values = None
                instance._pre_save_notification = None
        except Exception as e:
            logger.error("[PRE_SAVE] Error: %s", e)
            instance._pre_save_values = None
            instance._pre_save_notification = None
    else:
        instance._pre_save_values = None
        instance._pre_save_notification = None
        if instance.status == "UNLOADED" and not instance.unloaded_status_at:
            instance.unloaded_status_at = timezone.now()


@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, created, **kwargs):
    """При изменении ``unload_date`` контейнера — синхронно обновляет
    ``Car.unload_date`` (быстрый bulk UPDATE одним SQL), а пересчёт
    ``total_price`` / ``days`` / ``storage_cost`` каждой машины уносит
    в Celery (``_enqueue_recalc_cars_total_price`` → on_commit + task).

    Раньше пересчёт делался прямо в HTTP-потоке: для контейнера со
    100 авто это блокировало запрос на N итераций ``calculate_total_price()``
    плюс ``bulk_update``. Теперь HTTP отдаёт ответ сразу, тяжёлая работа
    идёт в фоне с graceful inline-fallback при недоступности брокера.
    """
    from core.signals.service_catalog import _enqueue_recalc_cars_total_price

    old_values = getattr(instance, "_pre_save_values", None)
    instance._pre_save_values = None

    if not instance.pk:
        return

    if old_values:
        old_unload_date = old_values.get("unload_date")
        new_unload_date = instance.unload_date

        if old_unload_date != new_unload_date and new_unload_date is not None:
            logger.info(
                "[SIGNAL] unload_date changed for container %s: %s -> %s",
                instance.number,
                old_unload_date,
                new_unload_date,
            )
            try:
                out_of_sync = instance.container_cars.exclude(unload_date=new_unload_date).count()
                if out_of_sync == 0:
                    return

                # 1) Быстрый bulk UPDATE даты — один SQL, без N+1.
                updated_count = instance.container_cars.update(unload_date=new_unload_date)
                logger.info(
                    "[SIGNAL] Updated unload_date to %s for %d cars in container %s",
                    new_unload_date,
                    updated_count,
                    instance.number,
                )

                if updated_count > 0:
                    # 2) Пересчёт total_price/days/storage_cost — в Celery.
                    car_ids = list(instance.container_cars.values_list("pk", flat=True))
                    _enqueue_recalc_cars_total_price(car_ids)
            except Exception as e:
                logger.error(
                    "[SIGNAL] Failed to update cars for container %s: %s",
                    instance.number,
                    e,
                    exc_info=True,
                )


@receiver(post_save, sender=Container)
def send_container_notifications_on_save(sender, instance, created, **kwargs):
    """Шлёт письма клиентам о plan/unload-датах контейнера (через Celery)."""
    if not instance.pk:
        return

    old_values = getattr(instance, "_pre_save_notification", None) or {}
    instance._pre_save_notification = None
    old_planned = old_values.get("planned_unload_date")
    old_unload = old_values.get("unload_date")

    should_notify_planned = False
    if instance.planned_unload_date:
        if created or old_planned is None:
            should_notify_planned = True

    should_notify_unload = False
    if instance.unload_date:
        if created or old_unload is None:
            should_notify_unload = True

    if should_notify_planned:

        def _enqueue_planned():
            try:
                from core.tasks import send_planned_notifications_task

                send_planned_notifications_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import ContainerNotificationService

                if not ContainerNotificationService.was_planned_notification_sent(instance):
                    ContainerNotificationService.send_planned_to_all_clients(instance)

        transaction.on_commit(_enqueue_planned)

    if should_notify_unload:

        def _enqueue_unload():
            try:
                from core.tasks import send_unload_notifications_task

                send_unload_notifications_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import ContainerNotificationService

                if not ContainerNotificationService.was_unload_notification_sent(instance):
                    ContainerNotificationService.send_unload_to_all_clients(instance)

        transaction.on_commit(_enqueue_unload)


@receiver(post_save, sender=Container)
def auto_sync_photos_on_container_change(sender, instance, created, **kwargs):
    """Лог-маркер: реальный синк фото из GDrive выполняется cron'ом.

    Раньше тут была попытка дёрнуть синхронизацию прямо из сигнала; это
    рвало транзакцию длинными HTTP-запросами к GDrive. Оставлен только
    лог-сообщение для аудита (по нему cron знает, какие контейнеры
    разгрузились с прошлого запуска).
    """
    if not instance.pk:
        return
    if instance.status == "UNLOADED":
        logger.info(
            "Container %s: status UNLOADED. Photo sync will run via cron.",
            instance.number,
        )
