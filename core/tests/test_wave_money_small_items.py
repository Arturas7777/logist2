"""M12: мелочи денежного домена — парсинг, округление, срок оплаты, FK-связи, переводы."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import QueryDict
from django.utils import timezone

from core.admin.billing.invoice_forms import NewInvoiceFormHandlerMixin
from core.models import Car, CarService, Client, Company, Container, Warehouse, WarehouseService
from core.models_billing import InvoiceItem, NewInvoice, PersonalCard, PersonalTransfer, Transaction
from core.services.balance_manager import BalanceManager
from core.services.billing_service import BillingService
from core.services.car_admin_service import update_existing_carservices
from core.utils import parse_money

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(settings):
    return Company.objects.create(name=settings.COMPANY_NAME)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Small Items Client")


def _invoice(company, client, total="100.00", document_type="INVOICE"):
    inv = NewInvoice.objects.create(
        document_type=document_type,
        issuer_company=company,
        recipient_client=client,
        date=timezone.localdate(),
        status="ISSUED",
    )
    InvoiceItem.objects.create(invoice=inv, description="Услуги", quantity=1, unit_price=Decimal(total))
    inv.refresh_from_db()
    return inv


# --- Парсинг сумм -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,50", Decimal("12.50")),
        ("1 200,5", Decimal("1200.50")),
        ("1\u00a0200.555", Decimal("1200.56")),
        (12.5, Decimal("12.50")),
        ("0", Decimal("0.00")),
        ("", None),
        ("  ", None),
        (None, None),
        ("abc", None),
        ("NaN", None),
        ("Infinity", None),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def _car_with_service(markup="15.00"):
    warehouse = Warehouse.objects.create(name="WH-Small", free_days=0)
    service = WarehouseService.objects.create(warehouse=warehouse, name="Разгрузка", default_price=Decimal("100"))
    container = Container.objects.create(number="C-SMALL1", status="FLOATING")
    car = Car.objects.create(
        year=2023, brand="Toyota", vin="SMALLITEMS0000001", status="FLOATING", container=container, warehouse=warehouse
    )
    CarService.objects.update_or_create(
        car=car,
        service_type="WAREHOUSE",
        service_id=service.id,
        defaults={"custom_price": Decimal("100"), "markup_amount": Decimal(markup)},
    )
    return car, service


def test_car_service_price_with_comma_is_applied():
    car, service = _car_with_service()
    post = {f"warehouse_service_{service.id}": "12,50", f"markup_warehouse_service_{service.id}": "3,25"}
    update_existing_carservices(car, post, prefix="warehouse", service_type="WAREHOUSE", removed_services=set())
    cs = CarService.objects.get(car=car, service_type="WAREHOUSE", service_id=service.id)
    assert cs.custom_price == Decimal("12.50")
    assert cs.markup_amount == Decimal("3.25")


def test_invalid_markup_keeps_previous_value():
    car, service = _car_with_service(markup="15.00")
    post = {f"warehouse_service_{service.id}": "abc", f"markup_warehouse_service_{service.id}": "1O"}
    update_existing_carservices(car, post, prefix="warehouse", service_type="WAREHOUSE", removed_services=set())
    cs = CarService.objects.get(car=car, service_type="WAREHOUSE", service_id=service.id)
    assert cs.custom_price == Decimal("100.00")
    assert cs.markup_amount == Decimal("15.00")


def test_empty_markup_clears_it():
    car, service = _car_with_service(markup="15.00")
    post = {f"warehouse_service_{service.id}": "100", f"markup_warehouse_service_{service.id}": ""}
    update_existing_carservices(car, post, prefix="warehouse", service_type="WAREHOUSE", removed_services=set())
    cs = CarService.objects.get(car=car, service_type="WAREHOUSE", service_id=service.id)
    assert cs.markup_amount == Decimal("0.00")


# --- Округление -------------------------------------------------------------


def test_invoice_item_rounds_half_up_before_save(company, client_a):
    inv = NewInvoice.objects.create(issuer_company=company, recipient_client=client_a, date=timezone.localdate())
    item = InvoiceItem.objects.create(invoice=inv, description="Хранение", quantity=3, unit_price=Decimal("10.125"))
    item.refresh_from_db()
    inv.refresh_from_db()
    assert item.unit_price == Decimal("10.13")
    assert item.total_price == Decimal("30.39")
    assert inv.total == Decimal("30.39")


def test_manual_items_with_comma_are_quantized(company, client_a):
    inv = NewInvoice.objects.create(issuer_company=company, recipient_client=client_a, date=timezone.localdate())
    post = QueryDict(mutable=True)
    post["manual_items_json"] = (
        '[{"description": "Доставка", "quantity": "2", "unit_price": "10,125"},'
        ' {"description": "Мусор", "quantity": "NaN", "unit_price": "5"}]'
    )
    NewInvoiceFormHandlerMixin()._handle_manual_items(SimpleNamespace(POST=post), inv)
    inv.refresh_from_db()
    assert list(inv.items.values_list("description", "unit_price", "total_price")) == [
        ("Доставка", Decimal("10.13"), Decimal("20.26"))
    ]
    assert inv.total == Decimal("20.26")
    client_a.refresh_from_db()
    assert client_a.total_balance == Decimal("0.00")


def test_manual_total_with_comma(company, client_a):
    inv = NewInvoice.objects.create(issuer_company=company, recipient_client=client_a, date=timezone.localdate())
    post = QueryDict(mutable=True)
    post["manual_total"] = "99,995"
    NewInvoiceFormHandlerMixin()._handle_manual_items(SimpleNamespace(POST=post), inv)
    inv.refresh_from_db()
    assert inv.total == Decimal("100.00")


# --- Срок оплаты ------------------------------------------------------------


def test_default_due_date_counts_from_invoice_date(company, client_a):
    issued = timezone.localdate() - timezone.timedelta(days=10)
    inv = NewInvoice.objects.create(issuer_company=company, recipient_client=client_a, date=issued)
    assert inv.due_date == issued + timezone.timedelta(days=14)


def test_create_invoice_due_date_from_invoice_date(company, client_a):
    inv = BillingService.create_invoice(
        company, client_a, items=[{"description": "Услуга", "unit_price": "50"}], due_days=30
    )
    assert inv.due_date == inv.date + timezone.timedelta(days=30)


# --- Возврат: FK на исходную транзакцию --------------------------------------


def test_refund_limit_uses_fk_not_description(company, client_a):
    inv = _invoice(company, client_a)
    payment = BillingService.pay_invoice(inv, Decimal("100"), "TRANSFER", client_a)["transaction"]

    # Посторонний REFUND с номером платежа в описании не уменьшает лимит возврата.
    Transaction.objects.create(
        type="REFUND",
        method="TRANSFER",
        status="COMPLETED",
        amount=Decimal("90"),
        from_company=company,
        to_client=client_a,
        description=f"Ручная запись, упоминает {payment.number}",
    )

    first = BillingService.refund(payment, amount=Decimal("60"), reason="Частично")
    assert first.refund_of_id == payment.pk
    with pytest.raises(ValueError, match="превышает доступный остаток"):
        BillingService.refund(payment, amount=Decimal("50"))
    second = BillingService.refund(payment, amount=Decimal("40"))
    assert set(payment.refunds.values_list("pk", flat=True)) == {first.pk, second.pk}

    inv.refresh_from_db()
    client_a.refresh_from_db()
    # Возвраты (60 + 40) и посторонний REFUND (90) зачислены клиенту авансом,
    # счёт снова открыт на 100: итог = 190 − 100.
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "ISSUED"
    assert client_a.balance == Decimal("190.00")
    assert client_a.total_balance == Decimal("90.00")


# --- Кассовая серия: флаг auto_cash ------------------------------------------


def test_reverse_cash_payments_touches_only_auto_payment(company, client_a):
    inv = _invoice(company, client_a, document_type="PROFORMA_BLC")
    manual = BillingService.pay_invoice(inv, Decimal("30"), "CASH", client_a, description="Оплата наличными в кассе")[
        "transaction"
    ]
    BillingService.change_invoice_series(inv, "INVOICE_BLC")
    auto = inv.transactions.get(type="PAYMENT", auto_cash=True)
    assert auto.amount == Decimal("70.00")
    assert not Transaction.objects.get(pk=manual.pk).auto_cash

    BillingService.change_invoice_series(inv, "PROFORMA")
    inv.refresh_from_db()
    auto.refresh_from_db()
    manual.refresh_from_db()
    client_a.refresh_from_db()
    assert auto.status == "CANCELLED"
    assert auto.paired_topup.status == "CANCELLED"
    assert manual.status == "COMPLETED"
    assert inv.paid_amount == Decimal("30.00")
    assert client_a.balance == Decimal("0.00")
    assert client_a.total_balance == Decimal("-70.00")


# --- Личные переводы --------------------------------------------------------


def _transfer(**kwargs):
    pt = PersonalTransfer(date=timezone.now(), **kwargs)
    pt.full_clean()
    pt.save()
    return pt


def test_personal_transfer_executes_once(company):
    card = PersonalCard.objects.create(name="Revolut", balance=Decimal("200.00"))
    pt = _transfer(transfer_type="CARD_TO_CASH", from_card=card, amount=Decimal("50.00"))

    pt.execute(company=company)
    PersonalTransfer.objects.get(pk=pt.pk).execute(company=company)

    card.refresh_from_db()
    company.refresh_from_db()
    pt.refresh_from_db()
    assert card.balance == Decimal("150.00")
    assert pt.executed_at is not None
    assert Transaction.objects.filter(type="ADJUSTMENT", to_company=company).count() == 1
    assert pt.linked_transaction.amount == Decimal("50.00")
    assert company.balance == Decimal("50.00")


def test_card_to_card_transfer(company):
    src = PersonalCard.objects.create(name="A", balance=Decimal("100.00"))
    dst = PersonalCard.objects.create(name="B", balance=Decimal("0.00"))
    pt = _transfer(transfer_type="CARD_TO_CARD", from_card=src, to_card=dst, amount=Decimal("40.00"))
    pt.execute(company=company)
    src.refresh_from_db()
    dst.refresh_from_db()
    assert (src.balance, dst.balance) == (Decimal("60.00"), Decimal("40.00"))
    assert pt.linked_transaction is None
    assert not Transaction.objects.exists()


def test_cash_to_card_moves_cash_out_of_company(company):
    card = PersonalCard.objects.create(name="Card", balance=Decimal("0.00"))
    pt = _transfer(transfer_type="CASH_TO_CARD", to_card=card, amount=Decimal("25.00"))
    pt.execute(company=company)
    card.refresh_from_db()
    company.refresh_from_db()
    assert card.balance == Decimal("25.00")
    assert pt.linked_transaction.from_company_id == company.pk
    assert company.balance == Decimal("-25.00")


# --- Массовый пересчёт балансов ---------------------------------------------


def test_recalculate_all_balances_survives_db_error(company, client_a, monkeypatch):
    other = Client.objects.create(name="ZZZ Second Client")
    BillingService.topup_balance(client_a, Decimal("10"), "TRANSFER")
    BillingService.topup_balance(other, Decimal("20"), "TRANSFER")
    Client.objects.filter(pk__in=[client_a.pk, other.pk]).update(balance=Decimal("0"))

    original = Transaction.recalculate_entity_balance

    def flaky(entity):
        if entity.pk == client_a.pk and isinstance(entity, Client):
            with connection.cursor() as cur:
                cur.execute("SELECT * FROM table_that_does_not_exist")
        return original(entity)

    monkeypatch.setattr(Transaction, "recalculate_entity_balance", staticmethod(flaky))
    result = BalanceManager.recalculate_all_balances()

    assert result["failed"] == 1
    assert not result["success"]
    other.refresh_from_db()
    assert other.balance == Decimal("20.00")
    assert other.total_balance == Decimal("20.00")
