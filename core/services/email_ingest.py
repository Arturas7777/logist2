"""sync_mailbox — подтягивание новых писем из Gmail и запись ContainerEmail.

Использует GmailSyncState для хранения last_history_id. На первом прогоне
(last_history_id=NULL) делаем полный re-sync через ``messages.list`` за
последние N дней. Дальше — инкремент через ``users.history.list``.

При `history expired` (404 от Google) автоматически фолбэкаемся на полный
re-sync.

Функция идемпотентна: по gmail_id/message_id используется
``get_or_create``. Повторный запуск не создаст дублей.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.services.email_matcher import build_booking_index, match_email_to_containers
from core.services.gmail_client import (
    GmailApiClient,
    GmailHistoryExpired,
    GmailNotConfigured,
    ParsedAttachment,
    ParsedMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    mode: str = 'skipped'               # 'incremental' | 'full' | 'skipped'
    processed: int = 0                  # сколько всего прошли messages.get
    created: int = 0                    # сколько ContainerEmail создали
    updated: int = 0                    # сколько существующих обновили
    matched: int = 0                    # у скольких container != NULL
    unmatched: int = 0
    attachments_saved: int = 0
    attachments_skipped: int = 0        # слишком большие
    errors: list[str] = field(default_factory=list)
    last_history_id: int | None = None
    started_at: str = ''
    finished_at: str = ''

    def as_dict(self) -> dict:
        return {
            'mode': self.mode,
            'processed': self.processed,
            'created': self.created,
            'updated': self.updated,
            'matched': self.matched,
            'unmatched': self.unmatched,
            'attachments_saved': self.attachments_saved,
            'attachments_skipped': self.attachments_skipped,
            'errors_count': len(self.errors),
            'errors_preview': self.errors[:5],
            'last_history_id': self.last_history_id,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
        }


# Gmail может «помнить» историю только ~7 дней; если дольше — делаем full.
_FALLBACK_LOOKBACK_DAYS = 'GMAIL_INITIAL_LOOKBACK_DAYS'


def sync_mailbox(*, force_full: bool = False) -> SyncReport:
    """Главная точка входа. Вызывается celery-задачей и ручным триггером из UI."""
    report = SyncReport(started_at=timezone.now().isoformat())

    if not settings.GMAIL_ENABLED:
        logger.info('[gmail_sync] GMAIL_ENABLED=False — пропуск.')
        report.mode = 'disabled'
        report.finished_at = timezone.now().isoformat()
        return report

    try:
        client = GmailApiClient()
    except GmailNotConfigured as err:
        logger.warning('[gmail_sync] Not configured: %s', err)
        report.mode = 'not_configured'
        report.errors.append(str(err))
        report.finished_at = timezone.now().isoformat()
        return report

    from core.models_email import GmailSyncState

    profile = client.get_profile()
    email_address = profile.get('emailAddress') or settings.GMAIL_USER_EMAIL or 'unknown@unknown'
    remote_history_id = int(profile.get('historyId') or 0) or None

    state, _ = GmailSyncState.objects.get_or_create(user_email=email_address)
    start_history_id = None if force_full else state.last_history_id

    booking_index = build_booking_index()

    if start_history_id:
        report.mode = 'incremental'
        try:
            _process_incremental(client, state, booking_index, report)
        except GmailHistoryExpired as err:
            logger.warning('[gmail_sync] History expired, fallback to full sync: %s', err)
            report.mode = 'full'
            report.errors.append(f'history_expired: {err}')
            _process_full(client, booking_index, report)
    else:
        report.mode = 'full'
        _process_full(client, booking_index, report)

    # В конце сохраняем max historyId, полученный от API (приоритет — свежий remote).
    new_hid = remote_history_id or state.last_history_id
    state.last_history_id = new_hid
    state.last_sync_at = timezone.now()
    state.last_error = '; '.join(report.errors[:5])[:2000]
    state.save(update_fields=['last_history_id', 'last_sync_at', 'last_error', 'updated_at'])
    report.last_history_id = new_hid
    report.finished_at = timezone.now().isoformat()

    logger.info('[gmail_sync] %s', report.as_dict())
    return report


# ---------------------------------------------------------------------------
# incremental / full
# ---------------------------------------------------------------------------

def _process_incremental(
    client: GmailApiClient,
    state,
    booking_index: dict[str, int],
    report: SyncReport,
) -> None:
    for gmail_id in client.list_history(state.last_history_id):
        _ingest_one(client, gmail_id, booking_index, report)


def _process_full(
    client: GmailApiClient,
    booking_index: dict[str, int],
    report: SyncReport,
) -> None:
    lookback_days = getattr(settings, _FALLBACK_LOOKBACK_DAYS, 30)
    query = f'newer_than:{int(lookback_days)}d -in:spam -in:trash'
    for gmail_id in client.list_messages(query):
        _ingest_one(client, gmail_id, booking_index, report)


# ---------------------------------------------------------------------------
# one message
# ---------------------------------------------------------------------------

def _ingest_one(
    client: GmailApiClient,
    gmail_id: str,
    booking_index: dict[str, int],
    report: SyncReport,
) -> None:
    from core.models_email import CarEmailLink, ContainerEmail, ContainerEmailLink

    if ContainerEmail.objects.filter(gmail_id=gmail_id).exists():
        return

    try:
        msg = client.get_message(gmail_id)
    except Exception as exc:
        logger.error('[gmail_sync] Failed to fetch %s: %s', gmail_id, exc, exc_info=True)
        report.errors.append(f'get_message({gmail_id}): {exc}')
        return

    report.processed += 1

    match = match_email_to_containers(msg, booking_index=booking_index)

    fallback_message_id = msg.message_id or f'gmail:{msg.gmail_id}'

    attachments_meta, saved, skipped = _persist_attachments(client, msg)
    report.attachments_saved += saved
    report.attachments_skipped += skipped

    defaults = {
        'thread_id': msg.thread_id,
        'in_reply_to': msg.in_reply_to,
        'references': msg.references,
        'direction': (
            ContainerEmail.DIRECTION_OUTGOING if msg.is_outgoing
            else ContainerEmail.DIRECTION_INCOMING
        ),
        'from_addr': msg.from_addr[:500],
        'to_addrs': msg.to_addrs,
        'cc_addrs': msg.cc_addrs,
        'subject': (msg.subject or '')[:1000],
        'body_text': msg.body_text,
        'body_html': msg.body_html,
        'snippet': (msg.snippet or '')[:500],
        'received_at': msg.received_at,
        'gmail_id': msg.gmail_id,
        'gmail_history_id': msg.history_id,
        'labels_json': list(msg.labels),
        'attachments_json': attachments_meta,
        'matched_by': match.primary_matched_by,
    }

    # Для обратной синхронизации UNREAD (см. ниже).
    is_incoming = not msg.is_outgoing
    gmail_is_unread = 'UNREAD' in (msg.labels or [])

    try:
        with transaction.atomic():
            obj, created = ContainerEmail.objects.get_or_create(
                message_id=fallback_message_id,
                defaults=defaults,
            )
            if created:
                report.created += 1
                # Создаём M2M-связи сразу при создании письма. Идемпотентно:
                # повторный sync этого gmail_id отфильтруется в начале функции.
                if match.hits:
                    # Reverse-sync при создании: INCOMING-письмо без UNREAD в
                    # Gmail считаем прочитанным и в карточках сразу (его уже
                    # прочитали где-то ещё в Gmail).
                    link_is_read = is_incoming and not gmail_is_unread
                    links = [
                        ContainerEmailLink(
                            email=obj,
                            container_id=hit.container_id,
                            matched_by=hit.matched_by,
                            is_read=link_is_read,
                        )
                        for hit in match.hits
                    ]
                    ContainerEmailLink.objects.bulk_create(
                        links, ignore_conflicts=True,
                    )
                # Линки к машинам по VIN. Reverse-sync через тот же флаг:
                # INCOMING-письмо без UNREAD в Gmail — сразу прочитано и в
                # карточках машин.
                if match.car_hits:
                    link_is_read = is_incoming and not gmail_is_unread
                    car_links = [
                        CarEmailLink(
                            email=obj,
                            car_id=hit.car_id,
                            matched_by=hit.matched_by,
                            is_read=link_is_read,
                        )
                        for hit in match.car_hits
                    ]
                    CarEmailLink.objects.bulk_create(
                        car_links, ignore_conflicts=True,
                    )
            else:
                # Идемпотентно обновим gmail_id/labels. Связи с контейнерами
                # не пересчитываем: пользователь мог вручную перепривязать.
                changed_fields: list[str] = []
                if not obj.gmail_id and msg.gmail_id:
                    obj.gmail_id = msg.gmail_id
                    changed_fields.append('gmail_id')
                if msg.history_id and obj.gmail_history_id != msg.history_id:
                    obj.gmail_history_id = msg.history_id
                    changed_fields.append('gmail_history_id')
                labels_changed = set(obj.labels_json or []) != set(msg.labels)
                if labels_changed:
                    obj.labels_json = list(msg.labels)
                    changed_fields.append('labels_json')
                if changed_fields:
                    obj.save(update_fields=changed_fields)
                    report.updated += 1

                # Reverse-sync при обновлении: если в Gmail сняли UNREAD
                # (пользователь прочитал письмо в почте) — протаскиваем
                # is_read=True на все links. Только INCOMING, чтобы не
                # ломать «unread»-бейджи для cross-linked OUTGOING-писем,
                # у которых в Gmail всегда нет UNREAD (они в SENT).
                if labels_changed and is_incoming and not gmail_is_unread:
                    ContainerEmailLink.objects.filter(
                        email_id=obj.pk, is_read=False,
                    ).update(is_read=True)
                    CarEmailLink.objects.filter(
                        email_id=obj.pk, is_read=False,
                    ).update(is_read=True)
    except Exception as exc:
        logger.error('[gmail_sync] Failed to save %s: %s', gmail_id, exc, exc_info=True)
        report.errors.append(f'save({gmail_id}): {exc}')
        return

    if match.is_matched:
        report.matched += 1
    else:
        report.unmatched += 1


# ---------------------------------------------------------------------------
# attachments
# ---------------------------------------------------------------------------

_SAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9._\-]+')


def _persist_attachments(
    client: GmailApiClient,
    msg: ParsedMessage,
) -> tuple[list[dict], int, int]:
    """Возвращает (attachments_json, сохранено, пропущено_из_за_размера)."""
    if not msg.attachments:
        return [], 0, 0

    limit_bytes = int(getattr(settings, 'GMAIL_MAX_ATTACHMENT_MB', 25)) * 1024 * 1024
    media_root = Path(settings.MEDIA_ROOT)
    now = msg.received_at or timezone.now()

    result: list[dict] = []
    saved = 0
    skipped = 0

    for idx, att in enumerate(msg.attachments):
        meta = {
            'filename': att.filename,
            'size': att.size,
            'content_type': att.mime_type,
            'attachment_id': att.attachment_id,
            'storage_path': '',
            'skipped_reason': '',
            'is_inline': att.is_inline,
            'content_id': att.content_id,
        }
        # Inline-картинки из HTML-вёрстки (логотипы, иконки соцсетей,
        # трекинг-пиксели) не скачиваем — только помечаем, чтобы UI их скрыл.
        if att.is_inline:
            meta['skipped_reason'] = 'inline'
            result.append(meta)
            skipped += 1
            continue
        if att.size and att.size > limit_bytes:
            meta['skipped_reason'] = 'too_large'
            result.append(meta)
            skipped += 1
            continue
        try:
            data = client.get_attachment(msg.gmail_id, att.attachment_id)
        except Exception as exc:
            logger.warning('[gmail_sync] attachment fetch failed (%s): %s', att.filename, exc)
            meta['skipped_reason'] = f'fetch_error: {exc}'
            result.append(meta)
            skipped += 1
            continue

        try:
            storage_path = _save_attachment_bytes(
                data, msg.gmail_id, idx, att, media_root, now,
            )
        except OSError as exc:
            # Permission denied / disk full / ENAMETOOLONG и пр. — не валим весь
            # sync_mailbox из-за одного вложения: пропускаем его и продолжаем,
            # чтобы последующие письма всё равно сохранились, а last_history_id
            # продвинулся вперёд.
            logger.warning(
                '[gmail_sync] attachment save failed (%s): %s',
                att.filename, exc, exc_info=True,
            )
            meta['skipped_reason'] = f'io_error: {exc}'
            result.append(meta)
            skipped += 1
            continue

        meta['storage_path'] = storage_path
        meta['size'] = len(data)
        result.append(meta)
        saved += 1

    return result, saved, skipped


def _save_attachment_bytes(
    data: bytes,
    gmail_id: str,
    idx: int,
    att: ParsedAttachment,
    media_root: Path,
    when,
) -> str:
    rel_dir = Path('container_emails') / when.strftime('%Y') / when.strftime('%m') / gmail_id
    abs_dir = media_root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _SAFE_FILENAME_RE.sub('_', att.filename or f'attachment_{idx}')
    if not safe_name or safe_name == '_':
        safe_name = f'attachment_{idx}'
    # Префикс индекса — чтобы файлы с одинаковым именем не затирались.
    filename = f'{idx:02d}_{safe_name}'
    abs_path = abs_dir / filename
    with open(abs_path, 'wb') as fh:
        fh.write(data)
    return str((rel_dir / filename).as_posix())


# ---------------------------------------------------------------------------
# helpers (публичные)
# ---------------------------------------------------------------------------

def ensure_sync_state(user_email: str):
    """Создаёт строку GmailSyncState, если её нет (для management-команд/тестов)."""
    from core.models_email import GmailSyncState
    state, _ = GmailSyncState.objects.get_or_create(user_email=user_email)
    return state


def media_path_abs(storage_path: str) -> Path:
    """Хелпер: resolve относительного пути из attachments_json в абсолютный."""
    return Path(settings.MEDIA_ROOT) / storage_path
