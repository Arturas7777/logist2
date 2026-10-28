"""M5: одна формула ``paid_amount`` для пересчёта, проверок и ремонта."""

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import InvoiceItem, NewInvoice
from core.services.billing_service import BillingService
from core.tasks import _collect_balance_mismatches, repair_balance_consistency

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Formula Client")


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


@pytest.fixture
def credited_invoice(company, client_a):
    inv = _pardp(company, client_a)
    BillingService.create_credit_note(inv, amount=Decimal("40"), reason="Скидка")
    BillingService.register_incoming_bank_payment(inv, Decimal("60"))
    inv.refresh_from_db()
    assert inv.status == "PAID"
    assert inv.paid_amount == Decimal("100.00")
    return inv


def _assert_untouched(inv, client):
    inv.refresh_from_db()
    client.refresh_from_db()
    assert inv.status == "PAID"
    assert inv.paid_amount == Decimal("100.00")
    assert client.total_balance == Decimal("0.00")


def test_expected_paid_counts_credit_note(credited_invoice):
    row = NewInvoice.objects.filter(pk=credited_invoice.pk).with_expected_paid().get()
    assert row.expected_paid == Decimal("100.00")
    assert not NewInvoice.objects.paid_amount_mismatches().exists()


def test_repair_task_leaves_credited_invoice_alone(credited_invoice, client_a):
    _, invoice_mismatches = _collect_balance_mismatches()
    assert invoice_mismatches == []
    result = repair_balance_consistency()
    assert result["invoice_fixes"] == 0
    _assert_untouched(credited_invoice, client_a)


def test_verify_balances_fix_leaves_credited_invoice_alone(credited_invoice, client_a):
    call_command("verify_balances", "--fix")
    _assert_untouched(credited_invoice, client_a)


def test_check_data_integrity_fix_leaves_credited_invoice_alone(credited_invoice, client_a):
    call_command("check_data_integrity", "--fix")
    _assert_untouched(credited_invoice, client_a)


def test_real_mismatch_is_repaired_through_recalculation(company, client_a):
    inv = _pardp(company, client_a)
    BillingService.register_incoming_bank_payment(inv, Decimal("30"))
    NewInvoice.objects.filter(pk=inv.pk).update(paid_amount=Decimal("0.00"), status="ISSUED")

    _, invoice_mismatches = _collect_balance_mismatches()
    assert [(m["number"], m["expected"]) for m in invoice_mismatches] == [(inv.number, Decimal("30.00"))]

    repair_balance_consistency()
    inv.refresh_from_db()
    assert inv.paid_amount == Decimal("30.00")
    assert inv.status == "PARTIALLY_PAID"


def test_linked_paid_is_not_reported(company, client_a):
    inv = _pardp(company, client_a)
    NewInvoice.objects.filter(pk=inv.pk).update(status="LINKED_PAID", paid_amount=Decimal("100.00"))
    assert not NewInvoice.objects.paid_amount_mismatches().exists()
