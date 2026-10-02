"""T1 (B1 + B2): регенерация позиций инвойса — только DRAFT; force-путь
пересчитывает оплату.

Сценарии:
- CarService изменён → ISSUED-инвойс не тронут, DRAFT пересобран
  (через Celery-задачу ``regenerate_invoices_for_car_task`` и inline-fallback);
- ``regenerate_items_from_cars()`` без force на ISSUED → False + warning;
- ``paid_amount > 0`` без force → отказ;
- PARTIALLY_PAID + force → total пересобран, paid_amount пересчитан,
  статус корректен (PAID при переплате);
- ``items_sync_diff`` показывает расхождение для выставленного счёта.

Запуск: pytest core/tests/test_wave_money_regen.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Client, Company, Container, Warehouse, WarehouseService
from core.models_billing import NewInvoice
from core.services.billing_service import BillingService


@pytest.fixture(autouse=True)
def _clear_pricing_thread_locals():
    from core.signals import car_service as cs_signals

    def _reset():
        for attr in ("_pricing_local", "_regen_local"):
            local = getattr(cs_signals, attr, None)
            if local is not None and getattr(local, "cars", None) is not None:
                local.cars.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def company(db, settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_entity(db):
    return Client.objects.create(name="Regen Client")


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="WH-REGEN", free_days=0)


@pytest.fixture
def unloading(warehouse):
    return WarehouseService.objects.create(
        warehouse=warehouse,
        name="Разгрузка",
        short_name="Порт",
        default_price=Decimal("100"),
        is_active=True,
        add_by_default=False,
    )


@pytest.fixture
def car(db, warehouse, client_entity):
    container = Container.objects.create(number="REGEN-CONT-1", status="FLOATING")
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin="REGENCAR000000001",
        status="FLOATING",
        container=container,
        warehouse=warehouse,
        client=client_entity,
    )


def _service(car, catalog, price):
    return CarService.objects.create(
        car=car,
        service_type="WAREHOUSE",
        service_id=catalog.id,
        custom_price=Decimal(str(price)),
        markup_amount=Decimal("0"),
        quantity=1,
    )


def _invoice(company, client_entity, car, status="DRAFT"):
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=client_entity,
        date=timezone.now().date(),
        status="DRAFT",
    )
    inv.cars.add(car)
    assert inv.regenerate_items_from_cars() is True
    inv.refresh_from_db()
    if status != "DRAFT":
        inv.status = status
        inv.save(update_fields=["status", "updated_at"])
        inv.refresh_from_db()
    return inv


@pytest.mark.django_db
class TestAutoRegenOnlyDraft:
    def test_task_rebuilds_draft_but_not_issued(self, company, client_entity, car, unloading):
        from core.tasks import regenerate_invoices_for_car_task

        svc = _service(car, unloading, 100)
        draft = _invoice(company, client_entity, car, status="DRAFT")
        issued = _invoice(company, client_entity, car, status="ISSUED")
        assert draft.total == Decimal("100.00")
        assert issued.total == Decimal("100.00")

        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("150"))
        result = regenerate_invoices_for_car_task(car.pk)

        draft.refresh_from_db()
        issued.refresh_from_db()
        assert result["regenerated"] == 1
        assert draft.total == Decimal("150.00")
        assert issued.total == Decimal("100.00")
        assert issued.items.get().unit_price == Decimal("100.00")

    def test_inline_fallback_also_skips_issued(self, company, client_entity, car, unloading):
        from core.signals.car_service import _regenerate_invoices_for_car_inline

        svc = _service(car, unloading, 100)
        draft = _invoice(company, client_entity, car, status="DRAFT")
        issued = _invoice(company, client_entity, car, status="ISSUED")

        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("120"))
        _regenerate_invoices_for_car_inline(car.pk)

        draft.refresh_from_db()
        issued.refresh_from_db()
        assert draft.total == Decimal("120.00")
        assert issued.total == Decimal("100.00")

    def test_issued_without_force_returns_false_with_warning(self, company, client_entity, car, unloading):
        from unittest import mock

        from core.models import billing as billing_module

        _service(car, unloading, 100)
        issued = _invoice(company, client_entity, car, status="ISSUED")
        item_before = issued.items.get().pk

        # Логгер «core» не пропагирует в root (LOGGING в base.py), поэтому
        # caplog его не видит — проверяем вызов напрямую.
        with mock.patch.object(billing_module.logger, "warning") as warn:
            assert issued.regenerate_items_from_cars() is False

        assert warn.called
        assert "автоматическая регенерация" in warn.call_args.args[0]
        assert issued.items.get().pk == item_before

    @pytest.mark.parametrize("status", ["OVERDUE", "PARTIALLY_PAID"])
    def test_other_open_statuses_are_frozen_too(self, company, client_entity, car, unloading, status):
        _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")
        NewInvoice.objects.filter(pk=inv.pk).update(status=status)
        inv.refresh_from_db()
        assert inv.regenerate_items_from_cars() is False

    def test_paid_amount_without_force_refuses(self, company, client_entity, car, unloading):
        _service(car, unloading, 100)
        draft = _invoice(company, client_entity, car, status="DRAFT")
        NewInvoice.objects.filter(pk=draft.pk).update(paid_amount=Decimal("10"))
        draft.refresh_from_db()
        assert draft.status == "DRAFT"

        assert draft.regenerate_items_from_cars() is False
        assert draft.items.count() == 1


@pytest.mark.django_db
class TestForceRegenRecalculatesPayment:
    def test_partially_paid_force_recalculates_status(self, company, client_entity, car, unloading):
        svc = _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")

        BillingService.pay_invoice(inv, Decimal("60"), "CASH", payer=client_entity)
        inv.refresh_from_db()
        assert inv.status == "PARTIALLY_PAID"
        assert inv.paid_amount == Decimal("60.00")

        # Услуга подешевела ниже уже оплаченного — после force-регена счёт закрыт.
        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("50"))
        assert inv.regenerate_items_from_cars(force=True) is True

        inv.refresh_from_db()
        assert inv.total == Decimal("50.00")
        assert inv.paid_amount == Decimal("60.00")
        assert inv.status == "PAID"
        assert inv.remaining_amount == Decimal("0.00")

    def test_force_regen_keeps_partially_paid_when_total_grows(self, company, client_entity, car, unloading):
        svc = _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")
        BillingService.pay_invoice(inv, Decimal("60"), "CASH", payer=client_entity)

        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("200"))
        assert inv.regenerate_items_from_cars(force=True) is True

        inv.refresh_from_db()
        assert inv.total == Decimal("200.00")
        assert inv.paid_amount == Decimal("60.00")
        assert inv.status == "PARTIALLY_PAID"
        assert inv.remaining_amount == Decimal("140.00")

    def test_client_balance_consistent_after_force(self, company, client_entity, car, unloading):
        """verify_balances-инвариант: сальдо транзакций клиента не меняется от регена."""
        from core.models_billing import Transaction

        svc = _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")
        BillingService.pay_invoice(inv, Decimal("60"), "CASH", payer=client_entity)
        client_entity.refresh_from_db()
        balance_before = client_entity.balance

        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("50"))
        inv.regenerate_items_from_cars(force=True)

        Transaction.recalculate_entity_balance(client_entity)
        client_entity.refresh_from_db()
        assert client_entity.balance == balance_before


@pytest.mark.django_db
class TestItemsSyncDiff:
    def test_no_diff_right_after_regen(self, company, client_entity, car, unloading):
        _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")
        assert inv.items_sync_diff() == []
        assert inv.items_out_of_sync() is False

    def test_diff_after_service_price_change(self, company, client_entity, car, unloading):
        svc = _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")

        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("130"))
        diff = inv.items_sync_diff()

        assert len(diff) == 1
        assert diff[0]["car"].pk == car.pk
        assert diff[0]["actual"] == Decimal("100.00")
        assert diff[0]["expected"] == Decimal("130.00")
        assert inv.items_out_of_sync() is True

    def test_diff_is_read_only(self, company, client_entity, car, unloading):
        svc = _service(car, unloading, 100)
        inv = _invoice(company, client_entity, car, status="ISSUED")
        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("130"))

        inv.items_sync_diff()

        inv.refresh_from_db()
        assert inv.total == Decimal("100.00")
        assert inv.items.get().unit_price == Decimal("100.00")


@pytest.mark.django_db
class TestAdminActions:
    """Admin action «Пересоздать позиции» трогает только черновики;
    force-action пишет LogEntry."""

    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from core.admin.billing.invoice import NewInvoiceAdmin

        return NewInvoiceAdmin(NewInvoice, AdminSite())

    def _request(self, post=None):
        from django.contrib.auth import get_user_model
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        user_model = get_user_model()
        user = user_model.objects.filter(username="regen-admin").first() or user_model.objects.create_superuser(
            "regen-admin", "a@b.c", "x"
        )
        rf = RequestFactory()
        request = rf.post("/", post or {})
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_regenerate_items_skips_issued(self, company, client_entity, car, unloading):
        svc = _service(car, unloading, 100)
        draft = _invoice(company, client_entity, car, status="DRAFT")
        issued = _invoice(company, client_entity, car, status="ISSUED")
        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("150"))

        request = self._request()
        self._admin().regenerate_items(request, NewInvoice.objects.filter(pk__in=[draft.pk, issued.pk]))

        draft.refresh_from_db()
        issued.refresh_from_db()
        assert draft.total == Decimal("150.00")
        assert issued.total == Decimal("100.00")
        texts = [m.message for m in request._messages]
        assert any(issued.number in t for t in texts)

    def test_force_action_requires_confirmation_then_logs(self, company, client_entity, car, unloading):
        from django.contrib.admin.models import LogEntry

        svc = _service(car, unloading, 100)
        issued = _invoice(company, client_entity, car, status="ISSUED")
        CarService.objects.filter(pk=svc.pk).update(custom_price=Decimal("150"))
        admin_obj = self._admin()
        qs = NewInvoice.objects.filter(pk=issued.pk)

        # Без confirm — промежуточная страница, данные не тронуты.
        response = admin_obj.regenerate_items_force(self._request(), qs)
        assert response is not None and response.status_code == 200
        issued.refresh_from_db()
        assert issued.total == Decimal("100.00")

        # С confirm — пересборка + LogEntry.
        result = admin_obj.regenerate_items_force(self._request({"confirm": "1"}), qs)
        assert result is None
        issued.refresh_from_db()
        assert issued.total == Decimal("150.00")
        assert LogEntry.objects.filter(object_id=str(issued.pk)).exists()
