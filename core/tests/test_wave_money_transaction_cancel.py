"""M1: отмена проведённой транзакции возвращает балансы и ``paid_amount``."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Cancel Client")


def _invoice(company, client, total="100.00", document_type="INVOICE"):
    inv = NewInvoice.objects.create(
        document_type=document_type,
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def _refresh(*objs):
    for obj in objs:
        obj.refresh_from_db()


def test_cancel_bank_payment_pair_restores_debt(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    topup = Transaction.objects.get(type="BALANCE_TOPUP", to_client=client_a)
    _refresh(inv, client_a)
    assert inv.status == "PAID"
    assert client_a.total_balance == Decimal("0.00")

    BillingService.cancel_transaction(payment, reason="ошибочная привязка")
    _refresh(inv, client_a, payment)
    assert payment.status == "CANCELLED"
    assert "ошибочная привязка" in payment.description
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "ISSUED"
    assert client_a.balance == Decimal("100.00")
    assert client_a.total_balance == Decimal("0.00")

    BillingService.cancel_transaction(topup, reason="деньги не пришли")
    _refresh(client_a)
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-100.00")


def test_cancel_partial_payment_returns_invoice_to_issued(company, client_a):
    inv = _invoice(company, client_a)
    result = BillingService.pay_invoice(inv, Decimal("40"), "CASH", client_a)
    _refresh(inv)
    assert inv.status == "PARTIALLY_PAID"

    BillingService.cancel_transaction(result["transaction"])
    _refresh(inv, client_a)
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "ISSUED"
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-100.00")


def test_status_change_outside_service_also_recalculates(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))

    payment.status = "CANCELLED"
    payment.save(update_fields=["status"])
    _refresh(inv, client_a)
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "ISSUED"
    assert client_a.balance == Decimal("100.00")


def test_cancel_twice_rejected(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    BillingService.cancel_transaction(payment)
    with pytest.raises(ValueError, match="уже отменена"):
        BillingService.cancel_transaction(payment)


def test_reverse_cash_payments_restores_client_balance(company, client_a):
    inv = _invoice(company, client_a, document_type="PROFORMA_BLC")
    BillingService.change_invoice_series(inv, "INVOICE_BLC")
    _refresh(inv, client_a)
    assert inv.status == "PAID"
    assert client_a.balance == Decimal("-100.00")

    BillingService.change_invoice_series(inv, "PROFORMA_BLC")
    _refresh(inv, client_a)
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "ISSUED"
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-100.00")


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser("m1admin", "m1@example.com", "pw")
    client.force_login(user)
    return client


def test_admin_status_readonly_for_completed(admin_client, company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    url = reverse("admin:core_transaction_change", args=[payment.pk])
    response = admin_client.get(url)
    assert response.status_code == 200
    assert 'name="status"' not in response.content.decode()
    assert reverse("admin:core_transaction_cancel", args=[payment.pk]) in response.content.decode()


def test_admin_cancel_button_goes_through_service(admin_client, company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    url = reverse("admin:core_transaction_cancel", args=[payment.pk])
    response = admin_client.post(url, {"reason": "дубль"})
    assert response.status_code == 302
    _refresh(payment, inv, client_a)
    assert payment.status == "CANCELLED"
    assert "дубль" in payment.description
    assert inv.status == "ISSUED"
    assert client_a.total_balance == Decimal("0.00")


def test_admin_cancel_requires_post(admin_client, company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.register_incoming_bank_payment(inv, Decimal("100"))
    response = admin_client.get(reverse("admin:core_transaction_cancel", args=[payment.pk]))
    assert response.status_code == 405
    _refresh(payment)
    assert payment.status == "COMPLETED"
