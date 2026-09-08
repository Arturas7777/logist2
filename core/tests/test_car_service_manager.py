"""
Tests for core.services.car_service_manager.

Run: python manage.py test core.tests.test_car_service_manager
"""

from decimal import Decimal

from django.test import TestCase

from core.models import (
    Car,
    CarService,
    Container,
    Line,
    LineService,
    LineTHSCoefficient,
    Warehouse,
    WarehouseService,
)


class CalculateTHSForContainerTest(TestCase):
    """Tests for proportional THS distribution."""

    def setUp(self):
        self.line = Line.objects.create(name="MAERSK")
        self.warehouse = Warehouse.objects.create(name="WH1")
        self.container = Container.objects.create(
            number="THS-001",
            status="FLOATING",
            line=self.line,
            ths=Decimal("500.00"),
        )

    def test_empty_container_returns_empty(self):
        from core.services.car_service_manager import calculate_ths_for_container

        result = calculate_ths_for_container(self.container)
        self.assertEqual(result, {})

    def test_single_car_gets_all_ths(self):
        from core.services.car_service_manager import calculate_ths_for_container

        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="THS01234567890123",
            status="FLOATING",
            container=self.container,
            vehicle_type="SEDAN",
        )
        result = calculate_ths_for_container(self.container)
        self.assertIn(car.id, result)
        self.assertGreater(result[car.id], Decimal("0"))

    def test_distribution_with_coefficients(self):
        from core.services.car_service_manager import calculate_ths_for_container

        LineTHSCoefficient.objects.create(line=self.line, vehicle_type="SEDAN", coefficient=Decimal("1.00"))
        LineTHSCoefficient.objects.create(line=self.line, vehicle_type="SUV", coefficient=Decimal("2.00"))

        sedan = Car.objects.create(
            year=2023,
            brand="Sedan",
            vin="SEDVN12345678901A",
            status="FLOATING",
            container=self.container,
            vehicle_type="SEDAN",
        )
        suv = Car.objects.create(
            year=2023,
            brand="SUV",
            vin="SUVVN12345678901A",
            status="FLOATING",
            container=self.container,
            vehicle_type="SUV",
        )

        result = calculate_ths_for_container(self.container)
        self.assertIn(sedan.id, result)
        self.assertIn(suv.id, result)
        self.assertGreater(result[suv.id], result[sedan.id])

    def test_no_ths_returns_empty(self):
        from core.services.car_service_manager import calculate_ths_for_container

        self.container.ths = Decimal("0")
        self.container.save(update_fields=["ths"])
        result = calculate_ths_for_container(self.container)
        self.assertEqual(result, {})

    def test_no_line_returns_empty(self):
        from core.services.car_service_manager import calculate_ths_for_container

        self.container.line = None
        self.container.save(update_fields=["line_id"])
        result = calculate_ths_for_container(self.container)
        self.assertEqual(result, {})


class ServiceLookupHelperTest(TestCase):
    """Tests for find_*_services_for_car helpers."""

    def test_find_warehouse_services_returns_defaults_only(self):
        from core.services.car_service_manager import find_warehouse_services_for_car

        wh = Warehouse.objects.create(name="WH2")
        WarehouseService.objects.create(
            warehouse=wh,
            name="Regular",
            default_price=10,
            is_active=True,
            add_by_default=False,
        )
        default_svc = WarehouseService.objects.create(
            warehouse=wh,
            name="Auto-add",
            default_price=20,
            is_active=True,
            add_by_default=True,
        )
        result = find_warehouse_services_for_car(wh)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, default_svc.id)

    def test_find_line_services_excludes_ths(self):
        from core.services.car_service_manager import find_line_services_for_car

        line = Line.objects.create(name="CMA")
        LineService.objects.create(
            line=line,
            name="THS CMA",
            default_price=100,
            is_active=True,
            add_by_default=True,
        )
        regular = LineService.objects.create(
            line=line,
            name="Ocean Freight",
            default_price=200,
            is_active=True,
            add_by_default=True,
        )
        result = find_line_services_for_car(line)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, regular.id)

    def test_find_returns_empty_for_none(self):
        from core.services.car_service_manager import (
            find_carrier_services_for_car,
            find_company_services_for_car,
            find_line_services_for_car,
            find_warehouse_services_for_car,
        )

        self.assertEqual(find_warehouse_services_for_car(None), [])
        self.assertEqual(find_line_services_for_car(None), [])
        self.assertEqual(find_carrier_services_for_car(None), [])
        self.assertEqual(find_company_services_for_car(None), [])


class CreateTHSServicesTest(TestCase):
    """Integration test for create_ths_services_for_container."""

    def test_creates_services_for_cars(self):
        from core.services.car_service_manager import create_ths_services_for_container

        line = Line.objects.create(name="MSC")
        container = Container.objects.create(
            number="CREATE-THS-001",
            status="FLOATING",
            line=line,
            ths=Decimal("300.00"),
        )
        Car.objects.create(
            year=2023,
            brand="Honda",
            vin="HNDVN12345678901A",
            status="FLOATING",
            container=container,
            vehicle_type="SEDAN",
        )
        Car.objects.create(
            year=2023,
            brand="Jeep",
            vin="JEPVN12345678901A",
            status="FLOATING",
            container=container,
            vehicle_type="SUV",
        )

        count = create_ths_services_for_container(container)
        self.assertEqual(count, 2)
        self.assertEqual(CarService.objects.filter(service_type="LINE").count(), 2)


class EnsureThsForTransferredCarTest(TestCase):
    """Постфактум: переданному авто без THS услуга появляется при ensure."""

    def setUp(self):
        self.line = Line.objects.create(name="MSC-THS")
        self.warehouse = Warehouse.objects.create(name="NETO-THS")
        WarehouseService.objects.create(
            warehouse=self.warehouse,
            name="THS NETO",
            code="ths",
            short_name="THS",
            default_price=Decimal("0"),
            is_active=True,
            add_by_default=False,
        )
        self.container = Container.objects.create(
            number="MISS-THS-001",
            status="FLOATING",
            line=self.line,
            warehouse=self.warehouse,
            ths=Decimal("250.00"),
            ths_payer="WAREHOUSE",
        )
        self.car = Car.objects.create(
            year=2022,
            brand="Audi",
            vin="WA1FECF38L1035831",
            status="TRANSFERRED",
            container=self.container,
            warehouse=self.warehouse,
            vehicle_type="SEDAN",
        )

    def test_creates_warehouse_ths_when_missing(self):
        from core.services.car_service_manager import ensure_ths_and_tariffs_for_car
        from core.service_codes import is_ths_service

        self.assertFalse(any(is_ths_service(cs) for cs in self.car.car_services.all()))

        created = ensure_ths_and_tariffs_for_car(self.car)

        self.assertTrue(created)
        ths_services = [cs for cs in self.car.car_services.all() if is_ths_service(cs)]
        self.assertEqual(len(ths_services), 1)
        self.assertEqual(ths_services[0].service_type, "WAREHOUSE")
        self.assertEqual(ths_services[0].custom_price, Decimal("250.00"))

    def test_noop_when_ths_already_present(self):
        from core.services.car_service_manager import (
            create_ths_services_for_container,
            ensure_ths_and_tariffs_for_car,
        )

        create_ths_services_for_container(self.container)
        self.assertFalse(ensure_ths_and_tariffs_for_car(self.car))

    def test_generate_invoices_fills_missing_ths(self):
        from django.conf import settings

        from core.models import AutoTransport, Carrier, Client, Company
        from core.models_billing import InvoiceItem
        from core.service_codes import is_ths_service

        Company.objects.create(name=getattr(settings, "COMPANY_NAME", "Caromoto Lithuania"))
        client = Client.objects.create(name="THS Invoice Client")
        self.car.client = client
        self.car.save(update_fields=["client"])
        carrier = Carrier.objects.create(name="THS Carrier")
        trip = AutoTransport.objects.create(carrier=carrier, status="FORMED")
        trip.cars.add(self.car)

        invoices = trip.generate_invoices()
        self.assertEqual(len(invoices), 1)
        self.car.refresh_from_db()
        self.assertTrue(any(is_ths_service(cs) for cs in self.car.car_services.all()))
        descriptions = list(
            InvoiceItem.objects.filter(invoice=invoices[0], car=self.car).values_list("description", flat=True)
        )
        self.assertIn("THS", descriptions)
