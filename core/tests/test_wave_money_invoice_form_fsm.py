"""M8: форма инвойса не обходит FSM и не меняет состав выставленного счёта."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import AutoTransport, Car, Carrier, Client, Company, Container
from core.models_billing import InvoiceItem, NewInvoice
from core.services.billing_service import BillingService

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Form Client A")


@pytest.fixture
def client_b():
    return Client.objects.create(name="Form Client B")


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser("m8admin", "m8@example.com", "pw")
    client.force_login(user)
    return client


def _invoice(company, client, status="ISSUED", total="100.00"):
    inv = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status=status,
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def _post_form(admin_client, inv, **overrides):
    data = {
        "date": inv.date.strftime("%Y-%m-%d"),
        "document_type": inv.document_type,
        "status": inv.status,
        "issuer": f"company_{inv.issuer_company_id}",
        "recipient": f"client_{inv.recipient_client_id}",
        "manual_total": str(inv.total),
        "manual_items_json": "[]",
        "_continue": "1",
    }
    data.update(overrides)
    response = admin_client.post(reverse("admin:core_newinvoice_change", args=[inv.pk]), data)
    assert response.status_code == 302
    return [str(m) for m in get_messages(response.wsgi_request)]


def test_cannot_cancel_paid_invoice_via_form(admin_client, company, client_a):
    inv = _invoice(company, client_a)
    BillingService.register_incoming_bank_payment(inv, Decimal("40"))
    inv.refresh_from_db()
    assert inv.status == "PARTIALLY_PAID"

    msgs = _post_form(admin_client, inv, status="CANCELLED")

    inv.refresh_from_db()
    client_a.refresh_from_db()
    assert inv.status == "PARTIALLY_PAID"
    assert inv.paid_amount == Decimal("40.00")
    assert client_a.total_balance == Decimal("-60.00")
    assert any("платежи" in m for m in msgs)


def test_paid_status_is_not_set_manually(admin_client, company, client_a):
    inv = _invoice(company, client_a)

    _post_form(admin_client, inv, status="PAID")

    inv.refresh_from_db()
    client_a.refresh_from_db()
    assert inv.status == "ISSUED"
    assert client_a.total_balance == Decimal("-100.00")


def test_unpaid_invoice_cancelled_through_service(admin_client, company, client_a):
    inv = _invoice(company, client_a)

    _post_form(admin_client, inv, status="CANCELLED")

    inv.refresh_from_db()
    client_a.refresh_from_db()
    assert inv.status == "CANCELLED"
    assert client_a.total_balance == Decimal("0.00")


def test_issued_invoice_parties_and_items_locked(admin_client, company, client_a, client_b):
    inv = _invoice(company, client_a)

    msgs = _post_form(
        admin_client,
        inv,
        recipient=f"client_{client_b.pk}",
        manual_total="999.00",
        manual_items_json='[{"description": "Другое", "quantity": 1, "unit_price": 999}]',
        notes="поправили примечание",
    )

    inv.refresh_from_db()
    assert inv.recipient_client_id == client_a.pk
    assert inv.total == Decimal("100.00")
    assert list(inv.items.values_list("description", flat=True)) == ["Услуги"]
    assert inv.notes == "поправили примечание"
    assert any("не изменены" in m for m in msgs)


def test_draft_invoice_stays_editable(admin_client, company, client_a, client_b):
    inv = _invoice(company, client_a, status="DRAFT")

    _post_form(admin_client, inv, recipient=f"client_{client_b.pk}", manual_total="150.00")

    inv.refresh_from_db()
    assert inv.status == "DRAFT"
    assert inv.recipient_client_id == client_b.pk
    assert inv.total == Decimal("150.00")


def test_new_invoice_cannot_be_created_paid(admin_client, company, client_a):
    response = admin_client.post(
        reverse("admin:core_newinvoice_add"),
        {
            "date": timezone.now().date().strftime("%Y-%m-%d"),
            "document_type": "INVOICE",
            "status": "PAID",
            "recipient": f"client_{client_a.pk}",
            "manual_total": "70.00",
            "manual_items_json": "[]",
        },
    )
    assert response.status_code == 302

    inv = NewInvoice.objects.get(recipient_client=client_a)
    assert inv.status == "ISSUED"
    assert inv.issuer_company_id == company.pk
    assert inv.paid_amount == Decimal("0.00")


def test_change_page_shows_lock_and_limited_statuses(admin_client, company, client_a):
    issued = _invoice(company, client_a)
    draft = _invoice(company, client_a, status="DRAFT")

    html = admin_client.get(reverse("admin:core_newinvoice_change", args=[issued.pk])).content.decode()
    assert "🔒" in html
    assert 'name="recipient" id="recipient_select" disabled' in html
    assert 'value="PAID"' not in html

    html = admin_client.get(reverse("admin:core_newinvoice_change", args=[draft.pk])).content.decode()
    assert "🔒" not in html

    assert admin_client.get(reverse("admin:core_newinvoice_add")).status_code == 200


def test_mark_as_issued_only_touches_drafts(admin_client, company, client_a):
    draft = _invoice(company, client_a, status="DRAFT")
    partial = _invoice(company, client_a)
    BillingService.register_incoming_bank_payment(partial, Decimal("30"))

    admin_client.post(
        reverse("admin:core_newinvoice_changelist"),
        {"action": "mark_as_issued", "_selected_action": [draft.pk, partial.pk]},
    )

    draft.refresh_from_db()
    partial.refresh_from_db()
    assert draft.status == "ISSUED"
    assert partial.status == "PARTIALLY_PAID"


def test_add_invoice_item_rejects_issued(company, client_a):
    inv = _invoice(company, client_a)
    with pytest.raises(ValueError, match="кредит-ноту"):
        BillingService.add_invoice_item(inv, "Доп. услуга", Decimal("1"), Decimal("10"))


def test_generate_invoices_keeps_issued_invoice_cars(company, client_a):
    carrier = Carrier.objects.create(name="Carrier-M8")
    at = AutoTransport.objects.create(carrier=carrier, status="FORMED")
    container = Container.objects.create(number="M8-CONT-1", status="FLOATING")
    first = Car.objects.create(
        year=2022, brand="Audi", vin="M8GENERATE0000001", status="FLOATING", container=container, client=client_a
    )
    at.cars.add(first)
    inv = _invoice(company, client_a)
    inv.auto_transport = at
    inv.save(update_fields=["auto_transport"])
    inv.cars.set([first])

    second = Car.objects.create(
        year=2022, brand="Audi", vin="M8GENERATE0000002", status="FLOATING", container=container, client=client_a
    )
    at.cars.add(second)
    at.generate_invoices()

    assert list(inv.cars.values_list("pk", flat=True)) == [first.pk]
    assert NewInvoice.objects.filter(auto_transport=at, recipient_client=client_a).count() == 1
