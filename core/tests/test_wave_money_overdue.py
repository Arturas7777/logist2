"""M3: OVERDUE только для выставленных счетов, черновик не просрочивается."""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import InvoiceItem, NewInvoice
from core.services.billing_service import BillingService
from core.tasks import check_overdue_invoices

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Overdue Client")


def _invoice(company, client, status, days_ago=30, total="100.00"):
    past = timezone.now().date() - timezone.timedelta(days=days_ago)
    inv = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=past,
        due_date=past + timezone.timedelta(days=14),
        status=status,
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def test_draft_with_past_due_date_stays_draft(company, client_a):
    inv = _invoice(company, client_a, "DRAFT")
    assert not inv.is_overdue

    inv.recalculate_paid_amount()
    inv.refresh_from_db()
    assert inv.status == "DRAFT"

    inv.notes = "правка черновика"
    inv.save()
    inv.refresh_from_db()
    assert inv.status == "DRAFT"

    check_overdue_invoices()
    inv.refresh_from_db()
    assert inv.status == "DRAFT"
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("0.00")


def test_issued_with_past_due_date_becomes_overdue(company, client_a):
    inv = _invoice(company, client_a, "ISSUED")
    inv.recalculate_paid_amount()
    inv.refresh_from_db()
    assert inv.status == "OVERDUE"
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("-100.00")


def test_cancelled_payment_on_paid_invoice_past_due_reopens_as_overdue(company, client_a):
    inv = _invoice(company, client_a, "ISSUED")
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    inv.refresh_from_db()
    assert inv.status == "PAID"

    BillingService.cancel_transaction(payment)
    inv.refresh_from_db()
    assert inv.status == "OVERDUE"
    assert inv.paid_amount == Decimal("0.00")


def test_cancelled_payment_before_due_date_reopens_as_issued(company, client_a):
    inv = _invoice(company, client_a, "ISSUED", days_ago=3)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    BillingService.cancel_transaction(payment)
    inv.refresh_from_db()
    assert inv.status == "ISSUED"
