"""L3: generate_invoices автовоза — блокировка и клиенты, чьи машины убрали."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AutoTransport, Car, Carrier, Client, Company, Container
from core.models_billing import InvoiceItem, NewInvoice

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def trip():
    carrier = Carrier.objects.create(name="Carrier-L3")
    return AutoTransport.objects.create(carrier=carrier, status="DRAFT")


def _car(vin, client):
    container, _ = Container.objects.get_or_create(number="L3-CONT-1", defaults={"status": "FLOATING"})
    return Car.objects.create(year=2022, brand="Audi", vin=vin, status="FLOATING", container=container, client=client)


def test_removed_client_draft_is_cleared_and_reused(company, trip):
    alice = Client.objects.create(name="L3 Alice")
    bob = Client.objects.create(name="L3 Bob")
    car_a = _car("L3GENERATE0000001", alice)
    car_b = _car("L3GENERATE0000002", bob)
    trip.cars.add(car_a, car_b)
    trip.generate_invoices()
    bob_draft = NewInvoice.objects.get(auto_transport=trip, recipient_client=bob)
    InvoiceItem.objects.create(invoice=bob_draft, description="Ручная", quantity=1, unit_price=Decimal("50"))
    bob_draft.calculate_totals()
    bob_draft.save(update_fields=["subtotal", "total"])

    trip.cars.remove(car_b)
    trip.generate_invoices()

    bob_draft.refresh_from_db()
    assert bob_draft.status == "DRAFT"
    assert not bob_draft.cars.exists()
    assert not bob_draft.items.exists()
    assert bob_draft.total == Decimal("0.00")
    assert NewInvoice.objects.get(auto_transport=trip, recipient_client=alice).cars.get() == car_a

    trip.cars.add(car_b)
    trip.generate_invoices()

    assert NewInvoice.objects.filter(auto_transport=trip, recipient_client=bob).count() == 1
    bob_draft.refresh_from_db()
    assert list(bob_draft.cars.all()) == [car_b]


def test_removed_client_issued_invoice_untouched(company, trip):
    bob = Client.objects.create(name="L3 Bob issued")
    car_b = _car("L3GENERATE0000003", bob)
    trip.cars.add(car_b)
    issued = NewInvoice.objects.create(
        document_type="INVOICE",
        issuer_company=company,
        recipient_client=bob,
        auto_transport=trip,
        date=timezone.localdate(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=issued, description="Услуги", quantity=1, unit_price=Decimal("100"))
    issued.calculate_totals()
    issued.save(update_fields=["subtotal", "total"])
    issued.cars.set([car_b])

    trip.cars.remove(car_b)
    trip.generate_invoices()

    issued.refresh_from_db()
    assert issued.status == "ISSUED"
    assert issued.total == Decimal("100.00")
    assert list(issued.cars.all()) == [car_b]


def test_generate_invoices_locks_autotransport_row(company, trip):
    if not connection.features.has_select_for_update:
        pytest.skip("SELECT … FOR UPDATE поддерживается только на PostgreSQL")
    trip.cars.add(_car("L3GENERATE0000004", Client.objects.create(name="L3 Lock")))

    with CaptureQueriesContext(connection) as queries:
        trip.generate_invoices()

    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and "core_autotransport" in q["sql"]]
    assert locks
