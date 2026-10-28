"""Фильтры Sentry: вычищают токен Telegram-бота из событий и breadcrumbs.

URL Bot API содержит токен (``/bot<token>/sendMessage``) и попадает в
HTTP-breadcrumbs, тексты исключений requests и сообщения логов.
Модуль не зависит от Django — импортируется из settings до setup().
"""

from __future__ import annotations

import os
import re

BOT_TOKEN_IN_URL_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


def _scrub(value, token: str):
    if isinstance(value, str):
        if token:
            value = value.replace(token, "***")
        return BOT_TOKEN_IN_URL_RE.sub("/bot***", value)
    if isinstance(value, dict):
        return {key: _scrub(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, token) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item, token) for item in value)
    return value


def scrub_telegram_token(payload, hint=None):
    """``before_send`` / ``before_breadcrumb`` для sentry_sdk.init."""
    return _scrub(payload, os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
