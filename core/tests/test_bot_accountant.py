"""Банковские операции, проформы CRM и тайтл для роли accountant."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import Car, Client, Company
from core.models.banking import BankAccount, BankConnection, BankTransaction
from core.models.billing import NewInvoice
from core.models.bots import Proforma
from core.services.billing_service import BillingService
from core.services.bot_runtime import issue_token

pytestmark = pytest.mark.django_db


def _call(token: str, *args: str) -> dict:
    out = StringIO()
    call_command("bot", *args, "--token", token, stdout=out)
    return json.loads(out.getvalue())


def _at(year, month, day) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime(year, month, day, 12, 0))


def test_bank_transactions_filters_links_and_does_not_call_revolut(settings):
    _principal, token = issue_token("accountant-bank", "accountant")
    company = Company.objects.create(name=settings.COMPANY_NAME)
    client = Client.objects.create(name="Buyer")
    connection = BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Revolut EUR")
    connection.access_token = "super-secret-revolut-token"
    connection.save()
    account = BankAccount.objects.create(
        connection=connection, external_id="acc-eur", name="Revolut EUR", currency="EUR", balance=Decimal("10")
    )
    invoice = NewInvoice.objects.create(
        number="AV-000013",
        document_type="PROFORMA",
        issuer_company=company,
        recipient_client=client,
        date=datetime.date(2026, 10, 10),
        status="ISSUED",
        total=Decimal("300.00"),
        subtotal=Decimal("300.00"),
    )
    kept = BankTransaction.objects.create(
        connection=connection,
        external_id="in-1",
        amount=Decimal("300.00"),
        currency="EUR",
        description="AV-000013 anger",
        counterparty_name="ANGER TRANSPORT",
        created_at=_at(2026, 10, 15),
        matched_invoice=invoice,
    )
    BankTransaction.objects.create(
        connection=connection,
        external_id="out-1",
        amount=Decimal("-40.00"),
        currency="EUR",
        description="fee",
        counterparty_name="Revolut",
        created_at=_at(2026, 10, 16),
    )
    BankTransaction.objects.create(
        connection=connection,
        external_id="old-1",
        amount=Decimal("900.00"),
        currency="EUR",
        description="old",
        counterparty_name="OLD",
        created_at=_at(2026, 9, 1),
    )
    recorded = Proforma.objects.create(number="BOT-1", amount=Decimal("300.00"))
    recorded.bank_transactions.add(kept)
    payment = BillingService.create_payment_for_bank_match(kept.pk)

    with patch("core.services.revolut_service.RevolutService.sync_all") as sync:
        payload = _call(
            token,
            "bank-transactions",
            "--date-from",
            "2026-10-01",
            "--date-to",
            "2026-10-31",
            "--direction",
            "in",
            "--account",
            "Revolut EUR",
            "--min-amount",
            "100",
            "--max-amount",
            "500",
            "--search",
            "ANGER",
        )
    sync.assert_not_called()
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    rows = payload["result"]["transactions"]
    assert [row["id"] for row in rows] == [kept.pk]
    row = rows[0]
    assert row["date"] == "2026-10-15"
    assert row["amount"] == "300.00"
    assert row["currency"] == "EUR"
    assert row["direction"] == "in"
    assert row["counterparty"] == "ANGER TRANSPORT"
    assert "AV-000013" in row["description"]
    assert row["account"]["id"] == account.pk
    assert row["account"]["name"] == "Revolut EUR"
    assert row["matched_invoice_id"] == invoice.pk
    assert row["matched_proforma_id"] == invoice.pk
    assert row["matched_proforma_ids"] == [recorded.pk]
    assert row["matched_payment_id"] == payment.pk
    assert payload["result"]["limit"] == 200
    blob = json.dumps(payload)
    assert "super-secret-revolut-token" not in blob
    assert "refresh_token" not in blob

    limited = _call(
        token,
        "bank-transactions",
        "--date-from",
        "2026-10-01",
        "--date-to",
        "2026-10-31",
        "--limit",
        "1",
    )
    assert limited["result"]["count"] == 1
    assert limited["result"]["total"] == 2


def test_neto_cannot_list_bank_transactions():
    _principal, token = issue_token("neto-no-bank", "neto")
    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "bank-transactions",
            "--token",
            token,
            "--date-from",
            "2026-10-01",
            "--date-to",
            "2026-10-31",
            stdout=out,
        )
    assert json.loads(out.getvalue())["ok"] is False


def test_proformas_lists_issued_crm_av_and_explains_sitepro():
    _principal, token = issue_token("accountant-av", "accountant")
    company = Company.objects.create(name="Caromoto Lithuania, MB")
    client = Client.objects.create(name="Site Buyer")
    issued = NewInvoice.objects.create(
        number="AV-000013",
        document_type="PROFORMA",
        issuer_company=company,
        recipient_client=client,
        date=timezone.localdate(),
        status="ISSUED",
        total=Decimal("1500.00"),
        subtotal=Decimal("1500.00"),
    )
    NewInvoice.objects.create(
        number="PARDP-000099",
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=datetime.date(2026, 10, 3),
        status="ISSUED",
        total=Decimal("10.00"),
        subtotal=Decimal("10.00"),
    )
    Proforma.objects.create(number="BOT-AV", kind="OFFICIAL", amount=Decimal("5.00"))

    payload = _call(token, "proformas")
    numbers = [row["number"] for row in payload["result"]["proformas"]]
    assert numbers == ["AV-000013"]
    row = payload["result"]["proformas"][0]
    assert row["id"] == issued.pk
    assert row["number_compact"] == "AV000013"
    assert row["status"] == "ISSUED"
    assert row["source"] == "crm"
    assert row["client"] == "Site Buyer"
    assert payload["result"]["bot_proformas"][0]["number"] == "BOT-AV"
    assert "site.pro" in payload["result"]["sitepro"]
    assert "PARDP" in payload["result"]["sitepro"]
    assert "AV000013" not in numbers


def test_accountant_can_download_car_title():
    from pathlib import Path

    _principal, token = issue_token("accountant-title", "accountant")
    car = Car.objects.create(year=2020, brand="Nissan", vin="3N1CP5BVXRL481232", status="UNLOADED")
    car.title_scan.save("nissan_title.pdf", ContentFile(b"title-bytes"), save=True)

    listed = _call(token, "car-docs", "--vin", car.vin)
    assert listed["result"]["documents"][0]["type"] == "TITLE"

    payload = _call(token, "download-car-doc", "--vin", car.vin, "--type", "TITLE")
    assert payload["ok"] is True
    assert Path(payload["result"]["path"]).read_bytes() == b"title-bytes"
