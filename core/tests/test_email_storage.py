"""Ретеншен почты и отчёт о занятом месте."""

import json
import os
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models.email import ContainerEmail
from core.services.email_storage import disk_usage, run_retention

pytestmark = pytest.mark.django_db

DAY = 86400


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.EMAIL_RETENTION_DAYS = 90
    settings.EMAIL_ATTACHMENT_CACHE_TTL_DAYS = 7
    settings.EMAIL_ATTACHMENT_CACHE_MAX_MB = 0
    return tmp_path


def _email(n, *, age_days, files=(), accessed_days_ago=None, media=None, size=100):
    received = timezone.now() - timedelta(days=age_days)
    attachments = []
    for idx, name in enumerate(files):
        rel = f"container_emails/{received:%Y}/{received:%m}/g{n}/{idx:02d}_{name}"
        attachments.append({"filename": name, "storage_path": rel, "attachment_id": f"a{idx}"})
        path = media / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        ts = time.time() - (accessed_days_ago if accessed_days_ago is not None else age_days) * DAY
        os.utime(path, (ts, ts))
    return ContainerEmail.objects.create(
        message_id=f"<s{n}@test>",
        thread_id=f"t{n}",
        gmail_id=f"g{n}",
        from_addr="a@b.c",
        subject=f"S{n}",
        body_text=f"text {n}",
        body_html=f"<p>{n}</p>",
        received_at=received,
        attachments_json=attachments,
    )


def _file(media, email, idx=0):
    return media / email.attachments_json[idx]["storage_path"]


def test_old_bodies_cleared_metadata_and_links_kept(media):
    old = _email(1, age_days=120)
    new = _email(2, age_days=10)

    report = run_retention()

    assert report["body_html_cleared"] == 1
    old.refresh_from_db()
    new.refresh_from_db()
    assert old.body_html == ""
    assert (old.body_text, old.subject, old.from_addr) == ("text 1", "S1", "a@b.c")
    assert new.body_html == "<p>2</p>"


def test_old_unused_files_removed_recent_and_recently_used_kept(media):
    stale = _email(1, age_days=120, files=["a.pdf"], media=media)
    used = _email(2, age_days=120, files=["b.pdf"], media=media, accessed_days_ago=1)
    recent = _email(3, age_days=10, files=["c.pdf"], media=media, accessed_days_ago=30)

    report = run_retention()

    assert report["files_removed_by_age"] == 1
    assert not _file(media, stale).exists()
    assert not _file(media, stale).parent.exists()
    assert _file(media, used).exists()
    assert _file(media, recent).exists()
    stale.refresh_from_db()
    assert stale.attachments_json[0]["storage_path"]


def test_size_cap_evicts_least_recently_used(media, settings):
    settings.EMAIL_ATTACHMENT_CACHE_MAX_MB = 1
    mb = 1024 * 1024
    lru = _email(1, age_days=5, files=["a.bin"], media=media, size=mb // 2 + 1, accessed_days_ago=4)
    mid = _email(2, age_days=5, files=["b.bin"], media=media, size=mb // 2, accessed_days_ago=2)
    mru = _email(3, age_days=5, files=["c.bin"], media=media, size=10, accessed_days_ago=0)

    report = run_retention()

    assert report["files_removed_by_size_cap"] == 1
    assert not _file(media, lru).exists()
    assert _file(media, mid).exists()
    assert _file(media, mru).exists()


def test_disabled_when_retention_zero(media, settings):
    settings.EMAIL_RETENTION_DAYS = 0
    old = _email(1, age_days=400, files=["a.pdf"], media=media)
    assert run_retention() == {"status": "disabled"}
    old.refresh_from_db()
    assert old.body_html
    assert _file(media, old).exists()


def test_cleanup_task_runs_retention(media):
    from core.tasks_email import cleanup_email_storage

    _email(1, age_days=120)
    assert cleanup_email_storage.apply().get()["body_html_cleared"] == 1


def test_disk_usage_report(media):
    _email(1, age_days=120, files=["a.pdf"], media=media, size=300)
    _email(2, age_days=120, files=["b.pdf"], media=media, size=200, accessed_days_ago=1)
    _email(3, age_days=10, files=["c.pdf"], media=media, size=100)

    report = disk_usage()

    assert report["db"]["emails"] == 3
    assert report["db"]["body_html_chars"] == 3 * len("<p>1</p>")
    assert report["db"]["body_html_chars_older_than_retention"] == 2 * len("<p>1</p>")
    att = report["attachments"]
    assert att["files"] == 3
    assert att["files_older_than_retention"] == 2
    assert att["total_mb"] == round(600 / 1024 / 1024, 2)
    assert report["filesystem"]["free_gb"] > 0
    # Отчёт ничего не удаляет.
    assert len(list((media / "container_emails").rglob("*.pdf"))) == 3


def test_disk_usage_command(media):
    _email(1, age_days=1)
    out = StringIO()
    call_command("email_disk_usage", stdout=out)
    assert json.loads(out.getvalue())["db"]["emails"] == 1
