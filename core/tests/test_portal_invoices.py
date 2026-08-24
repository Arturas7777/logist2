"""Раздел «Мои счета» в личном кабинете клиента.

Проверяем три вещи: клиент видит свои выставленные счета, не видит черновики
и отменённые, и ни при каких условиях не может скачать чужой документ.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import Client, Company
from core.models.billing import InvoiceItem, NewInvoice
from core.models.website import ClientUser

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal(client):
    company = Company.objects.create(name="Caromoto Lithuania")
    owner = Client.objects.create(name="Invoice Client")
    user = User.objects.create_user(username="invoice-client", password="secret123")
    ClientUser.objects.create(user=user, client=owner, is_verified=True)
    client.force_login(user)
    return client, owner, company


def _invoice(company, recipient, amount, *, status="ISSUED", paid=Decimal("0.00")):
    invoice = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=recipient,
        date=timezone.now().date(),
    )
    InvoiceItem.objects.create(invoice=invoice, description="Услуги", quantity=1, unit_price=amount)
    invoice.refresh_from_db()
    NewInvoice.objects.filter(pk=invoice.pk).update(status=status, paid_amount=paid)
    invoice.refresh_from_db()
    return invoice


def test_issued_invoice_is_listed(portal):
    http, owner, company = portal
    invoice = _invoice(company, owner, Decimal("1200.00"))

    html = http.get(reverse("website:invoices")).content.decode()

    assert invoice.number in html
    assert "Выставлен" in html


def test_draft_and_cancelled_are_hidden(portal):
    http, owner, company = portal
    draft = _invoice(company, owner, Decimal("100.00"), status="DRAFT")
    cancelled = _invoice(company, owner, Decimal("200.00"), status="CANCELLED")
    issued = _invoice(company, owner, Decimal("300.00"))

    html = http.get(reverse("website:invoices")).content.decode()

    assert draft.number not in html
    assert cancelled.number not in html
    assert issued.number in html


def test_unpaid_filter_excludes_paid(portal):
    http, owner, company = portal
    paid = _invoice(company, owner, Decimal("400.00"), status="PAID", paid=Decimal("400.00"))
    unpaid = _invoice(company, owner, Decimal("500.00"))

    html = http.get(reverse("website:invoices"), {"filter": "unpaid"}).content.decode()

    assert unpaid.number in html
    assert paid.number not in html


def test_foreign_invoice_is_not_accessible(portal):
    http, _owner, company = portal
    stranger = Client.objects.create(name="Someone Else")
    foreign = _invoice(company, stranger, Decimal("999.00"))

    assert http.get(reverse("website:invoices")).content.decode().count(foreign.number) == 0

    response = http.get(reverse("website:invoice_download", args=[foreign.pk]))
    assert response.status_code == 404


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("website:invoices"))
    assert response.status_code == 302
    assert "/login" in response["Location"]
