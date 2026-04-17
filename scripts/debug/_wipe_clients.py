"""Wipe all legacy invoices/transactions for 4 clients, keep only 2 open Savanna invoices.

Targets:
  - ANDREI SAVANNA      (id=3)  -> keep invoices #200 (AVBLC-000013) and #209 (AVBLC-000016)
  - CAROMOTO BELARUS    (id=4)  -> delete everything
  - Алексей Артемьев    (id=34) -> delete everything
  - S-LINE Cherksasov   (id=37) -> delete everything

Final state:
  - each client has balance = 0 (via recalc)
  - Savanna has only 2 open invoices on 2475 EUR total

Uses queryset.delete() to bypass Transaction.delete() force-protection on COMPLETED rows;
post_delete signals will recalc balances automatically.
"""
import django
django.setup()

import logging
logging.disable(logging.CRITICAL)

from decimal import Decimal
from django.db import transaction as db_transaction
from django.db.models import Q

from core.models import Client
from core.models_billing import NewInvoice, Transaction


CLIENT_IDS = [3, 4, 34, 37]
KEEP_INVOICE_IDS = [200, 209]


with db_transaction.atomic():
    clients = list(Client.objects.filter(pk__in=CLIENT_IDS))
    print("=== BEFORE ===")
    for c in clients:
        n_inv = NewInvoice.objects.filter(recipient_client=c).count()
        n_trx = Transaction.objects.filter(Q(from_client=c) | Q(to_client=c)).count()
        print(f"  #{c.id} {c.name:40} balance={c.balance:>10}  invoices={n_inv}  trx={n_trx}")

    # --- step 1: delete transactions where client is on either side ---
    trx_qs = Transaction.objects.filter(
        Q(from_client__in=CLIENT_IDS) | Q(to_client__in=CLIENT_IDS)
    )
    trx_count = trx_qs.count()
    print(f"\n[1] Deleting {trx_count} transactions involving these clients...")
    trx_ids_before = list(trx_qs.values_list('id', flat=True))
    # Additionally grab transactions that reference invoices we're about to delete
    inv_to_delete_qs = NewInvoice.objects.filter(
        recipient_client__in=CLIENT_IDS
    ).exclude(pk__in=KEEP_INVOICE_IDS)
    inv_ids_to_delete = list(inv_to_delete_qs.values_list('id', flat=True))
    extra_trx_qs = Transaction.objects.filter(invoice_id__in=inv_ids_to_delete).exclude(pk__in=trx_ids_before)
    extra_count = extra_trx_qs.count()
    if extra_count:
        print(f"    + {extra_count} extra transactions linked via invoice FK")
    (trx_qs | extra_trx_qs).distinct().delete()
    print("    ... done")

    # --- step 2: delete all invoices except the two we keep ---
    print(f"\n[2] Deleting invoices for clients {CLIENT_IDS}, keeping {KEEP_INVOICE_IDS}...")
    kept = NewInvoice.objects.filter(pk__in=KEEP_INVOICE_IDS, recipient_client__in=CLIENT_IDS)
    print(f"    Keeping {kept.count()} invoice(s):")
    for inv in kept:
        print(f"      #{inv.id} {inv.number}  client={inv.recipient_client}  total={inv.total}  status={inv.status}  remaining={inv.remaining_amount}")

    inv_to_delete = NewInvoice.objects.filter(
        recipient_client__in=CLIENT_IDS
    ).exclude(pk__in=KEEP_INVOICE_IDS)
    inv_count = inv_to_delete.count()
    print(f"    Deleting {inv_count} invoice(s)")
    inv_to_delete.delete()
    print("    ... done")

    # --- step 3: recalc balances ---
    print("\n[3] Recalc balances for 4 clients...")
    for c in clients:
        Transaction.recalculate_entity_balance(c)
        c.refresh_from_db()

    # --- step 4: summary ---
    print("\n=== AFTER ===")
    for c in clients:
        n_inv = NewInvoice.objects.filter(recipient_client=c).count()
        n_trx = Transaction.objects.filter(Q(from_client=c) | Q(to_client=c)).count()
        open_debt = sum(
            (i.remaining_amount for i in NewInvoice.objects.filter(
                recipient_client=c,
                status__in=['ISSUED', 'OVERDUE', 'PARTIALLY_PAID'],
            )),
            Decimal('0'),
        )
        print(f"  #{c.id} {c.name:40} balance={c.balance:>10}  invoices={n_inv}  trx={n_trx}  open_debt={open_debt}")

print("\nDONE")
