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
    """Проформы, которые уже лежат в CRM.

    Основные строки — ``NewInvoice`` серий AV (``PROFORMA``) и AVBLC
    (``PROFORMA_BLC``), любого статуса. Номер в CRM: ``AV-000013``.
    Таблица ``Proforma`` — отдельный журнал бота (``proforma-upsert``),
    она не связана с site.pro. Серия AV, выписанная в site.pro, в CRM
    не импортируется: ``import_sitepro_history`` её только печатает,
    ежедневный sync тянет PARDP.
    """
    from core.models.billing import NewInvoice
    from core.models.bots import Proforma

    invoices = (
        NewInvoice.objects.filter(document_type__in=["PROFORMA", "PROFORMA_BLC"])
        .select_related("recipient_client")
        .order_by("-date", "-id")[:300]
    )
    rows = []
    for item in invoices:
        number = item.number or ""
        rows.append(
            {
                "id": item.pk,
                "number": number,
                "number_compact": number.replace("-", "").replace(" ", ""),
                "source": "crm",
                "document_type": item.document_type,
                "status": item.status,
                "amount": str(item.total),
                "paid_amount": str(item.paid_amount),
                "currency": item.currency,
                "date": item.date.isoformat() if item.date else None,
                "client_id": item.recipient_client_id,
                "client": item.recipient_client.name if item.recipient_client_id else None,
            }
        )
    recorded = [
        {
            "id": item.pk,
            "number": item.number,
            "kind": item.kind,
            "status": item.status,
            "amount": str(item.amount),
            "paid_amount": str(item.paid_amount),
            "client_id": item.client_id,
            "source": "bot",
        }
        for item in Proforma.objects.all().order_by("number")[:300]
    ]
    drafts = [row for row in rows if row["status"] == "DRAFT" and row["number"].startswith("AV")]
    return {
        "proformas": rows,
        "bot_proformas": recorded,
        "draft_av_invoices": [{"number": row["number"], "total": row["amount"]} for row in drafts[:50]],
        "sitepro": (
            "Серия AV в site.pro (номер вида AV000013) в CRM не хранится. "
            "import_sitepro_history печатает такие предложения и не создаёт строки. "
            "Ежедневная синхронизация тянет только PARDP. "
            "Проформа, заведённая в CRM, имеет номер AV-000013."
        ),
    }


_BANK_TX_DEFAULT_LIMIT = 200
_BANK_TX_MAX_LIMIT = 1000


def list_bank_transactions(
    *,
    date_from,
    date_to,
    direction: str = "",
    account: str = "",
    min_amount: str = "",
    max_amount: str = "",
    search: str = "",
    limit: int | None = None,
) -> dict:
    """Банковские операции из таблицы CRM. Revolut API не вызывается."""
    from django.db.models import Q

    from core.models.banking import BankAccount, BankTransaction

    if date_from is None or date_to is None:
        raise BotError("Нужны --date-from и --date-to (ГГГГ-ММ-ДД).")
    if date_from > date_to:
        raise BotError("--date-from позже --date-to.")
    if limit is None:
        limit = _BANK_TX_DEFAULT_LIMIT
    if limit < 1:
        raise BotError("--limit должен быть больше 0.")
    limit = min(limit, _BANK_TX_MAX_LIMIT)

    kind = (direction or "").strip().lower()
    if kind and kind not in {"in", "out"}:
        raise BotError("--direction: in или out.")

    qs = BankTransaction.objects.select_related(
        "connection", "matched_invoice", "matched_transaction"
    ).prefetch_related("proformas")
    qs = qs.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    if kind == "in":
        qs = qs.filter(amount__gt=0)
    elif kind == "out":
        qs = qs.filter(amount__lt=0)
    low = _optional_decimal(min_amount, "--min-amount")
    high = _optional_decimal(max_amount, "--max-amount")
    if low is not None:
        qs = qs.filter(amount__gte=low)
    if high is not None:
        qs = qs.filter(amount__lte=high)
    needle = (search or "").strip()
    if needle:
        qs = qs.filter(
            Q(description__icontains=needle)
            | Q(counterparty_name__icontains=needle)
            | Q(matched_invoice__number__icontains=needle)
        )
    account_note = ""
    token = (account or "").strip()
    if token:
        qs = _filter_bank_account(qs, token)
        account_note = token

    total = qs.count()
    rows = list(qs.order_by("-created_at", "-id")[:limit])
    accounts = BankAccount.objects.filter(connection_id__in={row.connection_id for row in rows})
    by_key: dict[tuple, list] = {}
    for item in accounts:
        by_key.setdefault((item.connection_id, item.currency), []).append(item)

    payload = []
    for row in rows:
        amount = row.amount
        if amount > 0:
            way = "in"
        elif amount < 0:
            way = "out"
        else:
            way = "zero"
        invoice = row.matched_invoice
        proforma_ids = [item.pk for item in row.proformas.all()]
        crm_proforma_id = (
            invoice.pk if invoice is not None and invoice.document_type in {"PROFORMA", "PROFORMA_BLC"} else None
        )
        payload.append(
            {
                "id": row.pk,
                "date": timezone.localtime(row.created_at).date().isoformat(),
                "amount": str(amount),
                "currency": row.currency,
                "direction": way,
                "counterparty": row.counterparty_name,
                "description": row.description,
                "account": _account_payload(row, by_key.get((row.connection_id, row.currency), [])),
                "matched_invoice_id": invoice.pk if invoice is not None else None,
                "matched_invoice_number": invoice.number if invoice is not None else None,
                "matched_proforma_id": crm_proforma_id,
                "matched_proforma_ids": proforma_ids,
                "matched_payment_id": row.matched_transaction_id,
            }
        )
    return {
        "transactions": payload,
        "count": len(payload),
        "total": total,
        "limit": limit,
        "account": account_note,
        "source": "crm",
    }


def _optional_decimal(value: str, flag: str):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception as exc:
        raise BotError(f"{flag} не число: {value}") from exc


def _filter_bank_account(qs, token: str):
    from core.models.banking import BankAccount, BankConnection

    accounts = BankAccount.objects.select_related("connection")
    found = list(accounts.filter(pk=int(token))) if token.isdigit() else []
    if not found:
        found = list(accounts.filter(external_id__iexact=token))
    if not found:
        found = list(accounts.filter(name__iexact=token))
    if not found:
        found = list(accounts.filter(name__icontains=token))
    if found:
        query = None
        from django.db.models import Q

        for item in found:
            piece = Q(connection_id=item.connection_id, currency=item.currency)
            query = piece if query is None else query | piece
        return qs.filter(query)
    connections = BankConnection.objects.filter(name__icontains=token)
    if connections.exists():
        return qs.filter(connection__in=connections)
    raise BotError(f"Счёт {token} не найден.")


def _account_payload(row, accounts: list) -> dict:
    connection_name = row.connection.name if row.connection_id else ""
    if len(accounts) == 1:
        account = accounts[0]
        return {
            "id": account.pk,
            "name": account.name,
            "currency": account.currency,
            "connection": connection_name,
        }
    note = "счёт операции в CRM не сохранён"
    if len(accounts) > 1:
        note = "несколько счетов этой валюты на подключении, id счёта в операции не хранится"
    return {"id": None, "name": connection_name, "currency": row.currency, "connection": connection_name, "note": note}


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
