"""Реестр инструментов AI-агента для tool-use циклов (фазы 4-5).

Два класса инструментов:

* **read** — безопасное чтение данных (поиск контейнеров/авто/клиентов,
  тред письма, открытые дела). Выполняются сразу.
* **write** — намерения изменить систему. НЕ выполняются напрямую:
  создают :class:`core.models.AgentAction` через
  :func:`core.services.agent.agent_executor.propose_action`, где политика
  автономии (AgentPolicy) решает — выполнить сразу или ждать подтверждения.

Денежные операции и удаление данных в реестре отсутствуют сознательно
(см. правило 3 в .cursor/rules/ai-agent.mdc).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Спецификации для Anthropic tool use ─────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "name": "search_containers",
        "description": "Поиск контейнеров по номеру/букингу (частичное совпадение).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Номер контейнера или букинга"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_cars",
        "description": "Поиск авто по VIN (полному или части), марке или имени клиента.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_clients",
        "description": "Поиск клиентов по имени или email.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_email_thread",
        "description": "Вся переписка треда, к которому принадлежит письмо (по id письма).",
        "input_schema": {
            "type": "object",
            "properties": {"email_id": {"type": "integer"}},
            "required": ["email_id"],
        },
    },
    {
        "name": "list_open_tasks",
        "description": "Открытые дела (топ-30 по приоритету/дедлайну).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_email_reply",
        "description": (
            "Предложить ответ на письмо. Создаёт действие DRAFT_EMAIL_REPLY: "
            "письмо будет отправлено в тред после подтверждения владельцем "
            "(или сразу, если тип действия переведён в авто-режим)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "integer", "description": "ID письма, на которое отвечаем"},
                "reply_text": {"type": "string", "description": "Текст ответа (plain text, на языке письма)"},
                "summary": {"type": "string", "description": "Краткое описание для владельца, по-русски"},
            },
            "required": ["email_id", "reply_text", "summary"],
        },
    },
    {
        "name": "propose_complete_task",
        "description": "Предложить закрыть дело как выполненное (с комментарием, что сделано).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "comment": {"type": "string", "description": "Что сделано, по-русски"},
            },
            "required": ["task_id", "comment"],
        },
    },
    {
        "name": "propose_create_container",
        "description": (
            "Предложить создать новый контейнер в системе (с автомобилями, которые в нём едут). "
            "Перед вызовом ОБЯЗАТЕЛЬНО проверь через search_containers, что контейнера/букинга "
            "ещё нет. Контейнер будет создан после подтверждения владельцем."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "string", "description": "Номер контейнера (например MRSU6031875)"},
                "booking_number": {"type": "string", "description": "Номер букинга, если известен"},
                "eta": {"type": "string", "description": "Дата прибытия в формате YYYY-MM-DD, если известна"},
                "cars": {
                    "type": "array",
                    "description": "Автомобили в контейнере (из текста письма)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "vin": {"type": "string", "description": "VIN (17 символов)"},
                            "brand": {"type": "string", "description": "Марка и модель, например Toyota Camry"},
                            "year": {"type": "integer", "description": "Год выпуска"},
                        },
                        "required": ["vin"],
                    },
                },
                "summary": {"type": "string", "description": "Краткое описание для владельца, по-русски"},
            },
            "required": ["number", "summary"],
        },
    },
    {
        "name": "ask_owner",
        "description": (
            "Задать вопрос владельцу, когда непонятно, как действовать. Ответ попадёт в постоянную память."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "save_memory",
        "description": "Сохранить в постоянную память агента важный факт, выясненный в ходе работы.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Факт/правило в 1-3 предложениях, по-русски"},
                "kind": {"type": "string", "enum": ["RULE", "FACT", "CONTACT"]},
            },
            "required": ["content"],
        },
    },
]


# ── Реализации ───────────────────────────────────────────────────────────────


def _tool_search_containers(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Container

    query = (args.get("query") or "").strip()
    rows = Container.objects.filter(Q(number__icontains=query) | Q(booking_number__icontains=query)).order_by("-id")[
        :10
    ]
    return [
        {
            "id": c.pk,
            "number": c.number,
            "booking": c.booking_number or "",
            "status": str(getattr(c, "status", "") or ""),
            "eta": str(getattr(c, "eta", "") or ""),
            "cars": [car.vin for car in c.container_cars.all()[:10]],
        }
        for c in rows
    ]


def _tool_search_cars(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Car

    query = (args.get("query") or "").strip()
    rows = Car.objects.filter(
        Q(vin__icontains=query) | Q(brand__icontains=query) | Q(client__name__icontains=query)
    ).select_related("client", "container")[:10]
    return [
        {
            "id": car.pk,
            "vin": car.vin,
            "brand": car.brand or "",
            "client": str(car.client or ""),
            "container": car.container.number if car.container_id else "",
            "status": str(getattr(car, "status", "") or ""),
        }
        for car in rows
    ]


def _tool_search_clients(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Client

    query = (args.get("query") or "").strip()
    rows = Client.objects.filter(Q(name__icontains=query) | Q(email__icontains=query))[:10]
    return [{"id": c.pk, "name": c.name, "email": c.email or ""} for c in rows]


def _tool_get_email_thread(args: dict) -> list[dict]:
    from core.models import ContainerEmail

    email = ContainerEmail.objects.filter(pk=args.get("email_id")).first()
    if not email:
        return []
    thread = ContainerEmail.objects.filter(thread_id=email.thread_id).order_by("received_at")[:20]
    return [
        {
            "id": e.pk,
            "direction": e.direction,
            "from": e.from_addr[:200],
            "received_at": str(e.received_at),
            "subject": e.subject[:200],
            # 4000, а не 2000: в нотисах о прибытии списки авто идут в конце
            # письма и при жёсткой обрезке агент их не видит.
            "body": (e.body_text or e.snippet or "")[:4000],
        }
        for e in thread
    ]


def _tool_list_open_tasks(args: dict) -> list[dict]:
    from core.models import Task

    rows = Task.objects.filter(is_completed=False).select_related("car", "container")[:30]
    return [
        {
            "id": t.pk,
            "title": t.title,
            "priority": t.priority,
            "deadline": str(t.deadline or ""),
            "car": t.car.vin if t.car_id else "",
            "container": t.container.number if t.container_id else "",
            "overdue": t.is_overdue,
        }
        for t in rows
    ]


def _tool_propose_email_reply(args: dict, context: dict) -> dict:
    from core.models import AgentAction, ContainerEmail
    from core.services.agent.agent_executor import propose_action

    email = ContainerEmail.objects.filter(pk=args.get("email_id")).first()
    if not email:
        return {"error": f"Письмо {args.get('email_id')} не найдено"}
    action = propose_action(
        action_type=AgentAction.TYPE_DRAFT_EMAIL_REPLY,
        title=(args.get("summary") or f"Ответ на «{email.subject[:80]}»")[:300],
        payload={"email_id": email.pk, "reply_text": args.get("reply_text", "")},
        risk_level=AgentAction.RISK_MEDIUM,
        run=context.get("run"),
        source_email=email,
        task=context.get("task"),
        reasoning=args.get("summary", ""),
    )
    if action is None:
        return {"status": "disabled", "detail": "Тип действия запрещён политикой"}
    return {"status": action.status, "action_id": action.pk}


def _tool_propose_complete_task(args: dict, context: dict) -> dict:
    from core.models import AgentAction, Task
    from core.services.agent.agent_executor import propose_action

    task = Task.objects.filter(pk=args.get("task_id")).first()
    if not task:
        return {"error": f"Дело {args.get('task_id')} не найдено"}
    action = propose_action(
        action_type=AgentAction.TYPE_COMPLETE_TASK,
        title=f"Закрыть дело «{task.title[:80]}»",
        payload={"task_id": task.pk, "comment": args.get("comment", "")},
        risk_level=AgentAction.RISK_LOW,
        run=context.get("run"),
        task=task,
        reasoning=args.get("comment", ""),
    )
    if action is None:
        return {"status": "disabled", "detail": "Тип действия запрещён политикой"}
    return {"status": action.status, "action_id": action.pk}


def _tool_propose_create_container(args: dict, context: dict) -> dict:
    from django.db.models import Q

    from core.models import AgentAction, Container
    from core.services.agent.agent_executor import propose_action

    number = (args.get("number") or "").strip().upper()
    if not number:
        return {"error": "Не указан номер контейнера"}
    booking = (args.get("booking_number") or "").strip()

    dup_q = Q(number__iexact=number)
    if booking:
        dup_q |= Q(booking_number__iexact=booking)
    existing = Container.objects.filter(dup_q).first()
    if existing:
        return {
            "error": (
                f"Контейнер уже есть в системе: id={existing.pk}, номер {existing.number}, "
                f"букинг {existing.booking_number or 'нет'} — создавать дубликат нельзя"
            )
        }

    cars = [c for c in (args.get("cars") or []) if (c.get("vin") or "").strip()]
    action = propose_action(
        action_type=AgentAction.TYPE_CREATE_CONTAINER,
        title=(args.get("summary") or f"Создать контейнер {number}")[:300],
        payload={
            "number": number,
            "booking_number": booking,
            "eta": (args.get("eta") or "").strip(),
            "cars": cars,
        },
        risk_level=AgentAction.RISK_MEDIUM,
        run=context.get("run"),
        source_email=context.get("source_email"),
        task=context.get("task"),
        reasoning=args.get("summary", ""),
    )
    if action is None:
        return {"status": "disabled", "detail": "Тип действия запрещён политикой"}
    return {"status": action.status, "action_id": action.pk, "cars_count": len(cars)}


def _tool_ask_owner(args: dict, context: dict) -> dict:
    from core.models import AgentQuestion

    question = AgentQuestion.objects.create(
        question=(args.get("question") or "").strip(),
        run=context.get("run"),
        source_email=context.get("source_email"),
        context_json=context.get("question_context", {}),
    )
    return {"status": "created", "question_id": question.pk}


def _tool_save_memory(args: dict, context: dict) -> dict:
    from core.services.agent.memory import save_memory

    memory = save_memory(
        content=(args.get("content") or "").strip(),
        kind=args.get("kind", "FACT"),
        source="MANUAL",
        created_by="agent",
    )
    return {"status": "saved", "memory_id": memory.pk}


_READ_TOOLS = {
    "search_containers": _tool_search_containers,
    "search_cars": _tool_search_cars,
    "search_clients": _tool_search_clients,
    "get_email_thread": _tool_get_email_thread,
    "list_open_tasks": _tool_list_open_tasks,
}

_WRITE_TOOLS = {
    "propose_email_reply": _tool_propose_email_reply,
    "propose_complete_task": _tool_propose_complete_task,
    "propose_create_container": _tool_propose_create_container,
    "ask_owner": _tool_ask_owner,
    "save_memory": _tool_save_memory,
}


def execute_tool(name: str, args: dict, *, context: dict | None = None) -> Any:
    """Диспетчер инструментов для :meth:`AgentLLMClient.run_tool_loop`."""
    context = context or {}
    if name in _READ_TOOLS:
        return _READ_TOOLS[name](args)
    if name in _WRITE_TOOLS:
        return _WRITE_TOOLS[name](args, context)
    raise ValueError(f"Неизвестный инструмент: {name}")
