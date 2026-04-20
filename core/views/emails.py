"""HTTP-эндпоинты для переписки в карточке контейнера.

Маршруты подключаются через core/urls.py.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models_email import (
    CarEmailLink,
    ContainerEmail, ContainerEmailLink,
    EmailGroup,
)
from core.models_contact import Contact, ContactEmail
from core.services.email_reply_parser import (
    format_quoted_reply,
    split_reply_and_quote,
    split_reply_and_quote_html,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML-партиал для полного просмотра одного письма (inline-expand / modal)
# ---------------------------------------------------------------------------

@staff_member_required
@require_GET
def email_detail(request, email_id: int):
    email = get_object_or_404(
        ContainerEmail.objects.prefetch_related('containers'),
        pk=email_id,
    )

    show_html = bool(request.GET.get('html'))

    # ── Режем «суть» / цитату — как в Gmail ──────────────────────────────
    reply_text, quoted_text = split_reply_and_quote(
        email.body_text or email.snippet or ''
    )

    safe_html_reply = ''
    safe_html_quoted = ''
    if show_html and email.body_html:
        reply_html, quoted_html = split_reply_and_quote_html(email.body_html)
        safe_html_reply = _sanitize_html(reply_html)
        if quoted_html:
            safe_html_quoted = _sanitize_html(quoted_html)

    # Фильтруем inline-картинки из HTML-сигнатуры (логотипы, Outlook-… .png)
    # — пользователю они не нужны. Индекс сохраняем оригинальный, чтобы
    # email_attachment мог открыть файл по тому же attachments_json.
    from core.templatetags.email_extras import _INLINE_IMG_FILENAME

    attachments = []
    for idx, att in enumerate(email.attachments_json or []):
        filename = att.get('filename') or ''
        if att.get('is_inline') or att.get('skipped_reason') == 'inline':
            continue
        if filename and _INLINE_IMG_FILENAME.match(filename):
            continue
        attachments.append({
            'index': idx,
            'filename': filename or f'attachment_{idx}',
            'size': att.get('size') or 0,
            'mime_type': att.get('content_type') or 'application/octet-stream',
            'available': bool(att.get('storage_path')),
            'skipped_reason': att.get('skipped_reason') or '',
        })

    return render(request, 'admin/core/container/_email_detail.html', {
        'email': email,
        'show_html': show_html,
        'reply_text': reply_text,
        'quoted_text': quoted_text,
        'safe_html_reply': safe_html_reply,
        'safe_html_quoted': safe_html_quoted,
        'attachments': attachments,
    })


# ---------------------------------------------------------------------------
# Скачивание вложения
# ---------------------------------------------------------------------------

@staff_member_required
@require_GET
def email_attachment(request, email_id: int, idx: int):
    email = get_object_or_404(ContainerEmail, pk=email_id)
    attachments = email.attachments_json or []
    if idx < 0 or idx >= len(attachments):
        raise Http404('Attachment not found')

    meta = attachments[idx]
    storage_path = meta.get('storage_path') or ''
    if not storage_path:
        return HttpResponse(
            'Вложение не сохранено локально (слишком большое или ошибка загрузки).',
            status=410,
        )

    abs_path = Path(settings.MEDIA_ROOT) / storage_path
    if not abs_path.exists():
        logger.warning('[email_attachment] File missing on disk: %s', abs_path)
        raise Http404('File missing on disk')

    filename = meta.get('filename') or abs_path.name
    content_type = meta.get('content_type') or mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    response = FileResponse(open(abs_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{_safe_header_filename(filename)}"'
    return response


# ---------------------------------------------------------------------------
# Отметка прочитано / непрочитано
# ---------------------------------------------------------------------------

def _enqueue_gmail_mark_read(gmail_ids: list[str]) -> None:
    """Ставит в Celery задачу снять лейбл ``UNREAD`` с указанных писем.

    Используется после того, как пользователь пометил письмо прочитанным
    в карточке контейнера — чтобы Gmail в почте показывал тот же статус.

    Стратегия «Option A»: письмо считается прочитанным в Gmail, если
    хотя бы в одной карточке с ним работали. Поэтому дёргаем API на
    каждое подтверждение — Gmail сам идемпотентно обработает повтор.
    Если Gmail не сконфигурирован или очередь недоступна — просто
    логируем, UI не ломаем.
    """
    ids = [gid for gid in (gmail_ids or []) if gid]
    if not ids:
        return
    try:
        from core.tasks_email import gmail_mark_read_task
        gmail_mark_read_task.delay(ids)
    except Exception as exc:
        logger.warning('[emails] cannot enqueue gmail_mark_read_task: %s', exc)


@staff_member_required
@require_POST
def email_mark_read(request, email_id: int):
    """Отметить письмо прочитанным/непрочитанным.

    Параметры POST:
      * ``is_read`` (``1``/``0``) — новое значение.
      * ``scope`` — ``container`` | ``car`` | ``autotransport``; если не задан,
        а есть ``container_id`` — работаем в режиме контейнера (back-compat).
      * ``scope_id`` — id сущности, к ссылкам которой применяем изменение.
      * ``container_id`` — back-compat для старого фронта контейнеров.

    Для AutoTransport меняем все ``CarEmailLink`` машин этого рейса.
    При ``is_read=True`` + INCOMING — ставим задачу снять UNREAD в Gmail.
    """
    email = get_object_or_404(ContainerEmail, pk=email_id)
    new_val = request.POST.get('is_read', '1') == '1'
    scope = (request.POST.get('scope') or '').strip().lower()
    scope_id = request.POST.get('scope_id')
    container_id_back_compat = request.POST.get('container_id')

    updated = 0

    if scope == 'car' and scope_id:
        updated = CarEmailLink.objects.filter(
            email_id=email.pk, car_id=scope_id,
        ).update(is_read=new_val)
    elif scope == 'autotransport' and scope_id:
        from core.models import AutoTransport  # локально, чтобы избежать circular
        try:
            at = AutoTransport.objects.get(pk=scope_id)
        except AutoTransport.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'AutoTransport not found'}, status=404)
        car_ids = list(at.cars.values_list('id', flat=True))
        if car_ids:
            updated = CarEmailLink.objects.filter(
                email_id=email.pk, car_id__in=car_ids,
            ).update(is_read=new_val)
    else:
        # default/container — back-compat
        qs = ContainerEmailLink.objects.filter(email_id=email.pk)
        container_id = container_id_back_compat or (scope_id if scope == 'container' else None)
        if container_id:
            qs = qs.filter(container_id=container_id)
        updated = qs.update(is_read=new_val)

    if (
        new_val
        and updated
        and email.direction == ContainerEmail.DIRECTION_INCOMING
        and email.gmail_id
    ):
        _enqueue_gmail_mark_read([email.gmail_id])

    return JsonResponse({'ok': True, 'is_read': new_val, 'updated': updated})


# ---------------------------------------------------------------------------
# «Ответить позже» (follow-up flag)
# ---------------------------------------------------------------------------

@staff_member_required
@require_POST
def email_set_needs_reply(request, email_id: int):
    """Ставит/снимает глобальный follow-up флаг на письме.

    POST ``value=1|0``. Флаг хранится на самом ``ContainerEmail`` — виден из
    всех карточек (Container/Car/AutoTransport), где засветилось письмо.
    Авто-снятие при отправке ответа реализовано в ``reply_to_email``.
    """
    email = get_object_or_404(ContainerEmail, pk=email_id)
    raw = (request.POST.get('value') or '').strip()
    new_val = raw in ('1', 'true', 'True', 'on', 'yes')

    email.needs_reply = new_val
    if new_val:
        email.needs_reply_set_at = timezone.now()
        email.needs_reply_set_by = (
            request.user if request.user.is_authenticated else None
        )
    else:
        email.needs_reply_set_at = None
        email.needs_reply_set_by = None
    email.save(update_fields=[
        'needs_reply', 'needs_reply_set_at', 'needs_reply_set_by',
    ])

    return JsonResponse({
        'ok': True,
        'email_id': email.pk,
        'needs_reply': new_val,
    })


@staff_member_required
@require_POST
def email_mark_car_read(request, car_id: int):
    """Помечает всю переписку машины прочитанной — per-карточка.

    Обновляет только ``CarEmailLink`` этой машины; для INCOMING-писем,
    ставших прочитанными, триггерит снятие ``UNREAD`` в Gmail.
    """
    affected = list(
        CarEmailLink.objects
        .filter(car_id=car_id, is_read=False)
        .values_list('email_id', flat=True)
    )
    updated = CarEmailLink.objects.filter(
        car_id=car_id, is_read=False,
    ).update(is_read=True)

    if affected:
        gmail_ids = list(
            ContainerEmail.objects
            .filter(
                pk__in=affected,
                direction=ContainerEmail.DIRECTION_INCOMING,
            )
            .exclude(gmail_id='')
            .values_list('gmail_id', flat=True)
            .distinct()
        )
        if gmail_ids:
            _enqueue_gmail_mark_read(gmail_ids)

    return JsonResponse({'ok': True, 'updated': updated})


@staff_member_required
@require_POST
def email_mark_autotransport_read(request, at_id: int):
    """Помечает всю переписку рейса прочитанной (через машины рейса).

    Обновляем ``CarEmailLink`` всех машин этого рейса. Триггерит Gmail-sync
    для затронутых INCOMING-писем.
    """
    from core.models import AutoTransport
    at = get_object_or_404(AutoTransport, pk=at_id)
    car_ids = list(at.cars.values_list('id', flat=True))
    if not car_ids:
        return JsonResponse({'ok': True, 'updated': 0})

    affected = list(
        CarEmailLink.objects
        .filter(car_id__in=car_ids, is_read=False)
        .values_list('email_id', flat=True)
        .distinct()
    )
    updated = CarEmailLink.objects.filter(
        car_id__in=car_ids, is_read=False,
    ).update(is_read=True)

    if affected:
        gmail_ids = list(
            ContainerEmail.objects
            .filter(
                pk__in=affected,
                direction=ContainerEmail.DIRECTION_INCOMING,
            )
            .exclude(gmail_id='')
            .values_list('gmail_id', flat=True)
            .distinct()
        )
        if gmail_ids:
            _enqueue_gmail_mark_read(gmail_ids)

    return JsonResponse({'ok': True, 'updated': updated})


@staff_member_required
@require_POST
def email_mark_container_read(request, container_id: int):
    """Помечает всю переписку контейнера прочитанной — per-карточка.

    Вызывается автоматически при разворачивании блока «Переписка».
    Трогаем только ссылки ``ContainerEmailLink`` этой карточки — в других
    карточках письмо остаётся непрочитанным.

    Для INCOMING-писем, ставших прочитанными в этой карточке, ставим задачу
    снять ``UNREAD`` в Gmail.
    """
    affected_links = list(
        ContainerEmailLink.objects
        .filter(container_id=container_id, is_read=False)
        .values_list('email_id', flat=True)
    )
    updated = ContainerEmailLink.objects.filter(
        container_id=container_id,
        is_read=False,
    ).update(is_read=True)

    if affected_links:
        gmail_ids = list(
            ContainerEmail.objects
            .filter(
                pk__in=affected_links,
                direction=ContainerEmail.DIRECTION_INCOMING,
            )
            .exclude(gmail_id='')
            .values_list('gmail_id', flat=True)
            .distinct()
        )
        if gmail_ids:
            _enqueue_gmail_mark_read(gmail_ids)

    return JsonResponse({'ok': True, 'updated': updated})


# ---------------------------------------------------------------------------
# Ручной триггер Gmail-синхронизации
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Ответ в тред / новое письмо / черновик цитаты
# ---------------------------------------------------------------------------


def _compose_error_response(exc, status: int = 400) -> JsonResponse:
    return JsonResponse({'ok': False, 'error': str(exc)[:500]}, status=status)


@staff_member_required
@require_GET
def email_reply_draft(request, email_id: int):
    """Возвращает автозаполненный черновик ответа (JSON) — к этому письму.

    Используется UI-композером при клике на «Ответить»: фронту нужны значения
    для подстановки в form (To/Cc/Subject/цитата).
    """
    email = get_object_or_404(
        ContainerEmail.objects.prefetch_related('containers'), pk=email_id,
    )

    from_addr = getattr(settings, 'GMAIL_FROM_EMAIL', '') or getattr(settings, 'GMAIL_USER_EMAIL', '')
    from_addr_lower = (from_addr or '').lower()

    cc_raw = (email.cc_addrs or '') + ', ' + (email.to_addrs or '')
    cc_addrs: list[str] = []
    for item in (x.strip() for x in cc_raw.split(',') if x.strip()):
        lower = item.lower()
        if from_addr_lower and from_addr_lower in lower:
            continue
        if item not in cc_addrs:
            cc_addrs.append(item)

    subject_src = (email.subject or '').strip()
    subject = subject_src if subject_src.lower().startswith('re:') else f'Re: {subject_src}'

    quote = format_quoted_reply(email)

    return JsonResponse({
        'ok': True,
        'to': email.from_addr or '',
        'cc': ', '.join(cc_addrs),
        'bcc': '',
        'subject': subject.strip(),
        'quote': quote,
        'parent_email_id': email.pk,
        'signature_text': getattr(settings, 'GMAIL_SIGNATURE_TEXT', ''),
        'max_mb': int(getattr(settings, 'GMAIL_MAX_OUTBOUND_MB', 25)),
    })


def _resolve_origin_scope(request):
    """Определяет «карточку-источник» по POST-параметрам.

    Возвращает ``(scope, origin_container, origin_car, origin_autotransport)``.
    Поддерживаемые сценарии:
      * ``scope=container`` + ``scope_id`` (или back-compat ``container_id``);
      * ``scope=car`` + ``scope_id``;
      * ``scope=autotransport`` + ``scope_id``.
    Если scope не задан, пытаемся угадать по back-compat ``container_id``.
    """
    scope = (request.POST.get('scope') or '').strip().lower()
    scope_id = request.POST.get('scope_id')
    container_id_bc = request.POST.get('container_id')

    origin_container = None
    origin_car = None
    origin_autotransport = None

    if scope == 'car' and scope_id:
        from core.models import Car
        try:
            origin_car = Car.objects.only('id', 'vin').get(pk=int(scope_id))
        except (Car.DoesNotExist, ValueError):
            origin_car = None
        return 'car', origin_container, origin_car, origin_autotransport

    if scope == 'autotransport' and scope_id:
        from core.models import AutoTransport
        try:
            origin_autotransport = (
                AutoTransport.objects.only('id', 'number').get(pk=int(scope_id))
            )
        except (AutoTransport.DoesNotExist, ValueError):
            origin_autotransport = None
        return 'autotransport', origin_container, origin_car, origin_autotransport

    # container / default / back-compat
    from core.models import Container
    cid = scope_id if scope == 'container' else container_id_bc
    if cid:
        try:
            origin_container = Container.objects.only('id').get(pk=int(cid))
        except (Container.DoesNotExist, ValueError):
            origin_container = None
    return 'container', origin_container, origin_car, origin_autotransport


@staff_member_required
@require_POST
def email_reply_send(request, email_id: int):
    """Отправляет ответ в существующий тред."""
    if not getattr(settings, 'GMAIL_ENABLED', False):
        return JsonResponse({
            'ok': False,
            'error': 'GMAIL_ENABLED=False — отправка отключена.',
        }, status=400)

    parent = get_object_or_404(
        ContainerEmail.objects.prefetch_related('containers'), pk=email_id,
    )

    scope, origin_container, origin_car, origin_autotransport = _resolve_origin_scope(request)

    # Back-compat: если scope=container не задан и container_id тоже, берём
    # первый контейнер из parent (старое поведение).
    if scope == 'container' and origin_container is None and origin_car is None and origin_autotransport is None:
        origin_container = parent.containers.first()

    try:
        from core.services.email_compose import ComposeError, reply_to_email

        sent = reply_to_email(
            parent_email=parent,
            user=request.user,
            to=request.POST.get('to', ''),
            cc=request.POST.get('cc', ''),
            bcc=request.POST.get('bcc', ''),
            subject=request.POST.get('subject', ''),
            body_text=request.POST.get('body_text', ''),
            attachments=request.FILES.getlist('attachments'),
            origin_container=origin_container,
            origin_car=origin_car,
            origin_autotransport=origin_autotransport,
        )
    except ComposeError as exc:
        return _compose_error_response(exc, status=400)
    except Exception as exc:
        logger.exception('[email_reply_send] unexpected: %s', exc)
        return _compose_error_response(exc, status=500)

    return _render_bubble_response(
        request, sent,
        scope=scope,
        container_id=getattr(origin_container, 'pk', None),
        car_id=getattr(origin_car, 'pk', None),
        autotransport=origin_autotransport,
    )


@staff_member_required
@require_POST
def email_compose_send(request):
    """Новое письмо по контейнеру / машине / автовозу (без родителя)."""
    if not getattr(settings, 'GMAIL_ENABLED', False):
        return JsonResponse({
            'ok': False,
            'error': 'GMAIL_ENABLED=False — отправка отключена.',
        }, status=400)

    scope, origin_container, origin_car, origin_autotransport = _resolve_origin_scope(request)

    common_kwargs = dict(
        user=request.user,
        to=request.POST.get('to', ''),
        cc=request.POST.get('cc', ''),
        bcc=request.POST.get('bcc', ''),
        subject=request.POST.get('subject', ''),
        body_text=request.POST.get('body_text', ''),
        attachments=request.FILES.getlist('attachments'),
    )

    try:
        from core.services.email_compose import (
            ComposeError,
            compose_new_email,
            compose_new_email_from_autotransport,
            compose_new_email_from_car,
        )

        if scope == 'car':
            if origin_car is None:
                return JsonResponse(
                    {'ok': False, 'error': 'Машина не найдена.'}, status=400,
                )
            sent = compose_new_email_from_car(car=origin_car, **common_kwargs)
        elif scope == 'autotransport':
            if origin_autotransport is None:
                return JsonResponse(
                    {'ok': False, 'error': 'Автовоз не найден.'}, status=400,
                )
            sent = compose_new_email_from_autotransport(
                autotransport=origin_autotransport, **common_kwargs,
            )
        else:
            if origin_container is None:
                return JsonResponse(
                    {'ok': False, 'error': 'container_id обязателен.'},
                    status=400,
                )
            sent = compose_new_email(container=origin_container, **common_kwargs)
    except ComposeError as exc:
        return _compose_error_response(exc, status=400)
    except Exception as exc:
        logger.exception('[email_compose_send] unexpected: %s', exc)
        return _compose_error_response(exc, status=500)

    return _render_bubble_response(
        request, sent,
        scope=scope,
        container_id=getattr(origin_container, 'pk', None),
        car_id=getattr(origin_car, 'pk', None),
        autotransport=origin_autotransport,
    )


def _resolve_group_addrs(members) -> list:
    """Возвращает RFC 5322-адреса участников группы, предпочитая имя из ``Contact``
    над ``EmailGroupMember.display_name`` — чтобы имена одного email были
    консистентны между группами, карточкой контакта и автодополнением.

    Если email присутствует в ``ContactEmail`` — берём ``Contact.name`` (если
    задано), иначе остаёмся на имени из группы.
    """
    if not members:
        return []

    emails_lc = {(m.email or '').lower(): m.email for m in members if m.email}
    if not emails_lc:
        return [m.as_header_format for m in members]

    # Одним запросом собираем маппинг email_lc → Contact.name (если есть).
    ce_qs = (
        ContactEmail.objects
        .filter(email__in=list(emails_lc.values()))
        .select_related('contact')
        .order_by('-is_primary', 'position')
    )
    name_map: dict = {}
    for ce in ce_qs:
        lc = (ce.email or '').lower()
        if lc in name_map:
            continue  # первый (is_primary раньше в order_by) — побеждает
        cname = (ce.contact.name or '').strip() if ce.contact else ''
        if cname:
            name_map[lc] = cname

    def _quote_if_needed(name: str) -> str:
        if any(ch in name for ch in ',;<>"'):
            return '"' + name.replace('"', '\\"') + '"'
        return name

    out: list = []
    for m in members:
        lc = (m.email or '').lower()
        if lc in name_map:
            out.append(f'{_quote_if_needed(name_map[lc])} <{m.email}>')
        else:
            out.append(m.as_header_format)
    return out


@staff_member_required
@require_GET
def email_groups_list(request):
    """Список общих email-групп для composer.

    Возвращает JSON:
    ``[{id, name, description, count, addrs: [...]}, ...]``.

    ``addrs`` уже в RFC 5322-формате (``Имя <email>`` если задано display_name,
    иначе просто email) — фронтенду остаётся склеить их через запятую и
    вставить в поле ``To`` / ``Cc`` / ``Bcc``.
    """
    groups = (
        EmailGroup.objects
        .all()
        .prefetch_related('members')
        .order_by('name')
    )
    data = []
    for g in groups:
        members = list(g.members.all())  # prefetched, дешёвая операция
        if not members:
            continue  # пустые группы не засоряют dropdown
        data.append({
            'id': g.pk,
            'name': g.name,
            'description': g.description or '',
            'count': len(members),
            'addrs': _resolve_group_addrs(members),
        })
    return JsonResponse({'ok': True, 'groups': data})


@staff_member_required
@require_GET
def contacts_autocomplete(request):
    """Gmail-style autocomplete для полей получателей в composer.

    ``GET /core/emails/contacts/search/?q=<query>&limit=<n>``

    Возвращает смешанный список из трёх источников:
      1. Email-группы (``kind=group``) — name match.
      2. Контакты (``kind=contact``) — по name/position/email/counterparty name.
      3. Исторические адресаты (``kind=history``) — email-адреса, которые
         встречались в from/to/cc существующих писем, но не являются контактом.

    Группы всегда первыми. Далее контакты (приоритет: совпадение email →
    совпадение имени → должности). Исторические последними, упорядочены по
    частоте (``seen``).
    """
    from collections import Counter
    import re

    q_raw = (request.GET.get('q') or '').strip()
    try:
        limit = int(request.GET.get('limit', 12))
    except (TypeError, ValueError):
        limit = 12
    limit = max(1, min(limit, 25))

    # Пустой запрос → пустой список (иначе фронт может спамить БД впустую).
    if not q_raw:
        return JsonResponse({'ok': True, 'items': []})

    q = q_raw.lower()
    items: list = []

    # ── 1. Email-группы ────────────────────────────────────────────────
    groups_qs = (
        EmailGroup.objects
        .filter(name__icontains=q_raw)
        .prefetch_related('members')
        .order_by('name')[:limit]
    )
    for g in groups_qs:
        members = list(g.members.all())
        if not members:
            continue
        items.append({
            'kind': 'group',
            'id': g.pk,
            'label': g.name,
            'sub': f'{len(members)} адрес(ов)',
            'addrs': _resolve_group_addrs(members),
        })

    # ── 2. Контакты ────────────────────────────────────────────────────
    # Поиск по name / position / comment (у Contact) + email (через related).
    # Джойним вручную, чтобы дедуплицировать и собрать лучший email на контакт.
    contact_ids: set = set()

    # a) По email (максимальный приоритет).
    email_hits = (
        ContactEmail.objects
        .filter(email__icontains=q_raw)
        .select_related('contact', 'contact__content_type')
        .order_by('-is_primary', 'position', 'email')[:limit * 2]
    )
    seen_ids: set = set()
    contact_entries: list = []
    for ce in email_hits:
        c = ce.contact
        if c.pk in seen_ids:
            continue
        seen_ids.add(c.pk)
        contact_entries.append((c, ce.email))

    # b) По name / position / comment (без email-match выше).
    name_hits = (
        Contact.objects
        .filter(
            models_Q_name_or_position(q_raw)
        )
        .exclude(pk__in=seen_ids)
        .select_related('content_type')
        .prefetch_related('emails')
        .order_by('name')[:limit * 2]
    )
    for c in name_hits:
        if c.pk in seen_ids:
            continue
        seen_ids.add(c.pk)
        em = c.emails.first()
        email_str = em.email if em else ''
        contact_entries.append((c, email_str))

    for c, email_str in contact_entries[:limit]:
        if not email_str:
            continue
        addr = email_str
        if c.name:
            name = c.name.strip()
            if any(ch in name for ch in ',;<>"'):
                name = '"' + name.replace('"', '\\"') + '"'
            addr = f'{name} <{email_str}>'

        sub_parts = []
        if c.position:
            sub_parts.append(c.position)
        cp_name = c.counterparty_name if not c.is_orphan else ''
        if cp_name and cp_name != '(Осиротевший)':
            sub_parts.append(cp_name)
        sub = ' · '.join(sub_parts) or 'Контакт'

        items.append({
            'kind': 'contact',
            'id': c.pk,
            'label': c.name or email_str,
            'sub': sub,
            'email': email_str,
            'addr': addr,
        })
        contact_ids.add(c.pk)

    # ── 3. Исторические адресаты ──────────────────────────────────────
    # Собираем email-адреса из from_addr / to_addrs / cc_addrs — если
    # они похожи на q_raw и ещё не покрыты контактами. Распарсить адрес
    # из строк вида "Name <email@host>" через regex.
    known_emails: set = set()
    for c_id in contact_ids:
        for em in ContactEmail.objects.filter(contact_id=c_id).values_list('email', flat=True):
            known_emails.add(em.lower())

    history_counter = Counter()
    history_display: dict = {}

    # Эвристика: ищем письма, где подстрока q встречается в from/to/cc.
    email_pat = re.compile(r'[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+')

    emails_scan = (
        ContainerEmail.objects
        .filter(
            models_Q_email_contains(q_raw)
        )
        .values_list('from_addr', 'to_addrs', 'cc_addrs')[:200]
    )
    for from_addr, to_addrs, cc_addrs in emails_scan:
        for raw in (from_addr, to_addrs, cc_addrs):
            if not raw:
                continue
            for match in email_pat.findall(raw):
                if q not in match.lower():
                    continue
                lc = match.lower()
                if lc in known_emails:
                    continue
                history_counter[lc] += 1
                if lc not in history_display:
                    history_display[lc] = match

    hist_sorted = history_counter.most_common(limit)
    for lc, count in hist_sorted:
        addr = history_display[lc]
        items.append({
            'kind': 'history',
            'label': addr,
            'sub': f'{count} письм(ам/о/а) в истории',
            'email': addr,
            'addr': addr,
        })

    return JsonResponse({'ok': True, 'items': items[:limit * 2]})


def models_Q_name_or_position(q_raw: str):
    """Helper — Q-фильтр поиска контактов по ключевым полям."""
    from django.db.models import Q
    return (
        Q(name__icontains=q_raw)
        | Q(position__icontains=q_raw)
        | Q(comment__icontains=q_raw)
    )


def models_Q_email_contains(q_raw: str):
    """Helper — Q-фильтр поиска писем с email-подстрокой в адресных полях."""
    from django.db.models import Q
    return (
        Q(from_addr__icontains=q_raw)
        | Q(to_addrs__icontains=q_raw)
        | Q(cc_addrs__icontains=q_raw)
    )


def _render_bubble_response(
    request,
    email: ContainerEmail,
    container_id: int | None = None,
    scope: str = 'container',
    car_id: int | None = None,
    autotransport=None,
) -> JsonResponse:
    """Рендерит один баббл и возвращает его в JSON (ok + html).

    В зависимости от ``scope`` аннотирует ``is_read_here`` по нужной таблице
    связей, чтобы баббл корректно отразил статус именно в карточке-источнике.
    """
    from django.db.models import Exists, OuterRef, Subquery

    context: dict = {}

    if scope == 'car' and car_id:
        email = (
            ContainerEmail.objects
            .filter(pk=email.pk)
            .annotate(
                is_read_here=Subquery(
                    CarEmailLink.objects
                    .filter(email=OuterRef('pk'), car_id=car_id)
                    .values('is_read')[:1]
                )
            )
            .prefetch_related('containers', 'cars')
            .first()
        ) or email
    elif scope == 'autotransport' and autotransport is not None:
        car_ids = list(autotransport.cars.values_list('id', flat=True))
        if car_ids:
            has_unread = Exists(
                CarEmailLink.objects.filter(
                    email=OuterRef('pk'),
                    car_id__in=car_ids,
                    is_read=False,
                )
            )
            email = (
                ContainerEmail.objects
                .filter(pk=email.pk)
                .annotate(is_read_here=~has_unread)
                .prefetch_related('containers', 'cars')
                .first()
            ) or email
            context['autotransport_car_ids'] = set(car_ids)
    elif container_id:
        email = (
            ContainerEmail.objects
            .filter(pk=email.pk)
            .annotate(
                is_read_here=Subquery(
                    ContainerEmailLink.objects
                    .filter(email=OuterRef('pk'), container_id=container_id)
                    .values('is_read')[:1]
                )
            )
            .prefetch_related('containers')
            .first()
        ) or email

    context['email'] = email
    html = render(
        request,
        'admin/core/container/_email_bubble.html',
        context,
    ).content.decode('utf-8')
    return JsonResponse({
        'ok': True,
        'email_id': email.pk,
        'gmail_id': email.gmail_id,
        'thread_id': email.thread_id,
        'html': html,
    })


@staff_member_required
@require_GET
def email_container_updates(request, container_id: int):
    """Лёгкий polling-эндпоинт: отдаёт новые письма контейнера с pk > since_id.

    ``GET /core/emails/container/<container_id>/updates/?since_id=<N>``

    Используется фронтом для авто-обновления списка переписки на открытой
    карточке контейнера без необходимости reload страницы.

    Ответ:
        {
          ok: true,
          latest_id: <max pk среди всех писем контейнера>,
          total: <всего писем>,
          unread: <непрочитанных писем>,
          bubbles: [{id, html}, ...]  // только письма с pk > since_id,
                                       // уже отрендеренные в _email_bubble.html
        }
    """
    try:
        since_id = int(request.GET.get('since_id', 0))
    except (TypeError, ValueError):
        since_id = 0
    since_id = max(0, since_id)

    from django.db.models import OuterRef, Subquery
    qs = (
        ContainerEmail.objects
        .filter(containers__id=container_id)
        .annotate(
            is_read_here=Subquery(
                ContainerEmailLink.objects
                .filter(email=OuterRef('pk'), container_id=container_id)
                .values('is_read')[:1]
            )
        )
        .distinct()
    )
    total = qs.count()
    unread = ContainerEmailLink.objects.filter(
        container_id=container_id, is_read=False,
    ).count()

    latest = qs.order_by('-pk').values_list('pk', flat=True).first() or 0

    bubbles = []
    if latest > since_id:
        new_emails = qs.filter(pk__gt=since_id).order_by('-received_at', '-pk')
        # Ограничиваем — если since_id=0 (первый вызов из неоткрытой ленты),
        # не отдаём всё сразу: 50 штук уже покрывают 99% случаев.
        for email in new_emails[:50]:
            html = render(
                request,
                'admin/core/container/_email_bubble.html',
                {'email': email},
            ).content.decode('utf-8')
            bubbles.append({'id': email.pk, 'html': html})

    return JsonResponse({
        'ok': True,
        'latest_id': latest,
        'total': total,
        'unread': unread,
        'bubbles': bubbles,
    })


@staff_member_required
@require_GET
def email_car_updates(request, car_id: int):
    """Polling-эндпоинт для панели переписки в карточке машины.

    Отдаёт новые письма (pk > since_id), общий total и unread именно для
    этой машины (из ``CarEmailLink``). Строго per-VIN; ни thread, ни
    ``sent_from_container`` тут не влияют.
    """
    try:
        since_id = int(request.GET.get('since_id', 0))
    except (TypeError, ValueError):
        since_id = 0
    since_id = max(0, since_id)

    from django.db.models import OuterRef, Subquery
    qs = (
        ContainerEmail.objects
        .filter(cars__id=car_id)
        .annotate(
            is_read_here=Subquery(
                CarEmailLink.objects
                .filter(email=OuterRef('pk'), car_id=car_id)
                .values('is_read')[:1]
            )
        )
        .distinct()
    )
    total = qs.count()
    unread = CarEmailLink.objects.filter(
        car_id=car_id, is_read=False,
    ).count()
    latest = qs.order_by('-pk').values_list('pk', flat=True).first() or 0

    bubbles = []
    if latest > since_id:
        new_emails = qs.filter(pk__gt=since_id).order_by('-received_at', '-pk')
        for email in new_emails[:50]:
            html = render(
                request,
                'admin/core/container/_email_bubble.html',
                {'email': email},
            ).content.decode('utf-8')
            bubbles.append({'id': email.pk, 'html': html})

    return JsonResponse({
        'ok': True,
        'latest_id': latest,
        'total': total,
        'unread': unread,
        'bubbles': bubbles,
    })


@staff_member_required
@require_GET
def email_autotransport_updates(request, at_id: int):
    """Polling для панели переписки рейса — агрегирует по машинам.

    ``is_read_here`` = ~Exists(unread link среди машин рейса). ``unread`` —
    число УНИКАЛЬНЫХ писем, у которых есть хотя бы один непрочитанный
    link среди машин рейса (а не сумма непрочитанных линков).
    """
    from core.models import AutoTransport
    from django.db.models import Exists, OuterRef

    at = get_object_or_404(AutoTransport, pk=at_id)
    car_ids = list(at.cars.values_list('id', flat=True))

    try:
        since_id = int(request.GET.get('since_id', 0))
    except (TypeError, ValueError):
        since_id = 0
    since_id = max(0, since_id)

    if not car_ids:
        return JsonResponse({
            'ok': True, 'latest_id': 0, 'total': 0, 'unread': 0, 'bubbles': [],
        })

    has_unread = Exists(
        CarEmailLink.objects.filter(
            email=OuterRef('pk'),
            car_id__in=car_ids,
            is_read=False,
        )
    )
    qs = (
        ContainerEmail.objects
        .filter(cars__id__in=car_ids)
        .annotate(is_read_here=~has_unread)
        .distinct()
    )
    total = qs.count()
    unread = (
        ContainerEmail.objects
        .filter(cars__id__in=car_ids)
        .annotate(_has_unread=has_unread)
        .filter(_has_unread=True)
        .distinct()
        .count()
    )
    latest = qs.order_by('-pk').values_list('pk', flat=True).first() or 0

    bubbles = []
    if latest > since_id:
        new_emails = qs.filter(pk__gt=since_id).order_by('-received_at', '-pk')
        car_ids_set = set(car_ids)
        for email in new_emails[:50]:
            html = render(
                request,
                'admin/core/container/_email_bubble.html',
                {'email': email, 'autotransport_car_ids': car_ids_set},
            ).content.decode('utf-8')
            bubbles.append({'id': email.pk, 'html': html})

    return JsonResponse({
        'ok': True,
        'latest_id': latest,
        'total': total,
        'unread': unread,
        'bubbles': bubbles,
    })


@staff_member_required
@require_POST
def email_trigger_sync(request):
    if not getattr(settings, 'GMAIL_ENABLED', False):
        return JsonResponse({
            'ok': False,
            'error': 'GMAIL_ENABLED=False — синхронизация отключена.',
        }, status=400)

    try:
        from core.tasks_email import sync_emails_from_gmail
        async_result = sync_emails_from_gmail.delay()
        return JsonResponse({
            'ok': True,
            'task_id': async_result.id,
            'queued_at': timezone.now().isoformat(),
        })
    except Exception as exc:
        logger.exception('[email_trigger_sync] failed: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)[:300]}, status=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_TAGS = [
    'a', 'abbr', 'acronym', 'b', 'blockquote', 'br', 'code', 'div', 'em',
    'i', 'img', 'li', 'ol', 'p', 'pre', 'span', 'strong', 'table', 'tbody',
    'td', 'th', 'thead', 'tr', 'u', 'ul', 'hr', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'sub', 'sup', 'small',
]

_ALLOWED_ATTRS = {
    '*': ['style', 'class', 'title'],
    'a': ['href', 'name', 'target', 'rel'],
    'img': ['src', 'alt', 'width', 'height'],
}

_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto', 'cid', 'data']


def _sanitize_html(raw: str) -> str:
    """bleach.clean с whitelist; возвращает безопасный HTML для рендера в iframe/div.

    Если bleach не установлен — возвращаем пустую строку (лучше показать текст,
    чем рисковать XSS в админке).
    """
    try:
        import bleach  # type: ignore
    except ImportError:
        logger.warning('[email] bleach not installed; HTML rendering disabled.')
        return ''

    cleaned = bleach.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    return cleaned


def _safe_header_filename(filename: str) -> str:
    """Простейшая санитизация: убираем кавычки и CR/LF для Content-Disposition."""
    return filename.replace('"', '').replace('\r', '').replace('\n', '')
