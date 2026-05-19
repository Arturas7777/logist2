"""Views для страницы /admin/system-monitor/.

3 endpoint'а:
- `system_monitor_page` — HTML-страница с карточками реал-тайма и графиками.
- `system_monitor_snapshot` — JSON с текущим снимком (htmx auto-refresh 30s).
- `system_monitor_history` — JSON с метриками за период (24h/7d/30d) для Chart.js.

Все защищены `staff_member_required`. Real-time снимки берутся напрямую
через `collect_snapshot()` (не из БД), чтобы видеть мгновенное состояние;
графики читают из БД (наполняется celery beat'ом каждые 5 минут).
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncHour, TruncMinute
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ..models_monitoring import SystemMetric, UptimeCheck
from ..services.system_monitor import collect_snapshot, compute_alerts


@staff_member_required
@require_GET
def system_monitor_page(request: HttpRequest):
    """HTML-страница мониторинга."""
    snapshot = collect_snapshot()
    alerts = compute_alerts(snapshot)
    return render(request, 'admin/system_monitor.html', {
        'title': 'Мониторинг системы',
        'snapshot': snapshot,
        'alerts': alerts,
    })


@staff_member_required
@never_cache
@require_GET
def system_monitor_snapshot(request: HttpRequest):
    """JSON со свежим снимком — для htmx auto-refresh каждые 30 сек."""
    snapshot = collect_snapshot()
    alerts = compute_alerts(snapshot)
    return JsonResponse({'snapshot': snapshot, 'alerts': alerts})


_RANGE_CONFIG = {
    '24h': {'delta': timedelta(hours=24), 'trunc': TruncMinute, 'bucket': 'minute'},
    '7d': {'delta': timedelta(days=7), 'trunc': TruncHour, 'bucket': 'hour'},
    '30d': {'delta': timedelta(days=30), 'trunc': TruncHour, 'bucket': 'hour'},
}


@staff_member_required
@never_cache
@require_GET
def system_monitor_history(request: HttpRequest):
    """История метрик и uptime за выбранный период.

    Query param `range` ∈ {24h, 7d, 30d}. Дефолт — 24h.
    Возвращает структуру, готовую для Chart.js.
    """
    range_key = request.GET.get('range', '24h')
    cfg = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG['24h'])

    since = timezone.now() - cfg['delta']
    bucket_alias = cfg['bucket']
    trunc = cfg['trunc']

    qs = SystemMetric.objects.filter(created_at__gte=since)
    if range_key != '24h':
        qs = (
            qs.annotate(bucket=trunc('created_at'))
            .values('bucket')
            .annotate(
                cpu_percent=Avg('cpu_percent'),
                mem_percent=Avg('mem_percent'),
                mem_used_mb=Avg('mem_used_mb'),
                mem_available_mb=Avg('mem_available_mb'),
                swap_used_mb=Avg('swap_used_mb'),
                disk_percent=Avg('disk_percent'),
                gunicorn_rss_mb=Avg('gunicorn_rss_mb'),
                celery_rss_mb=Avg('celery_rss_mb'),
                mysql_rss_mb=Avg('mysql_rss_mb'),
                postgres_rss_mb=Avg('postgres_rss_mb'),
                postgres_connections=Avg('postgres_connections'),
                redis_memory_mb=Avg('redis_memory_mb'),
            )
            .order_by('bucket')
        )
        points = [
            {
                'ts': row['bucket'].isoformat(),
                'cpu_percent': round(row['cpu_percent'] or 0, 1),
                'mem_percent': round(row['mem_percent'] or 0, 1),
                'mem_used_mb': int(row['mem_used_mb'] or 0),
                'mem_available_mb': int(row['mem_available_mb'] or 0),
                'swap_used_mb': int(row['swap_used_mb'] or 0),
                'disk_percent': round(row['disk_percent'] or 0, 1),
                'gunicorn_rss_mb': int(row['gunicorn_rss_mb'] or 0),
                'celery_rss_mb': int(row['celery_rss_mb'] or 0),
                'mysql_rss_mb': int(row['mysql_rss_mb'] or 0),
                'postgres_rss_mb': int(row['postgres_rss_mb'] or 0),
                'postgres_connections': int(row['postgres_connections'] or 0),
                'redis_memory_mb': round(row['redis_memory_mb'] or 0, 1),
            }
            for row in qs
        ]
    else:
        points = [
            {
                'ts': m.created_at.isoformat(),
                'cpu_percent': round(m.cpu_percent, 1),
                'mem_percent': round(m.mem_percent, 1),
                'mem_used_mb': m.mem_used_mb,
                'mem_available_mb': m.mem_available_mb,
                'swap_used_mb': m.swap_used_mb,
                'disk_percent': round(m.disk_percent, 1),
                'gunicorn_rss_mb': m.gunicorn_rss_mb,
                'celery_rss_mb': m.celery_rss_mb,
                'mysql_rss_mb': m.mysql_rss_mb,
                'postgres_rss_mb': m.postgres_rss_mb,
                'postgres_connections': m.postgres_connections,
                'redis_memory_mb': round(m.redis_memory_mb, 1),
            }
            for m in qs.order_by('created_at')
        ]

    uptime_qs = UptimeCheck.objects.filter(created_at__gte=since)
    total = uptime_qs.count()
    ok_count = uptime_qs.filter(ok=True).count()
    uptime_pct = round(ok_count / total * 100, 3) if total else None
    avg_response = uptime_qs.filter(ok=True).aggregate(avg=Avg('response_ms'))['avg']

    uptime_buckets: list[dict] = []
    if total and range_key != '24h':
        uptime_buckets = list(
            uptime_qs.annotate(bucket=trunc('created_at'))
            .values('bucket')
            .annotate(
                total=Count('id'),
                ok_count=Count('id', filter=Q(ok=True)),
                avg_ms=Avg('response_ms'),
            )
            .order_by('bucket')
        )
        uptime_buckets = [
            {
                'ts': r['bucket'].isoformat(),
                'pct': round(r['ok_count'] / r['total'] * 100, 2) if r['total'] else 0,
                'avg_ms': round(r['avg_ms'] or 0, 1),
            }
            for r in uptime_buckets
        ]

    failures = (
        uptime_qs.filter(ok=False)
        .order_by('-created_at')[:20]
        .values('created_at', 'status_code', 'response_ms', 'error')
    )
    failures = [
        {
            'ts': f['created_at'].isoformat(),
            'status_code': f['status_code'],
            'response_ms': f['response_ms'],
            'error': f['error'],
        }
        for f in failures
    ]

    return JsonResponse({
        'range': range_key,
        'bucket': bucket_alias,
        'points': points,
        'uptime': {
            'total_checks': total,
            'ok_checks': ok_count,
            'pct': uptime_pct,
            'avg_response_ms': round(avg_response, 1) if avg_response else None,
            'buckets': uptime_buckets,
            'recent_failures': failures,
        },
    })
