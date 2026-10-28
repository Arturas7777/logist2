"""M7: выключение услуги каталога не трогает переданные машины и выставленные счета."""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Car, CarService, Client, Company, CompanyService, Container, Warehouse, WarehouseService
from core.models_billing import InvoiceItem, NewInvoice

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="WH-Deactivate", free_days=0)


@pytest.fixture
def client_a():
    return Client.objects.create(name="Deactivate Client")


@pytest.fixture
def service(warehouse):
    return WarehouseService.objects.create(
        warehouse=warehouse, name="Разгрузка", default_price=Decimal("160"), is_active=True
    )


def _car(vin, warehouse, client, status="UNLOADED", service=None):
    container = Container.objects.create(number=f"C-{vin[-6:]}", status="FLOATING")
    car = Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status=status,
        container=container,
        warehouse=warehouse,
        client=client,
        transfer_date=timezone.localdate() if status == "TRANSFERRED" else None,
    )
    if service is not None:
        CarService.objects.get_or_create(
            car=car,
            service_type="WAREHOUSE",
            service_id=service.id,
            defaults={"custom_price": Decimal("160")},
        )
    return car


def _invoice_with_car(car, client, status):
    company = Company.objects.get_or_create(name="Deactivate Co")[0]
    inv = NewInvoice.objects.create(
        issuer_company=company, recipient_client=client, date=timezone.localdate(), status=status
    )
    inv.cars.add(car)
    return inv


def _has_service(car, service):
    return CarService.objects.filter(car=car, service_type="WAREHOUSE", service_id=service.id).exists()


def _deactivate(service):
    service.is_active = False
    service.save()


def test_deactivation_keeps_transferred_and_issued_cars(warehouse, client_a, service):
    free_car = _car("DEACTIVAT00000001", warehouse, client_a, service=service)
    draft_car = _car("DEACTIVAT00000002", warehouse, client_a, service=service)
    issued_car = _car("DEACTIVAT00000003", warehouse, client_a, service=service)
    transferred_car = _car("DEACTIVAT00000004", warehouse, client_a, status="TRANSFERRED", service=service)
    _invoice_with_car(draft_car, client_a, "DRAFT")
    _invoice_with_car(issued_car, client_a, "ISSUED")

    _deactivate(service)

    assert not _has_service(free_car, service)
    assert not _has_service(draft_car, service)
    assert _has_service(issued_car, service)
    assert _has_service(transferred_car, service)


def test_deactivation_keeps_car_referenced_by_issued_invoice_item(warehouse, client_a, service):
    car = _car("DEACTIVAT00000005", warehouse, client_a, service=service)
    inv = _invoice_with_car(car, client_a, "DRAFT")
    inv.cars.clear()
    InvoiceItem.objects.create(invoice=inv, car=car, description="Разгрузка", quantity=1, unit_price=Decimal("160"))
    NewInvoice.objects.filter(pk=inv.pk).update(status="PAID")

    _deactivate(service)

    assert _has_service(car, service)


def test_zero_price_does_not_remove_service(warehouse, client_a, service):
    car = _car("DEACTIVAT00000006", warehouse, client_a, service=service)

    service.default_price = Decimal("0")
    service.save()

    cs = CarService.objects.get(car=car, service_type="WAREHOUSE", service_id=service.id)
    assert cs.custom_price == Decimal("0")


def test_saving_already_inactive_service_removes_nothing(warehouse, client_a, service):
    _deactivate(service)
    car = _car("DEACTIVAT00000007", warehouse, client_a)
    CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=service.id, custom_price=Decimal("160"))

    service.name = "Разгрузка (архив)"
    service.save()

    assert _has_service(car, service)


def test_company_service_deactivation_keeps_transferred(warehouse, client_a):
    company = Company.objects.create(name="Deactivate Main")
    svc = CompanyService.objects.create(company=company, name="Брокер", default_price=Decimal("50"), is_active=True)
    active_car = _car("DEACTIVAT00000008", warehouse, client_a)
    transferred_car = _car("DEACTIVAT00000009", warehouse, client_a, status="TRANSFERRED")
    for car in (active_car, transferred_car):
        CarService.objects.create(car=car, service_type="COMPANY", service_id=svc.id, custom_price=Decimal("50"))

    svc.is_active = False
    svc.save()

    remaining = set(
        CarService.objects.filter(service_type="COMPANY", service_id=svc.id).values_list("car_id", flat=True)
    )
    assert remaining == {transferred_car.pk}
