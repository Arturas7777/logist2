"""Кнопка копирования номера в буфер — один вид в списках и шапках карточек."""

from django.utils.html import format_html


def copy_button_html(value: str, title: str = "Копировать") -> str:
    """Маленькая кнопка с иконкой копирования. Пустая строка, если копировать нечего."""
    text = (value or "").strip()
    if not text:
        return ""
    return format_html(
        '<span class="vin-copy-btn" role="button" tabindex="0" data-vin="{}" title="{}">'
        '<svg class="cm-copy-icon" viewBox="0 0 24 24" aria-hidden="true">'
        '<rect class="cm-copy-back" x="8.25" y="8.25" width="11.5" height="11.5" rx="2.4"/>'
        '<path d="M6.4 15.4H5.6A2.4 2.4 0 0 1 3.2 13V5.6A2.4 2.4 0 0 1 5.6 3.2H13'
        'a2.4 2.4 0 0 1 2.4 2.4V6.4"/>'
        "</svg>"
        '<svg class="cm-copy-check" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M5 12.5 9.2 17 19 7"/>'
        "</svg>"
        "</span>",
        text,
        title,
    )
