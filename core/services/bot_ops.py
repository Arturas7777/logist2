"""Статусы, передача, расхождения, утренний снимок, задания."""

from __future__ import annotations

import datetime

from django.utils import timezone

from core.services.bot_runtime import BotError
from core.services.email_service import mask_email

_FORWARD = ("FLOATING", "IN_PORT", "UNLOADED", "TRANSFERRED")


def _rank(status: str) -> int:
    try:
        return _FORWARD.index(status)
    except ValueError:
        return -1


def _warehouse(code: str | None):
    from core.models import Warehouse

    if not code:
        return None
    folded = code.strip().upper()
    if folded == "NETO":
        return Warehouse.objects.filter(name__icontains="neto").order_by("id").first()
    if folded in {"ATLANTIC", "ATLANTIC EXPRESS"}:
        return Warehouse.objects.filter(name__icontains="atlantic").order_by("id").first()
    return Warehouse.objects.filter(name__iexact=code.strip()).first()


def notification_preview(container, kind: str) -> list[dict]:
    from core.models import Client

    client_ids = container.container_cars.exclude(client_id=None).values_list("client_id", flat=True)
    rows = []
    for client in Client.objects.filter(pk__in=client_ids).prefetch_related("notification_emails"):
        emails = [mask_email(item.email) for item in client.notification_emails.all()]
        rows.append({"client_id": client.pk, "client": client.name, "kind": kind, "emails": emails})
    return rows


def set_container_status(
    *,
    number: str,
    status: str,
    unload_date=None,
    warehouse_code: str | None = None,
    notify: bool = True,
    apply: bool = False,
) -> dict:
    from core.models import Container
    from core.models.containers import validate_status_transition
    from core.services.bot_runtime import mute_client_notifications

    if status not in {"IN_PORT", "UNLOADED"}:
        raise BotError("Статус команды: IN_PORT или UNLOADED.")
    container = Container.objects.filter(number__iexact=number.strip()).first()
    if container is None:
        raise BotError(f"Контейнер {number} не найден.")
    before = {
        "status": container.status,
        "unload_date": container.unload_date.isoformat() if container.unload_date else None,
        "warehouse_id": container.warehouse_id,
    }
    if container.status == status and (unload_date is None or container.unload_date == unload_date):
        return {
            "before": before,
            "after": before,
            "result": {"outcome": "already"},
            "notifications": [],
        }
    if _rank(container.status) > _rank(status):
        raise BotError(f"Назад нельзя: {container.status} → {status}.")
    preview_kind = "unload" if status == "UNLOADED" or unload_date else None
    notifications = notification_preview(container, preview_kind) if notify and preview_kind else []
    after = dict(before)
    after["status"] = status
    if unload_date:
        after["unload_date"] = unload_date.isoformat()
    warehouse = _warehouse(warehouse_code)
    if warehouse_code and warehouse is None:
        raise BotError(f"Склад {warehouse_code} не найден.")
    if warehouse:
        after["warehouse_id"] = warehouse.pk
    if not apply:
        return {"before": before, "after": after, "result": {"outcome": "would_update"}, "notifications": notifications}

    container.status = status
    if unload_date:
        container.unload_date = unload_date
    if warehouse:
        container.warehouse = warehouse
    validate_status_transition(container, before["status"])
    ctx = mute_client_notifications() if not notify else _null_context()
    with ctx:
        container.save()
    container.refresh_from_db()
    return {
        "before": before,
        "after": {
            "status": container.status,
            "unload_date": container.unload_date.isoformat() if container.unload_date else None,
            "warehouse_id": container.warehouse_id,
        },
        "result": {"outcome": "updated", "id": container.pk},
        "notifications": notifications if notify else [],
    }


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


def transfer_cars(*, vins: list[str], transfer_date, notify: bool = True, apply: bool = False) -> dict:
    from core.models import Car
    from core.services.bot_runtime import mute_client_notifications
    from core.services.car_lifecycle_service import transition_cars

    if transfer_date is None:
        raise BotError("transfer_date обязательна.")
    wanted = []
    seen = set()
    for vin in vins:
        clean = (vin or "").strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            wanted.append(clean)
    found = {car.vin: car for car in Car.objects.select_related("client").filter(vin__in=wanted)}
    missing = [vin for vin in wanted if vin not in found]
    already = [vin for vin, car in found.items() if car.status == "TRANSFERRED"]
    todo = [car for car in found.values() if car.status != "TRANSFERRED"]
    notifications = []
    if notify:
        for car in todo:
            if car.client_id:
                emails = [mask_email(item.email) for item in car.client.notification_emails.all()]
                notifications.append(
                    {
                        "client_id": car.client_id,
                        "client": car.client.name,
                        "vin": car.vin,
                        "kind": "transferred",
                        "emails": emails,
                    }
                )
    result = {"transferred": [], "already": already, "missing": missing}
    if not apply:
        result["transferred"] = [car.vin for car in todo]
        return {"before": {}, "after": result, "result": result, "notifications": notifications}
    ctx = mute_client_notifications() if not notify else _null_context()
    with ctx:
        moved = transition_cars(
            [car.pk for car in todo],
            "TRANSFERRED",
            transfer_date=transfer_date,
            source="BOT",
            note="bot transfer-cars",
            finalize=True,
        )
    vin_by_id = {car.pk: car.vin for car in todo}
    result["transferred"] = [vin_by_id[pk] for pk in moved["updated"] if pk in vin_by_id]
    result["skipped"] = {key: moved[key] for key in moved if key != "updated"}
    closed = complete_requests_for_vins(result["transferred"])
    result["requests_completed"] = closed
    return {"before": {}, "after": result, "result": result, "notifications": notifications if notify else []}


def advance_tr_to(request, target: str) -> list[str]:
    """Пройти к target через IN_PROGRESS, если FSM так требует. История — журнал бота и сами статусы."""
    path = []
    if request.status == target:
        return path
    steps = []
    if target == "COMPLETED" and request.status in {"DRAFT", "SUBMITTED", "ACCEPTED"}:
        steps = ["IN_PROGRESS", "COMPLETED"]
    elif target == "IN_PROGRESS" and request.status in {"DRAFT", "SUBMITTED", "ACCEPTED"}:
        steps = ["IN_PROGRESS"]
    elif request.can_transition(target):
        steps = [target]
    else:
        return path
    for step in steps:
        if request.status == step:
            continue
        if not request.can_transition(step):
            break
        request.status = step
        request.save(update_fields=["status"])
        path.append(step)
        request.refresh_from_db()
    return path


def complete_requests_for_vins(vins: list[str]) -> list[str]:
    from core.models.website import TransportRequest

    done = []
    requests = (
        TransportRequest.objects.filter(cars__vin__in=vins)
        .exclude(status__in=["COMPLETED", "CANCELLED", "DRAFT"])
        .distinct()
    )
    for request in requests:
        if request.cars.exclude(status="TRANSFERRED").exists():
            continue
        path = advance_tr_to(request, "COMPLETED")
        if path:
            done.append(request.number)
    return done


def complete_tr(number: str, *, apply: bool) -> dict:
    from core.models.website import TransportRequest

    request = TransportRequest.objects.filter(number__iexact=number.strip()).first()
    if request is None:
        raise BotError(f"Заявка {number} не найдена.")
    before = request.status
    if not apply:
        return {
            "before": {"status": before},
            "after": {"status": "COMPLETED"},
            "result": {"outcome": "would_complete", "number": request.number},
            "notifications": [],
        }
    path = advance_tr_to(request, "COMPLETED")
    request.refresh_from_db()
    return {
        "before": {"status": before},
        "after": {"status": request.status, "path": path},
        "result": {"outcome": "completed" if request.status == "COMPLETED" else "unchanged", "number": request.number},
        "notifications": [],
    }


def discrepancies() -> dict:
    from core.models import Car, Container
    from core.models.website import TransportRequest

    unloaded_container_cars = list(
        Car.objects.filter(container__status="UNLOADED")
        .exclude(status__in=["UNLOADED", "TRANSFERRED"])
        .values_list("vin", flat=True)[:200]
    )
    handed_not_transferred = list(
        Car.objects.filter(container__transferred_to_neto_at__isnull=False)
        .exclude(status="TRANSFERRED")
        .values_list("vin", flat=True)[:200]
    )
    stale_before = timezone.now() - datetime.timedelta(days=3)
    stale_requests = list(
        TransportRequest.objects.filter(status="SUBMITTED", updated_at__lt=stale_before).values_list(
            "number", flat=True
        )[:200]
    )
    unloaded_without_title = list(
        Car.objects.filter(status="UNLOADED", has_title=False).values_list("vin", flat=True)[:200]
    )
    from core.models.website import NotificationLog

    noted = NotificationLog.objects.filter(notification_type="UNLOADED", container_id__isnull=False).values_list(
        "container_id", flat=True
    )
    arrived_note_without_port = list(
        Container.objects.filter(pk__in=noted, status="FLOATING").values_list("number", flat=True)[:200]
    )
    return {
        "container_unloaded_car_not": unloaded_container_cars,
        "handed_to_neto_car_not_transferred": handed_not_transferred,
        "request_submitted_over_3_days": stale_requests,
        "unloaded_without_title": unloaded_without_title,
        "arrival_note_container_not_in_port": arrived_note_without_port,
    }


def arrived_not_handed() -> dict:
    from core.models import Container

    rows = list(
        Container.objects.filter(status__in=["IN_PORT", "UNLOADED"], transferred_to_neto_at__isnull=True)
        .order_by("number")
        .values_list("number", "status")[:300]
    )
    return {"containers": [{"number": number, "status": status} for number, status in rows]}


def plans_unconfirmed(day) -> dict:
    from core.models import Container

    rows = list(
        Container.objects.filter(planned_unload_date=day, unload_confirmed_at__isnull=True).values_list(
            "number", flat=True
        )
    )
    return {"date": day.isoformat(), "containers": rows}


def mark_handed(number: str, email_id: int | None, *, apply: bool) -> dict:
    from core.models import Container

    container = Container.objects.filter(number__iexact=number.strip()).first()
    if container is None:
        raise BotError(f"Контейнер {number} не найден.")
    before = {"transferred_to_neto_at": container.transferred_to_neto_at}
    if not apply:
        return {"before": before, "after": {"marked": True}, "result": {"outcome": "would_mark"}, "notifications": []}
    container.transferred_to_neto_at = timezone.now()
    if email_id:
        container.transferred_to_neto_email_id = email_id
    container.save(update_fields=["transferred_to_neto_at", "transferred_to_neto_email"])
    return {
        "before": {"at": None},
        "after": {"at": container.transferred_to_neto_at.isoformat()},
        "result": {"outcome": "marked", "number": container.number},
        "notifications": [],
    }


def backfill_transfer_dates(*, apply: bool) -> dict:
    from core.models import Car
    from core.models.status_history import CarStatusHistory, transferred_history_timestamp

    cars = list(Car.objects.filter(status="TRANSFERRED", transfer_date__isnull=True).order_by("id"))
    plan = []
    for car in cars:
        hist = (
            CarStatusHistory.objects.filter(car=car, status="TRANSFERRED")
            .order_by("-changed_at")
            .values_list("changed_at", flat=True)
            .first()
        )
        day = hist.date() if hist else (car.unload_date or timezone.localdate())
        plan.append({"vin": car.vin, "transfer_date": day.isoformat()})
        if apply:
            car.transfer_date = day
            car.save(update_fields=["transfer_date"])
            stamp = transferred_history_timestamp(day)
            if stamp:
                CarStatusHistory.objects.filter(car=car, status="TRANSFERRED").update(changed_at=stamp)
    return {
        "before": {"count": len(plan)},
        "after": {"filled": plan if apply else []},
        "result": {"count": len(plan), "rows": plan},
        "notifications": [],
    }


def mail_health() -> dict:
    from core.models.email import GmailSyncState

    rows = [
        {
            "mailbox": state.user_email,
            "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
            "last_error": state.last_error or "",
        }
        for state in GmailSyncState.objects.all().order_by("user_email")
    ]
    return {"mailboxes": rows}


def revolut_health() -> dict:
    from core.models.banking import BankConnection, BankTransaction

    connections = [
        {
            "id": conn.pk,
            "bank_type": conn.bank_type,
            "last_synced_at": conn.last_synced_at.isoformat() if conn.last_synced_at else None,
            "last_error": getattr(conn, "last_error", "") or "",
        }
        for conn in BankConnection.objects.all().order_by("id")
    ]
    since = timezone.now() - datetime.timedelta(days=7)
    recent = BankTransaction.objects.filter(created_at__gte=since).count()
    return {"connections": connections, "transactions_created_7d": recent}


def morning_snapshot() -> dict:
    return {
        "mail": mail_health(),
        "revolut": revolut_health(),
        "discrepancies": discrepancies(),
        "arrived_not_handed": arrived_not_handed(),
        "t1": t1_deadlines(),
    }


def t1_deadlines() -> dict:
    from core.models import Car
    from core.models.website import TransportRequest

    horizon = timezone.localdate() + datetime.timedelta(days=3)
    cars = list(
        Car.objects.filter(t1_deadline__isnull=False, t1_deadline__lte=horizon)
        .exclude(status="TRANSFERRED")
        .values("vin", "t1_deadline")[:200]
    )
    requests = list(
        TransportRequest.objects.filter(t1_deadline__isnull=False, t1_deadline__lte=horizon)
        .exclude(status__in=["COMPLETED", "CANCELLED"])
        .values("number", "t1_deadline")[:200]
    )
    for row in cars:
        row["t1_deadline"] = row["t1_deadline"].isoformat()
    for row in requests:
        row["t1_deadline"] = row["t1_deadline"].isoformat()
    return {"until": horizon.isoformat(), "cars": cars, "requests": requests}


def store_morning_job():
    from core.models.bots import BotJob

    today = timezone.localdate().isoformat()
    existing = BotJob.objects.filter(job_type="MORNING", payload__day=today).first()
    if existing:
        return existing
    return BotJob.objects.create(
        job_type="MORNING",
        role="chief",
        payload={"day": today, "snapshot": morning_snapshot()},
    )
