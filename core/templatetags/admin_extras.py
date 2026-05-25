from django import template
from django.contrib.contenttypes.models import ContentType
from django.utils.html import format_html, mark_safe

register = template.Library()


@register.simple_tag
def vin_diff(vin: str, reference: str):
    """Подсветить в ``vin`` символы, отличающиеся от ``reference``.

    Используется для VIN-mismatch review: показывает посимвольно, где
    именно строки расходятся, чтобы юзер сразу видел спорные позиции.
    Если длины не совпадают — рисуем без подсветки (запасной вариант).
    """
    vin = (vin or '').strip()
    reference = (reference or '').strip()
    if not vin:
        return ''
    if not reference or len(vin) != len(reference):
        return format_html(
            '<span style="font-family:monospace;">{}</span>', vin
        )
    parts = []
    for ch_vin, ch_ref in zip(vin, reference, strict=False):
        if ch_vin == ch_ref:
            parts.append(format_html(
                '<span style="font-family:monospace;">{}</span>', ch_vin
            ))
        else:
            parts.append(format_html(
                '<span style="font-family:monospace;background:#ffc107;'
                'color:#212529;padding:0 2px;border-radius:2px;'
                'font-weight:bold;">{}</span>',
                ch_vin,
            ))
    return mark_safe(''.join(parts))

@register.filter
def content_type_id(obj):
    """Возвращает ID content type для объекта"""
    if obj:
        return ContentType.objects.get_for_model(obj).id
    return None

@register.filter
def content_type_name(obj):
    """Возвращает имя content type для объекта"""
    if obj:
        return ContentType.objects.get_for_model(obj).name
    return None

