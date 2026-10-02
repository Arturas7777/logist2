"""Q12 — ретеншен служебных таблиц (`cleanup_monitoring_tables`)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Car, Client, Container
from core.models.agent import AgentAction, AgentRun
from core.models_monitoring import SystemMetric, UptimeCheck
from core.models_website import NotificationLog
from core.tasks_monitoring import cleanup_monitoring_tables, cleanup_old_metrics

pytestmark = pytest.mark.django_db


def _age(model, pk, field, days):
    model.objects.filter(pk=pk).update(**{field: timezone.now() - timedelta(days=days)})


def _metric(days):
    m = SystemMetric.objects.create(
        cpu_percent=1,
        mem_total_mb=1000,
        mem_used_mb=500,
        mem_available_mb=500,
        mem_percent=50,
        disk_total_gb=10,
        disk_used_gb=5,
        disk_percent=50,
    )
    _age(SystemMetric, m.pk, "created_at", days)
    return m


def _uptime(days):
    u = UptimeCheck.objects.create(ok=True, response_ms=1)
    _age(UptimeCheck, u.pk, "created_at", days)
    return u


def _run(days):
    r = AgentRun.objects.create(kind=AgentRun.KIND_PLANNER, status=AgentRun.STATUS_SUCCESS)
    _age(AgentRun, r.pk, "started_at", days)
    return r


def _notification(days, *, client, container=None, car=None):
    n = NotificationLog.objects.create(
        client=client,
        container=container,
        car=car,
        notification_type="UNLOADED",
        channel="EMAIL",
        subject="x",
    )
    _age(NotificationLog, n.pk, "sent_at", days)
    return n


def test_cleanup_respects_per_table_thresholds(settings):
    settings.MONITORING_UPTIME_RETENTION_DAYS = 30
    settings.MONITORING_METRICS_RETENTION_DAYS = 90
    settings.AGENT_RUNS_RETENTION_DAYS = 90
    settings.NOTIFICATION_LOG_RETENTION_DAYS = 180

    keep_uptime, old_uptime = _uptime(10), _uptime(31)
    keep_metric, old_metric = _metric(60), _metric(91)
    keep_run, old_run = _run(60), _run(91)
    # AgentAction от старого запуска должен пережить чистку (SET_NULL)
    action = AgentAction.objects.create(run=old_run, action_type=AgentAction.TYPE_OTHER, title="t")

    client = Client.objects.create(name="Retention Client")
    transferred = Container.objects.create(number="RETC0000001", status="TRANSFERRED")
    active = Container.objects.create(number="RETC0000002", status="FLOATING")
    keep_recent = _notification(10, client=client, container=transferred)
    old_transferred = _notification(181, client=client, container=transferred)
    old_active = _notification(181, client=client, container=active)
    old_orphan = _notification(181, client=client)

    result = cleanup_monitoring_tables()

    assert result["deleted_uptime"] == 1
    assert result["deleted_metrics"] == 1
    assert result["deleted_agent_runs"] == 1
    assert result["deleted_notifications"] == 2

    assert UptimeCheck.objects.filter(pk=keep_uptime.pk).exists()
    assert not UptimeCheck.objects.filter(pk=old_uptime.pk).exists()
    assert SystemMetric.objects.filter(pk=keep_metric.pk).exists()
    assert not SystemMetric.objects.filter(pk=old_metric.pk).exists()
    assert AgentRun.objects.filter(pk=keep_run.pk).exists()
    assert not AgentRun.objects.filter(pk=old_run.pk).exists()
    action.refresh_from_db()
    assert action.run_id is None

    assert NotificationLog.objects.filter(pk=keep_recent.pk).exists()
    assert not NotificationLog.objects.filter(pk=old_transferred.pk).exists()
    assert not NotificationLog.objects.filter(pk=old_orphan.pk).exists()
    # активный контейнер — лог нужен дедупу уведомлений, не трогаем
    assert NotificationLog.objects.filter(pk=old_active.pk).exists()


def test_cleanup_keeps_notification_for_active_car(settings):
    settings.NOTIFICATION_LOG_RETENTION_DAYS = 180
    client = Client.objects.create(name="Retention Client 2")
    car = Car.objects.create(year=2020, brand="BMW", vin="RETENTION00000001", status="UNLOADED", client=client)
    n = _notification(200, client=client, car=car)
    cleanup_monitoring_tables()
    assert NotificationLog.objects.filter(pk=n.pk).exists()


def test_cleanup_deletes_in_batches(settings):
    settings.MONITORING_CLEANUP_BATCH_SIZE = 3
    settings.MONITORING_UPTIME_RETENTION_DAYS = 30
    for _ in range(7):
        _uptime(40)
    _uptime(1)
    result = cleanup_monitoring_tables()
    assert result["deleted_uptime"] == 7
    assert UptimeCheck.objects.count() == 1


def test_legacy_cleanup_old_metrics_delegates(settings):
    settings.MONITORING_METRICS_RETENTION_DAYS = 90
    _metric(100)
    _metric(50)
    result = cleanup_old_metrics()
    assert result["deleted_metrics"] == 1
    assert SystemMetric.objects.count() == 1
