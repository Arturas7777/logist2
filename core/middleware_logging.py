"""Middleware и logging filter для structured logs (M7).

Добавляет к каждой записи лога контекст текущего запроса:

- ``request_id`` — uuid4, генерируется в начале запроса (или берётся из
  заголовка ``X-Request-ID`` — удобно для проксирования через nginx
  и корреляции с access-логами);
- ``user_id`` — id аутентифицированного пользователя или ``None`` для
  анонимов;
- ``path`` — путь запроса (без query string);
- ``method`` — HTTP-метод.

Хранится в ``contextvars.ContextVar`` — безопасно для async (Channels /
Daphne / threading-pool gunicorn'а).

Пример включения JSON-логов в ``.env``::

    LOG_FORMAT=json
    LOG_LEVEL=INFO
    LOG_DIR=/var/log/logist2

Тогда каждая строка в ``/var/log/logist2/app.log`` будет JSON-ом
вида::

    {"asctime": "...", "levelname": "INFO", "name": "core.billing",
     "message": "invoice paid", "request_id": "abc-123", "user_id": 42,
     "path": "/admin/core/newinvoice/123/change/", "method": "POST"}

См. ``docs/LOGGING.md``.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_request_id_var: ContextVar[str | None] = ContextVar("logist2_request_id", default=None)
_user_id_var: ContextVar[int | None] = ContextVar("logist2_user_id", default=None)
_path_var: ContextVar[str | None] = ContextVar("logist2_path", default=None)
_method_var: ContextVar[str | None] = ContextVar("logist2_method", default=None)


def get_request_id() -> str | None:
    """Текущий request_id (или None, если вызвано вне HTTP-запроса)."""
    return _request_id_var.get()


def set_request_id(value: str | None) -> None:
    """Установить request_id вручную (например, в Celery-таске)."""
    _request_id_var.set(value)


class RequestContextMiddleware:
    """Сохраняет request_id / user_id / path / method в contextvars.

    Должен стоять ПОСЛЕ ``AuthenticationMiddleware`` (чтобы видеть
    `request.user`).
    """

    HEADER_NAME = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.META.get(self.HEADER_NAME) or uuid.uuid4().hex
        token_rid = _request_id_var.set(rid)
        token_path = _path_var.set(request.path)
        token_method = _method_var.set(request.method)
        user = getattr(request, "user", None)
        token_user = _user_id_var.set(getattr(user, "id", None) if getattr(user, "is_authenticated", False) else None)
        try:
            response = self.get_response(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _request_id_var.reset(token_rid)
            _path_var.reset(token_path)
            _method_var.reset(token_method)
            _user_id_var.reset(token_user)


class RequestContextFilter(logging.Filter):
    """Logging filter — пристёгивает request-контекст к каждой записи.

    Используется и для JSON-, и для текстового formatter'а (см.
    base.py → LOGGING.formatters).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get() or "-"
        record.user_id = _user_id_var.get()
        record.path = _path_var.get() or "-"
        record.method = _method_var.get() or "-"
        return True
