"""Сигналы ``TransportRequest`` для кабинета клиента (C5).

«Заявка сменила статус» — уведомление клиенту (email + Telegram) после
commit. Исключения: создание заявки (старого статуса нет) и возврат в
черновик — это действия самого клиента, писать о них нечего. Переход
DRAFT → SUBMITTED клиент тоже делает сам, но письмо-подтверждение с
номером заявки ему полезно, поэтому оно отправляется.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models.website import TransportRequest

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=TransportRequest, dispatch_uid="client_notify_request_pre_save")
def snapshot_request_status(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "status" not in update_fields:
        instance._client_notify_old_status = instance.status
        return
    if not instance.pk:
        instance._client_notify_old_status = None
        return
    instance._client_notify_old_status = (
        TransportRequest.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )


@receiver(post_save, sender=TransportRequest, dispatch_uid="client_notify_request_status")
def notify_client_on_request_status(sender, instance, created, **kwargs):
    old_status = getattr(instance, "_client_notify_old_status", None)
    if hasattr(instance, "_client_notify_old_status"):
        del instance._client_notify_old_status
    if created or old_status is None or old_status == instance.status:
        return
    if instance.status == "DRAFT":
        return

    request_id, new_status = instance.pk, instance.status

    def _queue():
        try:
            from core.tasks import send_request_status_notification_task

            send_request_status_notification_task.delay(request_id, new_status)
        except Exception as exc:
            logger.warning("[client_notify] Celery недоступен для заявки %s: %s", request_id, exc)

    transaction.on_commit(_queue)
