"""Celery-задачи для страницы /admin/system-monitor/.

Расписание подключается в `logist2/celery.py` в `beat_schedule`:
- `collect_system_metrics` — каждые 5 минут (288 точек/день)
- `ping_uptime` — каждую минуту (1440 точек/день)
- `cleanup_old_metrics` — раз в день в 04:00

Retention: 30 дней (берётся из settings.MONITORING_RETENTION_DAYS, дефолт 30).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models_monitoring import SystemMetric, UptimeCheck
from .services.system_monitor import collect_snapshot, ping_health

logger = logging.getLogger(__name__)


@shared_task(name='core.tasks_monitoring.collect_system_metrics')
def collect_system_metrics() -> dict:
    """Снимает текущие метрики и сохраняет одну строку SystemMetric."""
    snap = collect_snapshot()

    mem = snap.get('memory') or {}
    disk = snap.get('disk') or {}
    cpu = snap.get('cpu') or {}
    pg = snap.get('postgres') or {}
    redis_info = snap.get('redis') or {}
    proc_groups = (snap.get('processes') or {}).get('groups') or {}

    def _grp_rss(name: str) -> int:
        g = proc_groups.get(name) or {}
        return int(g.get('rss_mb', 0))

    metric = SystemMetric.objects.create(
        cpu_percent=float(cpu.get('percent') or 0),
        load_avg_1=cpu.get('load_avg_1'),
        mem_total_mb=int(mem.get('total_mb') or 0),
        mem_used_mb=int(mem.get('used_mb') or 0),
        mem_available_mb=int(mem.get('available_mb') or 0),
        mem_percent=float(mem.get('percent') or 0),
        swap_total_mb=int(mem.get('swap_total_mb') or 0),
        swap_used_mb=int(mem.get('swap_used_mb') or 0),
        swap_percent=float(mem.get('swap_percent') or 0),
        disk_total_gb=float(disk.get('total_gb') or 0),
        disk_used_gb=float(disk.get('used_gb') or 0),
        disk_percent=float(disk.get('percent') or 0),
        gunicorn_rss_mb=_grp_rss('gunicorn'),
        celery_rss_mb=_grp_rss('celery'),
        daphne_rss_mb=_grp_rss('daphne'),
        postgres_rss_mb=_grp_rss('postgres'),
        redis_rss_mb=_grp_rss('redis'),
        mysql_rss_mb=_grp_rss('mysql'),
        postgres_connections=int(pg.get('connections') or 0),
        postgres_db_size_mb=float(pg.get('db_size_mb') or 0),
        postgres_cache_hit_ratio=float(pg.get('cache_hit_ratio') or 0),
        redis_memory_mb=float(redis_info.get('memory_mb') or 0),
        redis_clients=int(redis_info.get('clients') or 0),
        data={
            'services': snap.get('services') or [],
            'celery_queue': snap.get('celery') or {},
            'host': snap.get('host') or {},
            'top_processes': (snap.get('processes') or {}).get('top') or [],
        },
    )
    return {
        'metric_id': metric.pk,
        'mem_used_mb': metric.mem_used_mb,
        'cpu_percent': metric.cpu_percent,
    }


@shared_task(name='core.tasks_monitoring.ping_uptime')
def ping_uptime() -> dict:
    """Пингует /health/ endpoint и сохраняет результат."""
    ping = ping_health()
    check = UptimeCheck.objects.create(
        ok=bool(ping.get('ok')),
        response_ms=ping.get('response_ms'),
        status_code=ping.get('status_code'),
        error=(ping.get('error') or '')[:255],
    )
    return {'check_id': check.pk, 'ok': check.ok, 'ms': check.response_ms}


@shared_task(name='core.tasks_monitoring.cleanup_old_metrics')
def cleanup_old_metrics() -> dict:
    """Удаляет SystemMetric/UptimeCheck старше MONITORING_RETENTION_DAYS."""
    retention_days = int(getattr(settings, 'MONITORING_RETENTION_DAYS', 30))
    cutoff = timezone.now() - timedelta(days=retention_days)

    deleted_metrics, _ = SystemMetric.objects.filter(created_at__lt=cutoff).delete()
    deleted_uptime, _ = UptimeCheck.objects.filter(created_at__lt=cutoff).delete()

    logger.info(
        'monitoring cleanup: deleted %d metrics, %d uptime checks (cutoff=%s)',
        deleted_metrics, deleted_uptime, cutoff,
    )
    return {
        'deleted_metrics': deleted_metrics,
        'deleted_uptime': deleted_uptime,
        'cutoff': cutoff.isoformat(),
    }
