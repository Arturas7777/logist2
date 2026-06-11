"""
Контрактные тесты RevolutService (T1, AUDIT_ROUND3).

HTTP замокан через FakeSession, ответы — записанные JSON-фикстуры
(core/tests/fixtures/revolut_*.json) в формате Revolut Business API v1.0.

Проверяемый контракт:
- sync_all: счета + транзакции сохраняются в БД, last_synced_at обновляется
- парсинг legs: amount/currency/counterparty (включая "Payment from X")
- авто-пропуск служебных операций (fee/exchange/tax)
- обновление access_token при истечении (refresh_token + JWT assertion)
- ошибки API не пробрасываются из sync_all, а пишутся в last_error
- идемпотентность fetch_transactions (update_or_create по external_id)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Company
from core.models_banking import BankAccount, BankConnection, BankTransaction
from core.services.revolut_service import RevolutAPIError, RevolutService
from core.tests.mock_http import FakeResponse, FakeSession, load_fixture

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company(db):
    return Company.objects.create(name="Caromoto Lithuania, MB")


@pytest.fixture
def connection(db, company):
    conn = BankConnection.objects.create(
        bank_type="REVOLUT",
        company=company,
        name="Test Revolut",
    )
    conn.access_token = "valid-access-token"
    conn.refresh_token = "valid-refresh-token"
    conn.jwt_assertion = "header.payload.signature"
    conn.access_token_expires_at = timezone.now() + timedelta(hours=1)
    conn.save()
    return conn


def _make_service(connection, session: FakeSession) -> RevolutService:
    service = RevolutService(connection)
    service._session = session
    return service


def _happy_session() -> FakeSession:
    """Сессия с записанными ответами happy-path синхронизации."""
    session = FakeSession()
    session.add("GET", "/api/1.0/accounts", FakeResponse(load_fixture("revolut_accounts.json")))
    session.add("GET", "/api/1.0/transactions", FakeResponse(load_fixture("revolut_transactions.json")))
    session.add("GET", "/api/1.0/expenses", FakeResponse([]))
    return session


# ---------------------------------------------------------------------------
# sync_all — happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncAllContract:
    def test_sync_all_saves_accounts_and_transactions(self, connection):
        session = _happy_session()
        service = _make_service(connection, session)

        result = service.sync_all()

        assert result["error"] is None
        assert len(result["accounts"]) == 2
        assert len(result["transactions"]) == 3

        connection.refresh_from_db()
        assert connection.last_synced_at is not None
        assert connection.last_error == ""

        eur = BankAccount.objects.get(connection=connection, external_id="acc-eur-001")
        assert eur.name == "Main EUR"
        assert eur.currency == "EUR"
        assert eur.balance == Decimal("12345.67")
        assert eur.state == "active"

    def test_transfer_leg_parsing_and_payment_from_counterparty(self, connection):
        service = _make_service(connection, _happy_session())
        service.sync_all()

        tx = BankTransaction.objects.get(external_id="tx-transfer-in-001")
        assert tx.transaction_type == "transfer"
        assert tx.amount == Decimal("250.00")
        assert tx.currency == "EUR"
        # reference идёт в description
        assert tx.description == "Invoice CL-000123"
        # counterparty в legs пустой → парсится из "Payment from Acme Corp"
        assert tx.counterparty_name == "Acme Corp"
        assert tx.state == "completed"

    def test_card_payment_counterparty_from_merchant(self, connection):
        service = _make_service(connection, _happy_session())
        service.sync_all()

        tx = BankTransaction.objects.get(external_id="tx-card-001")
        assert tx.transaction_type == "card_payment"
        assert tx.amount == Decimal("-42.10")
        assert tx.counterparty_name == "AWS EMEA SARL"

    def test_fee_auto_skipped_from_reconciliation(self, connection):
        service = _make_service(connection, _happy_session())
        service.sync_all()

        fee = BankTransaction.objects.get(external_id="tx-fee-001")
        assert fee.transaction_type == "fee"
        assert fee.reconciliation_skipped is True
        assert "Комиссия банка" in fee.reconciliation_note

    def test_fetch_transactions_is_idempotent(self, connection):
        service = _make_service(connection, _happy_session())
        service.fetch_transactions()
        service.fetch_transactions()
        assert BankTransaction.objects.filter(connection=connection).count() == 3

    def test_removed_account_becomes_inactive(self, connection):
        service = _make_service(connection, _happy_session())
        service.fetch_accounts()
        assert BankAccount.objects.filter(connection=connection, state="active").count() == 2

        # Повторная синхронизация: в API остался только EUR-счёт
        session2 = FakeSession()
        session2.add(
            "GET",
            "/api/1.0/accounts",
            FakeResponse([load_fixture("revolut_accounts.json")[0]]),
        )
        service2 = _make_service(connection, session2)
        service2.fetch_accounts()

        usd = BankAccount.objects.get(connection=connection, external_id="acc-usd-001")
        assert usd.state == "inactive"


# ---------------------------------------------------------------------------
# token refresh
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTokenRefreshContract:
    def test_expired_token_triggers_refresh(self, connection):
        connection.access_token_expires_at = timezone.now() - timedelta(minutes=5)
        connection.save(update_fields=["access_token_expires_at"])

        session = _happy_session()
        session.add("POST", "/api/1.0/auth/token", FakeResponse(load_fixture("revolut_token.json")))
        service = _make_service(connection, session)

        service.fetch_accounts()

        # Запрос токена ушёл с правильным grant_type и JWT assertion
        token_calls = session.calls_to("/api/1.0/auth/token")
        assert len(token_calls) == 1
        form = token_calls[0][2]["data"]
        assert form["grant_type"] == "refresh_token"
        assert form["refresh_token"] == "valid-refresh-token"
        assert form["client_assertion_type"] == ("urn:ietf:params:oauth:client-assertion-type:jwt-bearer")

        connection.refresh_from_db()
        assert connection.access_token == "oa_prod_new-access-token-value"
        assert not connection.is_token_expired

    def test_valid_token_does_not_refresh(self, connection):
        session = _happy_session()
        service = _make_service(connection, session)
        service.fetch_accounts()
        assert session.calls_to("/api/1.0/auth/token") == []

    def test_refresh_failure_raises_and_saves_error(self, connection):
        connection.access_token_expires_at = timezone.now() - timedelta(minutes=5)
        connection.save(update_fields=["access_token_expires_at"])

        session = FakeSession()
        session.add(
            "POST",
            "/api/1.0/auth/token",
            FakeResponse({"error": "invalid_grant"}, status_code=401),
        )
        service = _make_service(connection, session)

        with pytest.raises(RevolutAPIError):
            service.fetch_accounts()

        connection.refresh_from_db()
        assert "Ошибка обновления токена" in connection.last_error


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncErrorContract:
    def test_api_error_recorded_not_raised(self, connection):
        session = FakeSession()
        session.add(
            "GET",
            "/api/1.0/accounts",
            FakeResponse({"message": "Internal error"}, status_code=500),
        )
        service = _make_service(connection, session)

        result = service.sync_all()

        assert result["error"] is not None
        connection.refresh_from_db()
        assert connection.last_error != ""
        assert connection.last_synced_at is None

    def test_expenses_403_is_non_fatal(self, connection):
        session = FakeSession()
        session.add("GET", "/api/1.0/accounts", FakeResponse(load_fixture("revolut_accounts.json")))
        session.add("GET", "/api/1.0/transactions", FakeResponse(load_fixture("revolut_transactions.json")))
        session.add(
            "GET",
            "/api/1.0/expenses",
            FakeResponse({"message": "Forbidden"}, status_code=403),
        )
        service = _make_service(connection, session)

        result = service.sync_all()

        assert result["error"] is None
        assert result["expenses_updated"] == 0
