"""L4: идемпотентность уведомлений — claim-запись до отправки, retry неудачных."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.utils import timezone

from core.models import Car, Client, Container, Warehouse
from core.models_website import NotificationClaim, NotificationLog
from core.services import notification_claims
from core.services.email_service import ContainerNotificationService
from core.tasks import _send_container_notifications_or_retry, send_car_unload_notifications_once

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def container():
    warehouse = Warehouse.objects.create(name="WH-L4")
    return Container.objects.create(number="L4-NOTIFY01", status="UNLOADED", warehouse=warehouse, unload_date=TODAY)


def _client_with_car(container, name, vin, email):
    client = Client.objects.create(name=name, email=email)
    Car.objects.create(year=2022, brand="Audi", vin=vin, status="UNLOADED", container=container, client=client)
    return client


# --- claim ---------------------------------------------------------------------


def test_claim_is_exclusive_until_failed(container):
    client = Client.objects.create(name="L4 claim")
    kwargs = {"channel": "EMAIL", "notification_type": "UNLOADED", "client": client, "container": container}

    first = notification_claims.claim(**kwargs)
    assert first is not None
    assert notification_claims.claim(**kwargs) is None

    notification_claims.finish(first, success=False, error="smtp down")
    retry = notification_claims.claim(**kwargs)
    assert retry is not None
    assert retry.attempts == 2

    notification_claims.finish(retry, success=True)
    assert notification_claims.claim(**kwargs) is None
    assert NotificationClaim.objects.get().status == NotificationClaim.STATUS_SENT


def test_stale_pending_claim_can_be_taken_again(container):
    client = Client.objects.create(name="L4 stale")
    kwargs = {"channel": "EMAIL", "notification_type": "UNLOADED", "client": client, "container": container}
    notification_claims.claim(**kwargs)
    NotificationClaim.objects.update(claimed_at=timezone.now() - datetime.timedelta(hours=1))

    assert notification_claims.claim(**kwargs) is not None


# --- рассылка по контейнеру -----------------------------------------------------


def test_unload_sent_once_per_client(container):
    _client_with_car(container, "L4 A", "L4NOTIFY000000001", "a@example.com")
    _client_with_car(container, "L4 B", "L4NOTIFY000000002", "b@example.com")

    assert ContainerNotificationService.send_unload_to_all_clients(container) == (2, 0)
    NotificationLog.objects.all().delete()
    assert ContainerNotificationService.send_unload_to_all_clients(container) == (0, 0)

    assert len(mail.outbox) == 2


def test_client_claimed_by_other_worker_is_skipped(container):
    client = _client_with_car(container, "L4 busy", "L4NOTIFY000000003", "busy@example.com")
    notification_claims.claim(channel="EMAIL", notification_type="UNLOADED", client=client, container=container)

    assert ContainerNotificationService.send_unload_to_all_clients(container) == (0, 0)
    assert mail.outbox == []


def test_failed_client_is_retried_and_others_not_resent(container):
    _client_with_car(container, "L4 ok", "L4NOTIFY000000004", "ok@example.com")
    _client_with_car(container, "L4 bad", "L4NOTIFY000000005", "bad@example.com")
    real_send = mail.EmailMultiAlternatives.send

    def flaky_send(message, *args, **kwargs):
        if "bad@example.com" in message.to:
            raise ConnectionError("smtp timeout")
        return real_send(message, *args, **kwargs)

    with patch("django.core.mail.EmailMultiAlternatives.send", flaky_send):
        assert ContainerNotificationService.send_unload_to_all_clients(container) == (1, 1)

    assert ContainerNotificationService.send_unload_to_all_clients(container) == (1, 0)
    assert sorted(m.to[0] for m in mail.outbox) == ["bad@example.com", "ok@example.com"]


def test_task_helper_requests_retry_on_failures(container):
    task = MagicMock()
    task.retry.side_effect = RuntimeError("retry requested")

    with pytest.raises(RuntimeError, match="retry requested"):
        _send_container_notifications_or_retry(task, container, "unload", lambda c: (0, 0), lambda c: (1, 1))
    _send_container_notifications_or_retry(task, container, "unload", lambda c: (0, 0), lambda c: (2, 0))
    assert task.retry.call_count == 1


def test_no_date_means_nothing_to_send(container):
    _client_with_car(container, "L4 nodate", "L4NOTIFY000000006", "nodate@example.com")

    assert ContainerNotificationService.send_planned_to_all_clients(container) == (0, 0)
    assert not NotificationClaim.objects.exists()


# --- отдельное ТС ---------------------------------------------------------------


def test_car_unload_once_and_skips_clients_without_channel():
    warehouse = Warehouse.objects.create(name="WH-L4-car")
    with_email = Client.objects.create(name="L4 car", email="car@example.com")
    car = Car.objects.create(
        year=2022, brand="BMW", vin="L4CARNOTIFY000001", status="UNLOADED", warehouse=warehouse, client=with_email
    )
    car.unload_date = TODAY
    Car.objects.filter(pk=car.pk).update(unload_date=TODAY)

    assert send_car_unload_notifications_once(car) == []
    assert send_car_unload_notifications_once(car) == []
    assert len(mail.outbox) == 1

    silent = Client.objects.create(name="L4 silent")
    Car.objects.filter(pk=car.pk).update(client=silent)
    car.refresh_from_db()
    assert send_car_unload_notifications_once(car) == []
    assert not NotificationClaim.objects.filter(client=silent).exists()
