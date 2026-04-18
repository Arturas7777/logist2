"""Template-filter'ы для messenger-стиля переписки по контейнерам.

Подключаются в шаблонах через ``{% load email_extras %}``.
"""

from __future__ import annotations

import hashlib
import re

from django import template
from django.utils.safestring import mark_safe

from core.services.email_reply_parser import (
    _fix_mojibake,
    extract_display_name,
    messenger_body,
    messenger_body_from_email,
    split_reply_and_quote,
)

register = template.Library()


@register.filter(name='messenger_body')
def messenger_body_filter(text: str) -> str:
    """Чистит письмо для messenger-просмотра: только «суть» без подписи/цитат."""
    return messenger_body(text or '')


@register.filter(name='messenger_body_auto')
def messenger_body_auto_filter(email) -> str:
    """Берёт body_text, а если пусто — извлекает text из body_html.

    Нужен для автонотификаций с одним HTML-альтернативом
    (тогда без этого фильтра в шаблоне показывается серый snippet).
    """
    if not email:
        return ''
    return messenger_body_from_email(
        getattr(email, 'body_text', '') or '',
        getattr(email, 'body_html', '') or '',
    )


@register.filter(name='quote_part')
def quote_part_filter(text: str) -> str:
    """Возвращает только цитируемую историю (то, что идёт после разделителя).

    Пусто — если явного маркера цитирования в письме нет.
    """
    if not text:
        return ''
    _, quote = split_reply_and_quote(_fix_mojibake(text))
    return quote


@register.filter(name='fix_mojibake')
def fix_mojibake_filter(text: str) -> str:
    """Фиксит mojibake (``SiunÄ¨iu`` → ``Siunčiu``) — для subject/from."""
    return _fix_mojibake(text or '')


@register.filter(name='display_name')
def display_name_filter(from_addr: str) -> str:
    """Преобразует ``"A B" <a@b>`` в ``A B``."""
    return extract_display_name(from_addr or '')


@register.filter(name='initials')
def initials_filter(name_or_addr: str) -> str:
    """Две первые буквы имени (или адреса) для аватара. Заглавные."""
    display = extract_display_name(name_or_addr or '')
    display = display.strip()
    if not display:
        return '??'
    parts = [p for p in display.replace('.', ' ').replace('-', ' ').split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return display[:2].upper()


# Детерминированная палитра (яркая, но с хорошим контрастом на белом) —
# выбирается через хэш, чтобы у одного отправителя всегда один цвет.
_AVATAR_COLORS = [
    '#ef4444',  # red
    '#f97316',  # orange
    '#f59e0b',  # amber
    '#84cc16',  # lime
    '#10b981',  # emerald
    '#06b6d4',  # cyan
    '#3b82f6',  # blue
    '#6366f1',  # indigo
    '#8b5cf6',  # violet
    '#ec4899',  # pink
    '#14b8a6',  # teal
    '#0ea5e9',  # sky
]


@register.filter(name='avatar_color')
def avatar_color_filter(name_or_addr: str) -> str:
    """Стабильный цвет-заливка аватара по хэшу отправителя."""
    key = (name_or_addr or '').strip().lower()
    if not key:
        return _AVATAR_COLORS[0]
    h = int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)]


# Inline-картинки, сохранённые ДО того как парсер научился их определять.
# Фильтруем по имени: Gmail/Outlook автосгенерированные имена для embedded
# изображений в HTML-сигнатурах.
_INLINE_IMG_FILENAME = re.compile(
    r'^(?:image\d+|outlook-[\w-]+)\.(?:png|jpe?g|gif|bmp)$',
    re.IGNORECASE,
)


@register.filter(name='visible_attachments')
def visible_attachments_filter(attachments):
    """Оставляет только «настоящие» вложения — без inline-картинок подписи.

    Поддерживает три признака:
      * ``is_inline: True`` (новые записи, после правки парсера);
      * ``skipped_reason == 'inline'`` — синоним первого;
      * эвристика по имени файла ``image001.png`` / ``Outlook-xxx.jpg`` —
        для уже сохранённых писем, где ``is_inline`` не был выставлен.
    """
    if not attachments:
        return []
    result = []
    for idx, att in enumerate(attachments):
        if not isinstance(att, dict):
            continue
        if att.get('is_inline'):
            continue
        if att.get('skipped_reason') == 'inline':
            continue
        filename = (att.get('filename') or '').strip()
        if filename and _INLINE_IMG_FILENAME.match(filename):
            continue
        # Прокидываем оригинальный индекс — view email_attachment открывает
        # файл по индексу в attachments_json, а мы смещаемся при фильтрации.
        result.append({**att, 'orig_index': idx})
    return result


@register.filter(name='linkify_urls', is_safe=True)
def linkify_urls_filter(text: str) -> str:
    """Обёртка поверх django.utils.html.urlize, возвращает safe-строку.

    Используем встроенный urlize — он уже умеет distinguish между ссылкой и
    обычным текстом, а HTML-escape делает перед тем как вставить <a>.
    """
    from django.template.defaultfilters import urlize
    return mark_safe(urlize(text or '', autoescape=True))
