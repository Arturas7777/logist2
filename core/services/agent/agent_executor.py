"""Исполнительный контур агента.

* :func:`propose_action` — единая точка создания :class:`AgentAction`.
  Сверяется с политикой автономии (:class:`AgentPolicy`):
  ASK → действие ждёт подтверждения владельца; AUTO → выполняется сразу;
  DISABLED → не создаётся вовсе.
* :func:`execute_action` — диспетчер исполнения одобренных действий.
* :func:`run_task_by_agent` — фаза 4: «Поручить ИИ» — tool-use цикл,
  в котором агент сам разбирается с делом (read-инструменты + предложения).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Создание действий с учётом политики ─────────────────────────────────────


def propose_action(
    *,
    action_type: str,
    title: str,
    payload: dict,
    risk_level: str = "LOW",
    run=None,
    source_email=None,
    task=None,
    reasoning: str = "",
    confidence: float | None = None,
):
    """Создаёт AgentAction с учётом политики автономии.

    Возвращает action (или None, если тип DISABLED). Высокорисковые
    действия игнорируют AUTO-режим — всегда требуют подтверждения.
    """
    from core.models import AgentAction, AgentPolicy

    mode = AgentPolicy.mode_for(action_type)
    if mode == AgentPolicy.MODE_DISABLED:
        logger.info("Действие %s запрещено политикой — не создаём", action_type)
        return None

    action = AgentAction.objects.create(
        action_type=action_type,
        title=title[:300],
        payload=payload,
        risk_level=risk_level,
        run=run,
        source_email=source_email,
        task=task,
        reasoning=reasoning,
        confidence=confidence,
    )

    if mode == AgentPolicy.MODE_AUTO and risk_level != AgentAction.RISK_HIGH:
        try:
            execute_action(action, auto=True)
        except Exception as exc:
            logger.exception("Авто-исполнение действия #%s упало", action.pk)
            action.mark_failed(str(exc))
    return action


# ── Исполнение действий ──────────────────────────────────────────────────────


def execute_action(action, *, auto: bool = False, by: str = "") -> dict:
    """Выполняет действие. Вызывается после одобрения (или сразу при AUTO).

    Возвращает result-словарь; статус и результат пишутся в action.
    """
    from core.models import AgentAction

    handlers = {
        AgentAction.TYPE_CREATE_TASK: _execute_create_task,
        AgentAction.TYPE_DRAFT_EMAIL_REPLY: _execute_email_reply,
        AgentAction.TYPE_COMPLETE_TASK: _execute_complete_task,
        AgentAction.TYPE_CREATE_CONTAINER: _execute_create_container,
    }
    handler = handlers.get(action.action_type)
    if handler is None:
        raise ValueError(f"Нет исполнителя для действия {action.action_type}")

    try:
        result = handler(action, by=by)
    except Exception as exc:
        action.mark_failed(str(exc))
        raise
    action.mark_executed(result, auto=auto)
    return result


def _execute_create_task(action, by: str = "") -> dict:
    from django.utils import timezone

    from core.models import Task

    payload = action.payload or {}
    deadline = None
    deadline_days = payload.get("deadline_days")
    if deadline_days is not None:
        try:
            deadline = timezone.now() + timezone.timedelta(days=int(deadline_days))
        except (TypeError, ValueError):
            deadline = None

    priority = payload.get("priority", "MEDIUM")
    if priority not in {"LOW", "MEDIUM", "HIGH"}:
        priority = "MEDIUM"

    task = Task.objects.create(
        title=(payload.get("title") or action.title)[:200],
        description=payload.get("description", ""),
        priority=priority,
        deadline=deadline,
        car_id=payload.get("car_id"),
        container_id=payload.get("container_id"),
        origin=Task.ORIGIN_AI,
        source_email=action.source_email,
        ai_summary=action.reasoning or "",
        created_by="AI-агент",
    )
    action.task = task
    action.save(update_fields=["task"])
    return {"task_id": task.pk, "title": task.title}


def _execute_email_reply(action, by: str = "") -> dict:
    """Отправляет согласованный ответ в тред исходного письма."""
    from email.utils import parseaddr

    from core.models import ContainerEmail
    from core.services.email_compose import reply_to_email

    payload = action.payload or {}
    parent = ContainerEmail.objects.filter(pk=payload.get("email_id")).first()
    if parent is None:
        raise ValueError(f"Письмо {payload.get('email_id')} не найдено")
    reply_text = (payload.get("reply_text") or "").strip()
    if not reply_text:
        raise ValueError("Пустой текст ответа")

    _, to_addr = parseaddr(parent.from_addr or "")
    if not to_addr:
        raise ValueError(f"Не удалось извлечь адрес получателя из «{parent.from_addr}»")

    sent = reply_to_email(parent_email=parent, user=None, to=to_addr, body_text=reply_text)
    return {"sent_email_id": sent.pk, "to": to_addr}


def _execute_create_container(action, by: str = "") -> dict:
    """Создаёт контейнер и заносит в него автомобили из payload.

    VIN, уже существующие в базе, не дублируются — такие авто
    привязываются к новому контейнеру (если ещё не привязаны к другому).
    """
    from datetime import date

    from core.models import Car, Container

    payload = action.payload or {}
    number = (payload.get("number") or "").strip().upper()
    if not number:
        raise ValueError("В payload нет номера контейнера")
    if Container.objects.filter(number__iexact=number).exists():
        raise ValueError(f"Контейнер {number} уже существует")

    eta = None
    if payload.get("eta"):
        try:
            eta = date.fromisoformat(payload["eta"])
        except ValueError:
            eta = None

    container = Container.objects.create(
        number=number,
        booking_number=(payload.get("booking_number") or "").strip(),
        eta=eta,
        status="FLOATING",
    )

    created_vins, attached_vins, skipped = [], [], []
    for item in payload.get("cars") or []:
        vin = (item.get("vin") or "").strip().upper()
        if not vin:
            continue
        existing = Car.objects.filter(vin__iexact=vin).first()
        if existing:
            if existing.container_id:
                skipped.append(f"{vin} (уже в контейнере {existing.container.number})")
            else:
                existing.container = container
                existing.save(update_fields=["container"])
                attached_vins.append(vin)
            continue
        try:
            year = int(item.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        Car.objects.create(
            vin=vin,
            brand=(item.get("brand") or "")[:50],
            year=year,
            status="FLOATING",
            container=container,
        )
        created_vins.append(vin)

    return {
        "container_id": container.pk,
        "number": container.number,
        "cars_created": created_vins,
        "cars_attached": attached_vins,
        "cars_skipped": skipped,
    }


def _execute_complete_task(action, by: str = "") -> dict:
    from core.models import Task

    payload = action.payload or {}
    task = Task.objects.filter(pk=payload.get("task_id")).first()
    if task is None:
        raise ValueError(f"Дело {payload.get('task_id')} не найдено")
    comment = (payload.get("comment") or "").strip()
    if comment:
        task.description = (task.description + f"\n\n[ИИ] {comment}").strip()
        task.save(update_fields=["description", "updated_at"])
    task.mark_completed(by=by or "AI-агент")
    return {"task_id": task.pk}


# ── Фаза 4: «Поручить ИИ» ────────────────────────────────────────────────────

EXECUTOR_SYSTEM_TEMPLATE = """Ты — AI-исполнитель в логистической компании Caromoto Lithuania.
Владелец поручил тебе дело. Разберись и сделай максимум возможного твоими
инструментами; то, что требует подтверждения, оформи как предложение
(propose_*) — владелец увидит его на странице «Дела».

Порядок работы:
1. Изучи дело и связанные данные (поиск, тред письма) read-инструментами.
2. Если для выполнения хватает инструментов — действуй: предложи ответ на
   письмо (propose_email_reply), создание контейнера с автомобилями
   (propose_create_container — сначала проверь дубликаты через
   search_containers), и если после этого дело можно считать закрытым —
   предложи закрытие (propose_complete_task).
3. Если непонятно, как действовать — задай вопрос владельцу (ask_owner)
   и НЕ предлагай сомнительных действий.
4. Узнал полезный переиспользуемый факт — сохрани его (save_memory).
5. В конце дай короткий отчёт по-русски: что выяснил, что предложил,
   что осталось на владельце.

Ограничения: деньги, балансы и инвойсы тебе недоступны; не выдумывай
данные — всё проверяй инструментами.

{context}
"""


def run_task_by_agent(task) -> object:
    """Выполняет дело tool-use циклом. Возвращает AgentRun с отчётом."""
    from core.models import AgentRun
    from core.services.agent.agent_tools import TOOL_SPECS, execute_tool
    from core.services.agent.context_builder import build_system_context, describe_email
    from core.services.agent.llm_client import AgentLLMClient

    run = AgentRun.objects.create(kind=AgentRun.KIND_EXECUTOR, input_ref=f"task:{task.pk}")

    task_lines = [
        f"ДЕЛО #{task.pk}: {task.title}",
        f"Описание: {task.description or '—'}",
        f"Приоритет: {task.priority}; дедлайн: {task.deadline or 'нет'}",
    ]
    if task.car_id:
        task_lines.append(f"Привязано к авто: VIN {task.car.vin} ({task.car.brand or ''})")
    if task.container_id:
        task_lines.append(f"Привязано к контейнеру: {task.container.number}")
    if task.source_email_id:
        task_lines.append("Письмо-источник:\n" + describe_email(task.source_email))

    context = {
        "run": run,
        "task": task,
        "source_email": task.source_email,
        "question_context": {"summary": f"Исполнение дела #{task.pk}: {task.title}"},
    }

    try:
        client = AgentLLMClient(run=run)
        system = EXECUTOR_SYSTEM_TEMPLATE.format(
            context=build_system_context(retrieval_query=f"{task.title} {task.description}"[:500])
        )
        report = client.run_tool_loop(
            system=system,
            messages=[{"role": "user", "content": "\n".join(task_lines)}],
            tools=TOOL_SPECS,
            tool_executor=lambda name, args: execute_tool(name, args, context=context),
            max_iterations=12,
        )
        run.finish_ok({"report": report})
    except Exception as exc:
        logger.exception("run_task_by_agent для дела #%s упал", task.pk)
        run.finish_error(str(exc))
    return run
