"""
Тесты биллинга (NewInvoice, InvoiceItem, Transaction).

Запуск: python manage.py test core.tests.test_billing
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import Client, Company, Warehouse
from core.models_billing import InvoiceItem, NewInvoice


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
        self.assertIn("recipient_company", ctx.exception.message_dict)

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
        self.assertIn("due_date", ctx.exception.message_dict)


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


class RegenerateItemsGuardTest(TestCase):
    """Guard: regenerate_items_from_cars не трогает оплаченные/отменённые инвойсы.

    Регенерация удаляет все позиции и перезаписывает total. Для PAID /
    LINKED_PAID / CANCELLED это нарушило бы инвариант «оплачен = total
    совпадает с paid_amount», поэтому метод должен быть no-op.
    """

    def setUp(self):
        self.company = Company.objects.create(name="Caromoto Lithuania")
        self.client = Client.objects.create(name="Test Client")

    def _make_invoice_with_item(self, status):
        invoice = NewInvoice.objects.create(
            issuer_company=self.company,
            recipient_client=self.client,
            date=timezone.now().date(),
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            description="Тест",
            quantity=1,
            unit_price=Decimal("100.00"),
        )
        # Статус выставляем через .update(), минуя сигналы пересчёта оплаты,
        # чтобы они не вернули PAID → ISSUED при отсутствии транзакций.
        NewInvoice.objects.filter(pk=invoice.pk).update(status=status)
        invoice.refresh_from_db()
        return invoice

    def test_paid_invoice_not_regenerated(self):
        invoice = self._make_invoice_with_item("PAID")
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 1)

    def test_linked_paid_invoice_not_regenerated(self):
        invoice = self._make_invoice_with_item("LINKED_PAID")
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 1)

    def test_cancelled_invoice_not_regenerated(self):
        invoice = self._make_invoice_with_item("CANCELLED")
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 1)

    def test_draft_invoice_is_regenerated(self):
        # DRAFT регенерируется: без привязанных машин позиции очищаются.
        invoice = self._make_invoice_with_item("DRAFT")
        invoice.regenerate_items_from_cars()
        self.assertEqual(invoice.items.count(), 0)

    def test_force_bypasses_guard(self):
        # force=True пересоздаёт позиции независимо от статуса.
        invoice = self._make_invoice_with_item("PAID")
        invoice.regenerate_items_from_cars(force=True)
        self.assertEqual(invoice.items.count(), 0)


class CompanyGetDefaultTest(TestCase):
    """Тесты Company.get_default()"""

    def test_returns_company_by_settings_name(self):
        """get_default() находит компанию по settings.COMPANY_NAME.

        Используем реальное значение settings.COMPANY_NAME, чтобы тест не
        зависел от точного написания (есть варианты «Caromoto Lithuania» и
        «Caromoto Lithuania, MB» в разных окружениях).
        """
        from django.conf import settings

        Company.objects.create(name=settings.COMPANY_NAME)
        result = Company.get_default()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, settings.COMPANY_NAME)

    def test_returns_none_when_no_company(self):
        """get_default() возвращает None если компании нет"""
        result = Company.get_default()
        self.assertIsNone(result)
