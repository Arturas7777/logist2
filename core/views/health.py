"""Liveness and readiness probes for load balancers / monitoring.

- `/health/` — liveness: process is alive, returns 200 immediately.
- `/ready/`  — readiness: checks that DB and cache (Redis) are reachable;
  returns 503 if any dependency is down.

Both endpoints:
- never require authentication;
- never hit the DB query cache;
- return a compact JSON payload for log aggregators.
"""
from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@csrf_exempt
@never_cache
@require_GET
def health(request):
    """Liveness probe. Always 200 if the Python process can respond."""
    return JsonResponse({'status': 'ok'})


def _check_database() -> tuple[bool, float, str | None]:
    started = time.monotonic()
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        return True, (time.monotonic() - started) * 1000, None
    except Exception as exc:  # noqa: BLE001
        return False, (time.monotonic() - started) * 1000, str(exc)[:200]


def _check_cache() -> tuple[bool, float, str | None]:
    started = time.monotonic()
    try:
        key = '_health_probe'
        cache.set(key, '1', timeout=5)
        value = cache.get(key)
        ok = value == '1'
        return ok, (time.monotonic() - started) * 1000, None if ok else 'cache roundtrip mismatch'
    except Exception as exc:  # noqa: BLE001
        return False, (time.monotonic() - started) * 1000, str(exc)[:200]


@csrf_exempt
@never_cache
@require_GET
def ready(request):
    """Readiness probe. Returns 503 if DB or cache are down."""
    db_ok, db_ms, db_err = _check_database()
    cache_ok, cache_ms, cache_err = _check_cache()

    payload = {
        'status': 'ok' if (db_ok and cache_ok) else 'degraded',
        'checks': {
            'database': {
                'ok': db_ok,
                'latency_ms': round(db_ms, 2),
                'error': db_err,
            },
            'cache': {
                'ok': cache_ok,
                'latency_ms': round(cache_ms, 2),
                'error': cache_err,
            },
        },
    }

    if not (db_ok and cache_ok):
        logger.warning('Readiness probe failed: %s', payload)
        return JsonResponse(payload, status=503)

    return JsonResponse(payload)
