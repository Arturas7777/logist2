"""Утренний планировщик (фаза 3).

Раз в день собирает картину: просроченные дела, дедлайны, письма с флагом
«требует ответа», открытые вопросы агента — и просит LLM составить
приоритизированный план дня. Дайджест сохраняется в AgentRun
(kind=PLANNER) и показывается на странице «Дела».
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_TEMPLATE = """Ты — AI-планировщик владельца логистической компании Caromoto Lithuania.
Каждое утро ты готовишь короткий план дня по открытым делам.

{context}

Верни ТОЛЬКО валидный JSON:
{{
  "digest": "план дня по-русски: 3-7 пунктов в порядке приоритета, каждый пункт — одна строка, начинающаяся с номера. Учитывай просрочки, дедлайны и письма без ответа.",
  "top_task_ids": [1, 2, 3],
  "warnings": "на что обратить особое внимание (1-2 предложения) или пустая строка"
}}

Правила приоритизации: просроченное — выше всего; затем дедлайны сегодня:
затем HIGH-приоритет; письма «требует ответа» старше 2 дней — это долг
перед контрагентом, поднимай их выше.
"""


def _collect_state() -> tuple[str, list[int]]:
    """Собирает текстовый снимок открытых дел/писем/вопросов."""
    from core.models import AgentQuestion, ContainerEmail, Task

    now = timezone.now()
    lines: list[str] = []
    task_ids: list[int] = []

    tasks = list(Task.objects.filter(is_completed=False).select_related("car", "container").order_by("deadline")[:40])
    lines.append(f"ОТКРЫТЫЕ ДЕЛА ({len(tasks)}):")
    for task in tasks:
        task_ids.append(task.pk)
        flags = []
        if task.is_overdue:
            flags.append("ПРОСРОЧЕНО")
        if task.deadline and not task.is_overdue and (task.deadline - now).days < 1:
            flags.append("дедлайн сегодня")
        link = ""
        if task.car_id:
            link = f" [авто {task.car.vin}]"
        elif task.container_id:
            link = f" [контейнер {task.container.number}]"
        lines.append(
            f"- #{task.pk} ({task.priority}{', ' + ', '.join(flags) if flags else ''}) "
            f"{task.title}{link}; дедлайн: {task.deadline:%d.%m %H:%M}"
            if task.deadline
            else f"- #{task.pk} ({task.priority}) {task.title}{link}; без дедлайна"
        )

    needs_reply = list(ContainerEmail.objects.filter(needs_reply=True).order_by("received_at")[:15])
    lines.append(f"\nПИСЬМА «ТРЕБУЕТ ОТВЕТА» ({len(needs_reply)}):")
    for email in needs_reply:
        age_days = (now - email.received_at).days
        lines.append(f"- от {email.from_addr[:80]}, «{email.subject[:100]}», висит {age_days} дн.")

    questions = list(AgentQuestion.objects.filter(status="OPEN")[:10])
    lines.append(f"\nОТКРЫТЫЕ ВОПРОСЫ АГЕНТА К ВЛАДЕЛЬЦУ ({len(questions)}):")
    for question in questions:
        lines.append(f"- {question.question[:150]}")

    return "\n".join(lines), task_ids


def build_morning_digest() -> dict:
    """Строит утренний дайджест. Возвращает result_json последнего запуска."""
    from core.models import AgentRun
    from core.services.agent.context_builder import build_system_context
    from core.services.agent.llm_client import AgentLLMClient

    run = AgentRun.objects.create(kind=AgentRun.KIND_PLANNER, input_ref=timezone.localdate().isoformat())
    state_text, task_ids = _collect_state()

    try:
        client = AgentLLMClient(run=run)
        system = PLANNER_SYSTEM_TEMPLATE.format(
            context=build_system_context(retrieval_query="приоритеты планирование дел")
        )
        data = client.complete_json(
            system=system,
            messages=[{"role": "user", "content": state_text}],
        )
        if not data or not data.get("digest"):
            raise ValueError(f"Планировщик вернул пусто: {str(data)[:200]}")
        result = {
            "digest": data["digest"],
            "top_task_ids": [t for t in (data.get("top_task_ids") or []) if t in task_ids],
            "warnings": data.get("warnings", ""),
            "date": timezone.localdate().isoformat(),
        }
        run.finish_ok(result)
        return result
    except Exception as exc:
        logger.exception("Утренний дайджест не построился")
        run.finish_error(str(exc))
        return {"error": str(exc)[:300]}


def get_latest_digest() -> dict | None:
    """Последний успешный дайджест для страницы «Дела»."""
    from core.models import AgentRun

    run = (
        AgentRun.objects.filter(kind=AgentRun.KIND_PLANNER, status=AgentRun.STATUS_SUCCESS)
        .order_by("-started_at")
        .first()
    )
    if not run:
        return None
    result = dict(run.result_json or {})
    result["generated_at"] = run.started_at
    return result or None
