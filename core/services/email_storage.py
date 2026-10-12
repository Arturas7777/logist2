"""Ретеншен почтовых данных и отчёт о занятом ими месте.

Политика (``run_retention``):
  * у писем старше ``EMAIL_RETENTION_DAYS`` очищается ``body_html``
    (``body_text``, ``snippet``, метаданные и связи остаются);
  * файлы вложений таких писем удаляются, если к ним не обращались
    ``EMAIL_ATTACHMENT_CACHE_TTL_DAYS`` дней (mtime = последнее обращение,
    см. ``email_attachments``). По запросу файл будет снова скачан из Gmail;
  * если задан ``EMAIL_ATTACHMENT_CACHE_MAX_MB``, сверх лимита вытесняются
    самые давно не использованные файлы любых писем.

``storage_path`` в БД не трогается: он нужен, чтобы вернуть файл на место.
``EMAIL_RETENTION_DAYS=0`` полностью выключает очистку.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.utils import timezone

from core.services.email_attachments import storage_root

logger = logging.getLogger(__name__)

_BODY_BATCH = 2000
_MB = 1024 * 1024


@dataclass
class CachedFile:
    path: Path
    size: int
    mtime: float
    gmail_id: str


def _settings() -> tuple[int, int, int]:
    return (
        int(getattr(settings, "EMAIL_RETENTION_DAYS", 90)),
        int(getattr(settings, "EMAIL_ATTACHMENT_CACHE_TTL_DAYS", 7)),
        int(getattr(settings, "EMAIL_ATTACHMENT_CACHE_MAX_MB", 0)),
    )


def iter_cached_files(root: Path | None = None):
    """Файлы вложений: container_emails/YYYY/MM/<gmail_id>/<file>."""
    root = root or storage_root()
    if not root.is_dir():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            yield CachedFile(path=path, size=st.st_size, mtime=st.st_mtime, gmail_id=path.parent.name)


def _old_gmail_ids(cutoff) -> set[str]:
    from core.models.email import ContainerEmail

    return set(
        ContainerEmail.objects.filter(received_at__lt=cutoff).exclude(gmail_id="").values_list("gmail_id", flat=True)
    )


def clear_old_bodies(cutoff) -> int:
    """Очищает body_html у писем старше cutoff короткими пачками UPDATE."""
    from core.models.email import ContainerEmail

    total = 0
    qs = ContainerEmail.objects.filter(received_at__lt=cutoff).exclude(body_html="")
    while True:
        ids = list(qs.values_list("pk", flat=True)[:_BODY_BATCH])
        if not ids:
            return total
        total += ContainerEmail.objects.filter(pk__in=ids).update(body_html="")


def _remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("[email_storage] cannot delete %s: %s", path, exc)
        return False


def remove_empty_dirs(root: Path | None = None) -> int:
    root = root or storage_root()
    if not root.is_dir():
        return 0
    removed = 0
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def evict_attachment_cache(*, cutoff, ttl_days: int, max_bytes: int, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    stale_before = now - ttl_days * 86400
    old_ids = _old_gmail_ids(cutoff)

    files = list(iter_cached_files())
    kept: list[CachedFile] = []
    removed = freed = 0
    for f in files:
        if f.gmail_id in old_ids and f.mtime < stale_before:
            if _remove(f.path):
                removed += 1
                freed += f.size
        else:
            kept.append(f)

    removed_by_cap = 0
    if max_bytes > 0:
        total = sum(f.size for f in kept)
        for f in sorted(kept, key=lambda item: item.mtime):
            if total <= max_bytes:
                break
            if _remove(f.path):
                removed_by_cap += 1
                freed += f.size
                total -= f.size

    return {
        "files_removed_by_age": removed,
        "files_removed_by_size_cap": removed_by_cap,
        "freed_mb": round(freed / _MB, 2),
        "empty_dirs_removed": remove_empty_dirs(),
    }


def run_retention(now=None) -> dict:
    retention_days, ttl_days, max_mb = _settings()
    if retention_days <= 0:
        return {"status": "disabled"}
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days)
    report = {
        "retention_days": retention_days,
        "cache_ttl_days": ttl_days,
        "cache_max_mb": max_mb,
        "cutoff": cutoff.isoformat(),
        "body_html_cleared": clear_old_bodies(cutoff),
    }
    report.update(evict_attachment_cache(cutoff=cutoff, ttl_days=ttl_days, max_bytes=max_mb * _MB, now=now.timestamp()))
    logger.info("[email_storage] retention: %s", report)
    return report


def _table_size_bytes() -> int | None:
    if connection.vendor != "postgresql":
        return None
    from core.models.email import ContainerEmail

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_total_relation_size(%s)", [ContainerEmail._meta.db_table])
        row = cursor.fetchone()
    return int(row[0]) if row else None


def disk_usage(now=None) -> dict:
    """Фактическое место под почту: строки БД и файлы вложений."""
    from core.models.email import ContainerEmail

    retention_days, ttl_days, max_mb = _settings()
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days) if retention_days > 0 else None

    old_q = Q(received_at__lt=cutoff) if cutoff else Q(pk__in=[])
    agg = ContainerEmail.objects.aggregate(
        emails=Count("pk"),
        body_html_chars=Sum(Length("body_html")),
        body_text_chars=Sum(Length("body_text")),
        body_html_chars_older_than_retention=Sum(Length("body_html"), filter=old_q),
    )

    old_ids = _old_gmail_ids(cutoff) if cutoff else set()
    stale_before = now.timestamp() - ttl_days * 86400
    files = count_old = 0
    total_bytes = old_bytes = reclaimable_bytes = 0
    for f in iter_cached_files():
        files += 1
        total_bytes += f.size
        if f.gmail_id in old_ids:
            count_old += 1
            old_bytes += f.size
            if f.mtime < stale_before:
                reclaimable_bytes += f.size

    table_bytes = _table_size_bytes()
    root = storage_root()
    fs_path = root if root.exists() else Path(settings.MEDIA_ROOT)
    while not fs_path.exists() and fs_path != fs_path.parent:
        fs_path = fs_path.parent
    fs = shutil.disk_usage(fs_path)

    return {
        "settings": {
            "retention_days": retention_days,
            "cache_ttl_days": ttl_days,
            "cache_max_mb": max_mb,
        },
        "db": {
            "emails": agg["emails"] or 0,
            "table_total_mb": round(table_bytes / _MB, 2) if table_bytes is not None else None,
            "body_html_chars": agg["body_html_chars"] or 0,
            "body_text_chars": agg["body_text_chars"] or 0,
            "body_html_chars_older_than_retention": agg["body_html_chars_older_than_retention"] or 0,
        },
        "attachments": {
            "path": str(root),
            "files": files,
            "total_mb": round(total_bytes / _MB, 2),
            "files_older_than_retention": count_old,
            "mb_older_than_retention": round(old_bytes / _MB, 2),
            "mb_reclaimable_now": round(reclaimable_bytes / _MB, 2),
        },
        "filesystem": {
            "path": str(fs_path),
            "total_gb": round(fs.total / 1024**3, 2),
            "free_gb": round(fs.free / 1024**3, 2),
        },
    }
