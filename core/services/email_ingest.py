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
    drafts_skipped: int = 0             # сколько Gmail-черновиков отфильтровали
    duplicates_skipped: int = 0         # сколько дубликатов по содержимому скрыли
    filtered_skipped: int = 0           # сколько писем спрятали пользовательскими фильтрами
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
            'drafts_skipped': self.drafts_skipped,
            'duplicates_skipped': self.duplicates_skipped,
            'filtered_skipped': self.filtered_skipped,
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
    ingest_filters = load_active_ingest_filters()

    if start_history_id:
        report.mode = 'incremental'
        try:
            _process_incremental(client, state, booking_index, report, ingest_filters)
        except GmailHistoryExpired as err:
            logger.warning('[gmail_sync] History expired, fallback to full sync: %s', err)
            report.mode = 'full'
            report.errors.append(f'history_expired: {err}')
            _process_full(client, booking_index, report, ingest_filters)
    else:
        report.mode = 'full'
        _process_full(client, booking_index, report, ingest_filters)

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
    ingest_filters: list[tuple],
) -> None:
    for gmail_id in client.list_history(state.last_history_id):
        _ingest_one(client, gmail_id, booking_index, report, ingest_filters)


def _process_full(
    client: GmailApiClient,
    booking_index: dict[str, int],
    report: SyncReport,
    ingest_filters: list[tuple],
) -> None:
    lookback_days = getattr(settings, _FALLBACK_LOOKBACK_DAYS, 30)
    # -in:drafts — не тянем черновики из Gmail (автосохранение web-интерфейса
    # создаёт десятки промежуточных message_id на один черновик, они
    # замусоривают карточки контейнеров/машин/автовозов).
    query = f'newer_than:{int(lookback_days)}d -in:spam -in:trash -in:drafts'
    for gmail_id in client.list_messages(query):
        _ingest_one(client, gmail_id, booking_index, report, ingest_filters)


# ---------------------------------------------------------------------------
# one message
# ---------------------------------------------------------------------------

def _ingest_one(
    client: GmailApiClient,
    gmail_id: str,
    booking_index: dict[str, int],
    report: SyncReport,
    ingest_filters: list[tuple] | None = None,
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

    # Черновики Gmail пропускаем: при наборе письма в web-интерфейсе Gmail
    # автосохраняет его каждые несколько секунд и прилетает в history.list
    # как messageAdded — без фильтра мы плодим «письма» в карточках на
    # каждое автосохранение. Свои черновики ведём на стороне проекта.
    if 'DRAFT' in (msg.labels or []):
        report.drafts_skipped += 1
        return

    report.processed += 1

    # Дедупликация по содержимому. Автоматика Caromoto/Maersk/Salesforce
    # периодически присылает одно и то же уведомление несколькими Gmail-
    # сообщениями с разными Message-ID (рассылка продублировалась внутри
    # их систем). В карточке контейнера мы видим два «близнеца» с одним
    # subject/body, но разными matched_by («по треду» + «по номеру»).
    # Такой дубль сохраняем как ContainerEmail (чтобы sync был идемпотентен
    # по gmail_id), но НЕ создаём ни ContainerEmailLink, ни CarEmailLink —
    # дубль не попадёт в emails_for_panel() и не засветится в карточках.
    is_duplicate = _is_content_duplicate(msg)

    # Пользовательские фильтры по ключевым фразам (админка → «Фильтры
    # Gmail-ингеста»). Если письмо матчится — сохраняем его в БД (чтобы
    # sync оставался идемпотентным по gmail_id), но не создаём связей ни
    # с контейнерами, ни с машинами: в карточках не появится.
    filter_hit = ''
    if ingest_filters:
        filter_hit = matches_ingest_filter(
            subject=msg.subject or '',
            body_text=msg.body_text or '',
            body_html=msg.body_html or '',
            filters=ingest_filters,
        )

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
                if is_duplicate:
                    # Не создаём линков — дубль «скрыт» из всех карточек.
                    report.duplicates_skipped += 1
                    return
                if filter_hit:
                    report.filtered_skipped += 1
                    logger.info(
                        '[gmail_sync] filtered out gmail_id=%s by phrase %r',
                        gmail_id, filter_hit,
                    )
                    return
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
# content-based deduplication
# ---------------------------------------------------------------------------

# Вырезаем блоки <script>/<style>/<head>/<meta>/<link> целиком вместе с
# содержимым — там часто сидят уникальные токены (Salesforce UserContext,
# Google Analytics client-id, трекинг-пиксели), из-за которых побайтное
# сравнение двух «одинаковых» писем не срабатывает.
_VOLATILE_HTML_BLOCK_RE = re.compile(
    r'<\s*(script|style|head|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>',
    re.IGNORECASE | re.DOTALL,
)
# Оставшиеся одиночные теги (<br>, <img>, закрывающие без пары и т.п.).
_HTML_TAG_RE = re.compile(r'<[^>]+>')
# Salesforce-рассылки (Maersk и др.) вставляют в тело длинный
# JSON-инициализатор `window.sfdcPage`/`UserContext.initialize(...)` даже в
# text/plain-часть. В нём зашиты currentTime/sessionId/csrfToken, поэтому
# два «визуально одинаковых» письма отличаются внутри этого блока. Рубим
# всё от триггерного ключа до конца текста — нас интересует только
# видимая часть письма ДО этого блока.
_SFDC_BLOCK_RE = re.compile(
    r'(?:window\.(?:sfdcPage|UserContext)|UserContext\.initialize)[\s\S]*',
    re.IGNORECASE,
)
# Длинные JSON-объекты в одну строку (>200 символов без переносов) — часто
# это «слепки» userPreferences/sessionState/labels. Снова — вариативный
# контент, который не должен влиять на дедуп.
_LONG_JSON_BLOB_RE = re.compile(r'\{[^{}\n\r]{200,}\}')
_WHITESPACE_RE = re.compile(r'\s+')


def _normalize_body_for_hash(text: str) -> str:
    """Нормализация тела письма для сравнения «по смыслу».

    * убирает <script>/<style>/<head>/<meta>/<link> блоки с содержимым;
    * срезает Salesforce-JS-инициализатор (где зашит currentTime/sessionId);
    * убирает остальные HTML-теги;
    * схлопывает пробельные последовательности и приводит к lower-case.
    """
    if not text:
        return ''
    s = text
    s = _VOLATILE_HTML_BLOCK_RE.sub(' ', s)
    s = _SFDC_BLOCK_RE.sub(' ', s)
    s = _HTML_TAG_RE.sub(' ', s)
    s = _LONG_JSON_BLOB_RE.sub(' ', s)
    s = _WHITESPACE_RE.sub(' ', s).strip().lower()
    return s


def _content_digest(
    *,
    from_addr: str,
    subject: str,
    body_text: str,
    body_html: str,
) -> str:
    """Стабильный хэш «видимого» содержимого письма (для дедупликации).

    Берёт ``from``+``subject``+нормализованное тело (первые 4000 символов,
    чтобы трекинг-пиксели и unsubscribe-хэши в хвосте не влияли).
    Источник тела — ``body_text``, если после нормализации не пуст, иначе
    ``body_html``. Возвращает sha256-hex.
    """
    import hashlib

    norm_text = _normalize_body_for_hash(body_text or '')
    norm_html = _normalize_body_for_hash(body_html or '')
    # Для писем, где в text/plain лежит тот же Salesforce-блок, что и в
    # html — после нормализации оба дают одинаковый «видимый» текст. Если
    # нет — берём более длинную версию (обычно body_text в ASCII-почте).
    body_norm = norm_text if len(norm_text) >= len(norm_html) else norm_html
    body_norm = body_norm[:4000]

    key = '||'.join((
        (from_addr or '').strip().lower(),
        (subject or '').strip().lower(),
        body_norm,
    ))
    return hashlib.sha256(key.encode('utf-8', 'replace')).hexdigest()


def load_active_ingest_filters() -> list[tuple]:
    """Загружает активные фильтры один раз на цикл синхронизации.

    Возвращает список кортежей ``(phrase_lower_or_pattern, scope,
    match_type, phrase_original)``. Для REGEX храним скомпилированный
    паттерн; некорректные выражения логируем и пропускаем.
    """
    from core.models_email import EmailIngestFilter

    result: list[tuple] = []
    qs = EmailIngestFilter.objects.filter(is_active=True).only(
        'phrase', 'scope', 'match_type',
    )
    for f in qs:
        phrase = (f.phrase or '').strip()
        if not phrase:
            continue
        if f.match_type == EmailIngestFilter.MATCH_REGEX:
            try:
                compiled = re.compile(phrase, re.IGNORECASE | re.DOTALL)
            except re.error as exc:
                logger.warning(
                    '[gmail_sync] skip invalid regex filter %r: %s',
                    phrase, exc,
                )
                continue
            result.append((compiled, f.scope, f.match_type, phrase))
        else:
            result.append((phrase.lower(), f.scope, f.match_type, phrase))
    return result


def matches_ingest_filter(
    *,
    subject: str,
    body_text: str,
    body_html: str,
    filters: list[tuple],
) -> str:
    """Проверяет письмо против активных фильтров.

    Возвращает исходную фразу сработавшего фильтра или пустую строку,
    если ни один не сработал. Для body используем тот же нормализатор,
    что и в дедупликации (вычищает HTML/script/style) — чтобы фразы
    срабатывали по «видимому» тексту письма, а не по кускам разметки.
    """
    if not filters:
        return ''

    subj_raw = subject or ''
    subj_lower = subj_raw.lower()

    body_raw = ''
    body_norm = ''
    body_norm_ready = False

    def _ensure_body_norm() -> str:
        nonlocal body_raw, body_norm, body_norm_ready
        if not body_norm_ready:
            src_text = body_text or ''
            src_html = body_html or ''
            body_raw = (src_text + '\n' + src_html)
            if src_html:
                body_norm = _normalize_body_for_hash(src_html)
            else:
                body_norm = _normalize_body_for_hash(src_text)
            body_norm_ready = True
        return body_norm

    for needle, scope, match_type, phrase_original in filters:
        if match_type == 'REGEX':
            pattern = needle  # compiled re.Pattern
            if scope in ('SUBJECT', 'ANY') and pattern.search(subj_raw):
                return phrase_original
            if scope in ('BODY', 'ANY'):
                if pattern.search(_ensure_body_norm()):
                    return phrase_original
        else:
            sub = needle  # уже lowercase
            if scope in ('SUBJECT', 'ANY') and sub in subj_lower:
                return phrase_original
            if scope in ('BODY', 'ANY'):
                if sub in _ensure_body_norm():
                    return phrase_original
    return ''


def _is_content_duplicate(msg: ParsedMessage) -> bool:
    """Есть ли уже в БД письмо с тем же «видимым» содержимым.

    Дубликаты встречаются у автоматических рассылок (Caromoto, Maersk/
    Salesforce, Fleet-Viewer и пр.), которые шлют одно и то же уведомление
    несколькими Gmail-сообщениями с разными Message-ID. В карточке они
    выглядят как «близнецы» с разными ярлыками сопоставления
    («по треду» / «по номеру контейнера»).

    Сравнение идёт по *нормализованному* дайджесту (FROM + SUBJECT +
    очищенное тело), т.к. побайтно отличаться могут Salesforce-скрипты,
    трекинг-пиксели, unsubscribe-токены и прочая вариативная служебка.
    """
    from datetime import timedelta
    from core.models_email import ContainerEmail

    from_addr = (msg.from_addr or '')[:500]
    subject = (msg.subject or '')[:1000]
    body_text = msg.body_text or ''
    body_html = msg.body_html or ''

    # Пустые письма не дедупим — не появляются в карточках всё равно.
    if not subject and not body_text and not body_html:
        return False

    new_digest = _content_digest(
        from_addr=from_addr, subject=subject,
        body_text=body_text, body_html=body_html,
    )

    # Ищем кандидатов по ключу (from_addr, subject) — это индекс-friendly,
    # а заодно сильно урезает набор. Окно 30 дней — достаточно, чтобы
    # поймать повторные уведомления (обычно приходят в пределах часа).
    since = timezone.now() - timedelta(days=30)
    candidates = (
        ContainerEmail.objects
        .filter(
            from_addr=from_addr,
            subject=subject,
            received_at__gte=since,
        )
        .exclude(gmail_id=msg.gmail_id)
        .only('id', 'from_addr', 'subject', 'body_text', 'body_html')
        # Бейлимся после первой же находки — не тянем тысячи кандидатов.
        .iterator(chunk_size=50)
    )
    for cand in candidates:
        cand_digest = _content_digest(
            from_addr=cand.from_addr or '',
            subject=cand.subject or '',
            body_text=cand.body_text or '',
            body_html=cand.body_html or '',
        )
        if cand_digest == new_digest:
            return True
    return False


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
