"""B7: кредит-нота уменьшает долг по PARDP и не двигает баланс клиента."""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def company():
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def client_a():
    return Client.objects.create(name="Credit Client")


def _pardp(company, client, total="100.00"):
    inv = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def test_full_credit_note_closes_invoice_without_touching_balance(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    result = BillingService.create_credit_note(inv, amount=Decimal("100"), reason="Ошибка в счёте")
    inv.refresh_from_db()
    client_a.refresh_from_db()
    note = result["credit_note"]
    assert note.document_type == "CREDIT_NOTE"
    assert note.credited_invoice_id == inv.pk
    assert note.total == Decimal("100.00")
    assert note.number.startswith("KRE-")
    assert inv.status == "PAID"
    assert inv.remaining_amount == Decimal("0.00")
    assert client_a.balance == Decimal("0.00")
    trx = result["transaction"]
    assert trx.type == "ADJUSTMENT"
    assert trx.invoice_id == inv.pk
    assert trx.to_company_id == company.pk
    assert trx.from_client_id is None


def test_partial_credit_note(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    BillingService.create_credit_note(inv, amount=Decimal("40"), reason="Частично")
    inv.refresh_from_db()
    assert inv.status == "PARTIALLY_PAID"
    assert inv.paid_amount == Decimal("40.00")
    assert inv.remaining_amount == Decimal("60.00")
    BillingService.create_credit_note(inv, amount=Decimal("60"), reason="Остаток")
    inv.refresh_from_db()
    assert inv.status == "PAID"
    assert inv.credit_notes.count() == 2


def test_credit_note_cannot_exceed_remaining(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    with pytest.raises(ValueError, match="больше остатка"):
        BillingService.create_credit_note(inv, amount=Decimal("100.01"), reason="Слишком много")
    assert Transaction.objects.filter(invoice=inv).count() == 0


def test_credit_note_items_must_match_amount(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    result = BillingService.create_credit_note(
        inv,
        items=[{"description": "Скидка", "quantity": 2, "unit_price": "15.00"}],
        reason="По позициям",
    )
    assert result["credit_note"].total == Decimal("30.00")
    inv.refresh_from_db()
    assert inv.remaining_amount == Decimal("70.00")
