"""M6: деньги от клиента — всегда пара BALANCE_TOPUP + PAYMENT, ложного долга нет."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Client, Company, Line
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Pair Client")


def _invoice(company, client, total="100.00", document_type="INVOICE"):
    inv = NewInvoice.objects.create(
        document_type=document_type,
        issuer_company=company,
        recipient_client=client,
        date=timezone.localdate(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def test_cash_series_payment_is_pair(company, client_a):
    inv = _invoice(company, client_a, document_type="PROFORMA_BLC")
    BillingService.change_invoice_series(inv, "INVOICE_BLC")
    inv.refresh_from_db()
    client_a.refresh_from_db()

    assert inv.status == "PAID"
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("0.00")

    payment = inv.transactions.get(type="PAYMENT", status="COMPLETED")
    assert payment.method == "CASH"
    assert payment.paired_topup.type == "BALANCE_TOPUP"
    assert payment.paired_topup.method == "CASH"
    assert payment.paired_topup.to_client == client_a
    assert payment.paired_topup.amount == Decimal("100.00")


def test_pay_invoice_cash_from_client_is_pair(company, client_a):
    inv = _invoice(company, client_a)
    result = BillingService.pay_invoice(inv, Decimal("100"), "CASH", client_a)
    client_a.refresh_from_db()

    assert result["transaction"].paired_topup is not None
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("0.00")


def test_pay_invoice_from_advance_is_single_payment(company, client_a):
    BillingService.topup_balance(client_a, Decimal("150"), "TRANSFER")
    inv = _invoice(company, client_a)
    result = BillingService.pay_invoice(inv, Decimal("100"), "BALANCE", client_a)
    client_a.refresh_from_db()

    assert result["transaction"].paired_topup is None
    assert Transaction.objects.filter(type="BALANCE_TOPUP", to_client=client_a).count() == 1
    assert client_a.balance == Decimal("50.00")
    assert client_a.total_balance == Decimal("50.00")


def test_pay_invoice_from_non_client_is_single_payment(company):
    line = Line.objects.create(name="Pair Line")
    inv = NewInvoice.objects.create(
        issuer_line=line,
        recipient_company=company,
        date=timezone.localdate(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Фрахт", quantity=1, unit_price=Decimal("80"))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])

    result = BillingService.pay_invoice(inv, Decimal("80"), "TRANSFER", company)

    assert result["transaction"].paired_topup is None
    assert not Transaction.objects.filter(type="BALANCE_TOPUP").exists()


def test_bank_payment_links_topup(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))

    assert payment.method == "BALANCE"
    assert payment.paired_topup.method == "TRANSFER"
    assert payment.paired_topup.to_client == client_a


def test_paired_topup_is_frozen_after_completion(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    payment.paired_topup = None
    with pytest.raises(ValidationError):
        payment.save()
