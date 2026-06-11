"""
Тесты иммутабельности леджера транзакций
(``Transaction._validate_ledger_rules`` + ``delete()``).

Правила:
- денежные поля COMPLETED-транзакции заморожены (LEDGER_FROZEN_FIELDS);
- статусы: COMPLETED → только CANCELLED; CANCELLED — терминальный;
- удаление COMPLETED/CANCELLED запрещено (force=True — escape hatch);
- метаданные (description, category) можно править и после проведения.

Запуск: pytest core/tests/test_ledger_immutability.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from core.models import Client, Company
from core.models_billing import Transaction


@pytest.fixture
def client_entity(db):
    return Client.objects.create(name="Ledger Client")


@pytest.fixture
def company(db):
    return Company.objects.create(name="Ledger Co")


def _payment(client_entity, company, status="COMPLETED", amount="100.00"):
    return Transaction.objects.create(
        type="TRANSFER",
        method="TRANSFER",
        status=status,
        amount=Decimal(amount),
        from_client=client_entity,
        to_company=company,
        description="ledger test",
    )


@pytest.mark.django_db
class TestFrozenFields:
    def test_amount_change_forbidden_after_completion(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.amount = Decimal("999.00")
        with pytest.raises(ValidationError, match="заморожены"):
            tx.save()

    def test_party_change_forbidden_after_completion(self, client_entity, company):
        tx = _payment(client_entity, company)
        other = Client.objects.create(name="Other Client")
        tx.from_client = other
        with pytest.raises(ValidationError, match="заморожены"):
            tx.save()

    def test_frozen_even_with_update_fields(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.amount = Decimal("1.00")
        with pytest.raises(ValidationError, match="заморожены"):
            tx.save(update_fields=["amount"])

    def test_metadata_editable_after_completion(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.description = "уточнённое описание"
        tx.save()
        assert Transaction.objects.get(pk=tx.pk).description == "уточнённое описание"

    def test_pending_fully_editable(self, client_entity, company):
        tx = _payment(client_entity, company, status="PENDING")
        tx.amount = Decimal("55.00")
        tx.save()
        assert Transaction.objects.get(pk=tx.pk).amount == Decimal("55.00")


@pytest.mark.django_db
class TestStatusFSM:
    def test_completed_to_cancelled_allowed(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.status = "CANCELLED"
        tx.save(update_fields=["status"])

    def test_completed_to_pending_forbidden(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.status = "PENDING"
        with pytest.raises(ValidationError, match="Недопустимый переход"):
            tx.save(update_fields=["status"])

    def test_cancelled_is_terminal(self, client_entity, company):
        tx = _payment(client_entity, company, status="PENDING")
        tx.status = "CANCELLED"
        tx.save(update_fields=["status"])
        tx.status = "COMPLETED"
        with pytest.raises(ValidationError, match="Недопустимый переход"):
            tx.save(update_fields=["status"])


@pytest.mark.django_db
class TestDeletion:
    def test_completed_delete_forbidden(self, client_entity, company):
        tx = _payment(client_entity, company)
        with pytest.raises(ValidationError):
            tx.delete()

    def test_cancelled_delete_forbidden(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.status = "CANCELLED"
        tx.save(update_fields=["status"])
        with pytest.raises(ValidationError):
            tx.delete()

    def test_pending_delete_allowed(self, client_entity, company):
        tx = _payment(client_entity, company, status="PENDING")
        tx.delete()
        assert not Transaction.objects.filter(pk=tx.pk).exists()

    def test_force_delete_escape_hatch(self, client_entity, company):
        tx = _payment(client_entity, company)
        tx.delete(force=True)
        assert not Transaction.objects.filter(pk=tx.pk).exists()
