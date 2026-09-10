"""Инструменты помощника админки: чтение CRM + создание дела.

Деньги и статусы бот не меняет — только читает и объясняет, куда нажать.
Создание Task разрешено: сотрудник явно просит об этом в чате админки.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

TOOL_SPECS: list[dict] = [
    {
        "name": "search_containers",
        "description": "Поиск контейнеров по номеру или букингу.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_cars",
        "description": "Поиск авто по VIN, марке или имени клиента.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_clients",
        "description": "Поиск клиентов по имени или email. Возвращает балансы.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_invoices",
        "description": "Поиск инвойсов по номеру, номеру контрагента или имени клиента.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_emails",
        "description": "Поиск писем Gmail по теме, отправителю или тексту.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_container",
        "description": "Полная карточка контейнера: статус, линия, склад, авто, даты.",
        "input_schema": {
            "type": "object",
            "properties": {
                "container_id": {"type": "integer"},
                "number": {"type": "string"},
            },
        },
    },
    {
        "name": "get_car",
        "description": "Полная карточка авто: статус, цены, услуги, хранение, диагностика.",
        "input_schema": {
            "type": "object",
            "properties": {
                "car_id": {"type": "integer"},
                "vin": {"type": "string"},
            },
        },
    },
    {
        "name": "get_client",
        "description": "Карточка клиента: контакты, баланс, последние авто и инвойсы.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "integer"},
                "name": {"type": "string"},
            },
        },
    },
    {
        "name": "get_invoice",
        "description": "Карточка инвойса: суммы, стороны, статус, привязанные авто.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "integer"},
                "number": {"type": "string"},
            },
        },
    },
    {
        "name": "list_open_tasks",
        "description": "Открытые дела (топ-20 по приоритету и дедлайну).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_today_overview",
        "description": "Сводка на сегодня: просроченные дела, письма без ответа, ближайшие ETA.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "diagnose_object",
        "description": (
            "Проверка типичных проблем карточки. "
            "model_name: car / container / newinvoice. Нужен object_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model_name": {"type": "string"},
                "object_id": {"type": "integer"},
            },
            "required": ["model_name", "object_id"],
        },
    },
    {
        "name": "search_how_to",
        "description": "Как сделать действие в админке Logist2 (кнопки, экраны, правила).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Создать дело в разделе «Дела». Вызывай только если сотрудник явно просит "
            "создать задачу/напоминание."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                "car_id": {"type": "integer"},
                "container_id": {"type": "integer"},
            },
            "required": ["title"],
        },
    },
]


def _money(value) -> str:
    try:
        return f"{Decimal(value or 0):.2f}"
    except Exception:
        return "0.00"


def _tool_search_containers(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Container

    query = (args.get("query") or "").strip()
    rows = Container.objects.filter(Q(number__icontains=query) | Q(booking_number__icontains=query)).select_related(
        "line", "warehouse"
    )[:10]
    return [
        {
            "id": c.pk,
            "number": c.number,
            "booking": c.booking_number or "",
            "status": c.get_status_display(),
            "eta": str(c.eta or ""),
            "line": c.line.name if c.line_id else "",
            "warehouse": c.warehouse.name if c.warehouse_id else "",
            "cars": [car.vin for car in c.container_cars.all()[:8]],
        }
        for c in rows
    ]


def _tool_search_cars(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Car

    query = (args.get("query") or "").strip()
    rows = Car.objects.filter(
        Q(vin__icontains=query) | Q(brand__icontains=query) | Q(client__name__icontains=query)
    ).select_related("client", "container", "warehouse")[:10]
    return [
        {
            "id": car.pk,
            "vin": car.vin,
            "brand": car.brand or "",
            "year": car.year,
            "client": str(car.client or ""),
            "container": car.container.number if car.container_id else "",
            "status": car.get_status_display(),
            "warehouse": car.warehouse.name if car.warehouse_id else "",
        }
        for car in rows
    ]


def _tool_search_clients(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import Client

    query = (args.get("query") or "").strip()
    rows = Client.objects.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(email2__icontains=query))[:10]
    result = []
    for client in rows:
        result.append(
            {
                "id": client.pk,
                "name": client.name,
                "email": client.email or "",
                "country": client.get_country_display() if client.country else "",
                "balance": _money(client.balance),
                "open_invoices_debt": _money(client.open_invoices_debt),
                "total_balance": _money(client.total_balance),
                "balance_status": client.balance_status,
            }
        )
    return result


def _tool_search_invoices(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models_billing import NewInvoice

    query = (args.get("query") or "").strip()
    rows = NewInvoice.objects.filter(
        Q(number__icontains=query)
        | Q(external_number__icontains=query)
        | Q(recipient_client__name__icontains=query)
        | Q(issuer_company__name__icontains=query)
    ).select_related("recipient_client")[:10]
    return [
        {
            "id": inv.pk,
            "number": inv.number,
            "external_number": inv.external_number or "",
            "type": inv.get_document_type_display(),
            "status": inv.get_status_display(),
            "total": _money(inv.total),
            "paid": _money(inv.paid_amount),
            "client": str(inv.recipient_client or ""),
            "date": str(inv.date or ""),
        }
        for inv in rows
    ]


def _tool_search_emails(args: dict) -> list[dict]:
    from django.db.models import Q

    from core.models import ContainerEmail

    query = (args.get("query") or "").strip()
    rows = (
        ContainerEmail.objects.filter(
            Q(subject__icontains=query) | Q(from_addr__icontains=query) | Q(body_text__icontains=query)
        )
        .filter(Q(hidden_reason__isnull=True) | Q(hidden_reason=""))
        .order_by("-received_at")[:10]
    )
    return [
        {
            "id": email.pk,
            "subject": (email.subject or "")[:200],
            "from": (email.from_addr or "")[:200],
            "received_at": str(email.received_at or ""),
            "needs_reply": bool(email.needs_reply),
            "direction": email.direction,
        }
        for email in rows
    ]


def _tool_get_container(args: dict) -> dict:
    from core.models import Container
    from core.services.admin_ai_agent import _diagnose_container, _summarize_container

    obj = None
    if args.get("container_id"):
        obj = Container.objects.select_related("line", "warehouse").filter(pk=args["container_id"]).first()
    if obj is None and args.get("number"):
        obj = Container.objects.select_related("line", "warehouse").filter(number__iexact=args["number"]).first()
    if obj is None:
        return {"error": "Контейнер не найден"}
    cars = [
        {"id": car.pk, "vin": car.vin, "brand": car.brand, "status": car.get_status_display(), "client": str(car.client or "")}
        for car in obj.container_cars.select_related("client").all()[:12]
    ]
    issues = _diagnose_container(obj)
    return {
        "summary": _summarize_container(obj),
        "booking": obj.booking_number or "",
        "cars": cars,
        "diagnostics": issues or ["критичных проблем нет"],
        "admin_url": f"/admin/core/container/{obj.pk}/change/",
    }


def _tool_get_car(args: dict) -> dict:
    from core.models import Car
    from core.services.admin_ai_agent import (
        _build_price_context,
        _diagnose_car,
        _summarize_car,
        _summarize_photos_for_car,
    )

    obj = None
    if args.get("car_id"):
        obj = Car.objects.select_related("container", "warehouse", "line", "carrier", "client").filter(pk=args["car_id"]).first()
    if obj is None and args.get("vin"):
        obj = (
            Car.objects.select_related("container", "warehouse", "line", "carrier", "client")
            .filter(vin__iexact=args["vin"])
            .first()
        )
    if obj is None:
        return {"error": "Авто не найдено"}
    issues = _diagnose_car(obj)
    return {
        "summary": _summarize_car(obj),
        "client": str(obj.client or ""),
        "notes": (obj.notes or "")[:400],
        "photos": _summarize_photos_for_car(obj),
        "price": _build_price_context(obj),
        "diagnostics": issues or ["критичных проблем нет"],
        "admin_url": f"/admin/core/car/{obj.pk}/change/",
    }


def _tool_get_client(args: dict) -> dict:
    from core.models import Car, Client
    from core.models_billing import NewInvoice

    obj = None
    if args.get("client_id"):
        obj = Client.objects.filter(pk=args["client_id"]).first()
    if obj is None and args.get("name"):
        obj = Client.objects.filter(name__icontains=args["name"]).first()
    if obj is None:
        return {"error": "Клиент не найден"}

    cars = [
        {
            "id": car.pk,
            "vin": car.vin,
            "status": car.get_status_display(),
            "container": car.container.number if car.container_id else "",
        }
        for car in Car.objects.filter(client=obj).select_related("container").order_by("-id")[:8]
    ]
    invoices = [
        {
            "id": inv.pk,
            "number": inv.number,
            "status": inv.get_status_display(),
            "total": _money(inv.total),
            "paid": _money(inv.paid_amount),
        }
        for inv in NewInvoice.objects.filter(recipient_client=obj).order_by("-date", "-id")[:6]
    ]
    return {
        "id": obj.pk,
        "name": obj.name,
        "country": obj.get_country_display() if obj.country else "",
        "emails": [e for e in [obj.email, obj.email2, obj.email3, obj.email4] if e],
        "telegram_enabled": bool(obj.telegram_enabled),
        "balance": _money(obj.balance),
        "open_invoices_debt": _money(obj.open_invoices_debt),
        "total_balance": _money(obj.total_balance),
        "balance_status": obj.balance_status,
        "cars": cars,
        "invoices": invoices,
        "admin_url": f"/admin/core/client/{obj.pk}/change/",
    }


def _tool_get_invoice(args: dict) -> dict:
    from core.models_billing import NewInvoice
    from core.services.admin_ai_agent import _diagnose_invoice, _summarize_invoice

    obj = None
    if args.get("invoice_id"):
        obj = NewInvoice.objects.filter(pk=args["invoice_id"]).first()
    if obj is None and args.get("number"):
        obj = NewInvoice.objects.filter(number__iexact=args["number"]).first()
    if obj is None:
        return {"error": "Инвойс не найден"}
    cars = [{"id": car.pk, "vin": car.vin} for car in obj.cars.all()[:12]]
    return {
        "summary": _summarize_invoice(obj),
        "type": obj.get_document_type_display(),
        "cars": cars,
        "diagnostics": _diagnose_invoice(obj) or ["критичных проблем нет"],
        "admin_url": f"/admin/core/newinvoice/{obj.pk}/change/",
    }


def _tool_list_open_tasks(args: dict) -> list[dict]:
    from core.models import Task

    rows = Task.objects.filter(is_completed=False).select_related("car", "container").order_by("-priority", "deadline")[:20]
    return [
        {
            "id": task.pk,
            "title": task.title,
            "priority": task.priority,
            "deadline": str(task.deadline or ""),
            "car": task.car.vin if task.car_id else "",
            "container": task.container.number if task.container_id else "",
            "overdue": task.is_overdue,
            "admin_url": f"/admin/core/task/{task.pk}/change/",
        }
        for task in rows
    ]


def _tool_get_today_overview(args: dict) -> dict:
    from django.db.models import Q
    from django.utils import timezone

    from core.models import Container, ContainerEmail, Task

    today = timezone.localdate()
    overdue = Task.objects.filter(is_completed=False, deadline__date__lt=today).count()
    open_tasks = Task.objects.filter(is_completed=False).count()
    need_reply = ContainerEmail.objects.filter(needs_reply=True).filter(
        Q(hidden_reason__isnull=True) | Q(hidden_reason="")
    ).order_by("-received_at")[:8]
    arriving = Container.objects.filter(eta__gte=today, status="FLOATING").order_by("eta")[:8]
    return {
        "open_tasks": open_tasks,
        "overdue_tasks": overdue,
        "emails_need_reply": [
            {"id": e.pk, "subject": (e.subject or "")[:160], "from": (e.from_addr or "")[:120]} for e in need_reply
        ],
        "arriving_containers": [
            {"id": c.pk, "number": c.number, "eta": str(c.eta or "")} for c in arriving
        ],
        "tasks_board_url": "/admin/tasks-board/",
    }


def _tool_diagnose_object(args: dict) -> dict:
    from core.services.admin_ai_agent import _run_diagnostics

    text = _run_diagnostics({"model_name": args.get("model_name"), "object_id": args.get("object_id")})
    return {"result": text}


def _tool_search_how_to(args: dict, context: dict) -> dict:
    from django.conf import settings

    from core.services.admin_ai_agent import _build_ui_guidance
    from core.services.ai_rag import build_rag_snippets

    query = args.get("query") or ""
    guidance = _build_ui_guidance(query, context.get("page_context") or {})
    rag = build_rag_snippets(query, top_k=getattr(settings, "AI_RAG_TOP_K", 4))
    return {"ui_guidance": guidance, "docs": rag}


def _tool_create_task(args: dict) -> dict:
    from core.models import Car, Container, Task

    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "Нужен заголовок дела"}
    task = Task(
        title=title[:200],
        description=(args.get("description") or "").strip(),
        priority=args.get("priority") or "MEDIUM",
        origin=Task.ORIGIN_AI,
    )
    if args.get("car_id"):
        task.car = Car.objects.filter(pk=args["car_id"]).first()
    if args.get("container_id"):
        task.container = Container.objects.filter(pk=args["container_id"]).first()
    task.save()
    return {
        "status": "created",
        "task_id": task.pk,
        "title": task.title,
        "admin_url": f"/admin/core/task/{task.pk}/change/",
    }


_TOOLS = {
    "search_containers": _tool_search_containers,
    "search_cars": _tool_search_cars,
    "search_clients": _tool_search_clients,
    "search_invoices": _tool_search_invoices,
    "search_emails": _tool_search_emails,
    "get_container": _tool_get_container,
    "get_car": _tool_get_car,
    "get_client": _tool_get_client,
    "get_invoice": _tool_get_invoice,
    "list_open_tasks": _tool_list_open_tasks,
    "get_today_overview": _tool_get_today_overview,
    "diagnose_object": _tool_diagnose_object,
    "search_how_to": _tool_search_how_to,
    "create_task": _tool_create_task,
}


def execute_admin_tool(name: str, args: dict, *, context: dict | None = None) -> Any:
    handler = _TOOLS.get(name)
    if handler is None:
        return {"error": f"Неизвестный инструмент: {name}"}
    context = context or {}
    if name == "search_how_to":
        return handler(args, context)
    return handler(args)


def parse_tool_arguments(raw: str) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
