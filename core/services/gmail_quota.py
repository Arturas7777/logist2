"""Пауза обращений к Gmail API после ответа «превышен лимит».

Квота Gmail на ящик общая для всех OAuth-клиентов, которые его читают.
Повторять запрос сразу после 429 бессмысленно: он снова тратит квоту и
снова получает 429. Поэтому при первом же отказе синк и докачка вложений
встают на паузу (ключ в общем кэше, т.е. в Redis на проде), а пауза растёт
при повторных отказах: 5 → 10 → 20 → 40 → 60 минут. Успешный синк
сбрасывает счётчик.

Отправку писем (``gmail_sender``) пауза не блокирует.
"""

from __future__ import annotations

import json
import time

from django.core.cache import cache

_COOLDOWN_UNTIL_KEY = "gmail_rate_limit:until"
_COOLDOWN_LEVEL_KEY = "gmail_rate_limit:level"
_BASE_COOLDOWN_SEC = 5 * 60
_MAX_COOLDOWN_SEC = 60 * 60
# Уровень эскалации живёт дольше самой паузы, иначе каждая следующая
# пауза снова начиналась бы с 5 минут.
_LEVEL_TTL_SEC = 6 * 60 * 60

_RATE_LIMIT_REASONS = {
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
    "resource_exhausted",
}


class GmailRateLimited(RuntimeError):
    """Gmail ответил 429 или 403 с причиной из семейства rate limit."""

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _reasons_of(exc: Exception) -> set[str]:
    content = getattr(exc, "content", b"") or b""
    try:
        data = json.loads(content.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return set()
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons = {str(e.get("reason", "")).lower() for e in error.get("errors") or [] if isinstance(e, dict)}
    if error.get("status"):
        reasons.add(str(error["status"]).lower())
    return reasons


def is_rate_limit_error(exc: Exception) -> bool:
    from googleapiclient.errors import HttpError

    if not isinstance(exc, HttpError):
        return False
    status = _status_of(exc)
    if status == 429:
        return True
    if status == 403:
        return bool(_reasons_of(exc) & _RATE_LIMIT_REASONS)
    return False


def retry_after_of(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    value = resp.get("retry-after") if hasattr(resp, "get") else None
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def cooldown_remaining() -> int:
    """Сколько секунд ещё нельзя обращаться к Gmail (0 — можно)."""
    until = cache.get(_COOLDOWN_UNTIL_KEY)
    if not until:
        return 0
    return max(0, int(until - time.time()))


def start_cooldown(retry_after: int | None = None) -> int:
    """Ставит паузу и возвращает её длительность в секундах."""
    level = int(cache.get(_COOLDOWN_LEVEL_KEY) or 0)
    seconds = min(_BASE_COOLDOWN_SEC * (2**level), _MAX_COOLDOWN_SEC)
    if retry_after:
        seconds = max(seconds, min(retry_after, _MAX_COOLDOWN_SEC))
    cache.set(_COOLDOWN_UNTIL_KEY, time.time() + seconds, seconds)
    cache.set(_COOLDOWN_LEVEL_KEY, level + 1, _LEVEL_TTL_SEC)
    return seconds


def reset_cooldown() -> None:
    cache.delete_many([_COOLDOWN_UNTIL_KEY, _COOLDOWN_LEVEL_KEY])
