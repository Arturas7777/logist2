"""
Тесты биллинга (NewInvoice, InvoiceItem, Transaction).

Запуск: python manage.py test core.tests.test_billing
"""
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Company, Client, Warehouse, Line
from core.models_billing import NewInvoice, InvoiceItem, ExpenseCategory


class NewInvoiceValidationTest(TestCase):
    """Тесты валидации NewInvoice.clean()"""

    def setUp(self):
        self.company = Company.objects.create(name="Caromoto Lithuania")
        self.client = Client.objects.create(name="Test Client")
        self.warehouse = Warehouse.objects.create(name="Test WH")

    def test_valid_invoice(self):
        """Валидный инвойс проходит clean()"""
        invoice = NewInvoice(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
        )
        # clean() не должен бросать исключение
        invoice.clean()

    def test_no_issuer_raises_error(self):
        """Инвойс без выставителя не проходит валидацию"""
        invoice = NewInvoice(
            recipient_client=self.client,
            date=timezone.now().date(),
        )
        with self.assertRaises(ValidationError):
            invoice.clean()

    def test_no_recipient_raises_error(self):
        """Инвойс без получателя не проходит валидацию"""
        invoice = NewInvoice(
            issuer_company=self.company,
            date=timezone.now().date(),
        )
        with self.assertRaises(ValidationError):
            invoice.clean()

    def test_same_issuer_and_recipient_company(self):
        """Выставитель и получатель не могут быть одной компанией"""
        invoice = NewInvoice(
            issuer_company=self.company,
            recipient_company=self.company,
            date=timezone.now().date(),
        )
        with self.assertRaises(ValidationError) as ctx:
            invoice.clean()
        self.assertIn('recipient_company', ctx.exception.message_dict)

    def test_due_date_before_date(self):
        """Срок оплаты не может быть раньше даты выставления"""
        invoice = NewInvoice(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
            due_date=timezone.now().date() - timezone.timedelta(days=5),
        )
        with self.assertRaises(ValidationError) as ctx:
            invoice.clean()
        self.assertIn('due_date', ctx.exception.message_dict)


class NewInvoiceSaveTest(TestCase):
    """Тесты сохранения NewInvoice"""

    def setUp(self):
        self.company = Company.objects.create(name="Caromoto Lithuania")
        self.client = Client.objects.create(name="Test Client")

    def test_auto_number_generation(self):
        """Номер инвойса генерируется автоматически"""
        invoice = NewInvoice.objects.create(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
        )
        self.assertTrue(invoice.number)
        self.assertTrue(len(invoice.number) > 0)

    def test_auto_due_date(self):
        """Срок оплаты устанавливается автоматически (+14 дней)"""
        invoice = NewInvoice.objects.create(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
        )
        self.assertIsNotNone(invoice.due_date)


class CompanyGetDefaultTest(TestCase):
    """Тесты Company.get_default()"""

    def test_returns_company_by_settings_name(self):
        """get_default() находит компанию по settings.COMPANY_NAME"""
        Company.objects.create(name="Caromoto Lithuania")
        result = Company.get_default()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Caromoto Lithuania")

    def test_returns_none_when_no_company(self):
        """get_default() возвращает None если компании нет"""
        result = Company.get_default()
        self.assertIsNone(result)
