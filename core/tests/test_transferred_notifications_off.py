"""Передача авто (TRANSFERRED) никому ничего не шлёт.

По умолчанию ``CAR_TRANSFERRED_NOTIFICATIONS`` выключен: ни письмо, ни задача
Celery. CLI, админское действие и ``Car.save()`` проверяются отдельно.
"""

from __future__ import annotations

import datetime
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.utils import timezone

from core.admin.car import CarAdmin
from core.models import Car, Client, Container, Warehouse
from core.models.clients import ClientEmail
from core.services.bot_runtime import issue_token
from core.services.client_notifications import notify_car_transferred
from core.tasks import send_car_transferred_notification_task

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _ready_car(vin: str, *, client_name: str, email: str) -> Car:
    warehouse = Warehouse.objects.create(name=f"WH-{vin[-4:]}")
    container = Container.objects.create(
        number=f"TR{vin[-8:]}",
        status="UNLOADED",
        warehouse=warehouse,
        unload_date=TODAY - datetime.timedelta(days=3),
    )
    client = Client.objects.create(name=client_name, email=email, notification_enabled=True)
    ClientEmail.objects.create(client=client, email=email, is_primary=True)
    return Car.objects.create(
        year=2018,
        brand="Nissan",
        vin=vin,
        status="UNLOADED",
        container=container,
        warehouse=warehouse,
        client=client,
        unload_date=container.unload_date,
    )


def test_cli_transfer_without_no_notify_sends_nothing(django_capture_on_commit_callbacks):
    car = _ready_car("TRNFCLI0VIN000001", client_name="Smerdov", email="smerdov@example.com")
    _principal, token = issue_token("neto-silent", "neto")
    out = StringIO()
    with (
        patch("core.tasks.send_car_transferred_notification_task.delay") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        call_command(
            "bot",
            "transfer-cars",
            "--token",
            token,
            "--vins",
            car.vin,
            "--transfer-date",
            TODAY.isoformat(),
            "--apply",
            "--idempotency-key",
            "handover-off-1",
            stdout=out,
        )
    car.refresh_from_db()
    payload = json.loads(out.getvalue())
    assert car.status == "TRANSFERRED"
    assert payload["ok"] is True
    assert payload["notifications"] == []
    assert payload["result"]["transferred"] == [car.vin]
    queued.assert_not_called()
    assert mail.outbox == []


def test_cli_transfer_with_no_notify_still_accepted(django_capture_on_commit_callbacks):
    car = _ready_car("TRNFNON0VIN000001", client_name="S-Line", email="sline@example.com")
    _principal, token = issue_token("neto-nonotify", "neto")
    out = StringIO()
    with (
        patch("core.tasks.send_car_transferred_notification_task.delay") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        call_command(
            "bot",
            "transfer-cars",
            "--token",
            token,
            "--vins",
            car.vin,
            "--transfer-date",
            TODAY.isoformat(),
            "--apply",
            "--idempotency-key",
            "handover-off-2",
            "--no-notify",
            stdout=out,
        )
    assert json.loads(out.getvalue())["ok"] is True
    car.refresh_from_db()
    assert car.status == "TRANSFERRED"
    queued.assert_not_called()
    assert mail.outbox == []


def test_model_save_to_transferred_sends_nothing(django_capture_on_commit_callbacks):
    car = _ready_car("TRNFMDL0VIN000001", client_name="Model Client", email="model@example.com")
    with (
        patch("core.tasks.send_car_transferred_notification_task.delay") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        car.status = "TRANSFERRED"
        car.transfer_date = TODAY
        car.save()
    queued.assert_not_called()
    assert mail.outbox == []
    assert notify_car_transferred(car) == {"email": 0, "telegram": 0}
    assert send_car_transferred_notification_task(car.pk) is None
    assert mail.outbox == []


def test_admin_action_set_transferred_sends_nothing(django_capture_on_commit_callbacks):
    car = _ready_car("TRNFADM0VIN000001", client_name="Admin Client", email="admin-client@example.com")
    admin = CarAdmin(Car, AdminSite())
    request = RequestFactory().post("/")
    with (
        patch.object(admin, "message_user"),
        patch("core.tasks.send_car_transferred_notification_task.delay") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        admin.set_transferred_today(request, Car.objects.filter(pk=car.pk))
    car.refresh_from_db()
    assert car.status == "TRANSFERRED"
    queued.assert_not_called()
    assert mail.outbox == []


@override_settings(CAR_TRANSFERRED_NOTIFICATIONS=True)
def test_setting_reenables_transferred_mail(django_capture_on_commit_callbacks):
    car = _ready_car("TRNFON00VIN000001", client_name="Opt In", email="optin@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        car.status = "TRANSFERRED"
        car.transfer_date = TODAY
        car.save()
    assert len(mail.outbox) == 1
    assert "optin@example.com" in mail.outbox[0].to
    assert car.vin in mail.outbox[0].subject
