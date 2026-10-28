"""M10: отвязка банковской операции и изменения уже сверенной операции из Revolut."""

import copy
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import Client, Company, Task
from core.models_banking import BankConnection, BankTransaction
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService
from core.services.revolut_service import RevolutService
from core.tests.mock_http import FakeResponse, FakeSession, load_fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Unlink Client A")


@pytest.fixture
def client_b():
    return Client.objects.create(name="Unlink Client B")


@pytest.fixture
def connection(company):
    conn = BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Unlink Revolut")
    conn.access_token = "valid-access-token"
    conn.refresh_token = "valid-refresh-token"
    conn.jwt_assertion = "header.payload.signature"
    conn.access_token_expires_at = timezone.now() + timedelta(hours=1)
    conn.save()
    return conn


def _invoice(company, client, total="100.00"):
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


def _bt(connection, amount="100.00", ext="BT-UNLINK-1"):
    return BankTransaction.objects.create(
        connection=connection,
        external_id=ext,
        transaction_type="transfer",
        amount=Decimal(amount),
        currency="EUR",
        counterparty_name="Unlink Payer",
        created_at=timezone.now(),
    )


def _link(bt, invoice):
    bt.matched_invoice = invoice
    bt.save(update_fields=["matched_invoice"])
    return BillingService.create_payment_for_bank_match(bt.pk)


def test_unlink_cancels_pair_and_allows_relink(connection, company, client_a, client_b):
    wrong = _invoice(company, client_a)
    right = _invoice(company, client_b)
    bt = _bt(connection)
    payment = _link(bt, wrong)
    assert payment.paired_topup is not None

    result = BillingService.unlink_bank_transaction(bt, reason="не тот клиент")

    assert {t.pk for t in result["cancelled"]} == {payment.pk, payment.paired_topup_id}
    assert result["orphan_topup_suspected"] is False
    for obj in (bt, wrong, client_a):
        obj.refresh_from_db()
    assert bt.matched_invoice_id is None and bt.matched_transaction_id is None
    assert "не тот клиент" in bt.reconciliation_note
    assert wrong.status == "ISSUED"
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-100.00")

    _link(bt, right)
    right.refresh_from_db()
    client_b.refresh_from_db()
    assert right.status == "PAID"
    assert client_b.total_balance == Decimal("0.00")


def test_unlink_allocation_cancels_every_part(connection, company, client_a):
    inv1 = _invoice(company, client_a, "100.00")
    inv2 = _invoice(company, client_a, "50.00")
    bt = _bt(connection, amount="200.00")
    BillingService.allocate_bank_transaction(bt, [(inv1, Decimal("100")), (inv2, Decimal("50"))])
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("50.00")

    result = BillingService.unlink_bank_transaction(bt)

    assert len(result["cancelled"]) == 5
    assert not Transaction.objects.filter(bank_transaction=bt, status="COMPLETED").exists()
    client_a.refresh_from_db()
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-150.00")


def test_unlink_legacy_payment_flags_orphan_topup(connection, company, client_a):
    inv = _invoice(company, client_a)
    bt = _bt(connection)
    Transaction.objects.create(
        type="BALANCE_TOPUP", method="TRANSFER", status="COMPLETED", amount=Decimal("100"), to_client=client_a
    )
    legacy = Transaction.objects.create(
        type="PAYMENT",
        method="BALANCE",
        status="COMPLETED",
        amount=Decimal("100"),
        invoice=inv,
        from_client=client_a,
        to_company=company,
    )
    bt.matched_invoice = inv
    bt.matched_transaction = legacy
    bt.save(update_fields=["matched_invoice", "matched_transaction"])

    result = BillingService.unlink_bank_transaction(bt)

    assert [t.pk for t in result["cancelled"]] == [legacy.pk]
    assert result["orphan_topup_suspected"] is True


def test_unlink_requires_link(connection):
    with pytest.raises(ValueError, match="не привязана"):
        BillingService.unlink_bank_transaction(_bt(connection))


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser("m10admin", "m10@example.com", "pw")
    client.force_login(user)
    return client


def test_admin_link_fields_readonly_and_unlink_button(admin_client, connection, company, client_a):
    bt = _bt(connection)
    _link(bt, _invoice(company, client_a))

    html = admin_client.get(reverse("admin:core_banktransaction_change", args=[bt.pk])).content.decode()
    assert 'name="matched_invoice"' not in html
    assert reverse("admin:core_banktransaction_unlink", args=[bt.pk]) in html

    assert admin_client.get(reverse("admin:core_banktransaction_unlink", args=[bt.pk])).status_code == 405
    response = admin_client.post(reverse("admin:core_banktransaction_unlink", args=[bt.pk]), {"reason": "ошибка"})
    assert response.status_code == 302
    bt.refresh_from_db()
    client_a.refresh_from_db()
    assert bt.matched_invoice_id is None
    assert client_a.total_balance == Decimal("-100.00")


def _session(transactions):
    session = FakeSession()
    session.add("GET", "/api/1.0/transactions", FakeResponse(transactions))
    return session


def test_revolut_change_of_matched_operation_creates_task(connection, company, client_a):
    data = load_fixture("revolut_transactions.json")
    service = RevolutService(connection)
    service._session = _session(data)
    service.fetch_transactions()
    bt = BankTransaction.objects.get(external_id="tx-transfer-in-001")
    payment = _link(bt, _invoice(company, client_a, "250.00"))

    reverted = copy.deepcopy(data)
    reverted[0]["state"] = "reverted"
    for _ in range(2):
        service._session = _session(reverted)
        service.fetch_transactions()

    bt.refresh_from_db()
    payment.refresh_from_db()
    assert bt.state == "reverted"
    assert payment.status == "COMPLETED"
    tasks = Task.objects.filter(title__contains="tx-transfer-in-001", is_completed=False)
    assert tasks.count() == 1
    assert "reverted" in tasks.get().description


def test_revolut_change_of_unmatched_operation_is_silent(connection):
    data = load_fixture("revolut_transactions.json")
    service = RevolutService(connection)
    service._session = _session(data)
    service.fetch_transactions()

    changed = copy.deepcopy(data)
    changed[0]["legs"][0]["amount"] = 240.0
    service._session = _session(changed)
    service.fetch_transactions()

    assert BankTransaction.objects.get(external_id="tx-transfer-in-001").amount == Decimal("240.00")
    assert not Task.objects.exists()
