"""Выдача вложений: кэш CRM, однократная докачка из Gmail, view и команда."""

import json
import os
import time
from io import BytesIO, StringIO
from types import SimpleNamespace

import httplib2
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from googleapiclient.errors import HttpError

from core.models.email import ContainerEmail
from core.services import gmail_quota
from core.services.email_attachments import AttachmentUnavailable, get_attachment_file
from core.services.gmail_quota import GmailRateLimited

pytestmark = pytest.mark.django_db

PATH = "container_emails/2026/10/g1/00_invoice.pdf"


def _http_error(status):
    return HttpError(resp=httplib2.Response({"status": str(status)}), content=b"{}")


class FakeClient:
    def __init__(self, data=b"from-gmail", attachments=None, attachment_error=None, message_error=None):
        self.data = data
        self.attachments = attachments or []
        self.attachment_error = attachment_error
        self.message_error = message_error
        self.attachment_calls = []
        self.message_calls = 0

    def __call__(self):
        return self

    def get_attachment(self, gmail_id, attachment_id):
        self.attachment_calls.append((gmail_id, attachment_id))
        if self.attachment_error and len(self.attachment_calls) == 1:
            raise self.attachment_error
        return self.data

    def get_message(self, gmail_id):
        self.message_calls += 1
        if self.message_error:
            raise self.message_error
        return SimpleNamespace(attachments=self.attachments)


def _no_gmail():
    raise AssertionError("Gmail не должен вызываться")


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _email(**meta):
    att = {
        "filename": "invoice.pdf",
        "size": 10,
        "content_type": "application/pdf",
        "attachment_id": "att-1",
        "storage_path": PATH,
        "skipped_reason": "",
        "is_inline": False,
    }
    att.update(meta)
    return ContainerEmail.objects.create(
        message_id="<att@test>",
        thread_id="t1",
        gmail_id="g1",
        from_addr="a@b.c",
        received_at=timezone.datetime(2026, 10, 1, tzinfo=timezone.get_current_timezone()),
        attachments_json=[att],
    )


def test_cache_hit_does_not_touch_gmail_and_refreshes_mtime(media):
    file = media / PATH
    file.parent.mkdir(parents=True)
    file.write_bytes(b"cached")
    old = time.time() - 30 * 86400
    os.utime(file, (old, old))

    path, _ = get_attachment_file(_email(), 0, client_factory=_no_gmail)

    assert path.read_bytes() == b"cached"
    assert file.stat().st_mtime > old + 86400


def test_evicted_file_is_refetched_to_same_path(media):
    email = _email()
    client = FakeClient()

    path, meta = get_attachment_file(email, 0, client_factory=client)

    assert path == (media / PATH).resolve()
    assert path.read_bytes() == b"from-gmail"
    assert client.attachment_calls == [("g1", "att-1")]
    assert meta["storage_path"] == PATH
    # Второй вызов — уже из кэша.
    get_attachment_file(email, 0, client_factory=_no_gmail)


def test_never_stored_attachment_is_cached_and_remembered(media):
    email = _email(storage_path="", skipped_reason="too_large")

    path, _ = get_attachment_file(email, 0, client_factory=FakeClient())

    email.refresh_from_db()
    meta = email.attachments_json[0]
    assert meta["storage_path"] == PATH
    assert meta["skipped_reason"] == ""
    assert meta["size"] == len(b"from-gmail")
    assert (media / PATH).read_bytes() == b"from-gmail"


def test_inline_marker_is_kept_after_caching(media):
    email = _email(storage_path="", skipped_reason="inline", is_inline=True)
    get_attachment_file(email, 0, client_factory=FakeClient())
    email.refresh_from_db()
    assert email.attachments_json[0]["skipped_reason"] == "inline"


def test_stale_attachment_id_falls_back_to_fresh_message(media):
    fresh = [SimpleNamespace(filename="invoice.pdf", attachment_id="att-new")]
    client = FakeClient(attachments=fresh, attachment_error=_http_error(400))

    get_attachment_file(_email(), 0, client_factory=client)

    assert client.message_calls == 1
    assert client.attachment_calls == [("g1", "att-1"), ("g1", "att-new")]


def test_outgoing_without_attachment_id_uses_message(media):
    other = SimpleNamespace(filename="other.pdf", attachment_id="x")
    mine = SimpleNamespace(filename="invoice.pdf", attachment_id="att-out")
    client = FakeClient(attachments=[other, mine])

    get_attachment_file(_email(attachment_id=""), 0, client_factory=client)

    assert client.attachment_calls == [("g1", "att-out")]


def test_no_gmail_id_is_unavailable(media):
    email = _email()
    email.gmail_id = ""
    with pytest.raises(AttachmentUnavailable, match="gmail_id"):
        get_attachment_file(email, 0, client_factory=_no_gmail)


def test_cooldown_blocks_gmail(media):
    gmail_quota.start_cooldown()
    with pytest.raises(AttachmentUnavailable, match="паузе"):
        get_attachment_file(_email(), 0, client_factory=_no_gmail)


def test_rate_limit_starts_cooldown(media):
    client = FakeClient(attachment_error=GmailRateLimited("429"))
    with pytest.raises(AttachmentUnavailable, match="квоте"):
        get_attachment_file(_email(), 0, client_factory=client)
    assert gmail_quota.cooldown_remaining() > 0
    assert client.message_calls == 0


def test_deleted_in_gmail_is_unavailable(media):
    client = FakeClient(attachment_error=_http_error(404), message_error=_http_error(404))
    with pytest.raises(AttachmentUnavailable, match="удалено"):
        get_attachment_file(_email(), 0, client_factory=client)


def test_path_outside_storage_is_rejected_before_gmail(media):
    with pytest.raises(AttachmentUnavailable, match="storage_path"):
        get_attachment_file(_email(storage_path="../../etc/passwd"), 0, client_factory=_no_gmail)


def test_bad_index():
    with pytest.raises(IndexError):
        get_attachment_file(_email(), 3, client_factory=_no_gmail)


# ── view админки ────────────────────────────────────────────────────────────


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user(username="att", password="x", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


def test_view_refetches_evicted_file(media, staff, monkeypatch):
    email = _email()
    monkeypatch.setattr("core.services.gmail_client.GmailApiClient", FakeClient(data=b"pdf-bytes"))

    resp = staff.get(f"/core/emails/{email.pk}/attachment/0/")

    assert resp.status_code == 200
    assert b"".join(resp.streaming_content) == b"pdf-bytes"
    assert 'filename="invoice.pdf"' in resp["Content-Disposition"]


def test_view_unavailable_is_410(media, staff):
    gmail_quota.start_cooldown()
    email = _email()
    assert staff.get(f"/core/emails/{email.pk}/attachment/0/").status_code == 410
    assert staff.get(f"/core/emails/{email.pk}/attachment/5/").status_code == 404


# ── management-команда ──────────────────────────────────────────────────────


def test_command_info_does_not_call_gmail(media, monkeypatch):
    monkeypatch.setattr("core.services.gmail_client.GmailApiClient", _no_gmail)
    email = _email()
    out = StringIO()
    call_command("get_email_attachment", str(email.pk), "0", "--info", stdout=out)
    assert json.loads(out.getvalue()) == {
        "email_id": email.pk,
        "index": 0,
        "filename": "invoice.pdf",
        "size": 10,
        "content_type": "application/pdf",
        "is_inline": False,
        "cached": False,
    }


def test_command_writes_bytes_to_stdout_and_output(media, monkeypatch, tmp_path):
    monkeypatch.setattr("core.services.gmail_client.GmailApiClient", FakeClient(data=b"%PDF-1"))
    email = _email()

    out = BytesIO()
    call_command("get_email_attachment", str(email.pk), "0", stdout=out)
    assert out.getvalue() == b"%PDF-1"

    target = tmp_path / "copy.pdf"
    call_command("get_email_attachment", str(email.pk), "0", "--output", str(target))
    assert target.read_bytes() == b"%PDF-1"


def test_command_errors(media):
    email = _email()
    gmail_quota.start_cooldown()
    with pytest.raises(CommandError, match="не найдено"):
        call_command("get_email_attachment", "999999", "0")
    with pytest.raises(CommandError, match="индексом 7"):
        call_command("get_email_attachment", str(email.pk), "7")
    with pytest.raises(CommandError, match="паузе"):
        call_command("get_email_attachment", str(email.pk), "0", stdout=BytesIO())
