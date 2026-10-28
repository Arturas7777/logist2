"""Claim-записи уведомлений: одно уведомление клиенту — одна отправка.

Порядок: :func:`claim` (строка PENDING под ``UniqueConstraint``) → отправка
вне транзакции → :func:`finish` (SENT / FAILED). Повторно взять можно FAILED
(retry задачи) и PENDING старше :data:`STALE_AFTER` (воркер упал посреди
отправки).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)


def claim(*, channel, notification_type, client, container=None, car=None):
    """Берёт уведомление в работу. None — уже отправлено или отправляется."""
    from core.models_website import NotificationClaim

    lookup = {
        "channel": channel,
        "notification_type": notification_type,
        "client": client,
        "container": container,
        "car": car,
    }
    now = timezone.now()
    try:
        with transaction.atomic():
            obj, created = NotificationClaim.objects.get_or_create(**lookup, defaults={"claimed_at": now})
    except IntegrityError:
        return None
    if created:
        return obj

    reclaimable = Q(status=NotificationClaim.STATUS_FAILED) | Q(
        status=NotificationClaim.STATUS_PENDING, claimed_at__lt=now - STALE_AFTER
    )
    updated = (
        NotificationClaim.objects.filter(pk=obj.pk)
        .filter(reclaimable)
        .update(status=NotificationClaim.STATUS_PENDING, claimed_at=now, attempts=F("attempts") + 1)
    )
    if not updated:
        return None
    obj.refresh_from_db()
    return obj


def send_once(send, *, channel, notification_type, client, container=None, car=None):
    """Claim → ``send()`` → finish. None — пропуск (уже отправлено/отправляется)."""
    claim_obj = claim(channel=channel, notification_type=notification_type, client=client, container=container, car=car)
    if claim_obj is None:
        return None
    try:
        ok = bool(send())
    except Exception as exc:
        finish(claim_obj, success=False, error=str(exc))
        raise
    finish(claim_obj, success=ok, error="" if ok else "Отправка не удалась (см. лог уведомлений)")
    return ok


def finish(claim_obj, *, success, error=""):
    from core.models_website import NotificationClaim

    if claim_obj is None:
        return
    claim_obj.status = NotificationClaim.STATUS_SENT if success else NotificationClaim.STATUS_FAILED
    claim_obj.last_error = "" if success else (error or "")[:2000]
    claim_obj.save(update_fields=["status", "last_error", "updated_at"])
