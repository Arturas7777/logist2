"""Отправка писем через Gmail API (Phase 2).

Строит RFC 5322 MIME-сообщение с корректными заголовками для threading
(``In-Reply-To`` / ``References``) и отдаёт его Gmail API
``users.messages.send`` с опциональным ``threadId`` — тогда Gmail сам подклеит
письмо к существующему треду (важно для пользователя: в Gmail переписка
выглядит как единая ветка).

Все public-функции бросают исключения на любые ошибки (API, лимиты, MIME).
Вызывающий код (``email_compose.py``) оборачивает их в try/except и сохраняет
``send_status='FAILED'`` в ``ContainerEmail``.
"""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Iterable, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)


__all__ = [
    'build_mime_message',
    'send_message',
    'SendError',
]


class SendError(RuntimeError):
    """Ошибка при сборке/отправке письма. Вызывающий код отлавливает её."""


# ---------------------------------------------------------------------------
# MIME
# ---------------------------------------------------------------------------


def _normalize_message_id(msg_id: str) -> str:
    """Возвращает Message-ID строго в формате ``<id@host>``.

    Gmail иногда отдаёт значения без угловых скобок, иногда с лишними пробелами.
    Для заголовков ``In-Reply-To`` / ``References`` RFC 5322 требует ``<...>``.
    """
    msg_id = (msg_id or '').strip()
    if not msg_id:
        return ''
    # Собираем все '<...>' и лепим заново (на случай мусора).
    if msg_id.startswith('<') and msg_id.endswith('>'):
        return msg_id
    return f'<{msg_id.strip("<>")}>'


def _build_references(parent_references: str, parent_message_id: str) -> str:
    """Собирает заголовок References: список предков треда + текущий родитель.

    По RFC 5322 — space-separated список Message-ID в угловых скобках.
    """
    parts: list[str] = []
    for chunk in (parent_references or '').split():
        chunk = chunk.strip()
        if chunk:
            parts.append(_normalize_message_id(chunk))
    parent_norm = _normalize_message_id(parent_message_id)
    if parent_norm and parent_norm not in parts:
        parts.append(parent_norm)
    return ' '.join(parts)


def _ensure_reply_prefix(subject: str) -> str:
    """Для ответа Gmail ожидает ``Re:`` в начале темы — иначе может разорвать тред.

    Если уже есть ``Re:`` (регистронезависимо) — не трогаем.
    """
    s = (subject or '').strip()
    if not s:
        return 'Re:'
    if s.lower().startswith('re:'):
        return s
    return f'Re: {s}'


def build_mime_message(
    *,
    from_addr: str,
    from_name: str = '',
    to: Sequence[str],
    cc: Sequence[str] | None = None,
    bcc: Sequence[str] | None = None,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: Iterable[tuple[str, bytes, str]] | None = None,
    is_reply: bool = False,
) -> EmailMessage:
    """Формирует валидное RFC 5322 ``EmailMessage``.

    * ``to`` / ``cc`` / ``bcc`` — списки адресов (строки); пустые — пропускаются.
    * ``attachments`` — ``[(filename, data_bytes, mime_type), ...]``.
    * ``in_reply_to`` — Message-ID родительского письма (со скобками или без).
    * ``references`` — уже готовый заголовок References (пробел-разделённый).
    * ``is_reply=True`` — гарантируем префикс ``Re:`` в теме.

    stdlib ``EmailMessage`` сам заботится о MIME-boundary, кодировании
    UTF-8 subject (``=?UTF-8?B?...?=``) и base64 для бинарных вложений.
    """
    if not to:
        raise SendError('Поле "Кому" пустое — нельзя отправить письмо.')
    if not (body_text or body_html):
        raise SendError('Пустое тело письма.')

    msg = EmailMessage()

    # From с display-name
    if from_name:
        msg['From'] = formataddr((from_name, from_addr))
    else:
        msg['From'] = from_addr

    msg['To'] = ', '.join(a.strip() for a in to if a and a.strip())
    if cc:
        cc_joined = ', '.join(a.strip() for a in cc if a and a.strip())
        if cc_joined:
            msg['Cc'] = cc_joined
    if bcc:
        bcc_joined = ', '.join(a.strip() for a in bcc if a and a.strip())
        if bcc_joined:
            msg['Bcc'] = bcc_joined

    subject = _ensure_reply_prefix(subject) if is_reply else (subject or '').strip()
    msg['Subject'] = subject

    # Собственный Message-ID, чтобы не полагаться на Gmail (и чтобы наша
    # локальная запись имела стабильный id до обращения к API).
    host = (from_addr.split('@', 1)[-1] or 'localhost').strip() or 'localhost'
    msg['Message-ID'] = make_msgid(domain=host)

    if in_reply_to:
        msg['In-Reply-To'] = _normalize_message_id(in_reply_to)
    if references:
        # references уже содержит обрамлённые Message-ID (в т.ч. parent).
        msg['References'] = references

    # Body: plain + optional HTML alternative.
    msg.set_content(body_text or '', subtype='plain', charset='utf-8')
    if body_html:
        msg.add_alternative(body_html, subtype='html')

    # Attachments
    for filename, data, mime in attachments or []:
        if not data:
            continue
        maintype, _, subtype = (mime or 'application/octet-stream').partition('/')
        if not subtype:
            maintype, subtype = 'application', 'octet-stream'
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename or 'attachment.bin',
        )

    return msg


# ---------------------------------------------------------------------------
# Gmail API send
# ---------------------------------------------------------------------------


def send_message(
    *,
    gmail_client,
    mime_msg: EmailMessage,
    thread_id: str | None = None,
) -> dict:
    """Отправляет MIME-сообщение через ``users.messages.send``.

    Возвращает dict от Gmail API ({'id': ..., 'threadId': ..., 'labelIds': [...]}).
    Если указан ``thread_id`` — Gmail положит письмо в тот же тред, важно
    чтобы Subject начинался с ``Re:`` (см. ``_ensure_reply_prefix``) и в
    References/In-Reply-To был хотя бы один Message-ID из треда.
    """
    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode('ascii')

    body: dict = {'raw': raw}
    if thread_id:
        body['threadId'] = thread_id

    try:
        response = gmail_client.service.users().messages().send(
            userId=gmail_client._user_email,
            body=body,
        ).execute()
    except Exception as exc:  # pragma: no cover — network/API error path
        logger.exception('[gmail_sender] Gmail API send failed: %s', exc)
        raise SendError(f'Gmail API отклонил письмо: {exc}') from exc

    logger.info(
        '[gmail_sender] sent: gmail_id=%s thread_id=%s to=%s subject=%s',
        response.get('id'),
        response.get('threadId'),
        mime_msg.get('To', ''),
        mime_msg.get('Subject', ''),
    )
    return response


# ---------------------------------------------------------------------------
# Настройки отправителя (public helper)
# ---------------------------------------------------------------------------


def get_from_address() -> tuple[str, str]:
    """Возвращает пару (email, display_name) отправителя для From-заголовка.

    Если ``GMAIL_FROM_EMAIL`` не задан — используем ``GMAIL_USER_EMAIL``.
    display_name берётся из ``GMAIL_FROM_NAME`` (или пустой).
    """
    addr = getattr(settings, 'GMAIL_FROM_EMAIL', '') or getattr(settings, 'GMAIL_USER_EMAIL', '')
    name = getattr(settings, 'GMAIL_FROM_NAME', '') or ''
    if not addr:
        raise SendError(
            'GMAIL_FROM_EMAIL / GMAIL_USER_EMAIL не заданы — непонятно, от кого отправлять.'
        )
    return addr, name
