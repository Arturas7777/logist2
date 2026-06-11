"""
Контрактные тесты SiteProService.push_invoice (T1, AUDIT_ROUND3).

HTTP замокан через FakeSession, ответы — записанные JSON-фикстуры
(core/tests/fixtures/sitepro_*.json) в формате site.pro Accounting API.

Проверяемый контракт:
- happy path: поиск клиента → sales/create → sale-items/create → SENT
- создание клиента, если не найден (включая обязательный locationId)
- идемпотентность: повторная отправка SENT-инвойса не делает HTTP-вызовов
- fallback "record already exists": линковка к существующей sale без дублей
- ошибка API → sync_status=FAILED, last_error на подключении, исключение
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Client, Company
from core.models_accounting import SiteProConnection, SiteProInvoiceSync
from core.models_billing import InvoiceItem, NewInvoice
from core.services.sitepro_service import SiteProAPIError, SiteProService
from core.tests.mock_http import FakeResponse, FakeSession, load_fixture

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def client_a(db):
    return Client.objects.create(name="Test Client A")


@pytest.fixture
def connection(db, company):
    conn = SiteProConnection.objects.create(
        company=company,
        name="Test site.pro",
        invoice_series="PARDP",
        default_warehouse_id=1,
        default_operation_type_id=2,
        default_location_id=1,
        default_item_id=24,
        default_calculation_mode=1,
    )
    conn.api_key = "test-api-key"
    conn.save()
    return conn


@pytest.fixture
def invoice(db, company, client_a):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client_a,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(
        invoice=inv,
        description="Услуги логистики",
        quantity=Decimal("1"),
        unit_price=Decimal("100.00"),
    )
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


def _make_service(connection, session: FakeSession) -> SiteProService:
    service = SiteProService(connection)
    service._session = session
    return service


# ---------------------------------------------------------------------------
# push_invoice — happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushInvoiceContract:
    def test_push_with_existing_client(self, connection, invoice):
        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        session.add("POST", "/warehouse/sales/create", FakeResponse(load_fixture("sitepro_sale_create.json")))
        session.add("POST", "/warehouse/sale-items/create", FakeResponse(load_fixture("sitepro_sale_item_create.json")))
        service = _make_service(connection, session)

        result = service.push_invoice(invoice)

        assert result["success"] is True
        assert result["external_id"] == "197"
        assert result["items_errors"] == []

        # Контракт sales/create: обязательные поля нового API
        sale_payload = session.posted_json("/warehouse/sales/create")[0]
        assert sale_payload["clientId"] == 77
        assert sale_payload["warehouseId"] == 1
        assert sale_payload["operationTypeId"] == 2
        assert sale_payload["currencyCode"] == "EUR"
        assert sale_payload["series"] == "PARDP"
        assert sale_payload["saleDate"] == invoice.date.strftime("%Y-%m-%d")

        # Контракт sale-items/create: позиция привязана к sale, мапится
        # на справочный item, описание уходит в addition
        item_payload = session.posted_json("/warehouse/sale-items/create")[0]
        assert item_payload["saleId"] == 197
        assert item_payload["itemId"] == 24
        assert item_payload["warehouseId"] == 1
        assert item_payload["calculationMode"] == 1
        assert item_payload["addition"] == "Услуги логистики"
        assert item_payload["quantity"] == 1.0
        assert item_payload["priceWithoutVat"] == 100.0

        sync = SiteProInvoiceSync.objects.get(connection=connection, invoice=invoice)
        assert sync.sync_status == "SENT"
        assert sync.external_id == "197"

        connection.refresh_from_db()
        assert connection.last_error == ""
        assert connection.last_synced_at is not None

    def test_push_creates_client_when_not_found(self, connection, invoice):
        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_empty.json")))
        session.add("POST", "/clients/create", FakeResponse(load_fixture("sitepro_client_create.json")))
        session.add("POST", "/warehouse/sales/create", FakeResponse(load_fixture("sitepro_sale_create.json")))
        session.add("POST", "/warehouse/sale-items/create", FakeResponse(load_fixture("sitepro_sale_item_create.json")))
        service = _make_service(connection, session)

        result = service.push_invoice(invoice)

        assert result["success"] is True

        # Контракт clients/create: имя + обязательный locationId (Tax Residency)
        create_payload = session.posted_json("/clients/create")[0]
        assert create_payload["name"] == "Test Client A"
        assert create_payload["locationId"] == 1

        # Созданный клиент (id=501 из data) подставлен в продажу
        sale_payload = session.posted_json("/warehouse/sales/create")[0]
        assert sale_payload["clientId"] == 501

    def test_api_key_required_in_headers(self, connection, invoice):
        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        session.add("POST", "/warehouse/sales/create", FakeResponse(load_fixture("sitepro_sale_create.json")))
        session.add("POST", "/warehouse/sale-items/create", FakeResponse(load_fixture("sitepro_sale_item_create.json")))
        service = _make_service(connection, session)

        service.push_invoice(invoice)

        for _method, _url, kwargs in session.calls:
            assert kwargs["headers"]["B1-Api-Key"] == "test-api-key"


# ---------------------------------------------------------------------------
# идемпотентность и fallback на существующую sale
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushInvoiceIdempotency:
    def test_already_synced_invoice_skips_http(self, connection, invoice):
        SiteProInvoiceSync.objects.create(
            connection=connection,
            invoice=invoice,
            sync_status="SENT",
            external_id="197",
            external_number="000197",
        )
        session = FakeSession()  # без маршрутов: любой запрос упадёт
        service = _make_service(connection, session)

        result = service.push_invoice(invoice)

        assert result["already_synced"] is True
        assert result["external_id"] == "197"
        assert session.calls == []

    def test_duplicate_sale_links_to_existing(self, connection, invoice):
        """sales/create вернул 'already exists' → находим существующую sale
        по series+number и линкуемся, items не дублируем."""
        invoice.number = "PARDP-000103"
        invoice.save(update_fields=["number"])

        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        session.add(
            "POST",
            "/warehouse/sales/create",
            FakeResponse({"message": "Sales document already exists", "code": 400}, status_code=400),
        )
        session.add("POST", "/warehouse/sales/list", FakeResponse(load_fixture("sitepro_sales_found.json")))
        session.add("POST", "/warehouse/sale-items/list", FakeResponse(load_fixture("sitepro_sale_items_found.json")))
        service = _make_service(connection, session)

        result = service.push_invoice(invoice)

        assert result["success"] is True
        assert result["external_id"] == "314"
        # Существующая sale уже содержит позиции — create не вызывался
        assert session.calls_to("/warehouse/sale-items/create") == []

        sync = SiteProInvoiceSync.objects.get(connection=connection, invoice=invoice)
        assert sync.sync_status == "SENT"
        assert sync.external_id == "314"
        assert "Linked to existing sale" in sync.error_message

    def test_series_prefix_stripped_from_number(self, connection, invoice):
        """В site.pro серия и номер раздельно: 'PARDP-000103' → number='000103'."""
        invoice.number = "PARDP-000103"
        invoice.save(update_fields=["number"])

        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        session.add("POST", "/warehouse/sales/create", FakeResponse(load_fixture("sitepro_sale_create.json")))
        session.add("POST", "/warehouse/sale-items/create", FakeResponse(load_fixture("sitepro_sale_item_create.json")))
        service = _make_service(connection, session)

        service.push_invoice(invoice)

        sale_payload = session.posted_json("/warehouse/sales/create")[0]
        assert sale_payload["number"] == "000103"
        assert sale_payload["series"] == "PARDP"


# ---------------------------------------------------------------------------
# ошибки
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushInvoiceErrors:
    def test_api_error_marks_sync_failed_and_raises(self, connection, invoice):
        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        session.add(
            "POST",
            "/warehouse/sales/create",
            FakeResponse({"message": "Internal error", "code": 500}, status_code=500),
        )
        service = _make_service(connection, session)

        with pytest.raises(SiteProAPIError):
            service.push_invoice(invoice)

        sync = SiteProInvoiceSync.objects.get(connection=connection, invoice=invoice)
        assert sync.sync_status == "FAILED"
        assert sync.error_message != ""

        connection.refresh_from_db()
        assert connection.last_error != ""

    def test_missing_warehouse_id_fails_fast(self, connection, invoice):
        connection.default_warehouse_id = None
        connection.save(update_fields=["default_warehouse_id"])

        session = FakeSession()
        session.add("POST", "/clients/list", FakeResponse(load_fixture("sitepro_clients_found.json")))
        service = _make_service(connection, session)

        with pytest.raises(SiteProAPIError, match="default_warehouse_id"):
            service.push_invoice(invoice)

        # До sales/create дело не дошло
        assert session.calls_to("/warehouse/sales/create") == []
