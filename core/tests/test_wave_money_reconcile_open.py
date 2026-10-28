"""M4: автосверка привязывает поступления только к открытым счетам.

Платёж по уже оплаченному счёту и переплата сверх остатка зачисляются
на аванс клиента (BALANCE_TOPUP), а не теряются «тихо сверенными».
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.management.commands.auto_reconcile import reconcile_incoming_payments
from core.models import Client, Company
from core.models_banking import BankConnection, BankTransaction
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def connection(company):
    return BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Test Revolut")


@pytest.fixture
def client_a():
    return Client.objects.create(name="Ivan Petrov")


def _invoice(company, client, number, total="100.00", status="ISSUED"):
    inv = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=timezone.localdate(),
        status=status,
    )
    NewInvoice.objects.filter(pk=inv.pk).update(number=number)
    inv.refresh_from_db()
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def _bt(connection, amount, description="", counterparty="Ivan Petrov"):
    return BankTransaction.objects.create(
        connection=connection,
        external_id=f"ext-{amount}-{description}-{counterparty}",
        amount=Decimal(amount),
        currency="EUR",
        description=description,
        counterparty_name=counterparty,
        created_at=timezone.now(),
    )


def test_payment_for_already_paid_invoice_goes_to_client_advance(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000777")
    BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    inv.refresh_from_db()
    assert inv.status == "PAID"
    bt = _bt(connection, "100.00", "Оплата PARDP-000777")

    stats = reconcile_incoming_payments()
    bt.refresh_from_db()
    inv.refresh_from_db()
    client_a.refresh_from_db()

    assert stats["already_paid"] == 1
    assert inv.paid_amount == Decimal("100.00")
    assert inv.transactions.filter(type="PAYMENT", status="COMPLETED").count() == 1
    assert bt.matched_transaction.type == "BALANCE_TOPUP"
    assert bt.matched_transaction.to_client_id == client_a.pk
    assert bt.matched_transaction.amount == Decimal("100.00")
    assert "PARDP-000777" in bt.reconciliation_note
    assert "уже оплачен" in bt.reconciliation_note
    assert client_a.balance == Decimal("100.00")
    assert client_a.total_balance == Decimal("100.00")


def test_overpayment_within_tolerance_tops_up_difference(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000778")
    bt = _bt(connection, "100.80", "PARDP-000778")

    stats = reconcile_incoming_payments()
    bt.refresh_from_db()
    inv.refresh_from_db()
    client_a.refresh_from_db()

    assert stats["rule1"] == 1
    assert inv.status == "PAID"
    assert inv.paid_amount == Decimal("100.00")
    assert bt.matched_invoice_id == inv.pk
    assert bt.matched_transaction.type == "PAYMENT"
    assert "0.80" in bt.reconciliation_note
    assert client_a.balance == Decimal("0.80")
    assert client_a.total_balance == Decimal("0.80")


def test_partially_paid_invoice_matches_by_remaining(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000779", total="500.00")
    BillingService.register_incoming_bank_payment(inv, Decimal("200"))
    _bt(connection, "300.00", "PARDP-000779")

    reconcile_incoming_payments()
    inv.refresh_from_db()
    client_a.refresh_from_db()
    assert inv.status == "PAID"
    assert client_a.total_balance == Decimal("0.00")


def test_name_and_amount_rule_ignores_paid_invoices(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000780", total="750.00")
    BillingService.register_incoming_bank_payment(inv, Decimal("750"))
    bt = _bt(connection, "750.00", "Transfer")

    stats = reconcile_incoming_payments()
    bt.refresh_from_db()
    assert stats["total"] == 0
    assert stats["no_match"] == 1
    assert bt.matched_invoice_id is None
    assert bt.matched_transaction_id is None
    assert not bt.is_reconciled


def test_draft_invoice_number_is_not_matched(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000781", status="DRAFT")
    bt = _bt(connection, "100.00", "PARDP-000781")

    stats = reconcile_incoming_payments()
    bt.refresh_from_db()
    assert stats["no_match"] == 1
    assert bt.matched_invoice_id is None
    assert not Transaction.objects.filter(invoice=inv).exists()
    assert not Transaction.objects.filter(to_client=client_a).exists()


def test_dry_run_writes_nothing_for_paid_invoice(company, connection, client_a):
    inv = _invoice(company, client_a, "PARDP-000782")
    BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    bt = _bt(connection, "100.00", "PARDP-000782")
    before = Transaction.objects.count()

    stats = reconcile_incoming_payments(dry_run=True)
    bt.refresh_from_db()
    assert stats["already_paid"] == 1
    assert bt.matched_transaction_id is None
    assert Transaction.objects.count() == before
