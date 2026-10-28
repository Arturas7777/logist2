"""Тесты email_ingest: обработка 404 от Gmail (письмо удалено из ящика).

Регрессия 2026-07-21: удалённые из ящика письма возвращали 404 на каждом
прогоне, ingest_errors не давал сдвинуть last_history_id, и те же письма
ретраились вечно (~20K ERROR-событий в Sentry за день).
"""

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.services.email_ingest import SyncReport, _ingest_one, _is_gmail_not_found


def _http_error(status: int) -> HttpError:
    return HttpError(resp=httplib2.Response({"status": str(status)}), content=b"error")


class TestIsGmailNotFound:
    def test_404_is_not_found(self):
        assert _is_gmail_not_found(_http_error(404)) is True

    def test_other_http_statuses_are_not(self):
        assert _is_gmail_not_found(_http_error(500)) is False
        assert _is_gmail_not_found(_http_error(403)) is False

    def test_non_http_error_is_not(self):
        assert _is_gmail_not_found(TimeoutError("read timed out")) is False


@pytest.mark.django_db
class TestIngestOneNotFound:
    def test_404_skips_without_ingest_error(self):
        """404 → not_found_skipped, ingest_errors=0 (курсор истории сдвинется)."""

        class FakeClient:
            def get_message(self, gmail_id):
                raise _http_error(404)

        report = SyncReport()
        _ingest_one(FakeClient(), "deadbeef", {}, report)

        assert report.not_found_skipped == 1
        assert report.ingest_errors == 0
        assert report.errors == []

    def test_transient_error_still_counts(self):
        """Не-404 ошибки по-прежнему блокируют сдвиг курсора (retry)."""

        class FakeClient:
            def get_message(self, gmail_id):
                raise _http_error(500)

        report = SyncReport()
        _ingest_one(FakeClient(), "deadbeef", {}, report)

        assert report.not_found_skipped == 0
        assert report.ingest_errors == 1
        assert len(report.errors) == 1


class _BrokenClient:
    def get_message(self, gmail_id):
        raise _http_error(500)


@pytest.mark.django_db
class TestIngestQuarantine:
    def test_poison_message_quarantined_after_n_failures(self, settings):
        from core.models_email import GmailIngestFailure

        settings.GMAIL_QUARANTINE_AFTER = 3
        reports = [SyncReport() for _ in range(4)]
        for report in reports:
            _ingest_one(_BrokenClient(), "poison", {}, report)

        assert [r.ingest_errors for r in reports] == [1, 1, 0, 0]
        assert [r.quarantined for r in reports] == [0, 0, 1, 1]
        failure = GmailIngestFailure.objects.get(gmail_id="poison")
        assert failure.quarantined_at is not None
        assert failure.attempts == 3

    def test_success_clears_failure_counter(self, settings):
        from core.models_email import GmailIngestFailure

        _ingest_one(_BrokenClient(), "flaky", {}, SyncReport())
        assert GmailIngestFailure.objects.filter(gmail_id="flaky").exists()

        _ingest_one(_FakeMessageClient(_message("flaky")), "flaky", {}, SyncReport())

        assert not GmailIngestFailure.objects.filter(gmail_id="flaky").exists()


def _message(gmail_id, message_id=None, attachments=()):
    from django.utils import timezone

    from core.services.gmail_client import ParsedMessage

    return ParsedMessage(
        gmail_id=gmail_id,
        thread_id=f"thread-{gmail_id}",
        history_id=1,
        message_id=message_id or f"<{gmail_id}@test>",
        in_reply_to="",
        references="",
        from_addr="sender@example.com",
        to_addrs="us@example.com",
        cc_addrs="",
        subject=f"Subject {gmail_id}",
        body_text="Body",
        body_html="",
        snippet="Body",
        received_at=timezone.now(),
        labels=["INBOX"],
        attachments=list(attachments),
    )


class _FakeMessageClient:
    def __init__(self, msg):
        self.msg = msg
        self.attachment_calls = 0

    def get_message(self, gmail_id):
        return self.msg

    def get_attachment(self, gmail_id, attachment_id):
        self.attachment_calls += 1
        return b"data"


@pytest.mark.django_db
def test_known_message_id_does_not_download_attachments():
    from django.utils import timezone

    from core.models_email import ContainerEmail
    from core.services.gmail_client import ParsedAttachment

    existing = ContainerEmail.objects.create(
        message_id="<sent-from-system@test>",
        thread_id="t",
        direction=ContainerEmail.DIRECTION_OUTGOING,
        from_addr="us@example.com",
        subject="s",
        received_at=timezone.now(),
    )
    attachment = ParsedAttachment(
        filename="a.pdf", mime_type="application/pdf", size=10, attachment_id="att-1", is_inline=False, content_id=""
    )
    client = _FakeMessageClient(_message("gm-1", message_id=existing.message_id, attachments=[attachment]))

    _ingest_one(client, "gm-1", {}, SyncReport())

    assert client.attachment_calls == 0
    existing.refresh_from_db()
    assert existing.gmail_id == "gm-1"
