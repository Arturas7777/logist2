"""Celery-задачи для очистки старых email-данных (retention policy).

Цель: контролировать рост хранилища на сервере (~18 GB свободно из 50).
Политика:
  - body_html старше EMAIL_RETENTION_DAYS дней очищается (остаётся body_text + snippet).
  - Закэшированные вложения старше EMAIL_RETENTION_DAYS удаляются с диска.
  - Метаданные писем (from/to/subject/received_at/links) остаются навсегда.

Переменные окружения (см. .env):
  EMAIL_RETENTION_DAYS (default: 90)
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, time_limit=600, soft_time_limit=540)
def cleanup_old_email_data(self) -> dict:
    """Периодическая задача: очистка body_html и cached attachments старых писем.

    Запуск: celery beat (расписание в logist2/celery.py).
    """
    retention_days = int(getattr(settings, "EMAIL_RETENTION_DAYS", 90))
    cutoff = timezone.now() - timedelta(days=retention_days)

    logger.info(
        "[cleanup_old_email_data] Start: retention=%d days, cutoff=%s",
        retention_days,
        cutoff.isoformat(),
    )

    report = {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "body_html_cleared": 0,
        "attachments_removed": 0,
        "disk_freed_mb": 0,
    }

    # ── 1. Очистка body_html (оставляем body_text + snippet) ─────────────────
    from core.models.email import ContainerEmail

    old_emails = ContainerEmail.objects.filter(
        received_at__lt=cutoff,
    ).exclude(body_html="")

    count = 0
    for email in old_emails.iterator(chunk_size=500):
        email.body_html = ""
        email.save(update_fields=["body_html"])
        count += 1

    report["body_html_cleared"] = count
    logger.info("[cleanup_old_email_data] Cleared body_html from %d emails", count)

    # ── 2. Удаление закэшированных вложений ──────────────────────────────────
    media_root = Path(settings.MEDIA_ROOT)
    removed_count = 0
    freed_bytes = 0

    old_with_attachments = ContainerEmail.objects.filter(
        received_at__lt=cutoff,
    ).exclude(attachments_json=[])

    for email in old_with_attachments.iterator(chunk_size=200):
        if not email.attachments_json:
            continue

        modified = False
        for att_meta in email.attachments_json:
            storage_path = att_meta.get("storage_path", "")
            if not storage_path:
                continue

            abs_path = media_root / storage_path
            if not abs_path.exists():
                # Уже удалён или не существовал
                continue

            try:
                file_size = abs_path.stat().st_size
                abs_path.unlink()
                removed_count += 1
                freed_bytes += file_size
                # Очищаем storage_path в метаданных
                att_meta["storage_path"] = ""
                modified = True
            except OSError as exc:
                logger.warning(
                    "[cleanup_old_email_data] Failed to delete %s: %s",
                    abs_path,
                    exc,
                )

        if modified:
            email.save(update_fields=["attachments_json"])

    report["attachments_removed"] = removed_count
    report["disk_freed_mb"] = round(freed_bytes / (1024 * 1024), 2)

    logger.info(
        "[cleanup_old_email_data] Removed %d cached attachments, freed %.2f MB",
        removed_count,
        report["disk_freed_mb"],
    )

    return report


@shared_task(bind=True, max_retries=0, time_limit=120)
def report_email_disk_usage(self) -> dict:
    """Отчёт о текущем использовании диска email-данными.

    Не выполняет очистку — только считает размеры для мониторинга.
    """
    from core.models.email import ContainerEmail

    media_root = Path(settings.MEDIA_ROOT)
    email_dir = media_root / "container_emails"

    if not email_dir.exists():
        return {
            "total_emails": 0,
            "emails_with_attachments": 0,
            "cached_attachments_count": 0,
            "cached_attachments_size_mb": 0,
        }

    # Подсчёт размера закэшированных вложений
    total_size = 0
    cached_count = 0

    for root, dirs, files in email_dir.walk():
        for file in files:
            path = root / file
            try:
                total_size += path.stat().st_size
                cached_count += 1
            except OSError:
                pass

    total_emails = ContainerEmail.objects.count()
    emails_with_atts = ContainerEmail.objects.exclude(attachments_json=[]).count()

    return {
        "total_emails": total_emails,
        "emails_with_attachments": emails_with_atts,
        "cached_attachments_count": cached_count,
        "cached_attachments_size_mb": round(total_size / (1024 * 1024), 2),
    }
