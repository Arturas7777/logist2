"""Сброс непрочитанных писем в карточках.

Прочтение в Gmail само по себе сюда не приходит: бейджи смотрят на
``is_read`` связей письмо↔карточка.
"""

from core.models.email import CarEmailLink, ContainerEmailLink, TransportRequestEmailLink


def mark_all_emails_read() -> dict[str, int]:
    """Пометить прочитанными все связи писем с контейнерами, авто и заявками."""
    return {
        "containers": ContainerEmailLink.objects.filter(is_read=False).update(is_read=True),
        "cars": CarEmailLink.objects.filter(is_read=False).update(is_read=True),
        "requests": TransportRequestEmailLink.objects.filter(is_read=False).update(is_read=True),
    }
