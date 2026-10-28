"""Проформы, прибыль, сверка счёта Neto. Статус счёта CRM бот сам не пишет."""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_date

from core.services.bot_runtime import BotError


def _as_decimal(value) -> Decimal:
    return Decimal(str(value or "0"))


def upsert_proforma(*, number: str, kind: str, amount, client_id=None, due_date=None, note="", apply: bool) -> dict:
    from core.models.bots import Proforma

    number = (number or "").strip()
    if not number:
        raise BotError("Нужен номер проформы.")
    existing = Proforma.objects.filter(number=number).first()
    before = {"paid_amount": str(existing.paid_amount), "status": existing.status} if existing else {}
    if not apply:
        return {
            "before": before,
            "after": {"number": number, "amount": str(amount)},
            "result": {"outcome": "would_upsert"},
            "notifications": [],
        }
    proforma, _created = Proforma.objects.get_or_create(
        number=number,
        defaults={
            "kind": kind or Proforma.KIND_UNOFFICIAL,
            "amount": _as_decimal(amount),
            "client_id": client_id,
            "due_date": due_date,
            "note": note or "",
        },
    )
    if not _created:
        proforma.amount = _as_decimal(amount)
        proforma.kind = kind or proforma.kind
        if client_id:
            proforma.client_id = client_id
        if due_date:
            proforma.due_date = due_date
        if note:
            proforma.note = note
    proforma.refresh_status()
    proforma.save()
    return {
        "before": before,
        "after": {"number": proforma.number, "status": proforma.status, "paid_amount": str(proforma.paid_amount)},
        "result": {"outcome": "upserted", "id": proforma.pk},
        "notifications": [],
    }


def list_proformas() -> dict:
    from core.models.billing import NewInvoice
    from core.models.bots import Proforma

    rows = [
        {
            "number": item.number,
            "kind": item.kind,
            "status": item.status,
            "amount": str(item.amount),
            "paid_amount": str(item.paid_amount),
            "client_id": item.client_id,
        }
        for item in Proforma.objects.all().order_by("number")[:300]
    ]
    drafts = list(NewInvoice.objects.filter(status="DRAFT", number__startswith="AV").values("number", "total")[:50])
    for row in drafts:
        row["total"] = str(row["total"])
    return {"proformas": rows, "draft_av_invoices": drafts}


def match_proforma(*, number: str, bank_transaction_id: int, confirm: bool) -> dict:
    from core.models.banking import BankTransaction
    from core.models.bots import Proforma

    proforma = Proforma.objects.filter(number=number).first()
    if proforma is None:
        raise BotError(f"Проформа {number} не найдена.")
    if not confirm:
        preview = {"proforma": proforma.number, "bank_transaction_id": bank_transaction_id}
        return {
            "before": {"paid_amount": str(proforma.paid_amount)},
            "after": preview,
            "result": {"outcome": "proposed"},
            "notifications": [],
        }
    bank = BankTransaction.objects.filter(pk=bank_transaction_id).first()
    if bank is None:
        raise BotError(f"Банковская операция {bank_transaction_id} не найдена.")
    remaining = proforma.amount - proforma.paid_amount
    piece = min(remaining, abs(_as_decimal(bank.amount)))
    if piece <= 0:
        raise BotError("Прибавлять нечего: проформа уже закрыта или сумма платежа 0.")
    proforma.paid_amount += piece
    proforma.refresh_status()
    proforma.save()
    proforma.bank_transactions.add(bank)
    return {
        "before": {},
        "after": {"paid_amount": str(proforma.paid_amount), "status": proforma.status},
        "result": {"outcome": "matched"},
        "notifications": [],
    }


def propose_invoice_payment(*, bank_transaction_id: int, invoice_id: int, amount, confirm: bool, apply: bool) -> dict:
    from core.models.banking import BankTransaction
    from core.models.billing import NewInvoice
    from core.models.bots import BotJob

    bank = BankTransaction.objects.filter(pk=bank_transaction_id).first()
    invoice = NewInvoice.objects.filter(pk=invoice_id).first()
    if bank is None or invoice is None:
        raise BotError("Платёж или счёт не найден.")
    payload = {
        "bank_transaction_id": bank.pk,
        "invoice_id": invoice.pk,
        "invoice_number": invoice.number,
        "amount": str(amount),
    }
    if not apply:
        return {"before": {}, "after": payload, "result": {"outcome": "would_propose"}, "notifications": []}
    if not confirm:
        job = BotJob.objects.create(job_type="INVOICE_PAYMENT", role="accountant", payload=payload)
        return {
            "before": {},
            "after": payload,
            "result": {"outcome": "proposed", "job_id": job.pk},
            "notifications": [],
        }
    from core.services.billing_service import BillingService

    BillingService.allocate_bank_transaction(bank, [(invoice, _as_decimal(amount))], note_prefix="Бот, подтверждено")
    return {"before": {}, "after": payload, "result": {"outcome": "allocated"}, "notifications": []}


def profit_report(date_from, date_to) -> dict:
    from core.models import Car
    from core.models.bots import LedgerEntry
    from core.models.services import CarService
    from core.service_codes import is_storage_service

    cars = Car.objects.filter(status="TRANSFERRED", transfer_date__gte=date_from, transfer_date__lte=date_to)
    income = Decimal("0")
    cost = Decimal("0")
    count = 0
    for car in cars.iterator():
        count += 1
        for service in CarService.objects.filter(car=car):
            income += _as_decimal(service.invoice_price)
            if not is_storage_service(service):
                cost += _as_decimal(service.final_price)
            else:
                cost += _as_decimal(getattr(service, "final_price", 0))
    extra_income = Decimal("0")
    extra_expense = Decimal("0")
    for entry in LedgerEntry.objects.filter(occurred_on__gte=date_from, occurred_on__lte=date_to):
        if entry.kind == LedgerEntry.KIND_INCOME:
            extra_income += entry.amount
        else:
            extra_expense += entry.amount
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "cars": count,
        "invoice_total": str(income),
        "cost_total": str(cost),
        "car_profit": str(income - cost),
        "off_car_income": str(extra_income),
        "off_car_expense": str(extra_expense),
        "profit": str(income - cost + extra_income - extra_expense),
    }


def add_ledger_entry(
    *, kind: str, amount, occurred_on, description: str, category_id=None, car_id=None, apply: bool
) -> dict:
    from core.models.bots import LedgerEntry

    if kind not in {LedgerEntry.KIND_INCOME, LedgerEntry.KIND_EXPENSE}:
        raise BotError("kind: INCOME или EXPENSE.")
    payload = {"kind": kind, "amount": str(amount), "description": description, "occurred_on": occurred_on.isoformat()}
    if not apply:
        return {"before": {}, "after": payload, "result": {"outcome": "would_add"}, "notifications": []}
    entry = LedgerEntry.objects.create(
        kind=kind,
        amount=_as_decimal(amount),
        occurred_on=occurred_on,
        description=description[:255],
        category_id=category_id,
        car_id=car_id,
    )
    return {"before": {}, "after": payload, "result": {"outcome": "added", "id": entry.pk}, "notifications": []}


def reconcile_neto(lines: list[dict]) -> dict:
    """Сверка строк счёта Neto. Ничего не пишет.

    Ожидание: разгрузка 140 € или 130 €; портовые только если склад NETO/WAREHOUSE
    в имени не пустой... TZ: портовые только при WAREHOUSE (ths_payer). Хранение:
    5 бесплатных дней, дальше 5 €/день.
    """
    from core.models import Car

    mismatches = []
    for line in lines:
        vin = (line.get("vin") or "").strip().upper()
        kind = (line.get("kind") or "").strip().lower()
        billed = _as_decimal(line.get("amount"))
        car = Car.objects.select_related("container", "warehouse").filter(vin=vin).first()
        if car is None:
            mismatches.append({"vin": vin, "reason": "нет в CRM", "billed": str(billed)})
            continue
        expected = _expected_neto(car, kind)
        if expected is None:
            mismatches.append({"vin": vin, "kind": kind, "reason": "неизвестный вид строки", "billed": str(billed)})
            continue
        if billed != expected:
            mismatches.append(
                {"vin": vin, "kind": kind, "billed": str(billed), "expected": str(expected), "reason": "сумма"}
            )
    return {"checked": len(lines), "mismatches": mismatches}


def _expected_neto(car, kind: str):
    if kind in {"unload", "разгрузка"}:
        return Decimal("140") if (car.warehouse and "atlantic" in car.warehouse.name.lower()) else Decimal("130")
    if kind in {"port", "портовые"}:
        payer = car.container.ths_payer if car.container_id else ""
        if payer != "WAREHOUSE":
            return Decimal("0")
        return Decimal("0")
    if kind in {"storage", "хранение"}:
        if not car.unload_date:
            return Decimal("0")
        end = car.transfer_date or timezone.localdate()
        days = max((end - car.unload_date).days, 0)
        billable = max(days - 5, 0)
        return Decimal(billable) * Decimal("5")
    return None


def draft_av_summary() -> dict:
    from django.db.models import Sum

    from core.models.billing import NewInvoice

    qs = NewInvoice.objects.filter(status="DRAFT", number__startswith="AV")
    total = qs.aggregate(total=Sum("total"))["total"] or Decimal("0")
    return {"count": qs.count(), "total": str(total)}


def parse_day(value: str):
    day = parse_date(value or "")
    if day is None:
        raise BotError(f"Дата не разобрана: {value}")
    return day
