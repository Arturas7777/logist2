"""Маленькая метка морской линии рядом с номером контейнера.

Не логотип-картинка (товарные знаки линий мы не храним), а цветной значок
с коротким кодом: цвет берётся из бренда линии, буквы всегда контрастные.
"""

from django.utils.html import format_html

# Более длинные ключи — раньше коротких, чтобы HAPPAG не стал HAP.
_LINE_CODES = (
    ("MAERSK", "MAE"),
    ("HAPPAG", "HPL"),
    ("HAPAG", "HPL"),
    ("EVERGREEN", "EMC"),
    ("YANG MING", "YML"),
    ("SEALAND", "SEA"),
    ("COSCO", "COS"),
    ("ARKAS", "ARK"),
    ("OOCL", "OOC"),
    ("CMA", "CMA"),
    ("MSC", "MSC"),
    ("ONE", "ONE"),
    ("HMM", "HMM"),
    ("ZIM", "ZIM"),
)


def line_short_code(name: str) -> str:
    upper = (name or "").upper()
    for key, code in _LINE_CODES:
        if key in upper:
            return code
    letters = "".join(ch for ch in upper if ch.isalpha())
    return (letters[:3] or "?")[:3]


def _ink_on(hex_color: str) -> str:
    """Тёмный текст на светлом фоне (MSC, Yang Ming), белый на остальных."""
    raw = (hex_color or "").lstrip("#")
    if len(raw) != 6:
        return "#ffffff"
    try:
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return "#ffffff"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#1a1a2e" if luminance > 0.62 else "#ffffff"


def line_mark_html(line) -> str:
    """HTML значка линии. Пустая строка, если линии нет."""
    if line is None:
        return ""
    color = line.container_color
    code = line_short_code(line.name)
    return format_html(
        '<span class="cm-line-mark" style="background:{};color:{}" title="{}">{}</span>',
        color,
        _ink_on(color),
        line.name,
        code,
    )


def container_number_html(container, *, link=False) -> str:
    """Номер контейнера со значком линии. ``link=True`` — ссылка в карточку."""
    if container is None:
        return "—"
    mark = line_mark_html(getattr(container, "line", None))
    number = container.number or "—"
    if link and container.pk:
        return format_html(
            '<a href="/admin/core/container/{}/change/" class="cm-container-no">{}'
            '<span class="cm-container-no-text">{}</span></a>',
            container.pk,
            mark,
            number,
        )
    return format_html(
        '<span class="cm-container-no">{}<span class="cm-container-no-text">{}</span></span>',
        mark,
        number,
    )
