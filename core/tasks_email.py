"""Celery-задачи для синхронизации Gmail ↔ ContainerEmail.

Регистрация в beat_schedule — в logist2/celery.py.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Простой advisory-lock через cache, чтобы две параллельные периодические задачи
# не дёргали Gmail одновременно (quota + гонка за historyId).
_LOCK_KEY = 'gmail_sync_lock'
_LOCK_TIMEOUT_SEC = 10 * 60


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=120,
    soft_time_limit=90,
)
def gmail_mark_read_task(self, gmail_ids: list[str]) -> int:
    """Асинхронно снимает лейбл UNREAD с писем в Gmail.

    Вызывается из ``email_mark_read`` / ``email_mark_container_read`` после
    того, как пользователь отметил письмо(а) прочитанным в карточке
    контейнера — чтобы состояние синхронизировалось с Gmail.

    ``gmail_ids`` — список Gmail message.id (НЕ ContainerEmail.pk).
    Идемпотентно: если лейбла уже нет, API ничего не меняет.
    """
    if not gmail_ids:
        return 0
    try:
        from core.services.gmail_client import GmailApiClient, GmailNotConfigured
        try:
            client = GmailApiClient()
        except GmailNotConfigured:
            logger.info('[gmail_mark_read_task] Gmail not configured — skip.')
            return 0
        return client.mark_messages_read(gmail_ids)
    except Exception as exc:
        logger.warning(
            '[gmail_mark_read_task] failed for %d ids: %s', len(gmail_ids), exc,
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return 0
        return 0


@shared_task(bind=True, max_retries=0, time_limit=600, soft_time_limit=540)
def sync_emails_from_gmail(self, force_full: bool = False) -> dict:
    """Периодическая задача: тянет новые письма из Gmail и сопоставляет с контейнерами.

    Запуск:
      * автоматически — celery beat (см. logist2/celery.py, расписание */5 мин)
      * вручную — из админки через POST /admin/emails/sync/

    ``force_full=True`` — форсировать полный re-sync вместо инкремента
    (сбрасывает зависимость от last_history_id для этого запуска).
    """
    from core.services.email_ingest import sync_mailbox

    if not cache.add(_LOCK_KEY, '1', _LOCK_TIMEOUT_SEC):
        logger.info('[sync_emails_from_gmail] Another sync in progress — skip.')
        return {'status': 'locked'}

    try:
        report = sync_mailbox(force_full=force_full)
        return report.as_dict()
    except Exception as exc:
        logger.exception('[sync_emails_from_gmail] Unhandled error: %s', exc)
        return {'status': 'error', 'error': str(exc)[:500]}
    finally:
        cache.delete(_LOCK_KEY)
