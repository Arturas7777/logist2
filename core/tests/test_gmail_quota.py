"""Отказы Gmail по квоте: распознавание, пауза синка, курсор истории."""

import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.models.email import GmailSyncState
from core.services import email_ingest, gmail_quota
from core.services.gmail_client import GmailApiClient
from core.services.gmail_quota import GmailRateLimited, is_rate_limit_error


def _http_error(status, reason=None, headers=None):
    resp = httplib2.Response({"status": str(status), **(headers or {})})
    body = {"error": {"code": status, "message": "x", "errors": [{"reason": reason}] if reason else []}}
    return HttpError(resp=resp, content=json.dumps(body).encode())


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (429, None, True),
        (403, "rateLimitExceeded", True),
        (403, "userRateLimitExceeded", True),
        (403, "insufficientPermissions", False),
        (404, None, False),
        (500, None, False),
    ],
)
def test_is_rate_limit_error(status, reason, expected):
    assert is_rate_limit_error(_http_error(status, reason)) is expected


def test_non_http_error_is_not_rate_limit():
    assert is_rate_limit_error(TimeoutError()) is False


def _client():
    return GmailApiClient(client_id="id", client_secret="secret", refresh_token="token", user_email="me")


class _Request:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def execute(self):
        self.calls += 1
        raise self.exc


def test_client_converts_rate_limit_without_retrying():
    request = _Request(_http_error(429, headers={"retry-after": "120"}))
    with pytest.raises(GmailRateLimited) as info:
        _client()._execute(request)
    assert request.calls == 1
    assert info.value.retry_after == 120


def test_client_keeps_other_http_errors():
    with pytest.raises(HttpError):
        _client()._execute(_Request(_http_error(500)))


def test_cooldown_escalates_and_resets():
    assert gmail_quota.cooldown_remaining() == 0
    assert gmail_quota.start_cooldown() == 300
    assert gmail_quota.start_cooldown() == 600
    assert gmail_quota.start_cooldown(retry_after=7200) == 3600
    assert gmail_quota.cooldown_remaining() > 0
    gmail_quota.reset_cooldown()
    assert gmail_quota.cooldown_remaining() == 0
    assert gmail_quota.start_cooldown() == 300


# ── sync_mailbox ────────────────────────────────────────────────────────────


class FakeGmail:
    def __init__(self, *, history=(), rate_limit_on=None):
        self.history = list(history)
        self.rate_limit_on = rate_limit_on
        self.calls = []

    def _maybe_fail(self, name):
        self.calls.append(name)
        if self.rate_limit_on == name:
            raise GmailRateLimited("429")

    def get_profile(self):
        self._maybe_fail("get_profile")
        return {"emailAddress": "box@test", "historyId": "500"}

    def list_history(self, start):
        self._maybe_fail("list_history")
        yield from self.history

    def get_message(self, gmail_id):
        self._maybe_fail("get_message")
        raise AssertionError("не ожидается в этом тесте")


@pytest.fixture
def gmail(settings, monkeypatch):
    settings.GMAIL_ENABLED = True
    holder = {}

    def factory():
        return holder["client"]

    monkeypatch.setattr(email_ingest, "GmailApiClient", factory)
    return holder


@pytest.mark.django_db
def test_rate_limit_stops_sync_keeps_cursor_and_starts_cooldown(gmail):
    GmailSyncState.objects.create(user_email="box@test", last_history_id=100)
    gmail["client"] = FakeGmail(history=["m1", "m2", "m3"], rate_limit_on="get_message")

    report = email_ingest.sync_mailbox()

    assert report.mode == "rate_limited"
    assert gmail["client"].calls.count("get_message") == 1
    state = GmailSyncState.objects.get()
    assert state.last_history_id == 100
    assert "rate_limited" in state.last_error
    assert gmail_quota.cooldown_remaining() > 0


@pytest.mark.django_db
def test_sync_skipped_during_cooldown(gmail):
    gmail_quota.start_cooldown()
    gmail["client"] = FakeGmail()

    report = email_ingest.sync_mailbox()

    assert report.mode == "cooldown"
    assert gmail["client"].calls == []


@pytest.mark.django_db
def test_successful_sync_moves_cursor_and_resets_backoff(gmail):
    GmailSyncState.objects.create(user_email="box@test", last_history_id=100)
    gmail_quota.start_cooldown()
    gmail_quota.start_cooldown()
    gmail_quota.cache.delete(gmail_quota._COOLDOWN_UNTIL_KEY)
    gmail["client"] = FakeGmail(history=[])

    report = email_ingest.sync_mailbox()

    assert report.mode == "incremental"
    assert gmail["client"].calls == ["get_profile", "list_history"]
    assert GmailSyncState.objects.get().last_history_id == 500
    assert gmail_quota.start_cooldown() == 300
