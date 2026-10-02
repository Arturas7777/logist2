"""Celery-задачи для страницы /admin/system-monitor/ и инфраструктурных
healthcheck'ов.

Расписание подключается в `logist2/celery.py` в `beat_schedule`:
- `collect_system_metrics` — каждые 5 минут (288 точек/день)
- `ping_uptime` — каждую минуту (1440 точек/день)
- `cleanup_monitoring_tables` — раз в день в 04:40 (Q12: UptimeCheck 30 дн.,
  SystemMetric 90 дн., AgentRun 90 дн., NotificationLog 180 дн.; пороги в
  settings `*_RETENTION_DAYS`, удаление батчами по 5000)
- `check_backup_freshness` — раз в день в 04:15 (после ночного бэкапа в 03:30)
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models_monitoring import SystemMetric, UptimeCheck
from .services.system_monitor import collect_snapshot, ping_health

logger = logging.getLogger(__name__)


@shared_task(name="core.tasks_monitoring.collect_system_metrics")
def collect_system_metrics() -> dict:
    """Снимает текущие метрики и сохраняет одну строку SystemMetric."""
    snap = collect_snapshot()

    mem = snap.get("memory") or {}
    disk = snap.get("disk") or {}
    cpu = snap.get("cpu") or {}
    pg = snap.get("postgres") or {}
    redis_info = snap.get("redis") or {}
    proc_groups = (snap.get("processes") or {}).get("groups") or {}

    def _grp_rss(name: str) -> int:
        g = proc_groups.get(name) or {}
        return int(g.get("rss_mb", 0))

    metric = SystemMetric.objects.create(
        cpu_percent=float(cpu.get("percent") or 0),
        load_avg_1=cpu.get("load_avg_1"),
        mem_total_mb=int(mem.get("total_mb") or 0),
        mem_used_mb=int(mem.get("used_mb") or 0),
        mem_available_mb=int(mem.get("available_mb") or 0),
        mem_percent=float(mem.get("percent") or 0),
        swap_total_mb=int(mem.get("swap_total_mb") or 0),
        swap_used_mb=int(mem.get("swap_used_mb") or 0),
        swap_percent=float(mem.get("swap_percent") or 0),
        disk_total_gb=float(disk.get("total_gb") or 0),
        disk_used_gb=float(disk.get("used_gb") or 0),
        disk_percent=float(disk.get("percent") or 0),
        gunicorn_rss_mb=_grp_rss("gunicorn"),
        celery_rss_mb=_grp_rss("celery"),
        daphne_rss_mb=_grp_rss("daphne"),
        postgres_rss_mb=_grp_rss("postgres"),
        redis_rss_mb=_grp_rss("redis"),
        mysql_rss_mb=_grp_rss("mysql"),
        postgres_connections=int(pg.get("connections") or 0),
        postgres_db_size_mb=float(pg.get("db_size_mb") or 0),
        postgres_cache_hit_ratio=float(pg.get("cache_hit_ratio") or 0),
        redis_memory_mb=float(redis_info.get("memory_mb") or 0),
        redis_clients=int(redis_info.get("clients") or 0),
        data={
            "services": snap.get("services") or [],
            "celery_queue": snap.get("celery") or {},
            "host": snap.get("host") or {},
            "top_processes": (snap.get("processes") or {}).get("top") or [],
        },
    )
    return {
        "metric_id": metric.pk,
        "mem_used_mb": metric.mem_used_mb,
        "cpu_percent": metric.cpu_percent,
    }


@shared_task(name="core.tasks_monitoring.ping_uptime")
def ping_uptime() -> dict:
    """Пингует /health/ endpoint и сохраняет результат."""
    ping = ping_health()
    check = UptimeCheck.objects.create(
        ok=bool(ping.get("ok")),
        response_ms=ping.get("response_ms"),
        status_code=ping.get("status_code"),
        error=(ping.get("error") or "")[:255],
    )
    return {"check_id": check.pk, "ok": check.ok, "ms": check.response_ms}


def _delete_in_batches(queryset, batch_size: int) -> int:
    """Удаляет строки queryset'а порциями по ``batch_size`` (по pk).

    Один `DELETE ... WHERE created_at < cutoff` по 40 000 строк держит
    блокировку и раздувает WAL; порции по 5 000 — короткие транзакции,
    между которыми успевают пройти записи beat-задач.
    """
    model = queryset.model
    total = 0
    while True:
        ids = list(queryset.order_by("pk").values_list("pk", flat=True)[:batch_size])
        if not ids:
            return total
        model.objects.filter(pk__in=ids).delete()
        total += len(ids)
        if len(ids) < batch_size:
            return total


@shared_task(name="core.tasks_monitoring.cleanup_monitoring_tables")
def cleanup_monitoring_tables() -> dict:
    """Ретеншен служебных таблиц (Q12): UptimeCheck / SystemMetric / AgentRun /
    NotificationLog. Пороги — settings (env), удаление батчами.

    NotificationLog чистится только по уже переданным контейнерам/авто (или
    без привязки): по нему дедуплицируются уведомления о разгрузке, и для
    активных объектов запись должна жить.
    """
    from .models.agent import AgentRun
    from .models_website import NotificationLog

    now = timezone.now()
    batch = int(getattr(settings, "MONITORING_CLEANUP_BATCH_SIZE", 5000))
    uptime_days = int(getattr(settings, "MONITORING_UPTIME_RETENTION_DAYS", 30))
    metrics_days = int(getattr(settings, "MONITORING_METRICS_RETENTION_DAYS", 90))
    agent_days = int(getattr(settings, "AGENT_RUNS_RETENTION_DAYS", 90))
    notif_days = int(getattr(settings, "NOTIFICATION_LOG_RETENTION_DAYS", 180))

    deleted_uptime = _delete_in_batches(
        UptimeCheck.objects.filter(created_at__lt=now - timedelta(days=uptime_days)), batch
    )
    deleted_metrics = _delete_in_batches(
        SystemMetric.objects.filter(created_at__lt=now - timedelta(days=metrics_days)), batch
    )
    deleted_agent_runs = _delete_in_batches(
        AgentRun.objects.filter(started_at__lt=now - timedelta(days=agent_days)), batch
    )
    notif_qs = (
        NotificationLog.objects.filter(sent_at__lt=now - timedelta(days=notif_days))
        .filter(Q(container__isnull=True) | Q(container__status="TRANSFERRED"))
        .filter(Q(car__isnull=True) | Q(car__status="TRANSFERRED"))
    )
    deleted_notifications = _delete_in_batches(notif_qs, batch)

    result = {
        "deleted_uptime": deleted_uptime,
        "deleted_metrics": deleted_metrics,
        "deleted_agent_runs": deleted_agent_runs,
        "deleted_notifications": deleted_notifications,
        "retention_days": {
            "uptime": uptime_days,
            "metrics": metrics_days,
            "agent_runs": agent_days,
            "notifications": notif_days,
        },
    }
    logger.info("cleanup_monitoring_tables: %s", result)
    return result


@shared_task(name="core.tasks_monitoring.cleanup_old_metrics")
def cleanup_old_metrics() -> dict:
    """Совместимость: прежняя задача чистки метрик → :func:`cleanup_monitoring_tables`.

    Раньше удаляла SystemMetric и UptimeCheck по одному порогу
    (MONITORING_RETENTION_DAYS=30). Теперь пороги раздельные и чистятся ещё
    AgentRun/NotificationLog. Beat-запись `cleanup-old-metrics-daily`
    заменена на `cleanup-monitoring-tables-daily` (04:40).
    """
    return cleanup_monitoring_tables()


@shared_task(name="core.tasks_monitoring.check_backup_freshness")
def check_backup_freshness() -> dict:
    """Проверяет, что в BACKUP_DIR есть свежий PostgreSQL-дамп.

    Если самый свежий файл `*.dump` старше BACKUP_MAX_AGE_HOURS (по умолчанию 36),
    или директории/файлов вообще нет — пишет `logger.warning(...)`, который
    подхватывается Sentry (через LoggingIntegration) и создаёт issue.

    Запускается ежедневно после ночного бэкапа в 03:30 (см. logist2/celery.py).
    На локалке/CI директория обычно пуста — функция тихо вернёт `not_configured`,
    без warning'а (чтобы не шуметь в Sentry в dev-окружении).

    Source of truth для самого бэкапа: scripts/server_pg_backup.sh.
    """
    backup_dir = Path(getattr(settings, "BACKUP_DIR", "/var/backups/logist2"))
    max_age_hours = int(getattr(settings, "BACKUP_MAX_AGE_HOURS", 36))

    if not backup_dir.exists():
        # На локалке/CI этой директории нет — это нормально, не алертим.
        logger.info("backup check: directory %s does not exist (skip)", backup_dir)
        return {"ok": True, "status": "not_configured", "dir": str(backup_dir)}

    dumps = sorted(
        backup_dir.glob("*.dump"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not dumps:
        logger.warning("backup check: no .dump files in %s", backup_dir)
        return {"ok": False, "reason": "no_dumps", "dir": str(backup_dir)}

    latest = dumps[0]
    age_hours = (time.time() - latest.stat().st_mtime) / 3600.0

    if age_hours > max_age_hours:
        logger.warning(
            "backup check: latest dump %s is %.1fh old (threshold=%dh)",
            latest.name,
            age_hours,
            max_age_hours,
        )
        return {
            "ok": False,
            "reason": "stale",
            "latest": latest.name,
            "age_hours": round(age_hours, 1),
            "threshold_hours": max_age_hours,
        }

    return {
        "ok": True,
        "latest": latest.name,
        "age_hours": round(age_hours, 1),
        "size_mb": round(latest.stat().st_size / 1024 / 1024, 1),
        "total_dumps": len(dumps),
    }
