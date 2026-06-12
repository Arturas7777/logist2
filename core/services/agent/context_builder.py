"""Сборка контекста для промптов агента.

Системный промпт агента состоит из трёх слоёв:

1. курируемый бизнес-контекст (``docs/AI_BUSINESS_CONTEXT.md``);
2. релевантная память агента (:func:`core.services.agent.memory.retrieve_memories`);
3. данные конкретной ситуации (письмо, дело и т.п. — собирают вызывающие
   модули: email_analyzer, task_planner, agent_executor).
"""

from __future__ import annotations

import logging
import os

from django.conf import settings

from core.services.agent.memory import format_memories_block, retrieve_memories

logger = logging.getLogger(__name__)

_BUSINESS_CONTEXT_MAX_CHARS = 12_000


def load_business_context() -> str:
    """Читает docs/AI_BUSINESS_CONTEXT.md (курируется владельцем)."""
    path = os.path.join(settings.BASE_DIR, "docs", "AI_BUSINESS_CONTEXT.md")
    try:
        with open(path, encoding="utf-8") as file_obj:
            return file_obj.read()[:_BUSINESS_CONTEXT_MAX_CHARS]
    except OSError:
        logger.warning("AI_BUSINESS_CONTEXT.md не найден (%s)", path)
        return ""


def build_system_context(retrieval_query: str = "") -> str:
    """Бизнес-контекст + релевантная память для системного промпта."""
    parts = []
    business = load_business_context()
    if business:
        parts.append(f"БИЗНЕС-КОНТЕКСТ КОМПАНИИ:\n{business}")
    if retrieval_query:
        memories = retrieve_memories(retrieval_query)
        block = format_memories_block(memories)
        if block:
            parts.append(block)
    return "\n\n".join(parts)


def email_body_as_text(email, limit: int = 6000) -> str:
    """Текст письма для промпта: body_text, иначе текст из body_html.

    Автоматические уведомления часто кладут содержимое (например, список
    автомобилей) только в HTML-таблицу — plain-text части у них нет или
    она обрезана. Поэтому HTML не игнорируем, а конвертируем в текст.
    """
    import re

    from django.utils.html import strip_tags

    body = (email.body_text or "").strip()
    if not body and email.body_html:
        html = re.sub(r"(?i)<(br|/p|/div|/tr|/li|/h[1-6])[^>]*>", "\n", email.body_html)
        html = re.sub(r"(?i)<(/td|/th)[^>]*>", " | ", html)
        body = strip_tags(html)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n\s*\n+", "\n", body).strip()
    if not body:
        body = (email.snippet or "").strip()
    return body[:limit]


def describe_email(email) -> str:
    """Текстовое описание письма для промпта (без HTML, с привязками)."""
    containers = list(email.containers.all()[:10])
    cars = list(email.cars.all()[:10])

    related = []
    for container in containers:
        status = getattr(container, "status", "") or ""
        related.append(f"контейнер {container.number} (статус: {status or 'н/д'})")
    for car in cars:
        related.append(f"авто {car.brand or ''} VIN {car.vin} (клиент: {car.client or 'н/д'})")

    body = email_body_as_text(email, limit=6000)

    client_info = describe_sender_client(email.from_addr)

    lines = [
        f"ID письма в системе: {email.pk} (используй его для get_email_thread / propose_email_reply)",
        f"От: {email.from_addr}",
        f"Кому: {email.to_addrs[:300]}",
        f"Тема: {email.subject[:300]}",
        f"Получено: {email.received_at:%d.%m.%Y %H:%M}",
        f"Привязки в системе: {', '.join(related) if related else 'нет (письмо не сопоставлено)'}",
        f"Отправитель в базе клиентов: {client_info or 'не найден'}",
        "",
        "ТЕКСТ ПИСЬМА:",
        body or "(пусто)",
    ]
    return "\n".join(lines)


def describe_thread_context(email, *, max_messages: int = 5, per_message_chars: int = 600) -> str:
    """Сжатая переписка треда ДО анализируемого письма (для промпта).

    Возвращает '' для одиночных писем. Включает оба направления — агенту
    важно видеть, отвечали ли мы уже, чтобы решить, требуется ли реакция
    владельца на новое письмо.
    """
    from core.models import ContainerEmail

    if not email.thread_id:
        return ""
    previous = list(
        ContainerEmail.objects.filter(thread_id=email.thread_id, received_at__lt=email.received_at)
        .exclude(pk=email.pk)
        .order_by("-received_at")[:max_messages]
    )
    if not previous:
        return ""

    lines = []
    for msg in reversed(previous):  # от старых к новым
        who = "МЫ (исходящее)" if msg.direction == ContainerEmail.DIRECTION_OUTGOING else f"ОНИ ({msg.from_addr[:80]})"
        body = email_body_as_text(msg, limit=per_message_chars)
        lines.append(f"[{msg.received_at:%d.%m %H:%M}] {who}:\n{body or '(пусто)'}")
    return (
        f"ПРЕДЫДУЩАЯ ПЕРЕПИСКА В ЭТОМ ТРЕДЕ (последние {len(previous)} сообщ., от старых к новым):\n"
        + "\n---\n".join(lines)
    )


def describe_sender_client(from_addr: str) -> str:
    """Ищет отправителя среди клиентов по email-адресу."""
    from email.utils import parseaddr

    from django.db.models import Q

    from core.models import Client

    _, addr = parseaddr(from_addr or "")
    addr = (addr or "").strip().lower()
    if not addr:
        return ""

    client = (
        Client.objects.filter(
            Q(email__iexact=addr)
            | Q(email2__iexact=addr)
            | Q(email3__iexact=addr)
            | Q(email4__iexact=addr)
            | Q(notification_emails__email__iexact=addr)
        )
        .distinct()
        .first()
    )
    if not client:
        return ""
    return f"клиент «{client.name}» (id={client.pk})"
