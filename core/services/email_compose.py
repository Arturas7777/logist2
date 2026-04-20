"""Высокоуровневые операции для отправки писем из карточки контейнера (Phase 2).

``reply_to_email`` — ответ в существующий тред.
``compose_new_email`` — новое письмо (без родителя).

Обе функции:
  1. Валидируют вход (адреса, размер вложений).
  2. Добавляют подпись из settings.
  3. Собирают MIME, зовут Gmail API через ``gmail_sender.send_message``.
  4. Сохраняют вложения в ``MEDIA_ROOT/container_emails/<yyyy>/<mm>/<gmail_id>/``.
  5. Создают локальную запись ``ContainerEmail`` с ``direction=OUTGOING``,
     ``send_status=SENT``, заполненным ``thread_id`` / ``in_reply_to`` /
     ``references`` для последующего matching входящих ответов.

При ошибке API создаётся запись с ``send_status=FAILED`` и пустым ``gmail_id``
(нужно для UI — показать кнопку «Повторить»). Чтобы избежать дубликатов
message_id в этом кейсе, используется локально-сгенерированный Message-ID.
"""

from __future__ import annotations

import logging
import re
import uuid
from email.utils import make_msgid
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from core.models_email import ContainerEmail, ContainerEmailLink
from core.models_contact import Contact, ContactEmail
from core.services.email_reply_parser import (
    compose_reply_html,
    format_quoted_reply,
    plain_text_to_simple_html,
)
from core.services.gmail_client import GmailApiClient
from core.services.gmail_sender import (
    SendError,
    build_mime_message,
    get_from_address,
    send_message,
)

logger = logging.getLogger(__name__)


__all__ = [
    'reply_to_email',
    'compose_new_email',
    'ComposeError',
]


class ComposeError(Exception):
    """Ошибка уровня «composer» — невалидный ввод, превышен лимит и т.п."""


# ---------------------------------------------------------------------------
# Валидация адресов
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$')


def _parse_addrs(raw: str | Iterable[str] | None) -> list[str]:
    """Принимает строку ``"a@x, b@y"`` или список — возвращает список валидных адресов."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = re.split(r'[,;\n]+', raw)
    else:
        items = list(raw)
    out: list[str] = []
    for it in items:
        addr = (it or '').strip()
        if not addr:
            continue
        # "Имя <email@host>" — вытащим email
        m = re.search(r'<([^>]+)>', addr)
        if m:
            addr = m.group(1).strip()
        if not _EMAIL_RE.match(addr):
            raise ComposeError(f'Некорректный email-адрес: {addr!r}')
        out.append(addr)
    return out


def _parse_addrs_with_names(raw: str | Iterable[str] | None) -> list[tuple[str, str]]:
    """Парсит raw-строки/список в ``[(email, display_name), ...]``.

    Сохраняет display_name из ``"Имя <email>"`` — нужно для авто-создания
    осиротевших контактов с осмысленным именем.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items = re.split(r'[,;\n]+', raw)
    else:
        items = list(raw)
    out: list[tuple[str, str]] = []
    for it in items:
        addr = (it or '').strip()
        if not addr:
            continue
        name = ''
        m = re.search(r'^(.*?)<([^>]+)>\s*$', addr)
        if m:
            name = (m.group(1) or '').strip().strip('"')
            addr = (m.group(2) or '').strip()
        if _EMAIL_RE.match(addr):
            out.append((addr, name))
    return out


# ---------------------------------------------------------------------------
# Вложения
# ---------------------------------------------------------------------------

_SAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9._\- а-яА-ЯёЁ]+')


def _validate_and_read_attachments(
    files: Iterable[UploadedFile] | None,
) -> list[tuple[str, bytes, str]]:
    """Читает загруженные файлы в память, проверяет суммарный лимит.

    Возвращает список кортежей ``(filename, data, mime)`` для ``build_mime_message``.
    """
    if not files:
        return []
    max_mb = int(getattr(settings, 'GMAIL_MAX_OUTBOUND_MB', 25))
    max_bytes = max_mb * 1024 * 1024

    result: list[tuple[str, bytes, str]] = []
    total = 0
    for f in files:
        if not f:
            continue
        data = f.read()
        total += len(data)
        if total > max_bytes:
            raise ComposeError(
                f'Суммарный размер вложений превышает лимит {max_mb} МБ.'
            )
        filename = getattr(f, 'name', '') or 'attachment.bin'
        mime = getattr(f, 'content_type', '') or 'application/octet-stream'
        result.append((filename, data, mime))
    return result


def _save_outgoing_attachments(
    attachments: list[tuple[str, bytes, str]],
    gmail_id: str,
    when,
) -> list[dict]:
    """Сохраняет вложения исходящего письма на диск (аналог email_ingest)."""
    if not attachments or not gmail_id:
        return []

    media_root = Path(settings.MEDIA_ROOT)
    rel_dir = Path('container_emails') / when.strftime('%Y') / when.strftime('%m') / gmail_id
    abs_dir = media_root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    out: list[dict] = []
    for idx, (filename, data, mime) in enumerate(attachments):
        safe_name = _SAFE_FILENAME_RE.sub('_', filename or f'attachment_{idx}')
        if not safe_name or safe_name == '_':
            safe_name = f'attachment_{idx}'
        stored = f'{idx:02d}_{safe_name}'
        abs_path = abs_dir / stored
        with open(abs_path, 'wb') as fh:
            fh.write(data)
        out.append({
            'filename': filename,
            'size': len(data),
            'content_type': mime,
            'storage_path': str((rel_dir / stored).as_posix()),
            'attachment_id': '',
            'is_inline': False,
            'skipped_reason': '',
        })
    return out


# ---------------------------------------------------------------------------
# Сигнатура
# ---------------------------------------------------------------------------


def _append_signature(body_text: str, body_html: str) -> tuple[str, str]:
    """Добавляет подпись В КОНЕЦ тела письма. Используется для compose_new_email,
    где цитаты нет и подпись естественно ставится в самом низу."""
    sig_text = getattr(settings, 'GMAIL_SIGNATURE_TEXT', '') or ''
    sig_html = getattr(settings, 'GMAIL_SIGNATURE_HTML', '') or ''
    if sig_text:
        body_text = (body_text or '').rstrip() + '\n\n' + sig_text.strip() + '\n'
    if sig_html:
        body_html = (body_html or '') + sig_html
    return body_text, body_html


# Регулярка для строки-атрибуции "On … wrote:", которую вставляет
# format_quoted_reply(). По ней разрезаем plain-text ответ, чтобы вставить
# подпись ПЕРЕД цитатой (как это делает веб-Gmail при клике "Ответить").
_REPLY_ATTRIBUTION_TEXT_RE = re.compile(
    r'^On\s[^\n]{0,400}\bwrote:\s*$',
    re.MULTILINE,
)


def _build_reply_bodies(body_text: str) -> tuple[str, str]:
    """Готовит финальные body_text / body_html для ответа.

    Подпись вставляется **между** текстом пользователя и строкой-атрибуцией
    "On ... wrote:" (и соответственно между <p>reply</p> и <blockquote>
    в HTML-версии) — так делает веб-Gmail. Это даёт естественный вид:
    получатель сначала видит ответ + подпись, цитата схлопывается в "...".
    """
    sig_text = getattr(settings, 'GMAIL_SIGNATURE_TEXT', '') or ''
    sig_html = getattr(settings, 'GMAIL_SIGNATURE_HTML', '') or ''
    src = body_text or ''

    # --- plain text: вставляем подпись перед "On ... wrote:" ---
    if sig_text:
        sig_block = '\n\n' + sig_text.strip() + '\n'
        m = _REPLY_ATTRIBUTION_TEXT_RE.search(src)
        if m:
            final_text = (
                src[:m.start()].rstrip()
                + sig_block
                + '\n' + src[m.start():]
            )
        else:
            final_text = src.rstrip() + sig_block
    else:
        final_text = src

    # --- HTML: вставка делается внутри compose_reply_html ---
    final_html = compose_reply_html(src, signature_html=sig_html)

    return final_text, final_html


# ---------------------------------------------------------------------------
# Локальная запись ContainerEmail
# ---------------------------------------------------------------------------


def _build_references_header(parent: ContainerEmail) -> str:
    """Собирает заголовок References = parent.references + parent.message_id."""
    parts: list[str] = []
    for chunk in (parent.references or '').split():
        chunk = chunk.strip()
        if chunk:
            if not (chunk.startswith('<') and chunk.endswith('>')):
                chunk = f'<{chunk.strip("<>")}>'
            parts.append(chunk)
    if parent.message_id:
        pm = parent.message_id.strip()
        if not (pm.startswith('<') and pm.endswith('>')):
            pm = f'<{pm.strip("<>")}>'
        if pm not in parts:
            parts.append(pm)
    return ' '.join(parts)


# ---------------------------------------------------------------------------
# Auto-create «осиротевших» контактов
# ---------------------------------------------------------------------------


def _autocreate_orphan_contacts(
    raw_inputs: Iterable[tuple[str, str]],
) -> list[Contact]:
    """Для каждого ``(email, display_name)`` — если в ``ContactEmail`` нет
    записи с таким ``email__iexact``, создаёт осиротевший ``Contact`` +
    ``ContactEmail``. Ошибки глушим (не ломаем отправку письма).

    Возвращает список созданных контактов (нужно для логов и тестов).
    """
    created: list[Contact] = []
    try:
        # Собираем существующие emails батчем (одним запросом).
        candidates = [(e.lower(), n) for e, n in raw_inputs if e]
        if not candidates:
            return created
        known = set(
            ContactEmail.objects
            .filter(email__in=[e for e, _ in candidates])
            .values_list('email', flat=True)
        )
        known_lc = {e.lower() for e in known}

        for email_addr, display_name in candidates:
            if email_addr in known_lc:
                continue
            # Не создаём дубликаты если уже в этом же вызове.
            known_lc.add(email_addr)

            fallback_name = (display_name or '').strip() or email_addr.split('@')[0]
            contact = Contact.objects.create(
                name=fallback_name[:200],
                is_orphan=True,
                comment='Создан автоматически при отправке email из карточки контейнера.',
            )
            ContactEmail.objects.create(
                contact=contact,
                email=email_addr,
                is_primary=True,
            )
            created.append(contact)
            logger.info(
                '[email_compose] auto-created orphan contact: %s <%s>',
                fallback_name, email_addr,
            )
    except Exception as exc:  # pragma: no cover — защитная обёртка
        logger.warning('[email_compose] failed to autocreate orphan contacts: %s', exc)
    return created


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reply_to_email(
    *,
    parent_email: ContainerEmail,
    user: AbstractBaseUser | None,
    to: str | Iterable[str],
    cc: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    subject: str = '',
    body_text: str = '',
    attachments: Iterable[UploadedFile] | None = None,
    origin_container=None,
) -> ContainerEmail:
    """Отвечает на существующее письмо. Кладёт новое в тот же Gmail thread.

    ``origin_container`` — контейнер, из карточки которого пользователь нажал
    «Ответить». Он пойдёт в ``sent_from_container`` и будет гарантированно
    привязан к исходящему. Если не передан — берём из parent_email.containers
    (первый связанный) как fallback.
    """

    if origin_container is None:
        origin_container = parent_email.containers.first()

    to_list = _parse_addrs(to)
    cc_list = _parse_addrs(cc)
    bcc_list = _parse_addrs(bcc)
    if not to_list:
        raise ComposeError('Не указан хотя бы один получатель.')
    # Сохраняем пары (email, name) для авто-создания контактов.
    raw_pairs: list[tuple[str, str]] = []
    raw_pairs.extend(_parse_addrs_with_names(to))
    raw_pairs.extend(_parse_addrs_with_names(cc))
    raw_pairs.extend(_parse_addrs_with_names(bcc))

    att_payload = _validate_and_read_attachments(attachments)

    subject = subject.strip() or f'Re: {parent_email.subject or ""}'.strip()
    body_text = body_text or ''
    # Для ответа — Gmail-совместимый layout: <p>reply</p> + <подпись> +
    # <blockquote class="gmail_quote">...</blockquote>. Подпись ставится
    # МЕЖДУ ответом и цитатой, как в веб-Gmail, а сам блок цитаты
    # клиенты-получатели схлопывают в «...».
    body_text_final, body_html_final = _build_reply_bodies(body_text)

    from_addr, from_name = get_from_address()
    references_hdr = _build_references_header(parent_email)

    mime = build_mime_message(
        from_addr=from_addr,
        from_name=from_name,
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        subject=subject,
        body_text=body_text_final,
        body_html=body_html_final,
        in_reply_to=parent_email.message_id or None,
        references=references_hdr or None,
        attachments=att_payload,
        is_reply=True,
    )

    local_message_id = mime.get('Message-ID', '') or make_msgid()
    subject_final = mime.get('Subject', subject)

    return _send_and_persist(
        mime=mime,
        thread_id=parent_email.thread_id or None,
        container=origin_container,
        parent_email=parent_email,
        user=user,
        local_message_id=local_message_id,
        subject_final=subject_final,
        from_addr=from_addr,
        to_list=to_list,
        cc_list=cc_list,
        body_text_final=body_text_final,
        body_html_final=body_html_final,
        attachments_payload=att_payload,
        matched_by=ContainerEmail.MATCHED_BY_THREAD,
        in_reply_to=parent_email.message_id or '',
        references=references_hdr,
        raw_recipient_pairs=raw_pairs,
        source_text_for_matching=f'{subject}\n{body_text_final}',
    )


def compose_new_email(
    *,
    container,
    user: AbstractBaseUser | None,
    to: str | Iterable[str],
    cc: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    subject: str = '',
    body_text: str = '',
    attachments: Iterable[UploadedFile] | None = None,
) -> ContainerEmail:
    """Новое письмо по контейнеру (без родителя). Gmail создаст новый тред."""

    to_list = _parse_addrs(to)
    cc_list = _parse_addrs(cc)
    bcc_list = _parse_addrs(bcc)
    if not to_list:
        raise ComposeError('Не указан хотя бы один получатель.')
    raw_pairs: list[tuple[str, str]] = []
    raw_pairs.extend(_parse_addrs_with_names(to))
    raw_pairs.extend(_parse_addrs_with_names(cc))
    raw_pairs.extend(_parse_addrs_with_names(bcc))

    att_payload = _validate_and_read_attachments(attachments)

    subject = (subject or '').strip() or f'Container {getattr(container, "number", "")}'
    body_text_final, body_html_final = _append_signature(
        body_text or '', plain_text_to_simple_html(body_text or '')
    )

    from_addr, from_name = get_from_address()

    mime = build_mime_message(
        from_addr=from_addr,
        from_name=from_name,
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        subject=subject,
        body_text=body_text_final,
        body_html=body_html_final,
        attachments=att_payload,
        is_reply=False,
    )

    local_message_id = mime.get('Message-ID', '') or make_msgid()
    subject_final = mime.get('Subject', subject)

    return _send_and_persist(
        mime=mime,
        thread_id=None,
        container=container,
        parent_email=None,
        user=user,
        local_message_id=local_message_id,
        subject_final=subject_final,
        from_addr=from_addr,
        to_list=to_list,
        cc_list=cc_list,
        body_text_final=body_text_final,
        body_html_final=body_html_final,
        attachments_payload=att_payload,
        matched_by=ContainerEmail.MATCHED_BY_MANUAL,
        in_reply_to='',
        references='',
        raw_recipient_pairs=raw_pairs,
        source_text_for_matching=f'{subject}\n{body_text_final}',
    )


# ---------------------------------------------------------------------------
# Общий helper: отправка + запись в БД
# ---------------------------------------------------------------------------


def _send_and_persist(
    *,
    mime,
    thread_id,
    container,
    parent_email,
    user,
    local_message_id,
    subject_final,
    from_addr,
    to_list,
    cc_list,
    body_text_final,
    body_html_final,
    attachments_payload,
    matched_by,
    in_reply_to,
    references,
    raw_recipient_pairs: list[tuple[str, str]] | None = None,
    source_text_for_matching: str = '',
) -> ContainerEmail:
    now = timezone.now()

    client = GmailApiClient()
    try:
        response = send_message(
            gmail_client=client,
            mime_msg=mime,
            thread_id=thread_id,
        )
    except SendError as exc:
        # Сохраняем «неудачную» запись — чтобы показать в UI с кнопкой «Повторить».
        failed_id = local_message_id or f'<failed-{uuid.uuid4().hex}@logist2.local>'
        email = ContainerEmail.objects.create(
            sent_from_container=container,
            message_id=failed_id,
            thread_id=getattr(parent_email, 'thread_id', '') if parent_email else '',
            in_reply_to=in_reply_to,
            references=references,
            direction=ContainerEmail.DIRECTION_OUTGOING,
            from_addr=from_addr,
            to_addrs=', '.join(to_list),
            cc_addrs=', '.join(cc_list),
            subject=subject_final,
            body_text=body_text_final,
            body_html=body_html_final,
            snippet=(body_text_final or '')[:300],
            received_at=now,
            gmail_id='',
            labels_json=[],
            attachments_json=[
                {
                    'filename': f[0],
                    'size': len(f[1]),
                    'content_type': f[2],
                    'storage_path': '',
                    'attachment_id': '',
                    'is_inline': False,
                }
                for f in attachments_payload
            ],
            matched_by=matched_by,
            is_read=True,
            sent_by_user=user if (user and getattr(user, 'is_authenticated', False)) else None,
            send_status=ContainerEmail.SEND_STATUS_FAILED,
            send_error=str(exc)[:2000],
        )
        _link_outgoing_to_containers(
            email=email,
            origin_container=container,
            parent_email=parent_email,
            source_text=source_text_for_matching,
            origin_matched_by=matched_by,
        )
        raise

    gmail_id = response.get('id', '') or ''
    gmail_thread_id = response.get('threadId', '') or thread_id or ''
    labels = list(response.get('labelIds') or [])

    stored_attachments = _save_outgoing_attachments(
        attachments_payload, gmail_id, now,
    )

    email = ContainerEmail.objects.create(
        sent_from_container=container,
        message_id=local_message_id,
        thread_id=gmail_thread_id,
        in_reply_to=in_reply_to,
        references=references,
        direction=ContainerEmail.DIRECTION_OUTGOING,
        from_addr=from_addr,
        to_addrs=', '.join(to_list),
        cc_addrs=', '.join(cc_list),
        subject=subject_final,
        body_text=body_text_final,
        body_html=body_html_final,
        snippet=(body_text_final or '')[:300],
        received_at=now,
        gmail_id=gmail_id,
        gmail_history_id=None,
        labels_json=labels,
        attachments_json=stored_attachments,
        matched_by=matched_by,
        is_read=True,
        sent_by_user=user if (user and getattr(user, 'is_authenticated', False)) else None,
        send_status=ContainerEmail.SEND_STATUS_SENT,
        send_error='',
    )

    _link_outgoing_to_containers(
        email=email,
        origin_container=container,
        parent_email=parent_email,
        source_text=source_text_for_matching,
        origin_matched_by=matched_by,
    )

    logger.info(
        '[email_compose] outgoing saved: pk=%s gmail_id=%s thread=%s container=%s',
        email.pk, gmail_id, gmail_thread_id,
        getattr(container, 'pk', None),
    )

    # После успешной отправки — подтягиваем/создаём контакты, чтобы в
    # следующий раз autocomplete их предложил без ручного заведения.
    if raw_recipient_pairs:
        _autocreate_orphan_contacts(raw_recipient_pairs)

    return email


def _link_outgoing_to_containers(
    *,
    email: ContainerEmail,
    origin_container,
    parent_email: ContainerEmail | None,
    source_text: str,
    origin_matched_by: str,
) -> None:
    """Линкует исходящее письмо к контейнерам через ContainerEmailLink.

    Набор контейнеров = {origin} ∪ {родительского треда} ∪ {найденные в
    subject/body номера контейнеров / букинги}. Так если юзер написал письмо
    из карточки MSKU1 с темой "Containers MSKU1, MRSU2" — оба контейнера
    увидят переписку.
    """
    from core.services.email_matcher import (
        _match_by_bookings,  # noqa: PLC2701 — свой же пакет
        _match_by_container_numbers,  # noqa: PLC2701
        build_booking_index,
    )

    seen: set[int] = set()
    links: list[ContainerEmailLink] = []

    def _add(cid: int | None, matched_by: str) -> None:
        if not cid or cid in seen:
            return
        seen.add(cid)
        links.append(ContainerEmailLink(
            email=email,
            container_id=cid,
            matched_by=matched_by,
        ))

    # 1) Карточка-источник (всегда)
    if origin_container is not None and getattr(origin_container, 'pk', None):
        _add(origin_container.pk, origin_matched_by or ContainerEmail.MATCHED_BY_MANUAL)

    # 2) Все контейнеры родительского треда
    if parent_email is not None and parent_email.pk:
        parent_cids = list(
            parent_email.containers.values_list('id', flat=True)
        )
        for cid in parent_cids:
            _add(cid, ContainerEmail.MATCHED_BY_THREAD)

    # 3) Упомянутые в тексте (subject + body) контейнеры и букинги
    if source_text:
        try:
            for cid in _match_by_container_numbers(source_text):
                _add(cid, ContainerEmail.MATCHED_BY_CONTAINER_NUMBER)
            booking_index = build_booking_index()
            for cid in _match_by_bookings(source_text, booking_index):
                _add(cid, ContainerEmail.MATCHED_BY_BOOKING_NUMBER)
        except Exception as exc:  # pragma: no cover — защитная обёртка
            logger.warning('[email_compose] link-by-text failed: %s', exc)

    if links:
        ContainerEmailLink.objects.bulk_create(links, ignore_conflicts=True)
