"""T9 (B6), T4 (B5): правила карточки авто, влияющие на деньги.

Запуск: pytest core/tests/test_wave_money_car_rules.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Car, CarService, Client, Company, Container, Warehouse, WarehouseService
from core.models_billing import NewInvoice


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
def warehouse(db):
    return Warehouse.objects.create(name="WH-RULES", free_days=0)


@pytest.mark.django_db
class TestImportantFlagBlocksStatusChange:
    """B6: снятие галочки «Важное» и смена статуса одним save → статус не меняется."""

    def _important_car(self, warehouse):
        container = Container.objects.create(number="RULES-IMP-1", status="FLOATING")
        return Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="RULESIMPORTANT001",
            status="UNLOADED",
            unload_date=timezone.now().date(),
            container=container,
            warehouse=warehouse,
            is_important=True,
        )

    def test_uncheck_and_change_status_in_one_save_rolls_back_status(self, warehouse):
        car = self._important_car(warehouse)

        car.is_important = False
        car.status = "TRANSFERRED"
        car.save()

        car.refresh_from_db()
        assert car.is_important is False
        assert car.status == "UNLOADED"
        assert car.transfer_date is None

    def test_rollback_sets_flag_for_admin_warning(self, warehouse):
        car = self._important_car(warehouse)

        car.is_important = False
        car.status = "IN_PORT"
        car.save()

        assert car._status_change_blocked == ("UNLOADED", "IN_PORT")

    def test_status_change_with_flag_still_raises(self, warehouse):
        car = self._important_car(warehouse)
        car.status = "TRANSFERRED"
        with pytest.raises(ValidationError):
            car.save()

    def test_after_uncheck_status_can_change_in_next_save(self, warehouse):
        car = self._important_car(warehouse)
        car.is_important = False
        car.save()

        car.status = "TRANSFERRED"
        car.save()

        car.refresh_from_db()
        assert car.status == "TRANSFERRED"
        assert car.transfer_date is not None


@pytest.mark.django_db
class TestClientChangeWithInvoices:
    """B5 / T4: смена клиента у авто, входящего в инвойс."""

    @pytest.fixture
    def company(self, db, settings):
        return Company.objects.create(name=settings.COMPANY_NAME)

    @pytest.fixture
    def old_client(self, db):
        return Client.objects.create(name="Old Client")

    @pytest.fixture
    def new_client(self, db):
        return Client.objects.create(name="New Client")

    def _car(self, warehouse, client, vin="RULESCLIENT000001"):
        container = Container.objects.create(number=f"RULES-CL-{vin[-3:]}", status="FLOATING")
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin=vin,
            status="FLOATING",
            container=container,
            warehouse=warehouse,
            client=client,
        )
        svc = WarehouseService.objects.create(
            warehouse=warehouse,
            name=f"Разгрузка {vin[-3:]}",
            short_name="Порт",
            default_price=Decimal("100"),
            is_active=True,
            add_by_default=False,
        )
        CarService.objects.create(
            car=car, service_type="WAREHOUSE", service_id=svc.id, custom_price=Decimal("100"), quantity=1
        )
        return car

    def _invoice(self, company, client, cars, status):
        inv = NewInvoice.objects.create(
            issuer_company=company, recipient_client=client, date=timezone.now().date(), status="DRAFT"
        )
        inv.cars.set(cars)
        inv.regenerate_items_from_cars()
        if status != "DRAFT":
            inv.status = status
            inv.save(update_fields=["status", "updated_at"])
        inv.refresh_from_db()
        return inv

    def test_issued_invoice_blocks_client_change(self, warehouse, company, old_client, new_client):
        car = self._car(warehouse, old_client)
        inv = self._invoice(company, old_client, [car], "ISSUED")

        car.client = new_client
        with pytest.raises(ValidationError) as exc:
            car.save()
        assert inv.number in str(exc.value)
        assert "кредит-ноту" in str(exc.value)

        car.refresh_from_db()
        assert car.client_id == old_client.pk
        assert inv.cars.filter(pk=car.pk).exists()

    def test_issued_invoice_blocks_in_clean_too(self, warehouse, company, old_client, new_client):
        """Форма админки использует full_clean → ошибка на поле client, а не 500."""
        car = self._car(warehouse, old_client)
        self._invoice(company, old_client, [car], "ISSUED")

        car.client = new_client
        with pytest.raises(ValidationError) as exc:
            car.clean()
        assert "client" in exc.value.message_dict

    def test_draft_detaches_car_and_regenerates(self, warehouse, company, old_client, new_client):
        car1 = self._car(warehouse, old_client, vin="RULESCLIENT000101")
        car2 = self._car(warehouse, old_client, vin="RULESCLIENT000102")
        draft = self._invoice(company, old_client, [car1, car2], "DRAFT")
        assert draft.total == Decimal("200.00")

        car1.client = new_client
        car1.save()

        draft.refresh_from_db()
        assert not draft.cars.filter(pk=car1.pk).exists()
        assert draft.cars.filter(pk=car2.pk).exists()
        assert draft.total == Decimal("100.00")
        assert draft.recipient_client_id == old_client.pk
        assert car1._client_change_result == {"detached": [draft.number], "deleted": []}
        # Новый DRAFT новому клиенту не создаём автоматически.
        assert not NewInvoice.objects.filter(recipient_client=new_client).exists()

    def test_empty_draft_is_deleted(self, warehouse, company, old_client, new_client):
        car = self._car(warehouse, old_client, vin="RULESCLIENT000201")
        draft = self._invoice(company, old_client, [car], "DRAFT")
        number = draft.number

        car.client = new_client
        car.save()

        assert not NewInvoice.objects.filter(pk=draft.pk).exists()
        assert car._client_change_result == {"detached": [], "deleted": [number]}

    def test_other_client_invoices_untouched(self, warehouse, company, old_client, new_client):
        """Инвойсы, где получатель — не старый клиент (напр. входящий FACT склада), не трогаем."""
        car = self._car(warehouse, old_client, vin="RULESCLIENT000301")
        fact = NewInvoice.objects.create(
            issuer_warehouse=warehouse,
            recipient_company=company,
            document_type="INVOICE_FACT",
            date=timezone.now().date(),
            status="ISSUED",
        )
        fact.cars.add(car)

        car.client = new_client
        car.save()

        car.refresh_from_db()
        assert car.client_id == new_client.pk
        assert fact.cars.filter(pk=car.pk).exists()

    def test_update_fields_without_client_skips_check(self, warehouse, company, old_client, new_client):
        car = self._car(warehouse, old_client, vin="RULESCLIENT000401")
        self._invoice(company, old_client, [car], "ISSUED")

        # Денормализованный пересчёт (update_fields без client) не должен спотыкаться о B5.
        car.client = new_client
        car.save(update_fields=["days", "storage_cost", "total_price"])
        car.refresh_from_db()
        assert car.client_id == old_client.pk
