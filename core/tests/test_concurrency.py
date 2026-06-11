"""Конкурентные тесты денежных инвариантов (T2, AUDIT_ROUND3).

Гонки, которые здесь проверяются:
- выдача номеров документов через SeriesCounter (B1) — параллельные потоки
  не должны получать одинаковые номера;
- параллельная регистрация платежей по одному инвойсу — пересчёт
  ``paid_amount`` идёт под SELECT FOR UPDATE и не должен терять обновления
  (lost update).

Требуют PostgreSQL (row-level locks) и реальных транзакций, поэтому:
- в SQLite-профиле (``logist2.settings.test``) пропускаются;
- в CI выполняются в джобе tests-with-migrations (PG-профиль).
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection, connections
from django.utils import timezone

from core.models import Client, Company
from core.models.series import SeriesCounter, next_document_number
from core.models_billing import InvoiceItem, NewInvoice, Transaction

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="конкурентные тесты требуют PostgreSQL",
    ),
]


def _run_concurrently(worker, n_threads: int):
    """Запустить ``worker(idx)`` в n потоках со стартовым барьером.

    Каждый поток получает собственное DB-соединение (thread-local в Django);
    закрываем его в конце, иначе тестовая БД не сможет быть удалена.
    """
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def runner(idx):
        try:
            barrier.wait(timeout=10)
            worker(idx)
        except Exception as exc:  # сохранить для assert в главном потоке
            errors.append(exc)
        finally:
            for conn in connections.all():
                conn.close()

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return errors


@pytest.mark.django_db(transaction=True)
class TestSeriesCounterConcurrency:
    """B1: параллельная выдача номеров не должна давать дубликатов."""

    N_THREADS = 8
    PER_THREAD = 5

    def test_concurrent_numbers_are_unique(self):
        issued: list[str] = []
        lock = threading.Lock()

        def worker(_idx):
            local = []
            for _ in range(self.PER_THREAD):
                local.append(next_document_number(NewInvoice, "TSTCC", 5))
            with lock:
                issued.extend(local)

        errors = _run_concurrently(worker, self.N_THREADS)

        assert not errors, f"потоки упали: {errors}"
        total = self.N_THREADS * self.PER_THREAD
        assert len(issued) == total
        assert len(set(issued)) == total, "обнаружены дубликаты номеров"
        # Счётчик дошёл ровно до количества выданных номеров.
        assert SeriesCounter.objects.get(prefix="TSTCC").last_value == total

    def test_concurrent_first_access_seeds_once(self):
        """Гонка двух «первых» обращений к серии: upsert не должен падать."""

        def worker(_idx):
            next_document_number(NewInvoice, "TSTSEED", 5)

        errors = _run_concurrently(worker, 4)
        assert not errors, f"потоки упали: {errors}"
        assert SeriesCounter.objects.get(prefix="TSTSEED").last_value == 4


@pytest.mark.django_db(transaction=True)
class TestConcurrentPayments:
    """Параллельные платежи по одному инвойсу: без lost update."""

    def _make_issued_invoice(self, company, client, total="100.00"):
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

    def test_two_concurrent_payments_sum_correctly(self):
        company = Company.objects.create(name="CC-Company")
        client = Client.objects.create(name="CC-Client")
        inv = self._make_issued_invoice(company, client, total="100.00")

        def worker(_idx):
            Transaction.objects.create(
                type="PAYMENT",
                method="CASH",
                status="COMPLETED",
                amount=Decimal("50"),
                invoice=inv,
                from_client=client,
                to_company=company,
            )

        errors = _run_concurrently(worker, 2)

        assert not errors, f"потоки упали: {errors}"
        inv.refresh_from_db()
        # Без SELECT FOR UPDATE в recalculate_paid_amount один из двух
        # post_save-пересчётов терялся бы (оба читают paid_amount=0).
        assert inv.paid_amount == Decimal("100.00")
        assert inv.status == "PAID"

    def test_many_concurrent_partial_payments(self):
        company = Company.objects.create(name="CC-Company-2")
        client = Client.objects.create(name="CC-Client-2")
        inv = self._make_issued_invoice(company, client, total="100.00")

        def worker(_idx):
            Transaction.objects.create(
                type="PAYMENT",
                method="CASH",
                status="COMPLETED",
                amount=Decimal("20"),
                invoice=inv,
                from_client=client,
                to_company=company,
            )

        errors = _run_concurrently(worker, 5)

        assert not errors, f"потоки упали: {errors}"
        inv.refresh_from_db()
        assert inv.paid_amount == Decimal("100.00")
        assert inv.status == "PAID"
        # Балансы тоже сходятся с суммой транзакций.
        client.refresh_from_db()
        assert Transaction.expected_entity_balance(client) == client.balance
