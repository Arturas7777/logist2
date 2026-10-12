"""Тесты для tasks_email_cleanup (retention policy)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from core.models.email import ContainerEmail

pytestmark = pytest.mark.django_db


def test_cleanup_old_body_html():
    """body_html старых писем должен очищаться."""
    from core.tasks_email_cleanup import cleanup_old_email_data

    # Создаём старое письмо
    old_email = ContainerEmail.objects.create(
        message_id="<old@test>",
        thread_id="told",
        subject="Old",
        from_addr="a@b.c",
        direction="INCOMING",
        body_html="<html><body>Old HTML</body></html>",
        body_text="Old text",
        received_at=timezone.now() - timedelta(days=100),
    )

    # Создаём свежее письмо
    new_email = ContainerEmail.objects.create(
        message_id="<new@test>",
        thread_id="tnew",
        subject="New",
        from_addr="a@b.c",
        direction="INCOMING",
        body_html="<html><body>New HTML</body></html>",
        body_text="New text",
        received_at=timezone.now() - timedelta(days=10),
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "EMAIL_RETENTION_DAYS", 90)
        report = cleanup_old_email_data()

    assert report["body_html_cleared"] == 1

    old_email.refresh_from_db()
    new_email.refresh_from_db()

    # Старое письмо: body_html очищен, body_text остался
    assert old_email.body_html == ""
    assert old_email.body_text == "Old text"

    # Свежее письмо: не тронуто
    assert new_email.body_html == "<html><body>New HTML</body></html>"


def test_cleanup_old_attachments(tmp_path):
    """Закэшированные вложения старых писем должны удаляться с диска."""
    from core.tasks_email_cleanup import cleanup_old_email_data

    media_root = tmp_path / "media"
    media_root.mkdir()

    # Создаём файл на диске
    att_dir = media_root / "container_emails/2024/01/gmail123"
    att_dir.mkdir(parents=True)
    att_file = att_dir / "00_invoice.pdf"
    att_file.write_bytes(b"PDF content" * 1000)  # ~11 KB

    old_email = ContainerEmail.objects.create(
        message_id="<oldatt@test>",
        thread_id="toldatt",
        from_addr="a@b.c",
        direction="INCOMING",
        gmail_id="gmail123",
        received_at=timezone.now() - timedelta(days=100),
        attachments_json=[
            {
                "filename": "invoice.pdf",
                "size": 11000,
                "content_type": "application/pdf",
                "attachment_id": "att_abc",
                "storage_path": "container_emails/2024/01/gmail123/00_invoice.pdf",
                "is_inline": False,
            }
        ],
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "MEDIA_ROOT", str(media_root))
        m.setattr(settings, "EMAIL_RETENTION_DAYS", 90)
        report = cleanup_old_email_data()

    assert report["attachments_removed"] == 1
    assert report["disk_freed_mb"] > 0

    # Файл удалён с диска
    assert not att_file.exists()

    # storage_path очищен в БД
    old_email.refresh_from_db()
    assert old_email.attachments_json[0]["storage_path"] == ""


def test_cleanup_preserves_metadata():
    """Метаданные писем должны оставаться даже после cleanup."""
    from core.tasks_email_cleanup import cleanup_old_email_data

    email = ContainerEmail.objects.create(
        message_id="<meta@test>",
        thread_id="tmeta",
        subject="Important",
        from_addr="sender@example.com",
        to_addrs="recipient@example.com",
        direction="INCOMING",
        body_html="<html>...</html>",
        received_at=timezone.now() - timedelta(days=100),
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "EMAIL_RETENTION_DAYS", 90)
        cleanup_old_email_data()

    email.refresh_from_db()

    # Метаданные сохранены
    assert email.subject == "Important"
    assert email.from_addr == "sender@example.com"
    assert email.to_addrs == "recipient@example.com"
    assert email.received_at is not None

    # body_html очищен
    assert email.body_html == ""


def test_report_email_disk_usage(tmp_path):
    """Отчёт о дисковом использовании email-данных."""
    from core.tasks_email_cleanup import report_email_disk_usage

    media_root = tmp_path / "media"
    media_root.mkdir()

    # Создаём файлы на диске
    att_dir = media_root / "container_emails/2024/01/gmail123"
    att_dir.mkdir(parents=True)
    (att_dir / "00_file1.pdf").write_bytes(b"x" * 1000)
    (att_dir / "01_file2.jpg").write_bytes(b"y" * 2000)

    ContainerEmail.objects.create(
        message_id="<report1@test>",
        thread_id="treport1",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        attachments_json=[
            {
                "filename": "file1.pdf",
                "storage_path": "container_emails/2024/01/gmail123/00_file1.pdf",
            }
        ]
    )
    ContainerEmail.objects.create(
        message_id="<report2@test>",
        thread_id="treport2",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
        attachments_json=[
            {
                "filename": "file2.jpg",
                "storage_path": "container_emails/2024/01/gmail123/01_file2.jpg",
            }
        ]
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "MEDIA_ROOT", str(media_root))
        report = report_email_disk_usage()

    assert report["total_emails"] == 2
    assert report["emails_with_attachments"] == 2
    assert report["cached_attachments_count"] == 2
    assert report["cached_attachments_size_mb"] > 0
