"""Сообщения сотруднику и клиенту в заявке на автовоз остаются в кабинете.

На email и в Telegram клиенту уходят только уведомления о планируемой
разгрузке и о разгрузке (``email_service`` / ``telegram_service``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify_client_about_message(message, user=None) -> dict:
    """Не шлёт клиенту email и Telegram.

    Сообщение остаётся в кабинете. ``user`` сохранён в сигнатуре: его
    передаёт карточка заявки.
    """
    logger.info(
        "[transport_request_notify] сообщение по заявке %s не отправлено: клиентам только разгрузка",
        getattr(getattr(message, "request", None), "number", ""),
    )
    return {"email": 0, "telegram": 0}
