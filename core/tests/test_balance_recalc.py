"""
Характеризующие тесты пересчёта баланса сущностей
(`Transaction.recalculate_entity_balance`, дёргается из post_save сигнала
транзакции).

Фиксируют ключевые инварианты ПЕРЕД рефакторингом (сеть безопасности):

- ``Client.balance`` считается по ВСЕМ COMPLETED-транзакциям
  (incoming to_client − outgoing from_client), включая инвойсные.
- ``Company`` / ``Warehouse`` / ``Line`` / ``Carrier`` — баланс считается
  ТОЛЬКО по транзакциям БЕЗ инвойса (авансы/залоги/возвраты без счёта).
  Инвойсные потоки учитываются через open_fact_debt / open_pardp_receivable,
  поэтому в ``balance`` дублироваться не должны.
- ``PENDING``-транзакции в баланс не попадают (только COMPLETED).

Запуск: pytest core/tests/test_balance_recalc.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Carrier, Client, Company, Line, Warehouse
from core.models_billing import InvoiceItem, NewInvoice, Transaction


@pytest.fixture
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def client_a(db):
    return Client.objects.create(name="Balance Client A")


def _issued_invoice(company, client, total="100.00"):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(
        invoice=inv,
        description="Услуги",
        quantity=Decimal("1"),
        unit_price=Decimal(total),
    )
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


# ---------------------------------------------------------------------------
# Client: учитываются ВСЕ COMPLETED-транзакции (в т.ч. инвойсные)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientBalance:
    def test_incoming_minus_outgoing(self, client_a, company):
        # Деньги пришли клиенту (incoming) и ушли от него (outgoing).
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("300"),
            to_client=client_a,
        )
        Transaction.objects.create(
            type="PAYMENT",
            method="BALANCE",
            status="COMPLETED",
            amount=Decimal("100"),
            from_client=client_a,
            to_company=company,
        )
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("200.00")

    def test_invoice_payment_counts_for_client(self, client_a, company):
        # Для клиента инвойсный PAYMENT учитывается в balance (в отличие от Company).
        inv = _issued_invoice(company, client_a, total="150.00")
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("150"),
            from_client=client_a,
            to_company=company,
            invoice=inv,
        )
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("-150.00")

    def test_pending_excluded(self, client_a):
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="PENDING",
            amount=Decimal("500"),
            to_client=client_a,
        )
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# Company / Warehouse / Line / Carrier: только транзакции БЕЗ инвойса
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNonInvoiceEntityBalance:
    def test_company_invoice_payment_does_not_change_balance(self, company, client_a):
        # Инвойсный PAYMENT к компании НЕ должен менять company.balance
        # (учитывается через open_pardp_receivable, не дублируется).
        inv = _issued_invoice(company, client_a, total="100.00")
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("100"),
            from_client=client_a,
            to_company=company,
            invoice=inv,
        )
        company.refresh_from_db()
        assert company.balance == Decimal("0.00")

    def test_company_non_invoice_topup_changes_balance(self, company):
        # А вот TOPUP без инвойса (наличная касса) — учитывается.
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("250"),
            to_company=company,
        )
        company.refresh_from_db()
        assert company.balance == Decimal("250.00")

    def test_warehouse_non_invoice_balance(self):
        wh = Warehouse.objects.create(name="WH-Bal")
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("80"),
            to_warehouse=wh,
        )
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("30"),
            from_warehouse=wh,
        )
        wh.refresh_from_db()
        assert wh.balance == Decimal("50.00")

    def test_line_non_invoice_balance(self):
        line = Line.objects.create(name="Line-Bal")
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("40"),
            to_line=line,
        )
        line.refresh_from_db()
        assert line.balance == Decimal("40.00")

    def test_carrier_non_invoice_balance(self):
        carrier = Carrier.objects.create(name="Carrier-Bal")
        Transaction.objects.create(
            type="BALANCE_TOPUP",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("60"),
            to_carrier=carrier,
        )
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("100"),
            from_carrier=carrier,
        )
        carrier.refresh_from_db()
        assert carrier.balance == Decimal("-40.00")
