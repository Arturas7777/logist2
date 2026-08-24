"""Баланс клиента в личном кабинете.

Кабинет показывает ``total_balance`` (сальдо минус долг по открытым счетам),
а не поле ``balance``. Последнее — авансовый счёт, который при штатной схеме
оплаты (BALANCE_TOPUP + PAYMENT) всегда в нуле, из-за чего клиент с реальной
задолженностью видел в кабинете «0.00 EUR».
См. .cursor/rules/accounting-context.mdc.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format

from core.models import Client, Company
from core.models.billing import InvoiceItem, NewInvoice
from core.models.website import ClientUser

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal(client):
    company = Company.objects.create(name="Caromoto Lithuania")
    owner = Client.objects.create(name="Balance Client")
    user = User.objects.create_user(username="balance-client", password="secret123")
    ClientUser.objects.create(user=user, client=owner, is_verified=True)
    client.force_login(user)
    return client, owner, company


def _issue_invoice(company, owner, amount):
    """Выставленный, но не оплаченный счёт клиенту."""
    invoice = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=owner,
        date=timezone.now().date(),
    )
    InvoiceItem.objects.create(invoice=invoice, description="Услуги", quantity=1, unit_price=amount)
    invoice.refresh_from_db()
    invoice.status = "ISSUED"
    invoice.save(update_fields=["status"])
    return invoice


def test_open_invoice_shown_as_amount_due(portal):
    http, owner, company = portal
    _issue_invoice(company, owner, Decimal("2040.00"))

    # Поле balance остаётся нулевым — именно оно раньше и показывалось.
    owner.refresh_from_db()
    assert owner.balance == Decimal("0.00")
    assert owner.total_balance == Decimal("-2040.00")

    html = http.get(reverse("website:dashboard")).content.decode()
    assert "К оплате" in html
    # Разделитель дробной части зависит от активной локали (ru/lt — запятая).
    assert number_format(Decimal("2040.00"), 2) in html
    assert "Аванс на счету" not in html


def test_prepayment_shown_as_advance(portal):
    http, owner, company = portal
    Client.objects.filter(pk=owner.pk).update(balance=Decimal("500.00"))
    owner.refresh_from_db()

    html = http.get(reverse("website:dashboard")).content.decode()
    assert "Аванс на счету" in html
    assert number_format(Decimal("500.00"), 2) in html
    assert "К оплате" not in html


def test_zero_balance_states_no_debt(portal):
    http, owner, _company = portal

    html = http.get(reverse("website:dashboard")).content.decode()
    assert "Задолженности нет" in html
    assert "К оплате" not in html


def test_paid_invoice_does_not_create_debt(portal):
    http, owner, company = portal
    invoice = _issue_invoice(company, owner, Decimal("300.00"))
    NewInvoice.objects.filter(pk=invoice.pk).update(status="PAID", paid_amount=Decimal("300.00"))

    owner.refresh_from_db()
    assert owner.open_invoices_debt == Decimal("0.00")

    html = http.get(reverse("website:dashboard")).content.decode()
    assert "К оплате" not in html
