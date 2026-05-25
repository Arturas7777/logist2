"""
Тесты автосопоставления банковских транзакций с инвойсами
(`core.management.commands.auto_reconcile`).

Покрывает:
- pure-функции `extract_invoice_number` и `fuzzy_match_name`
- интеграционный прогон `reconcile_incoming_payments`
  для всех трёх правил (R1: номер в описании, R2: Daniel Soltys →
  Caromoto-Bel, R3: fuzzy имя + сумма).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.management.commands.auto_reconcile import (
    extract_invoice_number,
    fuzzy_match_name,
    reconcile_incoming_payments,
)
from core.models import Client, Company
from core.models_banking import BankConnection, BankTransaction
from core.models_billing import InvoiceItem, NewInvoice

# ---------------------------------------------------------------------------
# pure: extract_invoice_number
# ---------------------------------------------------------------------------


class TestExtractInvoiceNumber:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("PARDP-000102", "PARDP-000102"),
            ("PARDP 000017", "PARDP-000017"),
            ("PARDP000005", "PARDP-000005"),
            # Pattern требует минимум 3 цифры — 2-цифровой номер игнорируется.
            ("Pardp-44", None),
            ("Pardp-444", "PARDP-000444"),
            ("Оплата INVOICE 000044 за услуги", "PARDP-000044"),
            ("FACTURA SERIA PARDP NO. 000088", "PARDP-000088"),
            ("Random payment without invoice ref", None),
            ("", None),
            (None, None),
        ],
    )
    def test_known_formats(self, text, expected):
        assert extract_invoice_number(text) == expected

    def test_inv_legacy_format(self):
        # INV-YYYYMM-NNNN — legacy формат до перехода на PARDP.
        assert extract_invoice_number("INV-202602-0001") == "INV-202602-0001"
        # Слитный вариант тоже должен парситься.
        assert extract_invoice_number("INV202602 0001") == "INV-202602-0001"


# ---------------------------------------------------------------------------
# pure: fuzzy_match_name
# ---------------------------------------------------------------------------


class TestFuzzyMatchName:
    def test_exact_match(self):
        assert fuzzy_match_name("Caromoto-Bel", "Caromoto-Bel")

    def test_normalized_equality_ignores_punctuation(self):
        assert fuzzy_match_name('"Caromoto-Bel", OOO', "Caromoto Bel OOO")

    def test_word_overlap_two_or_more(self):
        # "Daniel Soltys Pavel" vs "Daniel Soltys" — 2 общих слова → match.
        assert fuzzy_match_name("Daniel Soltys Pavel", "Daniel Soltys")

    def test_single_word_partial_no_match(self):
        # Одного общего слова не достаточно.
        assert not fuzzy_match_name("John Smith", "John Doe")

    def test_empty_strings(self):
        assert not fuzzy_match_name("", "Anything")
        assert not fuzzy_match_name("Anything", "")

    def test_completely_different_names(self):
        assert not fuzzy_match_name("Acme Corp", "Globex Ltd")


# ---------------------------------------------------------------------------
# integration: reconcile_incoming_payments
# ---------------------------------------------------------------------------


@pytest.fixture
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def bank_connection(db, company):
    return BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Test Revolut")


def _make_bank_tx(connection, *, amount, counterparty="", description="", external_id=None):
    return BankTransaction.objects.create(
        connection=connection,
        external_id=external_id or f"ext-{counterparty}-{amount}-{description[:10]}",
        amount=Decimal(str(amount)),
        currency="EUR",
        description=description,
        counterparty_name=counterparty,
        created_at=timezone.now(),
    )


def _make_invoice(company, client, *, number=None, total="100.00", paid="0.00"):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    if number:
        # generate_number() уже назначил автономер, но для теста перепишем
        # на детерминированный — чтобы R1 нашёл по точному совпадению.
        NewInvoice.objects.filter(pk=inv.pk).update(number=number)
        inv.refresh_from_db()
    InvoiceItem.objects.create(
        invoice=inv,
        description="Услуги",
        quantity=Decimal("1"),
        unit_price=Decimal(total),
    )
    inv.calculate_totals()
    inv.paid_amount = Decimal(paid)
    inv.save(update_fields=["subtotal", "total", "paid_amount"])
    return inv


@pytest.mark.django_db
class TestReconcileRule1:
    """R1: номер инвойса найден в описании платежа."""

    def test_match_by_invoice_number_in_description(self, company, bank_connection):
        client = Client.objects.create(name="Customer X")
        inv = _make_invoice(company, client, number="PARDP-000123", total="500.00")
        bt = _make_bank_tx(
            bank_connection,
            amount="500.00",
            counterparty="Some Bank Customer",
            description="Оплата по счёту PARDP-000123 за услуги",
        )

        stats = reconcile_incoming_payments(dry_run=False)

        bt.refresh_from_db()
        inv.refresh_from_db()
        assert stats["rule1"] == 1
        assert stats["total"] == 1
        assert bt.matched_invoice_id == inv.pk
        assert inv.status == "PAID"
        assert inv.paid_amount == Decimal("500.00")
        # Баланс клиента схлопывается через пару TOPUP+PAYMENT.
        client.refresh_from_db()
        assert client.balance == Decimal("0.00")

    def test_amount_tolerance_one_eur(self, company, bank_connection):
        client = Client.objects.create(name="Customer Y")
        _make_invoice(company, client, number="PARDP-000200", total="500.00")
        # Платёж на 500.50 — в допуске 1 EUR.
        _make_bank_tx(
            bank_connection,
            amount="500.50",
            counterparty="Y",
            description="PARDP-000200",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        assert stats["rule1"] == 1

    def test_amount_outside_tolerance_no_match(self, company, bank_connection):
        client = Client.objects.create(name="Customer Z")
        _make_invoice(company, client, number="PARDP-000300", total="500.00")
        # Платёж на 510 — вне допуска и не равен remaining (500).
        _make_bank_tx(
            bank_connection,
            amount="510.00",
            counterparty="Z",
            description="PARDP-000300",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        assert stats["rule1"] == 0
        assert stats["no_match"] == 1

    def test_dry_run_does_not_persist_match(self, company, bank_connection):
        client = Client.objects.create(name="Customer DryRun")
        inv = _make_invoice(company, client, number="PARDP-000401", total="250.00")
        bt = _make_bank_tx(
            bank_connection,
            amount="250.00",
            counterparty="Customer DryRun",
            description="PARDP-000401",
        )
        stats = reconcile_incoming_payments(dry_run=True)
        bt.refresh_from_db()
        inv.refresh_from_db()
        assert stats["rule1"] == 1
        assert bt.matched_invoice_id is None
        assert inv.status == "ISSUED"


@pytest.mark.django_db
class TestReconcileRule2:
    """R2: Daniel Soltys → Caromoto-Bel (по сумме инвойса)."""

    def test_soltys_matches_caromoto_bel_invoice(self, company, bank_connection):
        bel = Client.objects.create(name='"Caromoto-Bel", OOO')
        inv = _make_invoice(company, bel, number="PARDP-000500", total="1000.00")
        _make_bank_tx(
            bank_connection,
            amount="1000.00",
            counterparty="Daniel Soltys",
            description="Some unrelated text without invoice number",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        inv.refresh_from_db()
        assert stats["rule2"] == 1
        assert stats["rule1"] == 0
        assert inv.status == "PAID"

    def test_soltys_alias_with_reversed_name(self, company, bank_connection):
        bel = Client.objects.create(name='"Caromoto-Bel", OOO')
        _make_invoice(company, bel, number="PARDP-000501", total="200.00")
        _make_bank_tx(
            bank_connection,
            amount="200.00",
            counterparty="Soltys Daniel",
            description="Payment",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        assert stats["rule2"] == 1


@pytest.mark.django_db
class TestReconcileRule3:
    """R3: fuzzy имя контрагента + совпадение суммы."""

    def test_fuzzy_match_by_name_and_amount(self, company, bank_connection):
        client = Client.objects.create(name="Acme Logistics GmbH")
        inv = _make_invoice(company, client, number="PARDP-000600", total="750.00")
        _make_bank_tx(
            bank_connection,
            amount="750.00",
            counterparty="Acme Logistics International",
            description="Transfer",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        inv.refresh_from_db()
        assert stats["rule3"] == 1
        assert inv.status == "PAID"

    def test_amount_mismatch_no_match_even_with_name(self, company, bank_connection):
        client = Client.objects.create(name="Acme Logistics GmbH")
        _make_invoice(company, client, number="PARDP-000601", total="750.00")
        _make_bank_tx(
            bank_connection,
            amount="100.00",
            counterparty="Acme Logistics International",
            description="Transfer",
        )
        stats = reconcile_incoming_payments(dry_run=False)
        assert stats["rule3"] == 0
        assert stats["no_match"] == 1
