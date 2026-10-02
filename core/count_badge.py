"""Счётчик-пилюля: цифра по центру, один вид в списках и в меню."""

from django.utils.html import format_html

_TONES = {
    "alert": "cm-count-badge--alert",
    "ok": "cm-count-badge--ok",
    "reply": "cm-count-badge--reply",
    "warn": "cm-count-badge--warn",
    "info": "cm-count-badge--info",
}


def count_badge_html(value, *, tone: str = "ok", title: str = "", icon: str = "") -> str:
    """Пилюля со счётчиком. ``icon`` — класс Bootstrap Icons без префикса ``bi``, например ``flag-fill``."""
    tone_class = _TONES.get(tone, _TONES["ok"])
    icon_html = format_html('<i class="bi bi-{}" aria-hidden="true"></i>', icon) if icon else ""
    return format_html(
        '<span class="cm-count-badge {}" title="{}">{}{}</span>',
        tone_class,
        title,
        icon_html,
        value,
    )
