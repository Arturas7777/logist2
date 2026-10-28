"""B7: кредит-нота уменьшает долг по PARDP и не двигает баланс клиента."""

from decimal import Decimal

import pytest
from django.urls import reverse
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
        date=timezone.localdate(),
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
    assert client_a.total_balance == Decimal("0.00")
    trx = result["transaction"]
    assert trx.type == "ADJUSTMENT"
    assert trx.invoice_id == inv.pk
    assert trx.to_company_id == company.pk
    assert trx.from_client_id is None


def test_overpayment_guard_counts_credit_note(company, client_a):
    from django.core.exceptions import ValidationError

    inv = _pardp(company, client_a, "100.00")
    BillingService.create_credit_note(inv, amount=Decimal("30"), reason="Скидка")

    with pytest.raises(ValidationError, match="Переплата"):
        BillingService.register_incoming_bank_payment(inv, Decimal("80"))

    BillingService.register_incoming_bank_payment(inv, Decimal("70"))
    inv.refresh_from_db()
    client_a.refresh_from_db()
    assert inv.status == "PAID"
    assert client_a.total_balance == Decimal("0.00")


def test_full_credit_note_leaves_no_debt(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-100.00")

    note = BillingService.create_credit_note(inv, amount=Decimal("100"), reason="Ошибка")["credit_note"]
    client_a.refresh_from_db()
    assert note.status == "ISSUED"
    assert client_a.open_invoices_debt == Decimal("0.00")
    assert client_a.total_balance == Decimal("0.00")
    assert not NewInvoice.objects.receivable().filter(pk=note.pk).exists()
    assert Client.objects.with_balance_info().get(pk=client_a.pk).unpaid_invoices_count == 0


def test_credit_note_never_becomes_overdue(company, client_a):
    from core.services.dashboard_service import DashboardService
    from core.tasks import check_overdue_invoices

    inv = _pardp(company, client_a, "100.00")
    note = BillingService.create_credit_note(inv, amount=Decimal("40"), reason="Скидка")["credit_note"]
    past = timezone.localdate() - timezone.timedelta(days=15)
    NewInvoice.objects.filter(pk__in=[inv.pk, note.pk]).update(date=past, due_date=past + timezone.timedelta(days=1))

    check_overdue_invoices()
    note.refresh_from_db()
    inv.refresh_from_db()
    assert note.status == "ISSUED"
    assert not note.is_overdue
    assert inv.status == "OVERDUE"
    assert DashboardService().get_overdue_invoices_count() == 1
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-60.00")


def test_client_changelist_balance_ignores_credit_note(company, client_a, admin_client):
    inv = _pardp(company, client_a, "100.00")
    BillingService.create_credit_note(inv, amount=Decimal("100"), reason="Ошибка")
    response = admin_client.get(reverse("admin:core_client_changelist"))
    assert response.status_code == 200
    row = response.context_data["cl"].queryset.get(pk=client_a.pk)
    assert row._open_debt == Decimal("0.00")
    assert row._total_balance == Decimal("0.00")


def test_partial_credit_note(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    BillingService.create_credit_note(inv, amount=Decimal("40"), reason="Частично")
    inv.refresh_from_db()
    assert inv.status == "PARTIALLY_PAID"
    assert inv.paid_amount == Decimal("40.00")
    assert inv.remaining_amount == Decimal("60.00")
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-60.00")
    BillingService.create_credit_note(inv, amount=Decimal("60"), reason="Остаток")
    inv.refresh_from_db()
    assert inv.status == "PAID"
    assert inv.credit_notes.count() == 2
    client_a.refresh_from_db()
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("0.00")


def test_credit_note_cannot_exceed_remaining(company, client_a):
    inv = _pardp(company, client_a, "100.00")
    with pytest.raises(ValueError, match="больше остатка"):
        BillingService.create_credit_note(inv, amount=Decimal("100.01"), reason="Слишком много")
    assert Transaction.objects.filter(invoice=inv).count() == 0
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-100.00")


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
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-70.00")
