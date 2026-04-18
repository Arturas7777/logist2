"""Gmail API client — обёртка над google-api-python-client с OAuth 2.0.

Использует refresh_token из settings, чтобы автоматически получать access_token
(Google SDK делает это сам при первом вызове). На вход отдаёт парсинг payload
в унифицированный dict, удобный для записи в ContainerEmail.

Phase 1: read-only. Для отправки нужен scope gmail.send — добавляется в
settings.GMAIL_SCOPES + перегенерация refresh_token через
scripts/get_gmail_refresh_token.py.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Iterator

from django.conf import settings

logger = logging.getLogger(__name__)


class GmailNotConfigured(RuntimeError):
    """settings.GMAIL_ENABLED=False или отсутствуют client_id/secret/refresh_token."""


class GmailHistoryExpired(RuntimeError):
    """users.history.list вернул 404 — нужен полный re-sync через messages.list."""


# Gmail API дедуплицирует history events по messageId, но иногда отдаёт
# один и тот же id дважды (например, при messageAdded + labelAdded). Используем
# set для фильтрации.
_HISTORY_TYPES_ADDED = ['messageAdded']


@dataclass
class ParsedAttachment:
    filename: str
    mime_type: str
    size: int
    attachment_id: str  # Gmail attachmentId для messages.attachments.get
    data: bytes | None = None  # заполняется только если уже скачано


@dataclass
class ParsedMessage:
    gmail_id: str
    thread_id: str
    history_id: int | None
    message_id: str       # RFC 5322 Message-ID, может быть ''
    in_reply_to: str
    references: str
    subject: str
    from_addr: str
    to_addrs: str
    cc_addrs: str
    received_at: datetime  # aware UTC
    snippet: str
    body_text: str
    body_html: str
    labels: list[str] = field(default_factory=list)
    attachments: list[ParsedAttachment] = field(default_factory=list)

    @property
    def is_outgoing(self) -> bool:
        return 'SENT' in self.labels


class GmailApiClient:
    """Тонкая обёртка, которая:
      * Строит OAuth Credentials из settings (никаких файлов на проде).
      * Ленивая инициализация service — не тянет API до первого вызова.
      * Методы возвращают уже распарсенные dataclass-ы, а не сырые dict.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        user_email: str | None = None,
        scopes: Iterable[str] | None = None,
    ) -> None:
        self._client_id = client_id or settings.GMAIL_CLIENT_ID
        self._client_secret = client_secret or settings.GMAIL_CLIENT_SECRET
        self._refresh_token = refresh_token or settings.GMAIL_REFRESH_TOKEN
        self._user_email = user_email or settings.GMAIL_USER_EMAIL or 'me'
        self._scopes = list(scopes or settings.GMAIL_SCOPES)

        if not (self._client_id and self._client_secret and self._refresh_token):
            raise GmailNotConfigured(
                'GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN не заданы. '
                'Установите переменные окружения или GMAIL_ENABLED=false.'
            )

        self._service = None

    # ------------------------------------------------------------------
    # service (ленивая инициализация)
    # ------------------------------------------------------------------

    def _build_service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,  # access_token подтянется через refresh
            refresh_token=self._refresh_token,
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_uri=settings.GMAIL_TOKEN_URI,
            scopes=self._scopes,
        )
        return build('gmail', 'v1', credentials=creds, cache_discovery=False)

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def get_profile(self) -> dict[str, Any]:
        """users.getProfile — возвращает emailAddress, historyId и др."""
        return self.service.users().getProfile(userId=self._user_email).execute()

    def list_history(self, start_history_id: int) -> Iterator[str]:
        """Возвращает iterator по новым message_id с момента start_history_id.

        Кидает GmailHistoryExpired, если Google уже удалил эту историю (404).
        """
        from googleapiclient.errors import HttpError

        seen: set[str] = set()
        page_token: str | None = None
        try:
            while True:
                resp = self.service.users().history().list(
                    userId=self._user_email,
                    startHistoryId=str(start_history_id),
                    historyTypes=_HISTORY_TYPES_ADDED,
                    pageToken=page_token,
                ).execute()
                for entry in resp.get('history', []) or []:
                    for ma in entry.get('messagesAdded', []) or []:
                        msg = ma.get('message', {})
                        mid = msg.get('id')
                        if mid and mid not in seen:
                            seen.add(mid)
                            yield mid
                page_token = resp.get('nextPageToken')
                if not page_token:
                    return
        except HttpError as err:
            status = getattr(err, 'status_code', None) or getattr(err.resp, 'status', None)
            try:
                status = int(status) if status is not None else None
            except (TypeError, ValueError):
                status = None
            if status == 404:
                raise GmailHistoryExpired(str(err)) from err
            raise

    def list_messages(self, query: str, *, max_results: int = 500) -> Iterator[str]:
        """messages.list (пагинация) — возвращает id писем по Gmail-запросу (q=...)."""
        page_token: str | None = None
        while True:
            resp = self.service.users().messages().list(
                userId=self._user_email,
                q=query,
                maxResults=min(500, max_results),
                pageToken=page_token,
            ).execute()
            for m in resp.get('messages', []) or []:
                mid = m.get('id')
                if mid:
                    yield mid
            page_token = resp.get('nextPageToken')
            if not page_token:
                return

    def get_message(self, gmail_id: str) -> ParsedMessage:
        """messages.get(format=full) + парсинг payload в ParsedMessage."""
        raw = self.service.users().messages().get(
            userId=self._user_email,
            id=gmail_id,
            format='full',
        ).execute()
        return parse_gmail_message(raw)

    def get_attachment(self, gmail_id: str, attachment_id: str) -> bytes:
        """messages.attachments.get → raw bytes (декодированные из base64url)."""
        resp = self.service.users().messages().attachments().get(
            userId=self._user_email,
            messageId=gmail_id,
            id=attachment_id,
        ).execute()
        data = resp.get('data', '')
        return _b64url_decode(data)


# ----------------------------------------------------------------------
# payload parsing
# ----------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def parse_gmail_message(raw: dict[str, Any]) -> ParsedMessage:
    """Превращает сырой dict от Gmail API (format=full) в ParsedMessage.

    Тестируется юнит-тестами — содержит всю «грязь» по декодингу заголовков и
    обходу multipart-дерева.
    """
    payload = raw.get('payload') or {}
    headers = {h.get('name', '').lower(): h.get('value', '') for h in (payload.get('headers') or [])}

    subject = _decode_header_value(headers.get('subject', ''))
    from_addr = _decode_header_value(headers.get('from', ''))
    to_addrs = _decode_header_value(headers.get('to', ''))
    cc_addrs = _decode_header_value(headers.get('cc', ''))
    message_id = headers.get('message-id', '').strip()
    in_reply_to = headers.get('in-reply-to', '').strip()
    references = headers.get('references', '').strip()
    date_hdr = headers.get('date', '')

    received_at = _parse_date_header(date_hdr) or _internal_date_ms_to_dt(raw.get('internalDate'))

    body_text_parts: list[str] = []
    body_html_parts: list[str] = []
    attachments: list[ParsedAttachment] = []
    _walk_payload(payload, body_text_parts, body_html_parts, attachments)

    history_id = raw.get('historyId')
    try:
        history_id_int = int(history_id) if history_id is not None else None
    except (TypeError, ValueError):
        history_id_int = None

    return ParsedMessage(
        gmail_id=raw.get('id', ''),
        thread_id=raw.get('threadId', '') or message_id or raw.get('id', ''),
        history_id=history_id_int,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        subject=subject,
        from_addr=from_addr,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        received_at=received_at,
        snippet=raw.get('snippet', '') or '',
        body_text='\n'.join(body_text_parts).strip(),
        body_html='\n'.join(body_html_parts).strip(),
        labels=list(raw.get('labelIds') or []),
        attachments=attachments,
    )


def _walk_payload(
    part: dict[str, Any],
    body_text_parts: list[str],
    body_html_parts: list[str],
    attachments: list[ParsedAttachment],
) -> None:
    mime = (part.get('mimeType') or '').lower()
    filename = part.get('filename') or ''
    body = part.get('body') or {}
    body_data = body.get('data') or ''
    body_size = body.get('size') or 0
    attachment_id = body.get('attachmentId') or ''

    if filename and attachment_id:
        attachments.append(ParsedAttachment(
            filename=_decode_header_value(filename),
            mime_type=mime or 'application/octet-stream',
            size=int(body_size or 0),
            attachment_id=attachment_id,
        ))
        return

    if mime.startswith('multipart/') and part.get('parts'):
        for sub in part['parts']:
            _walk_payload(sub, body_text_parts, body_html_parts, attachments)
        return

    if body_data:
        try:
            decoded = _b64url_decode(body_data).decode(_guess_charset(part), errors='replace')
        except Exception:
            logger.warning("Failed to decode body data (mime=%s)", mime, exc_info=True)
            return
        if mime == 'text/plain':
            body_text_parts.append(decoded)
        elif mime == 'text/html':
            body_html_parts.append(decoded)


def _guess_charset(part: dict[str, Any]) -> str:
    headers = part.get('headers') or []
    for h in headers:
        if (h.get('name') or '').lower() == 'content-type':
            value = h.get('value', '')
            m = re.search(r'charset="?([\w\-]+)"?', value, re.IGNORECASE)
            if m:
                return m.group(1)
    return 'utf-8'


def _decode_header_value(value: str) -> str:
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_date_header(raw_date: str) -> datetime | None:
    if not raw_date:
        return None
    try:
        dt = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def _internal_date_ms_to_dt(value: Any) -> datetime:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return _EPOCH
    return datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)


def _b64url_decode(data: str) -> bytes:
    if not data:
        return b''
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)
