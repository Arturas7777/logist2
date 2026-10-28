"""M11: «сегодня» — по Вильнюсу, а не по UTC (окно 00:00–03:00 местного времени)."""

import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from core.models import Client, Company
from core.models_billing import InvoiceItem, NewInvoice

pytestmark = pytest.mark.django_db

# 28.10.2026 00:30 по Вильнюсу (зимнее время, UTC+2) = 27.10.2026 22:30 UTC.
VILNIUS_AFTER_MIDNIGHT = datetime.datetime(2026, 10, 27, 22, 30, tzinfo=datetime.UTC)


def test_settings_use_vilnius():
    with mock.patch("django.utils.timezone.now", return_value=VILNIUS_AFTER_MIDNIGHT):
        assert timezone.localdate() == datetime.date(2026, 10, 28)


def test_invoice_overdue_by_local_date(settings):
    company = Company.objects.create(name=settings.COMPANY_NAME)
    client = Client.objects.create(name="TZ Client")
    inv = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=datetime.date(2026, 10, 1),
        due_date=datetime.date(2026, 10, 27),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal("10"))

    with mock.patch("django.utils.timezone.now", return_value=VILNIUS_AFTER_MIDNIGHT):
        assert inv.is_overdue is True
