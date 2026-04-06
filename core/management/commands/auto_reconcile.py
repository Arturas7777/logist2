"""
Автоматическое сопоставление банковских транзакций с инвойсами.

Правила (в порядке приоритета):
1. Номер инвойса найден в описании платежа (PARDP-000102, INVOICE 000044, INV-202602-0001)
2. Daniel Soltys -> "Caromoto-Bel", OOO (по сумме)
3. Имя контрагента нечётко совпадает с клиентом + совпадение суммы

Использование:
    python manage.py auto_reconcile --dry-run
    python manage.py auto_reconcile
"""

import re
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import NewInvoice, Transaction
from core.models_banking import BankTransaction


INVOICE_PATTERNS = [
    re.compile(r'PARDP[\s\-]*(\d{3,7})', re.IGNORECASE),
    re.compile(r'INV[\s\-]*(20\d{4}[\s\-]*\d{4})', re.IGNORECASE),
    re.compile(r'INVOICE[\s\-]*(\d{3,7})', re.IGNORECASE),
    re.compile(r'FACTURA[\s\-]*(?:SERIA\s+)?PARDP[\s\-]*(?:NO\.?\s*)?(\d{3,7})', re.IGNORECASE),
]

SOLTYS_ALIASES = ['daniel soltys', 'soltys daniel']
CAROMOTO_BEL_NAME = '"Caromoto-Bel", OOO'


def normalize(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', name.lower())


def extract_invoice_number(text: str) -> str | None:
    """Extract invoice number from bank transaction description."""
    if not text:
        return None
    for pattern in INVOICE_PATTERNS:
        m = pattern.search(text)
        if m:
            num = re.sub(r'\s', '', m.group(1))
            if 'INV' in pattern.pattern.upper() and num.startswith('20'):
                return f'INV-{num[:6]}-{num[6:]}'
            return f'PARDP-{num.zfill(6)}'
    return None


def fuzzy_match_name(bank_name: str, client_name: str) -> bool:
    """Check if bank counterparty name matches a Django client name."""
    bn = normalize(bank_name)
    cn = normalize(client_name)
    if not bn or not cn:
        return False
    if bn == cn:
        return True
    # One contained in the other (for cases like "Ion Tabacari" vs "TABACARI ION")
    bn_parts = set(re.sub(r'[^a-z0-9\s]', '', bank_name.lower()).split())
    cn_parts = set(re.sub(r'[^a-z0-9\s]', '', client_name.lower()).split())
    if len(bn_parts) >= 2 and len(cn_parts) >= 2:
        if bn_parts == cn_parts:
            return True
        overlap = bn_parts & cn_parts
        if len(overlap) >= 2:
            return True
    return False


class Command(BaseCommand):
    help = 'Автоматическое сопоставление банковских транзакций с инвойсами'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('  === DRY RUN ===\n'))

        unreconciled = BankTransaction.objects.filter(
            amount__gt=0,
            matched_invoice__isnull=True,
            matched_transaction__isnull=True,
            reconciliation_skipped=False,
        ).select_related('connection')

        all_invoices = {
            inv.number: inv
            for inv in NewInvoice.objects.exclude(status='CANCELLED').select_related(
                'recipient_client',
            )
        }

        company = Company.get_default()

        caromoto_bel = Client.objects.filter(name__icontains='Caromoto-Bel').first()

        client_invoice_map = {}
        for inv in all_invoices.values():
            if inv.recipient_client_id:
                client_invoice_map.setdefault(inv.recipient_client_id, []).append(inv)

        self.stdout.write(f'  Несопоставленных входящих: {unreconciled.count()}')
        self.stdout.write(f'  Инвойсов в системе: {len(all_invoices)}')
        if caromoto_bel:
            self.stdout.write(f'  Caromoto-Bel client id: {caromoto_bel.pk}')

        matched_rule1 = 0
        matched_rule2 = 0
        matched_rule3 = 0
        already_paid = 0
        no_match = 0

        matched_invoice_ids = set()

        for bt in unreconciled:
            invoice = None
            rule = None

            # Rule 1: invoice number in description
            inv_num = extract_invoice_number(bt.description)
            if inv_num and inv_num in all_invoices:
                candidate = all_invoices[inv_num]
                if candidate.id not in matched_invoice_ids:
                    if abs(bt.amount - candidate.total) <= Decimal('1'):
                        invoice = candidate
                        rule = 1
                    elif abs(bt.amount - (candidate.total - candidate.paid_amount)) <= Decimal('1'):
                        invoice = candidate
                        rule = 1

            # Rule 2: Daniel Soltys -> Caromoto-Bel (match by total amount, including already-paid)
            if not invoice and caromoto_bel:
                cp_lower = (bt.counterparty_name or '').lower().strip()
                if any(alias in cp_lower for alias in SOLTYS_ALIASES):
                    bel_invoices = client_invoice_map.get(caromoto_bel.pk, [])
                    for inv in bel_invoices:
                        if inv.id in matched_invoice_ids:
                            continue
                        if abs(bt.amount - inv.total) <= Decimal('1'):
                            invoice = inv
                            rule = 2
                            break

            # Rule 3: fuzzy name + amount match (including already-paid)
            if not invoice:
                cp_name = bt.counterparty_name or ''
                if cp_name:
                    for client_id, invoices in client_invoice_map.items():
                        for inv in invoices:
                            if inv.id in matched_invoice_ids:
                                continue
                            client_name = inv.recipient_client.name if inv.recipient_client else ''
                            if not fuzzy_match_name(cp_name, client_name):
                                continue
                            if abs(bt.amount - inv.total) <= Decimal('1'):
                                invoice = inv
                                rule = 3
                                break
                        if invoice:
                            break

            if not invoice:
                no_match += 1
                continue

            is_already_paid = (invoice.status == 'PAID' and invoice.paid_amount >= invoice.total)
            if is_already_paid:
                already_paid += 1

            matched_invoice_ids.add(invoice.id)
            label = f'R{rule}'
            recipient = invoice.recipient_client or invoice.recipient
            paid_tag = ' [уже оплачен]' if is_already_paid else ''

            if rule == 1:
                matched_rule1 += 1
            elif rule == 2:
                matched_rule2 += 1
            else:
                matched_rule3 += 1

            self.stdout.write(
                f'    [{label}] {bt.created_at.strftime("%Y-%m-%d")} +{bt.amount} EUR  '
                f'{bt.counterparty_name[:25]:25} -> {invoice.number} ({recipient}){paid_tag}'
            )

            if dry_run:
                continue

            with db_transaction.atomic():
                bt.matched_invoice = invoice
                bt.reconciliation_note = f'Авто-сопоставление (правило {rule})'
                bt.save(update_fields=['matched_invoice', 'reconciliation_note', 'fetched_at'])

                payment_amount = min(bt.amount, invoice.total - invoice.paid_amount)
                if payment_amount > 0:
                    tx = Transaction(
                        type='PAYMENT',
                        method='TRANSFER',
                        status='COMPLETED',
                        amount=payment_amount,
                        currency=invoice.currency or 'EUR',
                        invoice=invoice,
                        from_client=invoice.recipient_client,
                        to_company=company,
                        description=(
                            f'Авто-сопоставление банковского платежа '
                            f'{bt.counterparty_name} -> {invoice.number}'
                        ),
                        date=bt.created_at,
                    )
                    tx.save()

                    bt.matched_transaction = tx
                    bt.save(update_fields=['matched_transaction', 'fetched_at'])

        total = matched_rule1 + matched_rule2 + matched_rule3
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Итог'))
        self.stdout.write(f'  Правило 1 (номер в описании): {matched_rule1}')
        self.stdout.write(f'  Правило 2 (Daniel Soltys -> Caromoto-Bel): {matched_rule2}')
        self.stdout.write(f'  Правило 3 (имя + сумма): {matched_rule3}')
        self.stdout.write(f'  Итого сопоставлено: {total}')
        self.stdout.write(f'  Уже оплачены (пропущено): {already_paid}')
        self.stdout.write(f'  Без совпадения: {no_match}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\n  DRY RUN. Запустите без --dry-run.\n'))
        else:
            self.stdout.write(self.style.SUCCESS('\n  Сопоставление завершено.\n'))
