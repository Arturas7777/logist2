"""
Автоматическое сопоставление банковских транзакций с инвойсами.

Правила (в порядке приоритета):
1. Номер инвойса найден в описании платежа (PARDP-000102, INVOICE 000044, INV-202602-0001)
2. Daniel Soltys -> "Caromoto-Bel", OOO (по сумме)
3. Имя контрагента нечётко совпадает с клиентом + совпадение суммы
4. (B9) Контрагент однозначно определён (правила 2/3), а сумма платежа равна
   сумме НЕСКОЛЬКИХ открытых инвойсов клиента (жадно по дате, допуск 1 €) —
   платёж разносится по инвойсам через ``BillingService.allocate_bank_transaction``.

Для несопоставленных операций в ``reconciliation_note`` пишется причина
(«нет номера инвойса…», «сумма не совпала…», «контрагент не распознан»,
«валюта не EUR») — это и есть колонка «почему не сматчилось» в админке.

Использование:
    python manage.py auto_reconcile --dry-run
    python manage.py auto_reconcile
"""

import logging
import re
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from core.models import Client
from core.models_banking import BankTransaction
from core.models_billing import NewInvoice

logger = logging.getLogger(__name__)

INVOICE_PATTERNS = [
    re.compile(r"PARDP[\s\-]*(\d{3,7})", re.IGNORECASE),
    re.compile(r"INV[\s\-]*(20\d{4}[\s\-]*\d{4})", re.IGNORECASE),
    re.compile(r"INVOICE[\s\-]*(\d{3,7})", re.IGNORECASE),
    re.compile(r"FACTURA[\s\-]*(?:SERIA\s+)?PARDP[\s\-]*(?:NO\.?\s*)?(\d{3,7})", re.IGNORECASE),
]

SOLTYS_ALIASES = ["daniel soltys", "soltys daniel"]


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def extract_invoice_number(text: str) -> str | None:
    """Extract invoice number from bank transaction description."""
    if not text:
        return None
    for pattern in INVOICE_PATTERNS:
        m = pattern.search(text)
        if m:
            # Нормализуем: убираем И пробелы, И дефисы — иначе для legacy-формата
            # INV-202602-0001 получали двойной дефис ("INV-202602--0001"),
            # который не совпадал с реальным номером инвойса в БД.
            num = re.sub(r"[\s\-]", "", m.group(1))
            if "INV" in pattern.pattern.upper() and num.startswith("20"):
                return f"INV-{num[:6]}-{num[6:]}"
            return f"PARDP-{num.zfill(6)}"
    return None


def fuzzy_match_name(bank_name: str, client_name: str) -> bool:
    """Check if bank counterparty name matches a Django client name."""
    bn = normalize(bank_name)
    cn = normalize(client_name)
    if not bn or not cn:
        return False
    if bn == cn:
        return True
    bn_parts = set(re.sub(r"[^a-z0-9\s]", "", bank_name.lower()).split())
    cn_parts = set(re.sub(r"[^a-z0-9\s]", "", client_name.lower()).split())
    if len(bn_parts) >= 2 and len(cn_parts) >= 2:
        if bn_parts == cn_parts:
            return True
        overlap = bn_parts & cn_parts
        if len(overlap) >= 2:
            return True
    return False


def _identify_client(bt, caromoto_bel, client_invoice_map):
    """Однозначный клиент по контрагенту BT (правила 2/3) или ``None``.

    Soltys → Caromoto-Bel; иначе нечёткое совпадение имени ровно с одним
    клиентом, у которого есть инвойсы. Два и более кандидата — неоднозначно.
    """
    cp_name = (bt.counterparty_name or "").strip()
    if not cp_name:
        return None
    if caromoto_bel and any(alias in cp_name.lower() for alias in SOLTYS_ALIASES):
        return caromoto_bel
    candidates = {}
    for client_id, invoices in client_invoice_map.items():
        client = next((inv.recipient_client for inv in invoices if inv.recipient_client), None)
        if client and fuzzy_match_name(cp_name, client.name):
            candidates[client_id] = client
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None


def plan_multi_invoice_allocation(bank_amount, open_invoices, tolerance=None):
    """Правило 4: жадно по дате набрать открытые инвойсы на сумму платежа.

    Возвращает список ``(invoice, amount)`` или ``None``, если никакой
    префикс по дате не даёт сумму остатков в пределах ``tolerance`` от
    платежа. Нужны минимум два инвойса — один закрывается правилами 1–3.
    Если набранная сумма чуть больше платежа (в пределах допуска) —
    последняя аллокация урезается; если меньше — хвост уйдёт в TOPUP.
    """
    from core.services.billing_service import BillingService

    tolerance = BillingService.ALLOCATION_TOLERANCE if tolerance is None else tolerance
    bank_amount = BillingService.quantize(bank_amount)
    ordered = sorted(
        (inv for inv in open_invoices if inv.remaining_amount > 0),
        key=lambda i: (i.date, i.pk),
    )
    cumulative = Decimal("0.00")
    picked = []
    for inv in ordered:
        remaining = BillingService.quantize(inv.remaining_amount)
        picked.append((inv, remaining))
        cumulative += remaining
        if abs(cumulative - bank_amount) <= tolerance:
            if len(picked) < 2:
                return None
            if cumulative > bank_amount:
                last_inv, last_amount = picked[-1]
                picked[-1] = (last_inv, last_amount - (cumulative - bank_amount))
            return picked
        if cumulative > bank_amount + tolerance:
            return None
    return None


def unmatched_reason(bt, all_invoices, client) -> str:
    """Почему входящая операция не сопоставлена (для ``reconciliation_note``)."""
    if (bt.currency or "EUR").upper() != "EUR":
        return f"Не сопоставлено: валюта {bt.currency}, не EUR"
    inv_num = extract_invoice_number(bt.description)
    if inv_num:
        candidate = all_invoices.get(inv_num)
        if candidate is None:
            return f"Не сопоставлено: номер {inv_num} в назначении не найден в системе"
        remaining = candidate.total - candidate.paid_amount
        return f"Не сопоставлено: сумма не совпала — платёж {bt.amount}, {inv_num} остаток {remaining}"
    if client is None:
        return "Не сопоставлено: нет номера инвойса в назначении, контрагент не распознан"
    return f"Не сопоставлено: нет номера инвойса; у {client.name} нет открытых инвойсов на {bt.amount}"


def _set_unmatched_note(bt, reason, dry_run):
    if dry_run or bt.reconciliation_note == reason:
        return
    bt.reconciliation_note = reason[:255]
    bt.save(update_fields=["reconciliation_note", "fetched_at"])


def reconcile_incoming_payments(dry_run=False):
    """
    Match incoming bank transactions (amount > 0) to outgoing invoices (to clients).
    Returns dict with counts: {rule1, rule2, rule3, rule4, already_paid, no_match, total}.
    """
    from core.mixins import OPEN_INVOICE_STATUSES
    from core.services.billing_service import BillingService

    unreconciled = BankTransaction.objects.filter(
        amount__gt=0,
        matched_invoice__isnull=True,
        matched_transaction__isnull=True,
        reconciliation_skipped=False,
    ).select_related("connection")

    all_invoices = {
        inv.number: inv
        for inv in NewInvoice.objects.exclude(status="CANCELLED").select_related(
            "recipient_client",
        )
    }

    caromoto_bel = Client.objects.filter(name__icontains="Caromoto-Bel").first()

    client_invoice_map = {}
    for inv in all_invoices.values():
        if inv.recipient_client_id:
            client_invoice_map.setdefault(inv.recipient_client_id, []).append(inv)

    stats = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "already_paid": 0, "no_match": 0}
    matched_invoice_ids = set()
    matches = []

    for bt in unreconciled:
        invoice = None
        rule = None

        if (bt.currency or "EUR").upper() != "EUR":
            stats["no_match"] += 1
            _set_unmatched_note(bt, unmatched_reason(bt, all_invoices, None), dry_run)
            continue

        inv_num = extract_invoice_number(bt.description)
        if inv_num and inv_num in all_invoices:
            candidate = all_invoices[inv_num]
            if candidate.id not in matched_invoice_ids:
                if abs(bt.amount - candidate.total) <= Decimal("1"):
                    invoice = candidate
                    rule = 1
                elif abs(bt.amount - (candidate.total - candidate.paid_amount)) <= Decimal("1"):
                    invoice = candidate
                    rule = 1

        if not invoice and caromoto_bel:
            cp_lower = (bt.counterparty_name or "").lower().strip()
            if any(alias in cp_lower for alias in SOLTYS_ALIASES):
                bel_invoices = client_invoice_map.get(caromoto_bel.pk, [])
                for inv in bel_invoices:
                    if inv.id in matched_invoice_ids:
                        continue
                    if abs(bt.amount - inv.total) <= Decimal("1"):
                        invoice = inv
                        rule = 2
                        break

        if not invoice:
            cp_name = bt.counterparty_name or ""
            if cp_name:
                for client_id, invoices in client_invoice_map.items():
                    for inv in invoices:
                        if inv.id in matched_invoice_ids:
                            continue
                        client_name = inv.recipient_client.name if inv.recipient_client else ""
                        if not fuzzy_match_name(cp_name, client_name):
                            continue
                        if abs(bt.amount - inv.total) <= Decimal("1"):
                            invoice = inv
                            rule = 3
                            break
                    if invoice:
                        break

        identified_client = None
        if not invoice:
            # Правило 4: объединённый платёж по нескольким инвойсам клиента.
            identified_client = _identify_client(bt, caromoto_bel, client_invoice_map)
            if identified_client is not None:
                open_invoices = [
                    inv
                    for inv in client_invoice_map.get(identified_client.pk, [])
                    if inv.status in OPEN_INVOICE_STATUSES and inv.id not in matched_invoice_ids
                ]
                plan = plan_multi_invoice_allocation(bt.amount, open_invoices)
                if plan:
                    stats["rule4"] += 1
                    matched_invoice_ids.update(inv.id for inv, _ in plan)
                    logger.info(
                        "[reconcile_incoming] R4: %s +%s EUR %s -> %s (%s)",
                        bt.created_at.strftime("%Y-%m-%d"),
                        bt.amount,
                        (bt.counterparty_name or "")[:25],
                        ", ".join(f"{inv.number} {amount}" for inv, amount in plan),
                        identified_client,
                    )
                    if not dry_run:
                        try:
                            BillingService.allocate_bank_transaction(bt, plan)
                        except ValueError as exc:
                            logger.warning("[reconcile_incoming] R4 BT %s не разнесён: %s", bt.pk, exc)
                            stats["rule4"] -= 1
                            stats["no_match"] += 1
                            _set_unmatched_note(bt, f"Не сопоставлено: правило 4 — {exc}", dry_run)
                    continue

        if not invoice:
            stats["no_match"] += 1
            _set_unmatched_note(bt, unmatched_reason(bt, all_invoices, identified_client), dry_run)
            continue

        is_already_paid = invoice.status == "PAID" and invoice.paid_amount >= invoice.total
        if is_already_paid:
            stats["already_paid"] += 1

        matched_invoice_ids.add(invoice.id)
        stats[f"rule{rule}"] += 1
        matches.append((bt, invoice, rule))

        recipient = invoice.recipient_client or invoice.recipient
        logger.info(
            "[reconcile_incoming] R%d: %s +%s EUR %s -> %s (%s)%s",
            rule,
            bt.created_at.strftime("%Y-%m-%d"),
            bt.amount,
            (bt.counterparty_name or "")[:25],
            invoice.number,
            recipient,
            " [already paid]" if is_already_paid else "",
        )

        if dry_run:
            continue

        with db_transaction.atomic():
            invoice = NewInvoice.objects.select_for_update().get(pk=invoice.pk)

            bt.matched_invoice = invoice
            bt.reconciliation_note = f"Авто-сопоставление (правило {rule})"
            bt.save(update_fields=["matched_invoice", "reconciliation_note", "fetched_at"])

            payment_amount = min(bt.amount, invoice.total - invoice.paid_amount)
            if payment_amount > 0:
                # Единая точка регистрации входящего банковского платежа:
                # для клиентов — пара BALANCE_TOPUP + PAYMENT(BALANCE), чтобы
                # авансовый счёт клиента не уходил в минус; для остальных —
                # одиночный PAYMENT(TRANSFER). См. BillingService.
                from core.services.billing_service import BillingService

                tx = BillingService.register_incoming_bank_payment(
                    invoice,
                    payment_amount,
                    date=bt.created_at,
                    description=(f"Авто-сопоставление банковского платежа {bt.counterparty_name} -> {invoice.number}"),
                )

                if tx is not None:
                    bt.matched_transaction = tx
                    bt.save(update_fields=["matched_transaction", "fetched_at"])

    stats["total"] = stats["rule1"] + stats["rule2"] + stats["rule3"] + stats["rule4"]
    return stats


class Command(BaseCommand):
    help = "Автоматическое сопоставление банковских транзакций с инвойсами"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding="utf-8")
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("  === DRY RUN ===\n"))

        stats = reconcile_incoming_payments(dry_run=dry_run)

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Итог"))
        self.stdout.write(f"  Правило 1 (номер в описании): {stats['rule1']}")
        self.stdout.write(f"  Правило 2 (Daniel Soltys -> Caromoto-Bel): {stats['rule2']}")
        self.stdout.write(f"  Правило 3 (имя + сумма): {stats['rule3']}")
        self.stdout.write(f"  Правило 4 (один платёж → несколько инвойсов): {stats['rule4']}")
        self.stdout.write(f"  Итого сопоставлено: {stats['total']}")
        self.stdout.write(f"  Уже оплачены (пропущено): {stats['already_paid']}")
        self.stdout.write(f"  Без совпадения: {stats['no_match']}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n  DRY RUN. Запустите без --dry-run.\n"))
        else:
            self.stdout.write(self.style.SUCCESS("\n  Сопоставление завершено.\n"))
