"""
Тесты ``BillingService.create_payment_for_bank_match`` — единой точки
создания платежа при ручной привязке BankTransaction.matched_invoice
(заменила post_save-сигнал ``auto_create_payment_on_bt_match``).

Запуск: pytest core/tests/test_bank_match_payment.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Client, Company
from core.models_banking import BankConnection, BankTransaction
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService


@pytest.fixture
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def bank_connection(db, company):
    return BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Test Revolut")


def _bt(connection, amount, **extra):
    return BankTransaction.objects.create(
        connection=connection,
        external_id=f"ext-{amount}-{extra.get('counterparty_name', '')}",
        amount=Decimal(str(amount)),
        currency="EUR",
        created_at=timezone.now(),
        **extra,
    )


def _outgoing_invoice(company, client, total="300.00"):
    inv = NewInvoice.objects.create(
        issuer_company=company, recipient_client=client,
        date=timezone.now().date(), status="ISSUED",
    )
    InvoiceItem.objects.create(
        invoice=inv, description="Услуги", quantity=Decimal("1"), unit_price=Decimal(total),
    )
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


@pytest.mark.django_db
class TestCreatePaymentForBankMatch:
    def test_client_payment_creates_topup_payment_pair(self, company, bank_connection):
        client = Client.objects.create(name="Pair Client")
        inv = _outgoing_invoice(company, client, total="300.00")
        bt = _bt(bank_connection, "300.00", counterparty_name="Pair Client")
        bt.matched_invoice = inv
        bt.save(update_fields=["matched_invoice"])

        tx = BillingService.create_payment_for_bank_match(bt.pk)

        assert tx is not None
        bt.refresh_from_db()
        inv.refresh_from_db()
        client.refresh_from_db()
        assert bt.matched_transaction_id == tx.pk
        assert inv.status == "PAID"
        assert inv.paid_amount == Decimal("300.00")
        # Пара TOPUP+PAYMENT: баланс клиента схлопывается в 0.
        assert client.balance == Decimal("0.00")
        assert Transaction.objects.filter(type="BALANCE_TOPUP", to_client=client).count() == 1

    def test_idempotent_when_already_matched(self, company, bank_connection):
        client = Client.objects.create(name="Idem Client")
        inv = _outgoing_invoice(company, client)
        bt = _bt(bank_connection, "300.00")
        bt.matched_invoice = inv
        bt.save(update_fields=["matched_invoice"])

        first = BillingService.create_payment_for_bank_match(bt.pk)
        second = BillingService.create_payment_for_bank_match(bt.pk)

        assert first is not None
        assert second is None
        assert Transaction.objects.filter(type="PAYMENT", invoice=inv).count() == 1

    def test_no_payment_without_matched_invoice(self, bank_connection):
        bt = _bt(bank_connection, "100.00")
        assert BillingService.create_payment_for_bank_match(bt.pk) is None

    def test_no_payment_for_cancelled_invoice(self, company, bank_connection):
        client = Client.objects.create(name="Cancel Client")
        inv = _outgoing_invoice(company, client)
        NewInvoice.objects.filter(pk=inv.pk).update(status="CANCELLED")
        bt = _bt(bank_connection, "300.00")
        bt.matched_invoice = inv
        bt.save(update_fields=["matched_invoice"])

        assert BillingService.create_payment_for_bank_match(bt.pk) is None

    def test_direction_mismatch_skipped(self, company, bank_connection):
        # Исходящий банковский платёж (минус) против нашего исходящего
        # инвойса — направление не совпадает, платёж не создаётся.
        client = Client.objects.create(name="Dir Client")
        inv = _outgoing_invoice(company, client)
        bt = _bt(bank_connection, "-300.00")
        bt.matched_invoice = inv
        bt.save(update_fields=["matched_invoice"])

        assert BillingService.create_payment_for_bank_match(bt.pk) is None

    def test_linking_alone_does_not_create_payment(self, company, bank_connection):
        # Сигнала больше нет: просто save() с matched_invoice не создаёт
        # платёж — только явный вызов сервиса.
        client = Client.objects.create(name="NoSig Client")
        inv = _outgoing_invoice(company, client)
        bt = _bt(bank_connection, "300.00")
        bt.matched_invoice = inv
        bt.save(update_fields=["matched_invoice"])

        assert not Transaction.objects.filter(invoice=inv).exists()
        inv.refresh_from_db()
        assert inv.status == "ISSUED"
