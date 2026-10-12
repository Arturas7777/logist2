"""Тесты для management-команды get_email_attachment."""

from __future__ import annotations

import json
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from core.models.email import ContainerEmail

pytestmark = pytest.mark.django_db


def test_get_attachment_info_only():
    """Режим --info-only возвращает метаданные без скачивания."""
    email = ContainerEmail.objects.create(
        message_id="<info@test>",
        thread_id="tinfo",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        gmail_id="gmail123",
        attachments_json=[
            {
                "filename": "invoice.pdf",
                "size": 123456,
                "content_type": "application/pdf",
                "attachment_id": "att_abc",
                "storage_path": "container_emails/2024/01/gmail123/00_invoice.pdf",
                "is_inline": False,
            }
        ],
    )

    out = StringIO()
    call_command(
        "get_email_attachment",
        str(email.pk),
        "0",
        "--info-only",
        stdout=out,
    )

    data = json.loads(out.getvalue())
    assert data["filename"] == "invoice.pdf"
    assert data["size"] == 123456
    assert data["cached"] is True


def test_get_attachment_from_cache(tmp_path):
    """Если файл уже закэширован — отдаём его без обращения к Gmail."""
    # Создаём временный файл
    media_root = tmp_path / "media"
    media_root.mkdir()
    cached_file = media_root / "container_emails/2024/01/gmail123/00_invoice.pdf"
    cached_file.parent.mkdir(parents=True)
    cached_file.write_bytes(b"PDF content")

    email = ContainerEmail.objects.create(
        message_id="<cache@test>",
        thread_id="tcache",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        gmail_id="gmail123",
        attachments_json=[
            {
                "filename": "invoice.pdf",
                "size": 11,
                "content_type": "application/pdf",
                "attachment_id": "att_abc",
                "storage_path": "container_emails/2024/01/gmail123/00_invoice.pdf",
                "is_inline": False,
            }
        ],
    )

    out = BytesIO()
    with patch.object(settings, "MEDIA_ROOT", str(media_root)):
        call_command(
            "get_email_attachment",
            str(email.pk),
            "0",
            stdout=out,
        )

    assert out.getvalue() == b"PDF content"


def test_get_attachment_fetch_from_gmail(tmp_path):
    """Если файл не закэширован — скачиваем из Gmail и сохраняем."""
    media_root = tmp_path / "media"
    media_root.mkdir()

    email = ContainerEmail.objects.create(
        message_id="<fetch@test>",
        thread_id="tfetch",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        gmail_id="gmail123",
        attachments_json=[
            {
                "filename": "invoice.pdf",
                "size": 0,
                "content_type": "application/pdf",
                "attachment_id": "att_abc",
                "storage_path": "",  # Не закэширован
                "is_inline": False,
            }
        ],
    )

    mock_client = MagicMock()
    mock_client.get_attachment.return_value = b"Downloaded PDF"

    out = BytesIO()
    with patch.object(settings, "MEDIA_ROOT", str(media_root)):
        with patch("core.management.commands.get_email_attachment.GmailApiClient", return_value=mock_client):
            call_command(
                "get_email_attachment",
                str(email.pk),
                "0",
                stdout=out,
            )

    assert out.getvalue() == b"Downloaded PDF"

    # Проверяем что файл сохранён в кэш
    email.refresh_from_db()
    storage_path = email.attachments_json[0]["storage_path"]
    assert storage_path != ""
    cached_file = media_root / storage_path
    assert cached_file.exists()
    assert cached_file.read_bytes() == b"Downloaded PDF"


def test_get_attachment_invalid_index():
    """Некорректный индекс вложения должен вызвать ошибку."""
    email = ContainerEmail.objects.create(
        message_id="<invalid@test>",
        thread_id="tinvalid",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        attachments_json=[
            {"filename": "file.pdf", "attachment_id": "att_1"}
        ],
    )

    with pytest.raises(SystemExit):
        call_command("get_email_attachment", str(email.pk), "5")
