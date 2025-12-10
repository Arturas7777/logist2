from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model

from .models import Client, Warehouse, Container, Car
from .models_billing import NewInvoice as Invoice, Transaction as Payment


class BillingTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Test Client")
        self.warehouse = Warehouse.objects.create(name="WH1", rate=0, free_days=0)
        self.container = Container.objects.create(number="CONT-001")

    def _make_car(self, price: Decimal = Decimal('100.00'), status: str = 'UNLOADED', transferred: bool = False):
        car = Car.objects.create(
            year=2020,
            brand='Brand',
            vin=f"VIN{timezone.now().timestamp()}".replace('.', ''),
            client=self.client_obj,
            status=status,
            warehouse=self.warehouse,
            container=self.container,
            price=price,
            rate=Decimal('0.00'),
            free_days=0,
            proft=Decimal('0.00'),
        )
        if transferred:
            car.status = 'TRANSFERRED'
            car.transfer_date = timezone.now().date()
            car.save()
        return car

    def test_payment_flow_and_overpayment(self):
        car = self._make_car(price=Decimal('100.00'))
        inv = Invoice.objects.create(number="INV-001", client=self.client_obj)
        inv.cars.set([car])
        inv.save()

        self.client_obj.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, Decimal('100.00'))
        self.assertEqual(self.client_obj.debt, Decimal('100.00'))

        p1 = Payment.objects.create(
            invoice=inv,
            amount=Decimal('100.00'),
            payment_type='CASH',
            payer=self.client_obj,
            recipient='WH',
        )
        inv.refresh_from_db()
        self.client_obj.refresh_from_db()
        self.assertTrue(inv.paid)
        self.assertEqual(self.client_obj.debt, Decimal('0.00'))

        p2 = Payment.objects.create(
            invoice=inv,
            amount=Decimal('10.00'),
            payment_type='CASH',
            payer=self.client_obj,
            recipient='WH',
        )
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.cash_balance, Decimal('10.00'))

    def test_payment_from_balance_validation(self):
        self.client_obj.cash_balance = Decimal('5.00')
        self.client_obj.save()
        with self.assertRaises(ValueError):
            Payment.objects.create(
                amount=Decimal('10.00'),
                payment_type='BALANCE',
                from_balance=True,
                from_cash_balance=True,
                payer=self.client_obj,
                recipient='Any',
            )

    def test_edit_payment_reverts_old_effects(self):
        car = self._make_car(price=Decimal('100.00'))
        inv = Invoice.objects.create(number="INV-002", client=self.client_obj)
        inv.cars.set([car])
        inv.save()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('100.00'))

        p = Payment.objects.create(invoice=inv, amount=Decimal('40.00'), payment_type='CASH', payer=self.client_obj, recipient='WH')
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('60.00'))

        p.amount = Decimal('70.00')
        p.save()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.debt, Decimal('30.00'))


class InvoiceCalculationTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Client A")
        self.warehouse = Warehouse.objects.create(name="WH1", rate=0, free_days=0)
        self.container = Container.objects.create(number="CONT-100")

    def test_update_total_amount_uses_current_and_total_correctly(self):
        car1 = Car.objects.create(
            year=2020, brand='B1', vin='VIN-A', client=self.client_obj,
            status='UNLOADED', warehouse=self.warehouse, container=self.container,
            price=Decimal('50.00'), rate=Decimal('0.00'), free_days=0, proft=Decimal('0.00')
        )
        car2 = Car.objects.create(
            year=2020, brand='B2', vin='VIN-B', client=self.client_obj,
            status='TRANSFERRED', warehouse=self.warehouse, container=self.container,
            price=Decimal('30.00'), rate=Decimal('0.00'), free_days=0, transfer_date=timezone.now().date(), proft=Decimal('0.00')
        )

        inv = Invoice.objects.create(number="INV-003", client=self.client_obj)
        inv.cars.set([car1, car2])
        inv.save()
        inv.refresh_from_db()

        self.assertEqual(inv.total_amount, Decimal('80.00'))


class APIPermissionsTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def test_api_requires_staff(self):
        resp_anon = self.client.get('/api/v1/cars/')
        self.assertIn(resp_anon.status_code, (401, 403))

        user = self.User.objects.create_user(username='admin', password='pass', is_staff=True)
        self.client.login(username='admin', password='pass')
        resp_auth = self.client.get('/api/v1/cars/')
        self.assertEqual(resp_auth.status_code, 200)


# Create your tests here.
