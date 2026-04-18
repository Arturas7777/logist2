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

from core.models_email import ContainerEmail
from core.services.email_reply_parser import (
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
        ContainerEmail.objects.select_related('container'),
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

    attachments = []
    for idx, att in enumerate(email.attachments_json or []):
        attachments.append({
            'index': idx,
            'filename': att.get('filename') or f'attachment_{idx}',
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

@staff_member_required
@require_POST
def email_mark_read(request, email_id: int):
    email = get_object_or_404(ContainerEmail, pk=email_id)
    new_val = request.POST.get('is_read', '1') == '1'
    if email.is_read != new_val:
        email.is_read = new_val
        email.save(update_fields=['is_read'])
    return JsonResponse({'ok': True, 'is_read': email.is_read})


@staff_member_required
@require_POST
def email_mark_container_read(request, container_id: int):
    """Помечает всю переписку контейнера как прочитанную — для messenger-UI.

    Вызывается автоматически при разворачивании блока «Переписка».
    """
    updated = ContainerEmail.objects.filter(
        container_id=container_id,
        is_read=False,
    ).update(is_read=True)
    return JsonResponse({'ok': True, 'updated': updated})


# ---------------------------------------------------------------------------
# Ручной триггер Gmail-синхронизации
# ---------------------------------------------------------------------------

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
