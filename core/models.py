from django.db import models
from django.core.validators import MinValueValidator
from .constants import STATUS_COLORS
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db.models import Sum, Q
from django.db import transaction
from decimal import Decimal
import logging

logger = logging.getLogger('django')

# Базовый менеджер для управления обновлениями
class BaseManager(models.Manager):
    def update_related(self, instance):
        pass

# Справочники
class Line(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название линии")

    def __str__(self):
        return self.name

class Client(models.Model):
    name = models.CharField(max_length=100, verbose_name="Имя клиента")
    debt = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Долг")
    cash_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Безналичные")

    def __str__(self):
        return self.name

    def balance_details(self):
        """Детализация баланса по инвойсам и платежам."""
        invoices = self.invoice_set.all()
        payments = self.payment_set.all()
        details = {
            'invoices': [],
            'payments': [],
            'total_debt': str(self.debt),
            'cash_balance': str(self.cash_balance),
            'card_balance': str(self.card_balance)
        }
        for invoice in invoices:
            total_paid = Payment.objects.filter(invoice=invoice).aggregate(total=Sum('amount'))['total'] or 0
            balance = total_paid - invoice.total_amount
            details['invoices'].append({
                'invoice_number': invoice.number,
                'total_amount': str(invoice.total_amount),
                'total_paid': str(total_paid),
                'balance': str(balance),
                'status': 'Переплата' if balance > 0 else 'Задолженность' if balance < 0 else 'Оплачено'
            })
        for payment in payments:
            details['payments'].append({
                'payment_id': payment.id,
                'amount': str(payment.amount),
                'payment_type': payment.payment_type,
                'from_balance': payment.from_balance,
                'from_cash_balance': payment.from_cash_balance,
                'invoice_number': payment.invoice.number if payment.invoice else 'Без инвойса',
                'date': str(payment.date),
                'description': payment.description
            })
        return details

    def can_pay_from_balance(self, amount, payment_type, from_cash_balance):
        logger.info(
            f"Checking can_pay_from_balance for client {self.name}: amount={amount}, payment_type={payment_type}, from_cash_balance={from_cash_balance}")
        if from_cash_balance:
            can_pay = self.cash_balance >= amount
        else:
            can_pay = self.card_balance >= amount
        logger.info(f"Can pay: {can_pay}")
        return can_pay

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

class Warehouse(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название склада")

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """Вычисляет текущий баланс склада."""
        invoices = self.invoice_set.all()
        total_due = invoices.aggregate(total=Sum('total_amount'))['total'] or 0
        total_paid = Payment.objects.filter(invoice__warehouse=self).aggregate(total=Sum('amount'))['total'] or 0
        return total_paid - total_due

    def balance_details(self):
        """Детализация баланса по инвойсам."""
        invoices = self.invoice_set.all()
        details = []
        for invoice in invoices:
            paid = Payment.objects.filter(invoice=invoice).aggregate(total=Sum('amount'))['total'] or 0
            balance = paid - invoice.total_amount
            if balance != 0:
                details.append({
                    'invoice_number': invoice.number,
                    'balance': str(balance),
                    'status': 'Переплата' if balance > 0 else 'Задолженность'
                })
        return details

# Контейнеры
class ContainerManager(BaseManager):
    def update_related(self, instance):
        cars = instance.cars.all()
        if not cars:
            return
        ths_per_car = (instance.ths or 0) / cars.count()
        for car in cars:
            car.sync_with_container(instance, ths_per_car)
            car.save()

class Container(models.Model):
    STATUS_CHOICES = [
        ('FLOATING', 'В пути'),
        ('IN_PORT', 'В порту'),
        ('UNLOADED', 'Разгружен'),
        ('TRANSFERRED', 'Передан'),
    ]

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, '#3a8c3d')  # Темнее зелёного по умолчанию

    CUSTOMS_PROCEDURE_CHOICES = (
        ('TRANSIT', 'Транзит'),
        ('IMPORT', 'Импорт'),
        ('REEXPORT', 'Реэкспорт'),
        ('EXPORT', 'Экспорт'),
    )

    number = models.CharField(max_length=100, unique=True, verbose_name="Номер контейнера")
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='FLOATING', verbose_name="Статус")
    line = models.ForeignKey('Line', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Морская линия")
    eta = models.DateField(null=True, blank=True, verbose_name="ETA")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    customs_procedure = models.CharField(max_length=20, choices=CUSTOMS_PROCEDURE_CHOICES, null=True, blank=True,
                                         verbose_name="Таможенная процедура")
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата линиям",
                              validators=[MinValueValidator(0)])
    sklad = models.DecimalField(max_digits=10, decimal_places=2, default=160, verbose_name="Оплата складу",
                                validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Декларация",
                               validators=[MinValueValidator(0)])
    proft = models.DecimalField(max_digits=10, decimal_places=2, default=20, verbose_name="Наценка",
                                validators=[MinValueValidator(0)])
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка",
                               validators=[MinValueValidator(0)])
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания")

    objects = ContainerManager()

    def update_days_and_storage(self):
        if self.status == 'UNLOADED' and self.unload_date:
            total_days = (timezone.now().date() - self.unload_date).days + 1
            self.days = max(0, total_days - self.free_days)
            self.storage_cost = self.days * (self.rate or 0)
        else:
            self.days = 0
            self.storage_cost = 0

    def sync_cars(self):
        self.update_days_and_storage()
        Container.objects.update_related(self)

    def save(self, *args, **kwargs):
        if self.status == 'UNLOADED' and (not self.warehouse or not self.unload_date):
            raise ValueError("Для статуса 'Разгружен' обязательны поля 'Склад' и 'Дата разгрузки'")
        super().save(*args, **kwargs)
        logger.debug(f"Syncing cars for container {self.number}")
        self.sync_cars()
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {"model": "Container", "id": self.id, "status": self.status}
            }
        )

    def __str__(self):
        return self.number

    class Meta:
        verbose_name = "Контейнер"
        verbose_name_plural = "Контейнеры"

# Автомобили
class CarManager(BaseManager):
    def update_related(self, instance):
        for invoice in instance.invoice_set.all():
            invoice.update_total_amount()
            invoice.save()

class Car(models.Model):
    year = models.PositiveIntegerField(verbose_name="Год выпуска")
    brand = models.CharField(max_length=50, verbose_name="Марка")
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    status = models.CharField(max_length=20, choices=Container.STATUS_CHOICES, verbose_name="Статус")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    transfer_date = models.DateField(null=True, blank=True, verbose_name="Дата передачи")
    warehouse_days = models.PositiveIntegerField(null=True, blank=True, verbose_name="Дней на складе")
    has_title = models.BooleanField(default=False, verbose_name="")
    title_notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания к тайтлу")
    final_storage_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                             verbose_name="Итоговая цена")
    container = models.ForeignKey('Container', on_delete=models.CASCADE, related_name="cars", verbose_name="Контейнер")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                      verbose_name="Текущая цена")
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата линиям",
                              validators=[MinValueValidator(0)])
    sklad = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата складу",
                                validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Декларация",
                               validators=[MinValueValidator(0)])
    proft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Наценка",
                                validators=[MinValueValidator(0)])
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка за сутки",
                               validators=[MinValueValidator(0)])
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")

    objects = CarManager()

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, '#3a8c3d')

    def calculate_total_price(self):
        ths = self.ths or 0
        sklad = self.sklad or 0
        dekl = self.dekl or 0
        proft = self.proft or 0
        storage_cost = self.final_storage_cost if self.status == 'TRANSFERRED' else self.storage_cost
        return ths + sklad + dekl + proft + (storage_cost or 0)

    def update_days_and_storage(self):
        if self.status == 'TRANSFERRED' and self.unload_date and self.transfer_date:
            total_days = (self.transfer_date - self.unload_date).days + 1
            self.warehouse_days = max(0, total_days - self.free_days)
            self.final_storage_cost = self.warehouse_days * (self.rate or 0)
            self.days = self.warehouse_days
            self.storage_cost = self.final_storage_cost
        elif self.status == 'UNLOADED' and self.unload_date:
            total_days = (timezone.now().date() - self.unload_date).days + 1
            self.days = max(0, total_days - self.free_days)
            self.storage_cost = self.days * (self.rate or 0)
            self.warehouse_days = None
            self.final_storage_cost = None
        else:
            self.days = 0
            self.storage_cost = 0
            self.warehouse_days = None
            self.final_storage_cost = None
        self.total_price = self.calculate_total_price()

    def sync_with_container(self, container, ths_per_car):
        self.status = container.status
        self.warehouse = container.warehouse
        self.unload_date = container.unload_date
        self.transfer_date = timezone.now().date() if container.status == 'TRANSFERRED' else None
        self.ths = ths_per_car
        self.sklad = container.sklad
        self.dekl = container.dekl
        self.proft = container.proft
        self.free_days = container.free_days
        self.rate = container.rate
        self.update_days_and_storage()
        self.total_price = self.calculate_total_price()

    def save(self, *args, **kwargs):
        if self.container and not self.client:
            self.client = self.container.client
        if self.status == 'TRANSFERRED' and not self.transfer_date:
            self.transfer_date = timezone.now().date()
        self.update_days_and_storage()
        super().save(*args, **kwargs)
        Car.objects.update_related(self)
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {"model": "Car", "id": self.id, "status": self.status}
            }
        )

    def __str__(self):
        return f"{self.brand} ({self.vin})"

# Инвойсы
class Invoice(models.Model):
    number = models.CharField(max_length=20, unique=True, verbose_name="Номер инвойса")
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    cars = models.ManyToManyField('Car', blank=True, verbose_name="Автомобили")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Сумма")
    issue_date = models.DateField(auto_now_add=True, verbose_name="Дата выпуска")
    paid = models.BooleanField(default=False, verbose_name="Оплачен")
    is_outgoing = models.BooleanField(default=False, verbose_name="Нужно оплатить")

    def update_total_amount(self):
        try:
            total = Decimal('0.00')
            if self.cars.exists():
                for car in self.cars.all():
                    car.update_days_and_storage()
                    price = car.calculate_total_price()
                    logger.debug(f"Car {car.vin} calculated price: {price}")
                    if not self.is_outgoing:
                        total += Decimal(str(price)) if price is not None else Decimal('0.00')
                    else:
                        cost = car.final_storage_cost if car.status == 'TRANSFERRED' else car.storage_cost
                        total += Decimal(str(cost)) if cost is not None else Decimal('0.00')
            self.total_amount = total
            logger.info(f"Updated total_amount for invoice {self.number}: {self.total_amount}")
        except Exception as e:
            logger.error(f"Error calculating total_amount for invoice {self.number}: {e}")
            raise

    @property
    def paid_amount(self):
        return Payment.objects.filter(invoice=self).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def balance(self):
        return self.paid_amount - self.total_amount

    def save(self, *args, **kwargs):
        logger.info(
            f"Saving invoice {self.number}, total_amount={self.total_amount}, client={self.client.name if self.client else 'None'}")
        with transaction.atomic():
            old_invoice = None
            if self.pk:
                try:
                    old_invoice = Invoice.objects.get(pk=self.pk)
                    logger.info(
                        f"Old invoice found: total_amount={old_invoice.total_amount}, client={old_invoice.client.name if old_invoice.client else 'None'}")
                except Invoice.DoesNotExist:
                    logger.warning(f"No old invoice found for id={self.pk}")
            if self.is_outgoing:
                self.client = None
            super().save(*args, **kwargs)
            self.update_total_amount()
            if self.client and not self.is_outgoing:
                if not old_invoice:
                    logger.info(f"Applying new invoice {self.number} for client {self.client.name}")
                    self.client.debt += self.total_amount
                elif old_invoice.total_amount != self.total_amount:
                    logger.info(f"Adjusting debt for client {self.client.name} due to total_amount change")
                    delta = self.total_amount - old_invoice.total_amount
                    self.client.debt += delta
                self.client.save()
                logger.info(f"Client {self.client.name} updated: debt={self.client.debt}")
            self.paid = self.paid_amount >= self.total_amount
            super().save(update_fields=['total_amount', 'paid'])
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "updates",
            {
                "type": "data_update",
                "data": {
                    "model": "Invoice",
                    "id": self.id,
                    "total_amount": str(self.total_amount),
                    "paid": self.paid
                }
            }
        )

    def __str__(self):
        direction = "Нужно оплатить" if self.is_outgoing else "Ждём оплату"
        return f"{self.number} ({direction})"

    class Meta:
        verbose_name = "Инвойс"
        verbose_name_plural = "Инвойсы"

# Платежи
class Payment(models.Model):
    TYPE_CHOICES = (
        ('CASH', 'Наличные'),
        ('CARD', 'Безналичные'),
        ('BALANCE', 'С баланса'),
    )
    invoice = models.ForeignKey('Invoice', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Инвойс")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    payment_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="Тип платежа")
    date = models.DateField(auto_now_add=True, verbose_name="Дата")
    payer = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Плательщик")
    recipient = models.CharField(max_length=100, verbose_name="Получатель")
    from_balance = models.BooleanField(default=False, verbose_name="Оплачено с баланса")
    from_cash_balance = models.BooleanField(default=False, verbose_name="Оплачено с наличного баланса")
    description = models.TextField(blank=True, verbose_name="Описание")

    def save(self, *args, **kwargs):
        logger.info(
            f"Saving payment id={self.pk or 'new'}, amount={self.amount}, payment_type={self.payment_type}, from_balance={self.from_balance}, from_cash_balance={self.from_cash_balance}, payer={self.payer.name if self.payer else 'None'}, invoice={self.invoice.number if self.invoice else 'None'}")
        old_payment = None
        if self.pk:
            try:
                old_payment = Payment.objects.get(pk=self.pk)
                logger.info(
                    f"Old payment found: amount={old_payment.amount}, payment_type={old_payment.payment_type}, from_balance={old_payment.from_balance}, from_cash_balance={old_payment.from_cash_balance}, payer={old_payment.payer.name if old_payment.payer else 'None'}")
            except Payment.DoesNotExist:
                logger.warning(f"No old payment found for id={self.pk}")
        if old_payment and old_payment.payer:
            logger.info(f"Reverting old payment for client {old_payment.payer.name}")
            old_amount = Decimal(str(old_payment.amount))
            if old_payment.from_balance:
                if old_payment.from_cash_balance:
                    old_payment.payer.cash_balance += old_amount
                else:
                    old_payment.payer.card_balance += old_amount
            else:
                if old_payment.invoice:
                    old_payment.payer.debt += old_amount
                    excess = old_payment.invoice.balance if old_payment.invoice else 0
                    if excess > 0:
                        if old_payment.payment_type == 'CASH' or (
                                old_payment.payment_type == 'BALANCE' and old_payment.from_cash_balance):
                            old_payment.payer.cash_balance -= excess
                        elif old_payment.payment_type == 'CARD' or (
                                old_payment.payment_type == 'BALANCE' and not old_payment.from_cash_balance):
                            old_payment.payer.card_balance -= excess
                else:
                    if old_payment.payment_type == 'CASH' or (
                            old_payment.payment_type == 'BALANCE' and old_payment.from_cash_balance):
                        old_payment.payer.cash_balance -= old_amount
                    elif old_payment.payment_type == 'CARD' or (
                            old_payment.payment_type == 'BALANCE' and not old_payment.from_cash_balance):
                        old_payment.payer.card_balance -= old_amount
            old_payment.payer.save()
            logger.info(
                f"Client {old_payment.payer.name} updated after revert: debt={old_payment.payer.debt}, cash_balance={old_payment.payer.cash_balance}, card_balance={old_payment.payer.card_balance}")
        if self.payer:
            amount = Decimal(str(self.amount))
            if self.from_balance:
                if 'Correction' not in self.description:
                    can_pay = self.payer.can_pay_from_balance(amount, self.payment_type, self.from_cash_balance)
                    logger.info(
                        f"Checking balance for client {self.payer.name}: can_pay={can_pay}, amount={amount}, payment_type={self.payment_type}, from_cash_balance={self.from_cash_balance}")
                    if not can_pay:
                        logger.error(
                            f"Insufficient funds for client {self.payer.name}: amount={amount}, from_cash_balance={self.from_cash_balance}")
                        raise ValueError(
                            f"Недостаточно средств на {'наличном' if self.from_cash_balance else 'безналичном'} балансе клиента")
                if self.from_cash_balance:
                    self.payer.cash_balance -= amount
                else:
                    self.payer.card_balance -= amount
            else:
                if self.invoice:
                    if old_payment and not old_payment.invoice:
                        if self.payment_type == 'CASH' or (self.payment_type == 'BALANCE' and self.from_cash_balance):
                            if self.payer.cash_balance >= amount:
                                self.payer.cash_balance -= amount
                            else:
                                logger.error(
                                    f"Insufficient cash_balance for client {self.payer.name}: amount={amount}, cash_balance={self.payer.cash_balance}")
                                raise ValueError(f"Недостаточно средств на наличном балансе клиента")
                        elif self.payment_type == 'CARD' or (
                                self.payment_type == 'BALANCE' and not self.from_cash_balance):
                            if self.payer.card_balance >= amount:
                                self.payer.card_balance -= amount
                            else:
                                logger.error(
                                    f"Insufficient card_balance for client {self.payer.name}: amount={amount}, card_balance={self.payer.card_balance}")
                                raise ValueError(f"Недостаточно средств на безналичном балансе клиента")
                    self.payer.debt -= amount
                else:
                    if self.payment_type == 'CASH' or (self.payment_type == 'BALANCE' and self.from_cash_balance):
                        self.payer.cash_balance += amount
                    elif self.payment_type == 'CARD' or (self.payment_type == 'BALANCE' and not self.from_cash_balance):
                        self.payer.card_balance += amount
            self.payer.save()
            logger.info(
                f"Client {self.payer.name} updated after new payment: debt={self.payer.debt}, cash_balance={self.payer.cash_balance}, card_balance={self.payer.card_balance}")
        super().save(*args, **kwargs)
        if self.invoice and not self.from_balance:
            invoice = self.invoice
            invoice.paid = invoice.paid_amount >= invoice.total_amount
            excess = invoice.balance
            if excess > 0:
                logger.info(f"Overpayment detected for invoice {invoice.number}: excess={excess}")
                if self.payer:
                    if self.payment_type == 'CASH' or (self.payment_type == 'BALANCE' and self.from_cash_balance):
                        self.payer.cash_balance += excess
                    elif self.payment_type == 'CARD' or (self.payment_type == 'BALANCE' and not self.from_cash_balance):
                        self.payer.card_balance += excess
                    self.payer.save()
                    logger.info(
                        f"Client {self.payer.name} updated after overpayment: debt={self.payer.debt}, cash_balance={self.payer.cash_balance}, card_balance={self.payer.card_balance}")
            invoice.save()
            logger.info(f"Invoice {invoice.number} updated: paid={invoice.paid}, balance={invoice.balance}")

    def __str__(self):
        invoice_str = self.invoice.number if self.invoice else "Без инвойса"
        return f"{self.amount} ({self.payment_type}, Инвойс: {invoice_str})"

    class Meta:
        verbose_name = "Платеж"
        verbose_name_plural = "Платежи"

# Декларации
class Declaration(models.Model):
    number = models.CharField(max_length=20, unique=True, verbose_name="Номер декларации")
    container = models.ForeignKey(Container, on_delete=models.CASCADE, verbose_name="Контейнер")
    customs_procedure = models.CharField(max_length=20, choices=Container.CUSTOMS_PROCEDURE_CHOICES,
                                         verbose_name="Таможенная процедура")
    date = models.DateField(verbose_name="Дата оформления")

    def __str__(self):
        return self.number

# Бухгалтерия
class Accounting(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, verbose_name="Инвойс")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Платеж")
    sync_status = models.CharField(max_length=20, default="PENDING", verbose_name="Статус синхронизации")
    sync_date = models.DateTimeField(null=True, blank=True, verbose_name="Дата синхронизации")

    def __str__(self):
        return f"{self.invoice} - {self.sync_status}"