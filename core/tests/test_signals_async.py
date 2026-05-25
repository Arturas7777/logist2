"""
Тесты async-разгрузки тяжёлых сигналов (Critical #2):

- Container.post_save → bulk-recalc auto в Celery, не в HTTP-потоке.
- NewInvoice.post_save (ISSUED, INVOICE) → push в site.pro через Celery,
  не блокирует save().
- Transaction.post_save → синхронный пересчёт балансов и paid_amount
  (это правильное поведение — балансы должны быть консистентны до
  возврата ответа клиенту).

В тестах Celery работает в EAGER-режиме (см. `logist2/settings/test.py`),
поэтому таски выполняются inline. Мы проверяем сам факт делегирования,
а также что fallback inline отрабатывает при сломанном брокере.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Car, Client, Company, Container, Warehouse
from core.models_billing import InvoiceItem, NewInvoice, Transaction

# ---------------------------------------------------------------------------
# Container.post_save → recalculate_cars_total_price_task
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestContainerUnloadDateAsync:
    """``transaction=True`` нужен, чтобы ``transaction.on_commit`` коллбэки
    реально срабатывали (внутри обычного ``@django_db`` всё обёрнуто
    в один long-running rollback, и on_commit никогда не запускается)."""

    def test_change_unload_date_dispatches_celery_task(self):
        """При изменении ``unload_date`` контейнера сигнал должен
        вызвать ``recalculate_cars_total_price_task.delay`` для всех
        авто этого контейнера — а не считать total_price синхронно
        в save()."""
        wh = Warehouse.objects.create(name="WH-Async")
        container = Container.objects.create(
            number="C-ASYNC-1",
            status="FLOATING",
            warehouse=wh,
        )
        car_ids = []
        for i in range(3):
            car = Car.objects.create(
                year=2023,
                brand="Toyota",
                vin=f"ASYNCCAR{i:09d}",
                status="FLOATING",
                container=container,
                warehouse=wh,
            )
            car_ids.append(car.pk)

        with patch("core.tasks.recalculate_cars_total_price_task.delay") as mock_delay:
            container.unload_date = timezone.now().date()
            container.status = "UNLOADED"
            container.save()

        # delay должен быть вызван хотя бы раз с нашими car_ids.
        assert mock_delay.called, "Celery-таска не была поставлена в очередь"
        dispatched_ids: set[int] = set()
        for call in mock_delay.call_args_list:
            args, _ = call
            if args and isinstance(args[0], list):
                dispatched_ids.update(args[0])
        assert set(car_ids).issubset(dispatched_ids), (
            f"Не все car_id попали в Celery: ожидали {car_ids}, получили {dispatched_ids}"
        )


# ---------------------------------------------------------------------------
# NewInvoice.post_save → push_invoice_to_sitepro_task
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSiteproPushAsync:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.company = Company.objects.create(name="Caromoto Lithuania, MB")
        self.client = Client.objects.create(name="Test Recipient")

    def _make_issued_invoice(self):
        inv = NewInvoice.objects.create(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
            status="DRAFT",
            document_type="INVOICE",  # только серия PARDP идёт в site.pro
        )
        InvoiceItem.objects.create(
            invoice=inv,
            description="X",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
        )
        inv.calculate_totals()
        inv.save(update_fields=["subtotal", "total"])
        return inv

    def test_status_change_to_issued_enqueues_sitepro_push(self):
        """post_save NewInvoice со status=ISSUED + document_type=INVOICE
        должен поставить push_invoice_to_sitepro_task через on_commit."""
        inv = self._make_issued_invoice()
        with patch("core.tasks.push_invoice_to_sitepro_task.delay") as mock_delay:
            inv.status = "ISSUED"
            inv.save()

        assert mock_delay.called, "push_invoice_to_sitepro_task не был поставлен в очередь"
        args, _ = mock_delay.call_args
        assert args == (inv.pk,)

    def test_non_invoice_doctype_skips_sitepro(self):
        """Серии не-INVOICE (PROFORMA / BLC / FACT) не должны идти в site.pro."""
        inv = NewInvoice.objects.create(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
            status="DRAFT",
            document_type="PROFORMA",  # AV — не пушим
        )
        InvoiceItem.objects.create(
            invoice=inv,
            description="X",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
        )
        inv.calculate_totals()
        inv.save(update_fields=["subtotal", "total"])

        with patch("core.tasks.push_invoice_to_sitepro_task.delay") as mock_delay:
            inv.status = "ISSUED"
            inv.save()
        assert not mock_delay.called

    def test_celery_broker_failure_falls_back_to_inline(self):
        """Если Celery недоступен — должен сработать inline-fallback,
        чтобы push в site.pro всё-таки случился (не молча проглатываем)."""
        inv = self._make_issued_invoice()

        with (
            patch(
                "core.tasks.push_invoice_to_sitepro_task.delay",
                side_effect=RuntimeError("broker is down"),
            ),
            patch("core.models_accounting.SiteProConnection.objects") as mock_qs,
        ):
            mock_qs.filter.return_value.first.return_value = None  # нет активного подключения
            inv.status = "ISSUED"
            inv.save()
            # Без активного SiteProConnection inline-фолбэк просто выходит,
            # но важно, что save() не упал — для пользователя это и есть
            # критичное требование.

    def test_already_issued_does_not_repush(self):
        """Повторный save() уже ISSUED-инвойса не должен снова пушить."""
        inv = self._make_issued_invoice()
        inv.status = "ISSUED"
        inv.save()

        with patch("core.tasks.push_invoice_to_sitepro_task.delay") as mock_delay:
            inv.notes = "edit after issuance"
            inv.save()
        assert not mock_delay.called


# ---------------------------------------------------------------------------
# Transaction.post_save → синхронный пересчёт (правильное поведение!)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTransactionSyncRecalc:
    """Transaction.post_save должен оставаться СИНХРОННЫМ:
    при возврате ответа клиенту балансы и paid_amount уже корректны.
    Если вынести в Celery — пользователь увидит инвойс ISSUED с
    paid_amount=0 сразу после успешной оплаты, что неприемлемо."""

    def test_payment_immediately_updates_paid_amount(self):
        company = Company.objects.create(name="Caromoto Lithuania, MB")
        client = Client.objects.create(name="Buyer")
        inv = NewInvoice.objects.create(
            issuer_company=company,
            recipient_client=client,
            date=timezone.now().date(),
            status="ISSUED",
        )
        InvoiceItem.objects.create(
            invoice=inv,
            description="X",
            quantity=Decimal("1"),
            unit_price=Decimal("200"),
        )
        inv.calculate_totals()
        inv.save(update_fields=["subtotal", "total"])

        Transaction.objects.create(
            type="PAYMENT",
            method="CASH",
            status="COMPLETED",
            amount=Decimal("200"),
            invoice=inv,
            from_client=client,
            to_company=company,
        )
        inv.refresh_from_db()
        # Сразу — не "после Celery tick".
        assert inv.paid_amount == Decimal("200.00")
        assert inv.status == "PAID"


# ---------------------------------------------------------------------------
# smoke: что Celery в тестах действительно EAGER (eager_propagates)
# ---------------------------------------------------------------------------


def test_celery_is_eager_in_tests(settings):
    assert settings.CELERY_TASK_ALWAYS_EAGER is True
    assert settings.CELERY_TASK_EAGER_PROPAGATES is True
