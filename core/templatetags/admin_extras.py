from django import template
from django.contrib.contenttypes.models import ContentType

register = template.Library()

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

