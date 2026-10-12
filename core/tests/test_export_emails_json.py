"""Тесты для management-команды export_emails_json."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Container
from core.models.email import ContainerEmail

pytestmark = pytest.mark.django_db


def test_export_emails_basic():
    """Базовая проверка экспорта писем."""
    container = Container.objects.create(number="MSKU1234567", status="FLOATING")
    email = ContainerEmail.objects.create(
        message_id="<test@example.com>",
        thread_id="thread123",
        subject="Test email",
        from_addr="test@example.com",
        direction="INCOMING",
        received_at=timezone.now(),
    )
    email.containers.add(container)

    out = StringIO()
    call_command("export_emails_json", "--limit=10", stdout=out)

    data = json.loads(out.getvalue())
    assert data["count"] == 1
    assert len(data["emails"]) == 1
    assert data["emails"][0]["subject"] == "Test email"
    assert data["emails"][0]["from"] == "test@example.com"
    assert "MSKU1234567" in data["emails"][0]["linked_containers"]


def test_export_emails_with_filters():
    """Фильтрация по sender/subject/direction."""
    ContainerEmail.objects.create(
        message_id="<invoice@test>",
        thread_id="thread1",
        subject="Invoice 123",
        from_addr="maersk@example.com",
        direction="INCOMING",
        received_at=timezone.now(),
    )
    ContainerEmail.objects.create(
        message_id="<booking@test>",
        thread_id="thread2",
        subject="Booking 456",
        from_addr="cma@example.com",
        direction="OUTGOING",
        received_at=timezone.now(),
    )

    # Фильтр по sender
    out = StringIO()
    call_command("export_emails_json", "--sender=maersk", stdout=out)
    data = json.loads(out.getvalue())
    assert data["count"] == 1
    assert "Invoice" in data["emails"][0]["subject"]

    # Фильтр по direction
    out = StringIO()
    call_command("export_emails_json", "--direction=OUTGOING", stdout=out)
    data = json.loads(out.getvalue())
    assert data["count"] == 1
    assert "Booking" in data["emails"][0]["subject"]


def test_export_emails_with_attachments():
    """Проверка вывода вложений."""
    email = ContainerEmail.objects.create(
        message_id="<att@test>",
        thread_id="thread3",
        subject="With attachment",
        from_addr="sender@test.com",
        direction="INCOMING",
        received_at=timezone.now(),
        attachments_json=[
            {
                "filename": "invoice.pdf",
                "size": 123456,
                "content_type": "application/pdf",
                "attachment_id": "att_123",
                "storage_path": "container_emails/2024/01/gmail123/00_invoice.pdf",
                "is_inline": False,
            }
        ],
    )

    out = StringIO()
    call_command("export_emails_json", "--limit=10", stdout=out)
    data = json.loads(out.getvalue())

    assert len(data["emails"]) == 1
    atts = data["emails"][0]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "invoice.pdf"
    assert atts[0]["size"] == 123456
    assert atts[0]["available"] is True


def test_export_emails_exclude_hidden():
    """Скрытые письма (дубли/фильтрованные) не должны попадать в вывод."""
    ContainerEmail.objects.create(
        message_id="<normal@test>",
        thread_id="t1",
        subject="Normal",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        hidden_reason="",
    )
    ContainerEmail.objects.create(
        message_id="<dup@test>",
        thread_id="t2",
        subject="Duplicate",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        hidden_reason="DUPLICATE",
    )
    ContainerEmail.objects.create(
        message_id="<filt@test>",
        thread_id="t3",
        subject="Filtered",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        hidden_reason="FILTERED",
    )

    out = StringIO()
    call_command("export_emails_json", stdout=out)
    data = json.loads(out.getvalue())

    assert data["count"] == 1
    assert data["emails"][0]["subject"] == "Normal"


def test_export_emails_since_filter():
    """Фильтр по --since."""
    now = timezone.now()
    old_email = ContainerEmail.objects.create(
        message_id="<old@test>",
        thread_id="told",
        subject="Old",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=now - timezone.timedelta(days=10),
    )
    new_email = ContainerEmail.objects.create(
        message_id="<new@test>",
        thread_id="tnew",
        subject="New",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=now - timezone.timedelta(hours=1),
    )

    out = StringIO()
    call_command("export_emails_json", "--since=5d", stdout=out)
    data = json.loads(out.getvalue())

    assert data["count"] == 1
    assert data["emails"][0]["subject"] == "New"
