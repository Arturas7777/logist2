"""Сбор системных метрик для /admin/system-monitor/.

Все функции **fail-safe**: при ошибке возвращают значения по умолчанию,
никогда не кидают исключений наружу. Это важно — страница мониторинга
должна оставаться живой даже если что-то на сервере сломалось.

Используется:
- `collect_snapshot()` — главная функция, возвращает полный снимок.
  Вызывается из celery beat (раз в 5 мин) для сохранения в БД,
  а также из view напрямую для real-time блока.
- `ping_health()` — пингует /health/ endpoint для uptime-трекинга.

Платформо-зависимое:
- На Windows (локальная разработка) `psutil` работает; systemd/systemctl
  возвращают «недоступно». Это нормально — реальные данные собираются
  на сервере (Linux).
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

import psutil
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


# ── PROCESS GROUPS ──────────────────────────────────────────────────────────
# Группируем процессы по «приложению» через подстроки в cmdline.
# Один процесс может породить много workers — суммируем их RSS.
_PROCESS_GROUPS = {
    'gunicorn':   ('gunicorn', 'logist2.wsgi'),
    'celery':     ('celery', 'logist2'),
    'daphne':     ('daphne', 'logist2.asgi'),
    'postgres':   ('postgres',),
    'redis':      ('redis-server',),
    'mysql':      ('mysqld', 'mariadbd'),
}


def _safe(fn, default=None):
    """Wrap a callable — log+return default on any exception."""
    try:
        return fn()
    except Exception as exc:
        logger.debug('system_monitor: %s failed: %s', getattr(fn, '__name__', fn), exc)
        return default


# ── CPU / LOAD ──────────────────────────────────────────────────────────────
def _collect_cpu() -> dict[str, Any]:
    cpu_percent = psutil.cpu_percent(interval=0.3)
    load_avg_1 = load_avg_5 = load_avg_15 = None
    if hasattr(os, 'getloadavg'):
        try:
            la1, la5, la15 = os.getloadavg()
            load_avg_1, load_avg_5, load_avg_15 = la1, la5, la15
        except (OSError, AttributeError):
            pass
    return {
        'percent': cpu_percent,
        'count': psutil.cpu_count(logical=True) or 1,
        'count_physical': psutil.cpu_count(logical=False) or 1,
        'load_avg_1': load_avg_1,
        'load_avg_5': load_avg_5,
        'load_avg_15': load_avg_15,
    }


# ── MEMORY / SWAP ───────────────────────────────────────────────────────────
def _collect_memory() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        'total_mb': vm.total // (1024 * 1024),
        'used_mb': vm.used // (1024 * 1024),
        'available_mb': vm.available // (1024 * 1024),
        'percent': vm.percent,
        'swap_total_mb': sw.total // (1024 * 1024),
        'swap_used_mb': sw.used // (1024 * 1024),
        'swap_percent': sw.percent,
    }


# ── DISK ────────────────────────────────────────────────────────────────────
def _collect_disk() -> dict[str, Any]:
    path = '/' if platform.system() != 'Windows' else 'C:\\'
    try:
        du = shutil.disk_usage(path)
        return {
            'path': path,
            'total_gb': round(du.total / (1024 ** 3), 2),
            'used_gb': round(du.used / (1024 ** 3), 2),
            'free_gb': round(du.free / (1024 ** 3), 2),
            'percent': round(du.used / du.total * 100, 1) if du.total else 0,
        }
    except OSError as exc:
        logger.warning('disk_usage(%s) failed: %s', path, exc)
        return {'path': path, 'total_gb': 0, 'used_gb': 0, 'free_gb': 0, 'percent': 0}


# ── PROCESSES (grouped by app) ──────────────────────────────────────────────
def _collect_processes() -> dict[str, dict[str, Any]]:
    """RSS суммарно по группам + top-10 по RSS отдельно."""
    groups: dict[str, dict[str, Any]] = {
        name: {'rss_mb': 0, 'count': 0} for name in _PROCESS_GROUPS
    }
    top_processes: list[dict[str, Any]] = []

    for proc in psutil.process_iter(['pid', 'name', 'username', 'memory_info', 'cmdline']):
        try:
            info = proc.info
            rss_mb = (info['memory_info'].rss if info['memory_info'] else 0) // (1024 * 1024)
            if rss_mb == 0:
                continue

            name = (info['name'] or '').lower()
            cmdline = ' '.join(info.get('cmdline') or []).lower()

            for group_name, patterns in _PROCESS_GROUPS.items():
                if any(p in name or p in cmdline for p in patterns):
                    groups[group_name]['rss_mb'] += rss_mb
                    groups[group_name]['count'] += 1
                    break

            top_processes.append({
                'pid': info['pid'],
                'name': info['name'],
                'user': info.get('username') or '',
                'rss_mb': rss_mb,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    top_processes.sort(key=lambda p: p['rss_mb'], reverse=True)
    return {
        'groups': groups,
        'top': top_processes[:10],
    }


# ── SYSTEMD SERVICES ────────────────────────────────────────────────────────
SERVICE_NAMES = ['gunicorn', 'daphne', 'celery', 'nginx', 'postgresql', 'redis-server', 'mysql']


def _systemctl(name: str, prop: str) -> str:
    """Запрос одного свойства юнита. На Windows / при ошибке → ''."""
    if platform.system() == 'Windows':
        return ''
    try:
        res = subprocess.run(
            ['systemctl', 'show', name, '-p', prop, '--value'],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return res.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug('systemctl show %s %s failed: %s', name, prop, exc)
        return ''


def _collect_services() -> list[dict[str, Any]]:
    if platform.system() == 'Windows':
        return [{'name': n, 'available': False} for n in SERVICE_NAMES]

    out = []
    for name in SERVICE_NAMES:
        active_state = _systemctl(name, 'ActiveState')
        sub_state = _systemctl(name, 'SubState')
        mem_current = _systemctl(name, 'MemoryCurrent')
        active_enter = _systemctl(name, 'ActiveEnterTimestamp')

        mem_mb = 0
        if mem_current and mem_current.isdigit():
            mem_mb = int(mem_current) // (1024 * 1024)

        out.append({
            'name': name,
            'available': bool(active_state),
            'active': active_state == 'active',
            'failed': active_state == 'failed',
            'state': active_state or 'unknown',
            'sub_state': sub_state,
            'memory_mb': mem_mb,
            'active_since': active_enter,
        })
    return out


# ── POSTGRESQL ──────────────────────────────────────────────────────────────
def _collect_postgres() -> dict[str, Any]:
    """Метрики из самой postgres через `connection`. Безопасно от ошибок."""
    result = {
        'connections': 0,
        'connections_max': 0,
        'db_size_mb': 0.0,
        'cache_hit_ratio': 0.0,
        'slow_queries': [],
        'available': False,
    }
    try:
        db_name = settings.DATABASES['default'].get('NAME', '')
        with connection.cursor() as cur:
            cur.execute('SELECT current_setting(%s)::int', ['max_connections'])
            result['connections_max'] = cur.fetchone()[0]

            cur.execute(
                'SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()'
            )
            result['connections'] = cur.fetchone()[0]

            cur.execute('SELECT pg_database_size(current_database())')
            result['db_size_mb'] = round(cur.fetchone()[0] / (1024 * 1024), 2)

            cur.execute("""
                SELECT
                    sum(heap_blks_hit)::float
                    / NULLIF(sum(heap_blks_hit) + sum(heap_blks_read), 0)
                FROM pg_statio_user_tables
            """)
            ratio = cur.fetchone()[0]
            result['cache_hit_ratio'] = round(ratio or 0.0, 4)

            try:
                cur.execute("""
                    SELECT
                        substr(query, 1, 120) AS q,
                        calls,
                        round(mean_exec_time::numeric, 1) AS avg_ms,
                        round(total_exec_time::numeric, 0) AS total_ms
                    FROM pg_stat_statements
                    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                      AND query NOT ILIKE '%%pg_stat_statements%%'
                    ORDER BY mean_exec_time DESC
                    LIMIT 10
                """)
                cols = ['query', 'calls', 'avg_ms', 'total_ms']
                result['slow_queries'] = [
                    dict(zip(cols, row, strict=False)) for row in cur.fetchall()
                ]
            except Exception as exc:
                logger.debug('pg_stat_statements not available: %s', exc)
                result['slow_queries'] = []
                result['slow_queries_error'] = (
                    'pg_stat_statements не настроен. Включите расширение в postgresql.conf.'
                )

        _ = db_name
        result['available'] = True
    except Exception as exc:
        logger.warning('postgres metrics failed: %s', exc)
        result['error'] = str(exc)[:200]
    return result


# ── REDIS ───────────────────────────────────────────────────────────────────
def _collect_redis() -> dict[str, Any]:
    result = {
        'memory_mb': 0.0,
        'clients': 0,
        'keys': 0,
        'hit_ratio': 0.0,
        'available': False,
    }
    try:
        import redis as redis_lib

        host = os.getenv('REDIS_HOST', '127.0.0.1')
        port = int(os.getenv('REDIS_PORT', '6379'))
        client = redis_lib.Redis(host=host, port=port, socket_timeout=2)
        info = client.info()

        result['memory_mb'] = round(info.get('used_memory', 0) / (1024 * 1024), 2)
        result['clients'] = info.get('connected_clients', 0)
        result['uptime_days'] = round(info.get('uptime_in_seconds', 0) / 86400, 1)

        hits = info.get('keyspace_hits', 0)
        misses = info.get('keyspace_misses', 0)
        total = hits + misses
        result['hit_ratio'] = round(hits / total, 4) if total else 0.0

        keys = 0
        for k, v in info.items():
            if k.startswith('db') and isinstance(v, dict):
                keys += v.get('keys', 0)
        result['keys'] = keys

        result['available'] = True
    except Exception as exc:
        logger.warning('redis metrics failed: %s', exc)
        result['error'] = str(exc)[:200]
    return result


# ── CELERY QUEUE LENGTH ─────────────────────────────────────────────────────
def _collect_celery_queue() -> dict[str, Any]:
    """Длина основной очереди celery (через Redis LLEN)."""
    try:
        import redis as redis_lib

        host = os.getenv('REDIS_HOST', '127.0.0.1')
        port = int(os.getenv('REDIS_PORT', '6379'))
        client = redis_lib.Redis(host=host, port=port, socket_timeout=2, db=2)
        celery_len = client.llen('celery')
        return {'queue_len': celery_len, 'available': True}
    except Exception as exc:
        logger.debug('celery queue length failed: %s', exc)
        return {'queue_len': 0, 'available': False, 'error': str(exc)[:200]}


# ── UPTIME / NETWORK ────────────────────────────────────────────────────────
def _collect_host() -> dict[str, Any]:
    boot = psutil.boot_time()
    return {
        'hostname': socket.gethostname(),
        'platform': platform.platform(),
        'python_version': platform.python_version(),
        'uptime_seconds': int(time.time() - boot),
        'uptime_days': round((time.time() - boot) / 86400, 1),
    }


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def collect_snapshot() -> dict[str, Any]:
    """Возвращает полный снимок всех метрик.

    Все секции независимы — если одна сломалась, остальные всё равно вернутся.
    """
    return {
        'cpu': _safe(_collect_cpu, {}),
        'memory': _safe(_collect_memory, {}),
        'disk': _safe(_collect_disk, {}),
        'processes': _safe(_collect_processes, {'groups': {}, 'top': []}),
        'services': _safe(_collect_services, []),
        'postgres': _safe(_collect_postgres, {}),
        'redis': _safe(_collect_redis, {}),
        'celery': _safe(_collect_celery_queue, {}),
        'host': _safe(_collect_host, {}),
        'collected_at': time.time(),
    }


def ping_health() -> dict[str, Any]:
    """Пингует свой /health/ endpoint, возвращает {ok, response_ms, status_code, error}.

    Используется в celery beat task'е `ping_uptime` (раз в минуту).
    Идёт по локальному gunicorn сокету через requests, без обращения наружу.
    """
    import requests

    url = getattr(settings, 'MONITORING_HEALTH_URL', 'http://127.0.0.1/health/')
    started = time.monotonic()
    try:
        resp = requests.get(url, timeout=5, headers={'User-Agent': 'logist2-uptime-monitor'})
        elapsed_ms = (time.monotonic() - started) * 1000
        ok = resp.status_code == 200
        return {
            'ok': ok,
            'response_ms': round(elapsed_ms, 2),
            'status_code': resp.status_code,
            'error': '' if ok else f'HTTP {resp.status_code}',
        }
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return {
            'ok': False,
            'response_ms': round(elapsed_ms, 2),
            'status_code': None,
            'error': str(exc)[:255],
        }


def compute_alerts(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Превращает сырой snapshot в список алертов для верхней плашки.

    Уровни: 'critical' (красный), 'warning' (жёлтый).
    """
    alerts = []

    mem = snapshot.get('memory') or {}
    if mem.get('available_mb', 9999) < 200:
        alerts.append({
            'level': 'critical',
            'message': f"Мало RAM: доступно {mem.get('available_mb')} MB (< 200 MB)",
        })

    disk = snapshot.get('disk') or {}
    if disk.get('free_gb', 999) < 1:
        alerts.append({
            'level': 'critical',
            'message': f"Мало места на диске: свободно {disk.get('free_gb')} GB",
        })
    elif disk.get('percent', 0) > 90:
        alerts.append({
            'level': 'warning',
            'message': f"Диск заполнен на {disk.get('percent')}%",
        })

    if mem.get('swap_used_mb', 0) > 500:
        alerts.append({
            'level': 'warning',
            'message': f"Swap активно используется: {mem.get('swap_used_mb')} MB",
        })

    for svc in snapshot.get('services') or []:
        if svc.get('available') and svc.get('failed'):
            alerts.append({
                'level': 'critical',
                'message': f"Сервис {svc['name']} в состоянии FAILED",
            })
        elif svc.get('available') and not svc.get('active') and svc['name'] != 'mysql':
            alerts.append({
                'level': 'warning',
                'message': f"Сервис {svc['name']} не запущен ({svc.get('state')})",
            })

    pg = snapshot.get('postgres') or {}
    if pg.get('available') and pg.get('connections_max'):
        usage = pg['connections'] / pg['connections_max']
        if usage > 0.8:
            alerts.append({
                'level': 'warning',
                'message': f"PostgreSQL: {pg['connections']}/{pg['connections_max']} коннектов",
            })

    return alerts
