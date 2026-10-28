"""L4: исходящее письмо — запись PENDING до отправки, без гонки с Gmail-синком."""

from __future__ import annotations

import pytest

from core.models import Container
from core.models_email import ContainerEmail
from core.services import email_compose
from core.services.gmail_sender import SendError

pytestmark = pytest.mark.django_db


@pytest.fixture
def container():
    return Container.objects.create(number="L4-COMPOSE1", status="FLOATING")


@pytest.fixture(autouse=True)
def fake_gmail(monkeypatch, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr(email_compose, "GmailApiClient", lambda: object())


def _compose(container):
    return email_compose.compose_new_email(
        container=container, user=None, to="partner@example.com", subject="Hello", body_text="Body"
    )


def test_record_is_pending_during_send_and_sent_after(container, monkeypatch):
    seen = {}

    def fake_send(*, gmail_client, mime_msg, thread_id):
        email = ContainerEmail.objects.get(message_id=mime_msg["Message-ID"])
        seen["status"] = email.send_status
        seen["linked"] = email.containers.filter(pk=container.pk).exists()
        return {"id": "gm-sent-1", "threadId": "th-1", "labelIds": ["SENT"]}

    monkeypatch.setattr(email_compose, "send_message", fake_send)

    email = _compose(container)

    assert seen == {"status": ContainerEmail.SEND_STATUS_PENDING, "linked": True}
    email.refresh_from_db()
    assert email.send_status == ContainerEmail.SEND_STATUS_SENT
    assert (email.gmail_id, email.thread_id, email.labels_json) == ("gm-sent-1", "th-1", ["SENT"])


def test_sync_racing_the_send_does_not_break_persist(container, monkeypatch):
    def fake_send(*, gmail_client, mime_msg, thread_id):
        # Gmail-синк успел подтянуть отправленное письмо раньше нас.
        ContainerEmail.objects.filter(message_id=mime_msg["Message-ID"]).update(
            gmail_id="gm-sent-2", labels_json=["SENT"]
        )
        return {"id": "gm-sent-2", "threadId": "th-2", "labelIds": ["SENT"]}

    monkeypatch.setattr(email_compose, "send_message", fake_send)

    email = _compose(container)

    assert ContainerEmail.objects.count() == 1
    email.refresh_from_db()
    assert email.send_status == ContainerEmail.SEND_STATUS_SENT
    assert email.gmail_id == "gm-sent-2"


def test_failed_send_marks_record_failed(container, monkeypatch):
    def fake_send(*, gmail_client, mime_msg, thread_id):
        raise SendError("quota")

    monkeypatch.setattr(email_compose, "send_message", fake_send)

    with pytest.raises(SendError):
        _compose(container)

    email = ContainerEmail.objects.get()
    assert email.send_status == ContainerEmail.SEND_STATUS_FAILED
    assert "quota" in email.send_error
    assert email.gmail_id == ""
