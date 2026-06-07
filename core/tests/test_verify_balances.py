"""
Тесты ревизора балансов (фаза 4, PR 4.3) и канонической формулы
``Transaction.expected_entity_balance``.

Главная регрессия: контрагент (company/warehouse/line/carrier) с
инвойсным COMPLETED-платежом НЕ должен попадать в расхождения — его
balance считается только по транзакциям без инвойса. Раньше
``_collect_balance_mismatches`` не учитывал этот фильтр и давал ложные
расхождения, а ``--fix`` мог затереть верный баланс.

Запуск: pytest core/tests/test_verify_balances.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Client, Company, Warehouse
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.tasks import _collect_balance_mismatches


@pytest.fixture
def company(db):
    return Company.objects.create(name="VB Company")


@pytest.fixture
def client_a(db):
    return Client.objects.create(name="VB Client")


def _issued_invoice(company, client, total="100.00"):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(
        invoice=inv, description="Услуги",
        quantity=Decimal("1"), unit_price=Decimal(total),
    )
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


@pytest.mark.django_db
class TestExpectedEntityBalance:
    def test_partner_excludes_invoice_transactions(self, company, client_a):
        inv = _issued_invoice(company, client_a, total="100.00")
        Transaction.objects.create(
            type="PAYMENT", method="CASH", status="COMPLETED",
            amount=Decimal("100"), from_client=client_a, to_company=company,
            invoice=inv,
        )
        # Инвойсный платёж не входит в balance контрагента.
        assert Transaction.expected_entity_balance(company) == Decimal("0.00")

    def test_partner_includes_non_invoice_transactions(self, company):
        Transaction.objects.create(
            type="BALANCE_TOPUP", method="CASH", status="COMPLETED",
            amount=Decimal("250"), to_company=company,
        )
        assert Transaction.expected_entity_balance(company) == Decimal("250.00")

    def test_client_includes_invoice_transactions(self, company, client_a):
        inv = _issued_invoice(company, client_a, total="150.00")
        Transaction.objects.create(
            type="PAYMENT", method="CASH", status="COMPLETED",
            amount=Decimal("150"), from_client=client_a, to_company=company,
            invoice=inv,
        )
        # Для клиента инвойсный платёж учитывается.
        assert Transaction.expected_entity_balance(client_a) == Decimal("-150.00")


@pytest.mark.django_db
class TestCollectMismatchesNoFalsePositives:
    def test_partner_with_invoice_payment_not_flagged(self, company, client_a):
        inv = _issued_invoice(company, client_a, total="100.00")
        Transaction.objects.create(
            type="PAYMENT", method="CASH", status="COMPLETED",
            amount=Decimal("100"), from_client=client_a, to_company=company,
            invoice=inv,
        )
        company.refresh_from_db()
        assert company.balance == Decimal("0.00")

        balance_mismatches, _ = _collect_balance_mismatches()
        flagged = [m for m in balance_mismatches if m['model'] is Company and m['pk'] == company.pk]
        assert flagged == [], (
            "Контрагент с инвойсным платежом не должен попадать в расхождения "
            f"(получили: {flagged})"
        )


@pytest.mark.django_db
class TestVerifyBalancesCommand:
    def test_fix_restores_corrupted_balance(self, client_a, company):
        Transaction.objects.create(
            type="BALANCE_TOPUP", method="CASH", status="COMPLETED",
            amount=Decimal("200"), to_client=client_a,
        )
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("200.00")

        # Портим баланс в обход сигналов.
        Client.objects.filter(pk=client_a.pk).update(balance=Decimal("999.00"))

        call_command("verify_balances", "--fix", "--no-invoices")

        client_a.refresh_from_db()
        assert client_a.balance == Decimal("200.00")

    def test_report_only_does_not_change_balance(self, company):
        wh = Warehouse.objects.create(name="VB-WH")
        Warehouse.objects.filter(pk=wh.pk).update(balance=Decimal("123.00"))

        call_command("verify_balances", "--no-invoices")

        wh.refresh_from_db()
        # Без --fix баланс не трогаем, даже если он расходится.
        assert wh.balance == Decimal("123.00")
