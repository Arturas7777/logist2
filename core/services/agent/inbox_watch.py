"""Включение и выключение разбора почты с доски дел.

При включении запоминается момент и id последних входящих писем.
Дальше агент разбирает только их и письма, пришедшие после включения.
Более раннюю почту не трогает.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

# Сколько последних писем разобрать сразу после включения.
BOOTSTRAP_LIMIT = 10


def enable_mail_assistant(by: str = "") -> tuple[object, int]:
    """Включает помощника. Возвращает (watch, сколько из стартовых ещё не разобрано)."""
    from core.models import AgentInboxWatch, ContainerEmail

    now = timezone.now()
    latest = list(
        ContainerEmail.objects.filter(
            direction=ContainerEmail.DIRECTION_INCOMING,
            hidden_reason="",
        )
        .order_by("-received_at", "-pk")
        .only("id", "agent_analyzed_at")[:BOOTSTRAP_LIMIT]
    )
    pending = sum(1 for email in latest if email.agent_analyzed_at is None)

    with transaction.atomic():
        watch = AgentInboxWatch.objects.select_for_update().filter(pk=1).first()
        if watch is None:
            watch = AgentInboxWatch(pk=1)
        watch.enabled = True
        watch.enabled_at = now
        watch.analyze_since = now
        watch.bootstrap_ids = [email.pk for email in latest]
        watch.updated_by = (by or "")[:150]
        watch.save()
    return watch, pending


def disable_mail_assistant(by: str = ""):
    """Выключает помощника. Очередь стартовых писем сбрасывается."""
    from core.models import AgentInboxWatch

    with transaction.atomic():
        watch = AgentInboxWatch.objects.select_for_update().filter(pk=1).first()
        if watch is None:
            watch = AgentInboxWatch(pk=1)
        watch.enabled = False
        watch.bootstrap_ids = []
        watch.updated_by = (by or "")[:150]
        watch.save()
    return watch
