"""Модели для страницы /admin/system-monitor/.

Хранят историю метрик системы (RAM/CPU/disk/процессы/Postgres/Redis)
и пингов uptime. Используются для построения графиков и расчёта SLA.

Retention настраивается через MONITORING_RETENTION_DAYS в settings (default 30).
Очистка делается task'ом `core.tasks_monitoring.cleanup_old_metrics`.
"""
from __future__ import annotations

from django.db import models


class SystemMetric(models.Model):
    """Снимок метрик системы.

    Создаётся раз в 5 минут celery beat task'ом
    `core.tasks_monitoring.collect_system_metrics`.

    JSONB-поле `data` хранит сырой снимок (для расширения без миграций),
    плюс ключевые числа вынесены в отдельные индексированные колонки
    для быстрой агрегации в графиках.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    cpu_percent = models.FloatField(help_text='Загрузка CPU в %, 0-100')
    load_avg_1 = models.FloatField(null=True, blank=True, help_text='Linux loadavg 1m')

    mem_total_mb = models.IntegerField()
    mem_used_mb = models.IntegerField()
    mem_available_mb = models.IntegerField()
    mem_percent = models.FloatField()

    swap_total_mb = models.IntegerField(default=0)
    swap_used_mb = models.IntegerField(default=0)
    swap_percent = models.FloatField(default=0.0)

    disk_total_gb = models.FloatField()
    disk_used_gb = models.FloatField()
    disk_percent = models.FloatField()

    gunicorn_rss_mb = models.IntegerField(default=0, help_text='Sum RSS gunicorn workers')
    celery_rss_mb = models.IntegerField(default=0)
    daphne_rss_mb = models.IntegerField(default=0)
    postgres_rss_mb = models.IntegerField(default=0)
    redis_rss_mb = models.IntegerField(default=0)
    mysql_rss_mb = models.IntegerField(default=0)

    postgres_connections = models.IntegerField(default=0)
    postgres_db_size_mb = models.FloatField(default=0.0)
    postgres_cache_hit_ratio = models.FloatField(default=0.0, help_text='0-1')

    redis_memory_mb = models.FloatField(default=0.0)
    redis_clients = models.IntegerField(default=0)

    data = models.JSONField(default=dict, blank=True, help_text='Сырой расширенный снимок')

    class Meta:
        db_table = 'core_system_metric'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='sm_created_at_desc_idx'),
        ]

    def __str__(self) -> str:
        return f'SystemMetric@{self.created_at:%Y-%m-%d %H:%M}'


class UptimeCheck(models.Model):
    """Пинг health endpoint'а сайта.

    Делается раз в минуту celery beat task'ом
    `core.tasks_monitoring.ping_uptime`. По истории считается % доступности
    за последние 24h / 7d / 30d.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ok = models.BooleanField()
    response_ms = models.FloatField(null=True, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'core_uptime_check'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='uc_created_at_desc_idx'),
            models.Index(fields=['ok', '-created_at'], name='uc_ok_created_idx'),
        ]

    def __str__(self) -> str:
        status = 'OK' if self.ok else f'FAIL ({self.error or self.status_code})'
        return f'UptimeCheck@{self.created_at:%Y-%m-%d %H:%M} {status}'
