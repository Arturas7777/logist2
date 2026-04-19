"""Отделение «сути» письма от цитируемой истории.

Для каждого письма возвращаем две части:
    * reply   — актуальный ответ автора (то, что человек написал сейчас);
    * quoted  — вся цитируемая история (forward/quote/Outlook separators).

UI по умолчанию показывает только reply; quoted прячется под
``<details>`` и разворачивается по клику — как в Gmail / мессенджерах.

Реализация — набор регулярных эвристик, покрывающих типовые клиенты:
Gmail (en/ru), Outlook (en/ru), Yahoo, Apple Mail, Thunderbird,
а также plain-text шаблоны «On … wrote:», «От:/Отправлено:»,
«-----Original Message-----», «________________________________».

Зависимостей не добавляет. При отсутствии маркеров возвращает исходный
текст целиком как reply (quoted=''), что безопасно — пользователь
просто увидит всё письмо.
"""

from __future__ import annotations

import re
from typing import Tuple

try:
    import ftfy  # type: ignore
except ImportError:  # pragma: no cover
    ftfy = None  # graceful degradation — мозайки останутся, но ничего не упадёт


# Балтийские буквы, которые ftfy часто не распознаёт — литовские/латышские
# символы, где второй байт UTF-8 попадает в C1-диапазон (0x80-0x9F) и во
# время декодирования как cp1252 был заменён на "похожий" видимый символ
# (например 0x8D → ¨, 0x96 → –). Таблица написана вручную под нашу
# корреспонденцию (Neto / MSC / Caromoto LT).
_LT_MOJIBAKE_MAP = [
    # строчные
    ('Ä…', 'ą'),   # C4 85
    ('Ä¨', 'č'),   # C4 8D — нестандарт: 0x8D показали как ¨ (0xA8)
    ('Ä\u008d', 'č'),
    ('Ä™', 'ę'),   # C4 99
    ('Ä—', 'ė'),   # C4 97
    ('Ä¯', 'į'),   # C4 AF
    ('Å¡', 'š'),   # C5 A1
    ('Å³', 'ų'),   # C5 B3
    ('Å«', 'ū'),   # C5 AB
    ('Å¾', 'ž'),   # C5 BE
    # заглавные
    ('Ä„', 'Ą'),
    ('Ä\u008c', 'Č'),
    ('Ä˜', 'Ę'),
    ('Ä–', 'Ė'),
    ('Ä®', 'Į'),
    ('Å ', 'Š'),
    ('Å²', 'Ų'),
    ('Åª', 'Ū'),
    ('Å½', 'Ž'),
    # NBSP (UTF-8 C2 A0), декодированный как cp1252 → 'Â '
    ('Â ', ' '),
    ('Â\xa0', ' '),
]


def _fix_mojibake(text: str) -> str:
    """Лечит классический mojibake (UTF-8, декодированный как cp1252 и т.п.).

    Порядок важен:
    1) Ручная таблица балтийских букв — ftfy порой «валидно» декодирует
       ``Ä¨`` (байты C4 A8) в ``Ĩ`` и наш replace после ftfy уже ничего
       не находит. Поэтому сначала заменяем известные последовательности.
    2) ``ftfy.fix_text`` — добивает общий случай (``kopijÄ…`` и т.п.).
    """
    if not text:
        return text
    fixed = text
    for bad, good in _LT_MOJIBAKE_MAP:
        if bad in fixed:
            fixed = fixed.replace(bad, good)
    if ftfy is not None:
        fixed = ftfy.fix_text(fixed)
    return fixed


__all__ = [
    'split_reply_and_quote',
    'split_reply_and_quote_html',
    'clean_message_body',
    'messenger_body',
    'messenger_body_from_email',
    'html_to_plain',
    'extract_display_name',
    'format_quoted_reply',
    'plain_text_to_simple_html',
    'compose_reply_html',
]


# ---------------------------------------------------------------------------
# Plain-text
# ---------------------------------------------------------------------------

# Шапки цитат. Паттерны ищутся в любом месте текста; берётся самое раннее
# вхождение и текст режется по нему.
_QUOTE_HEADER_PATTERNS = [
    # Gmail en: "On Wed, Apr 15, 2026 at 3:02 PM John <j@x.com> wrote:"
    re.compile(r'^\s*On\s[^\n]{0,300}\bwrote:\s*$', re.MULTILINE | re.IGNORECASE),
    # Gmail ru: "ср, 15 апр. 2026 г. в 15:02, Иван <i@x.com>:"
    re.compile(
        r'^\s*(?:пн|вт|ср|чт|пт|сб|вс)[^\n]{0,300},\s*\n?[^\n]{0,300}[:：]\s*$',
        re.MULTILINE | re.IGNORECASE,
    ),
    # "15.04.2026 в 15:02, John <j@x.com>:" / "15.04.2026 15:02 пользователь X написал:"
    re.compile(
        r'^\s*\d{1,2}\.\d{1,2}\.\d{2,4}[^\n]{0,300}[:：]\s*$',
        re.MULTILINE,
    ),
    # "2026-04-15 15:02 GMT+03:00 John <j@x.com>:"
    re.compile(
        r'^\s*\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}[^\n]{0,300}[:：]\s*$',
        re.MULTILINE,
    ),
    # Outlook en-block: "From: …" (часто следом идёт Sent:/To:/Subject:)
    re.compile(r'^\s*From:\s[^\n]+$', re.MULTILINE),
    # Outlook ru-block: "От: …"
    re.compile(r'^\s*От:\s[^\n]+$', re.MULTILINE),
    # "-----Original Message-----" / "-----Пересланное сообщение-----"
    re.compile(
        r'^\s*-{2,}\s*(?:Original Message|Пересланное сообщение|'
        r'Forwarded message|Исходное сообщение)\s*-{2,}',
        re.MULTILINE | re.IGNORECASE,
    ),
    # Outlook HTML-ish plain-text separator — длинная линия underscore'ов
    re.compile(r'^_{8,}\s*$', re.MULTILINE),
    # Apple Mail / некоторые клиенты: "Begin forwarded message:"
    re.compile(r'^\s*Begin forwarded message:\s*$', re.MULTILINE | re.IGNORECASE),
]


def _first_quote_line_offset(text: str) -> int:
    """Смещение первой строки, начинающейся с '>' (классическая RFC-цитата).

    Если такой строки нет — возвращает ``len(text)``.
    """
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('>'):
            return offset
        offset += len(line)
    return len(text)


def split_reply_and_quote(text: str) -> Tuple[str, str]:
    """Разделяет текст на (reply, quoted).

    Если разделитель не найден — возвращает ``(text.strip(), '')``.
    Если reply получился слишком коротким (<5 значимых символов) и есть цитата —
    считаем что разделитель ложноположительный и тоже отдаём весь текст как reply
    (человек не хочет, чтобы «пусто» было вверху).
    """
    if not text:
        return '', ''

    earliest = len(text)
    for pattern in _QUOTE_HEADER_PATTERNS:
        m = pattern.search(text)
        if m and m.start() < earliest:
            earliest = m.start()

    quote_line_off = _first_quote_line_offset(text)
    if quote_line_off < earliest:
        earliest = quote_line_off

    if earliest >= len(text):
        return text.strip(), ''

    reply = text[:earliest].rstrip()
    quote = text[earliest:].strip()

    if len(reply.strip()) < 5 and quote:
        return text.strip(), ''

    return reply, quote


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

# Типовые контейнеры цитируемой истории в HTML-письмах.
_HTML_QUOTE_ENTRY_PATTERNS = [
    # Gmail: <div class="gmail_quote">
    re.compile(
        r'<div[^>]*class=["\'][^"\']*\bgmail_quote\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Gmail новый: <div class="gmail_attr">On … wrote:</div>
    re.compile(
        r'<div[^>]*class=["\'][^"\']*\bgmail_attr\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Outlook reply/forward separator
    re.compile(r'<div[^>]*id=["\']divRplyFwdMsg["\'][^>]*>', re.IGNORECASE),
    re.compile(
        r'<div[^>]*id=["\']appendonsend["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Outlook header-block class'ы
    re.compile(
        r'<div[^>]*class=["\'][^"\']*\bOutlookMessageHeader\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Yahoo
    re.compile(
        r'<div[^>]*class=["\'][^"\']*\byahoo_quoted\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Apple Mail
    re.compile(
        r'<blockquote[^>]*type=["\']cite["\'][^>]*>',
        re.IGNORECASE,
    ),
    # Обычная <blockquote> — финальный фолбэк (Thunderbird, ручная вставка)
    re.compile(r'<blockquote[^>]*>', re.IGNORECASE),
]


def split_reply_and_quote_html(html: str) -> Tuple[str, str]:
    """Аналог для HTML. Возвращает (reply_html, quoted_html).

    Режем ПО НАЧАЛУ контейнера цитаты — это несбалансированный HTML,
    но для дальнейшего ``bleach.clean`` это не проблема (он достраивает
    закрывающие теги автоматически).
    """
    if not html:
        return '', ''

    earliest = len(html)
    for p in _HTML_QUOTE_ENTRY_PATTERNS:
        m = p.search(html)
        if m and m.start() < earliest:
            earliest = m.start()

    if earliest >= len(html):
        return html, ''

    reply_html = html[:earliest]
    quoted_html = html[earliest:]

    # Если reply получился почти пустым — лучше не резать.
    visible = re.sub(r'<[^>]+>', '', reply_html).strip()
    if len(visible) < 5 and quoted_html:
        return html, ''

    return reply_html, quoted_html


# ---------------------------------------------------------------------------
# Очистка «сути» от подписи / прощальных фраз / шума
# ---------------------------------------------------------------------------

# Closing-фразы — одна из них обычно открывает блок подписи.
_CLOSING_PHRASE = re.compile(
    r'^\s*(?:'
    r'Kind\s+regards|Best\s+regards|Warm\s+regards|Regards|'
    r'Yours\s+sincerely|Sincerely|Yours\s+faithfully|Yours\s+truly|'
    r'Thanks(?:\s+again)?|Thank\s+you|Many\s+thanks|Cheers|'
    r'Best|BR|'
    r'С\s+уважением|С\s+наилучшими(?:\s+пожеланиями)?|Благодарю|'
    r'Pagarbiai|Ačiū|'
    r'Mit\s+freundlichen\s+Grüßen|Viele\s+Grüße|Beste\s+Grüße|'
    r'Cordialement|Bien\s+cordialement|Sincères\s+salutations'
    r')'
    # Допустимо продолжение вроде «Pagarbiai/Best Regards» или
    # «С уважением / Regards» — один закрывающий токен через слэш.
    r'(?:\s*/\s*[^\n]{0,40})?'
    r'[,\s.!]*\s*$',
    re.IGNORECASE | re.UNICODE,
)

# Строки, которые 100% относятся к подписи/шаблонному хвосту — cut-off.
_SIGNATURE_SEPARATORS = [
    # RFC 3676 signature delimiter
    re.compile(r'^\s*--\s*$'),
    # Горизонтальная линия «---», «====», «___»
    re.compile(r'^\s*[-=_]{3,}\s*$'),
    # Gmail inline-image / attachment placeholders
    re.compile(r'^\s*\[(?:cid|image):[^\]]*\]\s*$', re.IGNORECASE),
    # Outlook-подобные баннеры классификации
    re.compile(r'^\s*(?:CAUTION|WARNING|EXTERNAL)\s*[:!].{0,300}$', re.IGNORECASE),
    # Автодисклеймеры: "This email and any attachments..." / "Confidentiality Notice:"
    re.compile(
        r'^\s*(?:This\s+e-?mail(?:\s+message)?|This\s+message\s+is|'
        r'Confidentiality\s+Notice|Disclaimer|'
        r'Please\s+consider\s+the\s+environment|'
        r'Dear\s+customer,?)\b.{0,300}$',
        re.IGNORECASE,
    ),
]

# Inline-токены (внутри строк), которые не несут смысла для чтения.
_NOISE_INLINE = [
    # [cid:image001.png@...]
    re.compile(r'\[(?:cid|image):[^\]]*\]', re.IGNORECASE),
    # <mailto:x@y.com>, <http://...>, <https://...>
    re.compile(r'<(?:mailto:|https?://)[^>]+>', re.IGNORECASE),
    # Markdown-style «Label ( https://tracker/long-url )» — типичный вид
    # plain-text альтернативы письма от SendGrid / Mailgun / Mandrill, где
    # каждая ссылка раскрывается в скобки, а URL — трекинговый (100+ симв.).
    # Убираем только URL-часть: якорь «Label» остаётся и остаётся читаемым;
    # исходную ссылку можно увидеть в full view (raw HTML).
    re.compile(r'[ \t]*\(\s*https?://[^\s)]+\s*\)', re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Мусор от Salesforce / Visualforce / JSF email-templates:
# в text/plain попадает JavaScript-инициализация (UserContext.initialize({…})),
# onload-стабы и `if(!window.xxx){...}`. Вырезаем до применения остальной логики.
# ---------------------------------------------------------------------------
_SCRIPT_GARBAGE_PATTERNS = [
    # UserContext.initialize({...});   (до закрывающей скобки с ; — жадно)
    re.compile(
        r'UserContext\.initialize\s*\(\s*\{[\s\S]*?\}\s*\)\s*;?',
        re.IGNORECASE,
    ),
    # if(!window.xxx) { window.xxx = new Something(); }
    re.compile(
        r'if\s*\(\s*!\s*window\.[\w$]+\s*\)\s*\{[\s\S]*?\}\s*;?',
        re.IGNORECASE,
    ),
    # j_id0__emailTemplate__j_id3__… = window.onload; window.onload=function(){ … };
    re.compile(
        r'j_id\d+[\s\S]{0,400}?window\.onload[\s\S]{0,400}?\};?',
        re.IGNORECASE,
    ),
    # window.onload = function () { … };
    re.compile(
        r'window\.onload\s*=\s*function\s*\([^)]*\)\s*\{[\s\S]*?\}\s*;?',
        re.IGNORECASE,
    ),
    # На всякий случай — любые остатки <script>…</script>
    re.compile(r'<script[\s\S]*?</script>', re.IGNORECASE),
    # И <style>…</style> если html-теги затесались в plain-text.
    re.compile(r'<style[\s\S]*?</style>', re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# CSS-правила, «просочившиеся» в plain-text версию письма.
#
# Некоторые рассыльщики (в т.ч. наш собственный notification-скрипт для
# «контейнер разгружен») генерируют HTML-письмо и параллельно создают
# plain-text alternative простым «strip tags», из-за чего содержимое блока
# ``<style>`` (десятки правил ``.class { padding: 10px; }``) попадает в
# тело письма как текст. Вырезаем: любой селектор + фигурные скобки, где
# внутри есть хотя бы одна пара ``ключ: значение;``.
# ---------------------------------------------------------------------------
_CSS_BLOCK_PATTERN = re.compile(
    r'(?m)^[ \t]*[^\n{}]{0,200}\{[ \t]*\n'
    r'(?:[ \t]*[\w\-]+[ \t]*:[^\n{}]*;?[ \t]*\n)+'
    r'[ \t]*\}[ \t]*\n?'
)
# Однострочный вариант: ``.foo { padding: 4px; margin: 0; }``
_CSS_INLINE_PATTERN = re.compile(
    r'(?m)^[ \t]*[^\n{}]{0,120}\{[ \t]*'
    r'(?:[\w\-]+[ \t]*:[^;{}\n]+;[ \t]*){1,}'
    r'[^{}\n]*\}[ \t]*$'
)


def clean_message_body(text: str) -> str:
    """Срезает подпись/прощания/[cid:]/mailto-дубли и схлопывает пустые строки.

    Не agressive — если после чистки остаётся <3 значимых символов, возвращает
    исходный текст (лучше показать шум, чем потерять содержание короткого
    ответа вроде «OK»).
    """
    if not text:
        return ''

    # 0a. Фиксим mojibake (UTF-8, ошибочно декодированный как cp1252 и т.п.).
    cleaned = _fix_mojibake(text).replace('\r\n', '\n')

    # 0b. Вырезаем мусор от Salesforce/JSF email-шаблонов (до split на строки,
    # т.к. эти блоки часто многострочные).
    for pat in _SCRIPT_GARBAGE_PATTERNS:
        cleaned = pat.sub('', cleaned)

    # 0c. Вырезаем CSS-правила, попавшие в plain-text (``.foo { padding: 10px; }``).
    cleaned = _CSS_BLOCK_PATTERN.sub('', cleaned)
    cleaned = _CSS_INLINE_PATTERN.sub('', cleaned)

    lines = [line.rstrip() for line in cleaned.split('\n')]
    cutoff = len(lines)

    def _earliest(predicate) -> int:
        for i, line in enumerate(lines):
            if predicate(line):
                return i
        return len(lines)

    for pat in _SIGNATURE_SEPARATORS:
        cutoff = min(cutoff, _earliest(lambda ln, p=pat: bool(p.match(ln))))

    # Closing phrase — срезаем, только если перед ней уже есть реальный
    # контент. Иначе короткие ответы вида «Ačiū», «Thanks», «OK» сами
    # воспринимаются как подпись и уничтожают весь текст (из-за чего
    # fallback-ветка потом возвращает нечищеный оригинал с подписью).
    def _earliest_closing() -> int:
        has_prior_content = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if _CLOSING_PHRASE.match(line):
                if has_prior_content:
                    return i
                # Первая «прощальная» фраза без предыдущего контента —
                # это, вероятно, сам текст ответа, а не начало подписи.
                continue
            if stripped:
                has_prior_content = True
        return len(lines)

    cutoff = min(cutoff, _earliest_closing())

    head = lines[:cutoff]
    head_joined = '\n'.join(head)

    for pat in _NOISE_INLINE:
        head_joined = pat.sub('', head_joined)

    # После удаления [image:] и <mailto:> иногда остаётся строка-«скелет»
    # из markdown-символов — убираем такие строки целиком.
    head_joined = re.sub(r'(?m)^\s*[*_~`\-\s]{0,40}\s*$', '', head_joined)

    # Схлопываем 3+ пустых строк в одну пустую
    head_joined = re.sub(r'\n{3,}', '\n\n', head_joined)
    head_joined = head_joined.strip()

    # Если всё вырезали — вернём текст без cut-off (но всё равно без script-мусора)
    if len(head_joined) < 3:
        full = cleaned
        for pat in _NOISE_INLINE:
            full = pat.sub('', full)
        full = re.sub(r'\n{3,}', '\n\n', full).strip()
        return full

    return head_joined


def messenger_body(text: str) -> str:
    """Композиция: split_reply_and_quote → clean_message_body для reply."""
    reply, _ = split_reply_and_quote(text or '')
    return clean_message_body(reply)


# ---------------------------------------------------------------------------
# HTML → plain text (fallback, если у письма нет text/plain alternative)
# ---------------------------------------------------------------------------

import html as _html_module  # stdlib; импорт локальный чтобы не торчал в шапке

_BR_RE = re.compile(r'<br\s*/?\s*>', re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(
    r'</\s*(?:p|div|li|tr|h[1-6])\s*>', re.IGNORECASE,
)
_TAG_RE = re.compile(r'<[^>]+>')


def html_to_plain(html: str) -> str:
    """Быстрая HTML→text-конвертация для писем без plain-text alternative.

    Не претендует на bleach-grade качество — задача скромная: сделать
    notification-письма (``Container: XXX<br>Booking: YYY<br>…``) читаемыми
    в messenger-ленте. Полный HTML-просмотр живёт в expand-view.
    """
    if not html:
        return ''
    t = re.sub(r'<style[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
    t = re.sub(r'<script[\s\S]*?</script>', '', t, flags=re.IGNORECASE)
    t = _BR_RE.sub('\n', t)
    t = _BLOCK_CLOSE_RE.sub('\n\n', t)
    t = _TAG_RE.sub('', t)
    t = _html_module.unescape(t)
    t = t.replace('\xa0', ' ')
    return t.strip()


def messenger_body_from_email(body_text: str, body_html: str) -> str:
    """Выбирает источник текста: plain, а если пусто — извлекает из HTML.

    Используется в шаблоне через фильтр ``messenger_body_auto`` —
    покрывает HTML-only автонотификации, у которых ``body_text == ''``
    и в fallback раньше показывался серый Gmail-snippet.
    """
    if (body_text or '').strip():
        return messenger_body(body_text)
    plain = html_to_plain(body_html or '')
    if not plain:
        return ''
    reply, _ = split_reply_and_quote(plain)
    return clean_message_body(reply)


# ---------------------------------------------------------------------------
# Парсинг имени отправителя
# ---------------------------------------------------------------------------

_FROM_NAME = re.compile(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$')
_EMAIL_ONLY = re.compile(r'^\s*<?([^\s<>@]+@[^\s<>]+)>?\s*$')


def format_quoted_reply(parent, *, max_lines: int = 200) -> str:
    """Формирует шапку цитаты для ответа в Gmail-стиле.

    ``parent`` — объект с атрибутами ``received_at`` (datetime), ``from_addr``,
    ``body_text``. Результат — готовый к подстановке в textarea текст:

        \\n\\n\\nOn 17 Apr 2026, 14:23, ivan@example.com wrote:\\n
        > первая строка\\n
        > вторая строка\\n
    """
    if parent is None:
        return ''
    received = getattr(parent, 'received_at', None)
    when = received.strftime('%d %b %Y, %H:%M') if received else ''
    who = (getattr(parent, 'from_addr', '') or '').strip()
    header = f"\n\n\nOn {when}, {who} wrote:" if when else f"\n\n\n{who} wrote:"
    body = (getattr(parent, 'body_text', '') or '').strip()
    if not body:
        # fallback: plain из html
        body = html_to_plain(getattr(parent, 'body_html', '') or '')
    lines = body.splitlines()[:max_lines]
    quoted = '\n'.join('> ' + ln for ln in lines)
    return f"{header}\n{quoted}\n"


_HTML_ESCAPE = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
}


def plain_text_to_simple_html(text: str) -> str:
    """Простейшая plain → HTML конвертация для body_html в исходящих письмах.

    * Экранируем спецсимволы.
    * ``\\n\\n`` → ``</p><p>``, одиночный ``\\n`` → ``<br>``.
    * Строки, начинающиеся с ``>``, оборачиваются в ``<blockquote>``
      (ограниченно — стандартная цитата для клиентов-получателей).
    """
    if not text:
        return ''
    escaped = ''.join(_HTML_ESCAPE.get(ch, ch) for ch in text)
    paragraphs = escaped.split('\n\n')
    rendered: list[str] = []
    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue
        lines = para.split('\n')
        # Если все строки цитата — выделим blockquote
        if all(ln.lstrip().startswith('&gt;') for ln in lines if ln.strip()):
            cleaned = '<br>'.join(ln.lstrip().removeprefix('&gt;').lstrip() for ln in lines)
            rendered.append(
                '<blockquote style="border-left:2px solid #cbd5e1;'
                'padding-left:10px;color:#64748b;margin:8px 0;">'
                f'{cleaned}</blockquote>'
            )
        else:
            rendered.append('<p>' + '<br>'.join(lines) + '</p>')
    return ''.join(rendered)


# Attribution-заголовок, который мы сами генерируем в format_quoted_reply —
# "On 17 Apr 2026, 14:23, ivan@example.com wrote:". Используется для того,
# чтобы разделить в исходящем письме собственно reply и цитируемую историю
# и обернуть цитату в Gmail-совместимый blockquote.
_REPLY_ATTRIBUTION_RE = re.compile(
    r'^On\s[^\n]{0,400}\bwrote:\s*$',
    re.MULTILINE,
)


def compose_reply_html(text: str, signature_html: str = '') -> str:
    """Сериализует текст ответа в HTML с Gmail-совместимой цитатой.

    Логика:
      1) Ищем строку-атрибуцию ``On ... wrote:`` (та, что вставляет
         ``format_quoted_reply``). Всё до неё — это непосредственно ответ
         пользователя, всё после — процитированная история.
      2) Ответ рендерим обычными ``<p>`` / ``<br>`` параграфами.
      3) Если передан ``signature_html`` — вставляем его **между** ответом
         и блоком цитаты (как это делает веб-Gmail при клике «Ответить»).
      4) Цитату сначала очищаем от ``> `` префиксов (Gmail сам добавит
         визуальный отступ через ``<blockquote>``), затем оборачиваем в
         ``<blockquote class="gmail_quote">`` — ровно тот же маркап, что
         генерирует веб-Gmail. В клиентах-получателях (Gmail, Outlook,
         Apple Mail) этот блок автоматически схлопывается в «...».

    Если атрибуция не найдена — делегируем в ``plain_text_to_simple_html``
    и дописываем подпись в конец.
    """
    if not text:
        return signature_html or ''

    match = _REPLY_ATTRIBUTION_RE.search(text)
    if not match:
        base = plain_text_to_simple_html(text)
        return base + (signature_html or '')

    reply_part = text[:match.start()].rstrip()
    attribution = match.group(0).strip()
    quote_part = text[match.end():].lstrip('\n')

    # Убираем один уровень '> ' из каждой строки цитаты — итоговый HTML
    # рендерит цитату через <blockquote> (клиент сам нарисует левую полоску).
    unquoted_lines: list[str] = []
    for line in quote_part.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('>'):
            stripped = stripped[1:]
            if stripped.startswith(' '):
                stripped = stripped[1:]
        unquoted_lines.append(stripped)
    quote_clean = '\n'.join(unquoted_lines).strip('\n')

    reply_html = plain_text_to_simple_html(reply_part) if reply_part.strip() else ''
    quote_inner_html = plain_text_to_simple_html(quote_clean) if quote_clean else ''

    attr_escaped = ''.join(_HTML_ESCAPE.get(ch, ch) for ch in attribution)

    gmail_quote = (
        '<div class="gmail_quote gmail_quote_container">'
        f'<div dir="ltr" class="gmail_attr">{attr_escaped}<br></div>'
        '<blockquote class="gmail_quote" '
        'style="margin:0 0 0 0.8ex;border-left:1px solid rgb(204,204,204);'
        'padding-left:1ex;color:#555;">'
        f'{quote_inner_html}'
        '</blockquote>'
        '</div>'
    )

    return reply_html + (signature_html or '') + gmail_quote


def extract_display_name(from_addr: str) -> str:
    """Из ``"Ivan Ivanov" <i@x.com>`` возвращает ``Ivan Ivanov``.

    Если имени нет — возвращает часть до ``@``. Mojibake в имени
    (``RamunÄ—`` → ``Ramunė``) фиксится через ftfy.
    """
    if not from_addr:
        return '—'
    from_addr = _fix_mojibake(from_addr)
    m = _FROM_NAME.match(from_addr)
    if m:
        name = m.group(1).strip().strip('"').strip()
        if name:
            return name
    m = _EMAIL_ONLY.match(from_addr)
    if m:
        return m.group(1).split('@')[0]
    return from_addr.strip()
