"""L4: getUpdates с offset — обработанные апдейты подтверждаются.

Без offset Telegram отдаёт одни и те же 100 непрочитанных апдейтов, и
после их накопления новые ``/start`` не обрабатывались.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.management import call_command

from core.models import Client
from core.services import telegram_service

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _bot(settings):
    settings.TELEGRAM_BOT_TOKEN = "1:test"
    cache.clear()
    yield
    cache.clear()


def _response(*updates):
    resp = MagicMock()
    resp.content = b"x"
    resp.json.return_value = {"ok": True, "result": list(updates)}
    return resp


def _update(update_id, chat_id, text, first_name="Ivan"):
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id, "type": "private", "first_name": first_name}, "text": text},
    }


def test_first_poll_without_offset_then_offset_after_last_update():
    with (
        patch.object(telegram_service.requests, "get", return_value=_response(_update(41, 7, "hi"))) as get,
        patch.object(telegram_service, "send_telegram_message", return_value=(True, "")),
    ):
        telegram_service.process_telegram_starts()
        telegram_service.process_telegram_starts()

    first, second = (c.kwargs["params"] for c in get.call_args_list)
    assert "offset" not in first
    assert second["offset"] == 42


def test_start_beyond_first_hundred_is_linked():
    client = Client.objects.create(name="Offset Client")
    token = client.ensure_telegram_link_token()
    pending = [_update(i, 1000 + i, "hello") for i in range(1, 101)] + [_update(101, 555, f"/start {token}")]

    def fake_get(url, params, timeout):
        # Как Telegram: подтверждённые (update_id < offset) не отдаются, не больше limit.
        fresh = [u for u in pending if u["update_id"] >= params.get("offset", 0)]
        return _response(*fresh[: params["limit"]])

    with (
        patch.object(telegram_service.requests, "get", side_effect=fake_get),
        patch.object(telegram_service, "send_telegram_message", return_value=(True, "")),
    ):
        assert telegram_service.process_telegram_starts() == 0
        assert telegram_service.process_telegram_starts() == 1

    client.refresh_from_db()
    assert client.telegram_chat_id == "555"


def test_failed_poll_keeps_offset():
    cache.set(telegram_service._TELEGRAM_LAST_REPLY_KEY, 10, 60)
    bad = MagicMock(content=b"x")
    bad.json.return_value = {"ok": False, "description": "Conflict"}
    with patch.object(telegram_service.requests, "get", return_value=bad):
        assert telegram_service.process_telegram_starts() == 0
    assert cache.get(telegram_service._TELEGRAM_LAST_REPLY_KEY) == 10


def test_telegram_updates_command_shows_chats_already_confirmed():
    with (
        patch.object(telegram_service.requests, "get", return_value=_response(_update(5, 777, "hi", "Petras"))),
        patch.object(telegram_service, "send_telegram_message", return_value=(True, "")),
    ):
        telegram_service.process_telegram_starts()

    out = StringIO()
    with patch("core.management.commands.telegram_updates.requests.get", return_value=_response()):
        call_command("telegram_updates", stdout=out)

    assert "777" in out.getvalue()
    assert "Petras" in out.getvalue()
