"""A5: токен Telegram-бота не попадает в логи, NotificationLog и Sentry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from core.services import telegram_service
from logist2.sentry_filters import scrub_telegram_token

TOKEN = "123456789:AAFakeTokenForTests_abc-XYZ"


@pytest.fixture
def bot_token(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    return TOKEN


def _connection_error():
    return requests.ConnectionError(
        f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: "
        f"/bot{TOKEN}/sendMessage (Caused by NewConnectionError)"
    )


def test_send_message_error_has_type_and_no_token(bot_token):
    with patch.object(telegram_service.requests, "post", side_effect=_connection_error()):
        ok, error = telegram_service.send_telegram_message(1, "hi")

    assert ok is False
    assert error.startswith("ConnectionError: ")
    assert TOKEN not in error
    assert "/bot***/sendMessage" in error


def test_get_updates_error_log_has_no_token(bot_token):
    with (
        patch.object(telegram_service.requests, "get", side_effect=_connection_error()),
        patch.object(telegram_service, "logger") as log,
    ):
        assert telegram_service.process_telegram_starts() == 0

    message, *args = log.error.call_args.args
    assert TOKEN not in message % tuple(args)


def test_mask_without_configured_token_still_masks_url(settings):
    settings.TELEGRAM_BOT_TOKEN = ""

    assert telegram_service.mask_bot_token(f"https://api.telegram.org/bot{TOKEN}/getUpdates") == (
        "https://api.telegram.org/bot***/getUpdates"
    )


def test_mask_keeps_unrelated_urls():
    assert telegram_service.mask_bot_token("/bottles/42") == "/bottles/42"


def test_sentry_filter_scrubs_breadcrumbs_and_events(bot_token):
    crumb = {"category": "httplib", "data": {"url": f"https://api.telegram.org/bot{TOKEN}/sendMessage"}}
    event = {
        "exception": {"values": [{"value": f"boom {TOKEN}", "stacktrace": {"frames": [{"vars": {"t": TOKEN}}]}}]},
        "extra": ("x", f"/bot{TOKEN}/getUpdates"),
    }

    scrubbed_crumb = scrub_telegram_token(crumb, {})
    scrubbed_event = scrub_telegram_token(event, {})

    assert TOKEN not in repr(scrubbed_crumb)
    assert TOKEN not in repr(scrubbed_event)
    assert scrubbed_crumb["data"]["url"] == "https://api.telegram.org/bot***/sendMessage"
