"""
Тесты BillingService (core/services/billing_service.py) — критичные
финансовые операции: пополнение, переводы, оплата, возвраты, корректировки.

Покрывает основные инварианты:
- сумма транзакций → пересчёт `Client.balance` через сигнал
- `pay_invoice` → корректный пересчёт `paid_amount` и `status`
- частичная/полная оплата, переплата, оплата отменённого
- `refund` → не может превысить оригинал; двойной refund блокируется
- `transfer` без денег → ValueError
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Client, Company, Warehouse
from core.models_billing import InvoiceItem, NewInvoice, Transaction
from core.services.billing_service import BillingService

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
def client_b(db):
    return Client.objects.create(name="Test Client B")


def _make_invoice(company, client, total="100.00"):
    """Создать ISSUED-инвойс с одной позицией."""
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client,
        date=timezone.now().date(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(
        invoice=inv,
        description="Услуги",
        quantity=Decimal("1"),
        unit_price=Decimal(total),
    )
    inv.calculate_totals()
    inv.save(update_fields=["subtotal", "total"])
    return inv


# ---------------------------------------------------------------------------
# topup_balance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTopupBalance:
    def test_topup_creates_transaction_and_updates_balance(self, client_a):
        trx = BillingService.topup_balance(
            entity=client_a, amount=Decimal("500"), method="CASH", description="Test topup"
        )
        assert trx.type == "BALANCE_TOPUP"
        assert trx.method == "CASH"
        assert trx.amount == Decimal("500.00")
        assert trx.to_client_id == client_a.pk
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("500.00")

    def test_topup_negative_amount_raises(self, client_a):
        with pytest.raises(ValueError):
            BillingService.topup_balance(entity=client_a, amount=Decimal("-1"))

    def test_topup_zero_raises(self, client_a):
        with pytest.raises(ValueError):
            BillingService.topup_balance(entity=client_a, amount=Decimal("0"))

    def test_topup_no_entity_raises(self):
        with pytest.raises(ValueError):
            BillingService.topup_balance(entity=None, amount=Decimal("100"))


# ---------------------------------------------------------------------------
# transfer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTransfer:
    def test_transfer_between_clients(self, client_a, client_b):
        BillingService.topup_balance(entity=client_a, amount=Decimal("300"))
        BillingService.transfer(from_entity=client_a, to_entity=client_b, amount=Decimal("100"), method="TRANSFER")
        client_a.refresh_from_db()
        client_b.refresh_from_db()
        assert client_a.balance == Decimal("200.00")
        assert client_b.balance == Decimal("100.00")

    def test_transfer_without_funds_raises(self, client_a, client_b):
        # client_a баланс 0 — не хватит
        with pytest.raises(ValueError):
            BillingService.transfer(from_entity=client_a, to_entity=client_b, amount=Decimal("50"))

    def test_transfer_negative_amount_raises(self, client_a, client_b):
        BillingService.topup_balance(entity=client_a, amount=Decimal("100"))
        with pytest.raises(ValueError):
            BillingService.transfer(from_entity=client_a, to_entity=client_b, amount=Decimal("-10"))


# ---------------------------------------------------------------------------
# pay_invoice
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPayInvoice:
    def test_full_payment_marks_invoice_paid(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        inv.refresh_from_db()
        assert inv.status == "PAID"
        assert inv.paid_amount == Decimal("100.00")
        assert result["remaining"] == Decimal("0.00")
        assert result["overpayment"] == Decimal("0.00")

    def test_partial_payment_marks_partially_paid(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        BillingService.pay_invoice(invoice=inv, amount=Decimal("40"), method="CASH", payer=client_a)
        inv.refresh_from_db()
        assert inv.status == "PARTIALLY_PAID"
        assert inv.paid_amount == Decimal("40.00")
        assert inv.remaining_amount == Decimal("60.00")

    def test_overpayment_clamped_to_remaining(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        result = BillingService.pay_invoice(invoice=inv, amount=Decimal("500"), method="CASH", payer=client_a)
        inv.refresh_from_db()
        # Сумма обрезается до remaining (100), переплаты не должно быть
        assert inv.status == "PAID"
        assert inv.paid_amount == Decimal("100.00")
        assert result["overpayment"] == Decimal("0.00")

    def test_pay_cancelled_invoice_raises(self, company, client_a):
        inv = _make_invoice(company, client_a)
        inv.status = "CANCELLED"
        inv.save(update_fields=["status"])
        with pytest.raises(ValueError, match="отмен"):
            BillingService.pay_invoice(invoice=inv, amount=Decimal("50"), method="CASH", payer=client_a)

    def test_pay_already_paid_raises(self, company, client_a):
        inv = _make_invoice(company, client_a)
        BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        with pytest.raises(ValueError, match="уже"):
            BillingService.pay_invoice(invoice=inv, amount=Decimal("10"), method="CASH", payer=client_a)

    def test_two_partial_payments_sum_correctly(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        BillingService.pay_invoice(invoice=inv, amount=Decimal("30"), method="CASH", payer=client_a)
        BillingService.pay_invoice(invoice=inv, amount=Decimal("70"), method="CASH", payer=client_a)
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("100.00")
        assert inv.status == "PAID"

    def test_payment_creates_transaction_with_correct_fields(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="TRANSFER", payer=client_a)
        trx = result["transaction"]
        assert trx.type == "PAYMENT"
        assert trx.method == "TRANSFER"
        assert trx.invoice_id == inv.pk
        assert trx.from_client_id == client_a.pk
        assert trx.to_company_id == company.pk
        assert trx.status == "COMPLETED"


# ---------------------------------------------------------------------------
# refund
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRefund:
    def test_full_refund_creates_refund_transaction(self, company, client_a):
        inv = _make_invoice(company, client_a)
        payment_result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        original = payment_result["transaction"]

        refund = BillingService.refund(original_transaction=original, reason="Test")
        assert refund.type == "REFUND"
        assert refund.amount == Decimal("100.00")
        # У возврата отправитель/получатель меняются местами
        assert refund.from_company_id == company.pk
        assert refund.to_client_id == client_a.pk

        # paid_amount пересчитан сигналом
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("0.00")

    def test_partial_refund(self, company, client_a):
        inv = _make_invoice(company, client_a)
        payment_result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        original = payment_result["transaction"]
        BillingService.refund(original_transaction=original, amount=Decimal("30"))
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("70.00")

    def test_double_refund_blocked(self, company, client_a):
        inv = _make_invoice(company, client_a)
        payment_result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        original = payment_result["transaction"]
        BillingService.refund(original_transaction=original, amount=Decimal("60"))
        # Второй refund на оставшиеся 40 — OK
        BillingService.refund(original_transaction=original, amount=Decimal("40"))
        # Третий refund (хоть на 1) — должен упасть
        with pytest.raises(ValueError, match="превышает"):
            BillingService.refund(original_transaction=original, amount=Decimal("1"))

    def test_refund_more_than_original_raises(self, company, client_a):
        inv = _make_invoice(company, client_a)
        payment_result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        with pytest.raises(ValueError):
            BillingService.refund(
                original_transaction=payment_result["transaction"],
                amount=Decimal("200"),
            )

    def test_refund_of_refund_raises(self, company, client_a):
        inv = _make_invoice(company, client_a)
        payment_result = BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="CASH", payer=client_a)
        refund = BillingService.refund(original_transaction=payment_result["transaction"])
        with pytest.raises(ValueError):
            BillingService.refund(original_transaction=refund)


# ---------------------------------------------------------------------------
# adjust_balance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdjustBalance:
    def test_positive_adjustment(self, client_a):
        BillingService.adjust_balance(entity=client_a, amount=Decimal("50"), reason="Bonus")
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("50.00")

    def test_negative_adjustment(self, client_a):
        BillingService.topup_balance(entity=client_a, amount=Decimal("100"))
        BillingService.adjust_balance(entity=client_a, amount=Decimal("-30"), reason="Penalty")
        client_a.refresh_from_db()
        assert client_a.balance == Decimal("70.00")


# ---------------------------------------------------------------------------
# auto reconciliation flow (двойная транзакция TOPUP + PAYMENT)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReconciliationFlow:
    """Имитируем то, что делает `auto_reconcile`: создаём пару
    TOPUP + PAYMENT(BALANCE) — клиент.balance остаётся 0, инвойс PAID."""

    def test_topup_plus_balance_payment_zeroes_client_balance(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")

        # 1) Деньги пришли на баланс клиента
        BillingService.topup_balance(entity=client_a, amount=Decimal("100"), method="TRANSFER")
        # 2) Оплата инвойса с баланса
        BillingService.pay_invoice(invoice=inv, amount=Decimal("100"), method="BALANCE", payer=client_a)

        client_a.refresh_from_db()
        inv.refresh_from_db()
        # Баланс схлопнулся, инвойс закрыт.
        assert client_a.balance == Decimal("0.00")
        assert inv.status == "PAID"
        assert inv.paid_amount == Decimal("100.00")


# ---------------------------------------------------------------------------
# Transaction signal: пересчёт invoice.paid_amount при удалении
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTransactionSignals:
    def test_creating_payment_recalculates_invoice(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        # Создаём Transaction напрямую, минуя BillingService, чтобы проверить
        # что сигнал post_save пересчитывает paid_amount/status сам.
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("100"),
            invoice=inv,
            from_client=client_a,
            to_company=company,
        )
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("100.00")
        assert inv.status == "PAID"

    def test_pending_payment_does_not_count_toward_paid_amount(self, company, client_a):
        inv = _make_invoice(company, client_a, total="100.00")
        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="PENDING",  # не COMPLETED
            amount=Decimal("100"),
            invoice=inv,
            from_client=client_a,
            to_company=company,
        )
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("0.00")
        assert inv.status == "ISSUED"

    def test_warehouse_topup_updates_warehouse_balance(self):
        wh = Warehouse.objects.create(name="WH-Topup")
        BillingService.topup_balance(entity=wh, amount=Decimal("250"), method="CASH")
        wh.refresh_from_db()
        # У Warehouse balance считается только по Tx без invoice → TOPUP без invoice учитывается.
        assert wh.balance == Decimal("250.00")
