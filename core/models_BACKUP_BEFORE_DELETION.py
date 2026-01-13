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

from datetime import timedelta

# Импортируем оптимизированные менеджеры
from .managers import (
    OptimizedCarManager, OptimizedInvoiceManager, OptimizedPaymentManager,
    OptimizedContainerManager, OptimizedClientManager, OptimizedWarehouseManager,
    OptimizedCompanyManager
)



logger = logging.getLogger('django')

def get_current_user():
    """Получить текущего пользователя"""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.auth import get_user
    
    try:
        user = get_user()
        if user.is_authenticated:
            return user.username
        return 'system'
    except:
        return 'system'

# Базовый менеджер для управления обновлениями
class BaseManager(models.Manager):
    def update_related(self, instance):
        pass

# Новые модели для системы балансов



# Справочники
class Line(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название линии")
    
    # Балансы линии
    invoice_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Инвойс-баланс")
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Безнал")
    
    # Услуги и цены
    ocean_freight_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за авто)")
    documentation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость документов")
    handling_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость обработки")
    ths_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="THS сбор (оплата линиям)")
    additional_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы")

    def __str__(self):
        return self.name
    
    def get_balance(self, balance_type):
        """Получить баланс определенного типа"""
        if balance_type == 'INVOICE':
            return self.invoice_balance
        elif balance_type == 'CASH':
            return self.cash_balance
        elif balance_type == 'CARD':
            return self.card_balance
        return Decimal('0.00')
    
    def update_balance(self, balance_type, amount):
        """Обновить баланс определенного типа"""
        if balance_type == 'INVOICE':
            self.invoice_balance += amount
        elif balance_type == 'CASH':
            self.cash_balance += amount
        elif balance_type == 'CARD':
            self.card_balance += amount
        self.save()
    
    def get_balance_summary(self):
        """Получить сводку по всем балансам"""
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.invoice_balance + self.cash_balance + self.card_balance
        }
    
    def update_balance_from_invoices(self):
        """Обновляет инвойс-баланс на основе реальных инвойсов и платежей"""
        from django.db.models import Sum
        from decimal import Decimal
        
        # Сумма всех исходящих инвойсов (мы выставляем счета)
        outgoing_invoices = Invoice.objects.filter(
            from_entity_type='LINE',
            from_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих инвойсов (нам выставляют счета)
        incoming_invoices = Invoice.objects.filter(
            to_entity_type='LINE',
            to_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Инвойс-баланс = входящие - исходящие
        self.invoice_balance = incoming_invoices - outgoing_invoices
        self.save(update_fields=['invoice_balance'])


class Carrier(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название перевозчика")
    short_name = models.CharField(max_length=20, blank=True, null=True, verbose_name="Короткое название")
    contact_person = models.CharField(max_length=100, blank=True, null=True, verbose_name="Контактное лицо")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, null=True, verbose_name="Email")
    
    # Балансы
    invoice_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Инвойс-баланс")
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Безнал")
    
    # Услуги и цены
    transport_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за км)")
    loading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость погрузки")
    unloading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость разгрузки")
    fuel_surcharge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Топливная надбавка")
    additional_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    def __str__(self):
        return self.name
    
    def get_balance(self, balance_type):
        """Получить баланс определенного типа"""
        if balance_type == 'INVOICE':
            return self.invoice_balance
        elif balance_type == 'CASH':
            return self.cash_balance
        elif balance_type == 'CARD':
            return self.card_balance
        return Decimal('0.00')
    
    def update_balance(self, balance_type, amount):
        """Обновить баланс определенного типа"""
        if balance_type == 'INVOICE':
            self.invoice_balance += amount
        elif balance_type == 'CASH':
            self.cash_balance += amount
        elif balance_type == 'CARD':
            self.card_balance += amount
        self.save()
    
    def get_balance_summary(self):
        """Получить сводку по всем балансам"""
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.invoice_balance + self.cash_balance + self.card_balance
        }
    
    def update_balance_from_invoices(self):
        """Обновляет инвойс-баланс на основе реальных инвойсов и платежей"""
        from django.db.models import Sum
        from decimal import Decimal
        
        # Сумма всех исходящих инвойсов (мы выставляем счета)
        outgoing_invoices = Invoice.objects.filter(
            from_entity_type='CARRIER',
            from_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих инвойсов (нам выставляют счета)
        incoming_invoices = Invoice.objects.filter(
            to_entity_type='CARRIER',
            to_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Инвойс-баланс = входящие - исходящие
        self.invoice_balance = incoming_invoices - outgoing_invoices
        self.save(update_fields=['invoice_balance'])
    
    objects = OptimizedCompanyManager()  # Используем CompanyManager для Carrier
    
    class Meta:
        verbose_name = "Перевозчик"
        verbose_name_plural = "Перевозчики"


class Client(models.Model):
    name = models.CharField(max_length=100, verbose_name="Имя клиента")
    
    # Новые балансы
    invoice_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Инвойс-баланс")
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Безнал")
    
    # Устаревшие поля (оставляем для совместимости)
    debt = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Долг/Переплата (устарело)")
    
    def __str__(self):
        return self.name

    @property
    def total_invoiced_amount(self):
        """Общая сумма всех входящих инвойсов клиента"""
        from django.db.models import Sum
        return self.invoice_set.filter(is_outgoing=False).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')

    @property
    def total_paid_amount(self):
        """Общая сумма всех платежей клиента по инвойсам (включая списания с баланса)"""
        from django.db.models import Sum
        return Payment.objects.filter(
            from_client=self,
            invoice__isnull=False  # Только платежи по инвойсам
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')

    @property
    def total_balance_payments(self):
        """Общая сумма всех платежей клиента без инвойса (пополнение баланса)"""
        from django.db.models import Sum
        return Payment.objects.filter(
            from_client=self,
            invoice__isnull=True  # Только платежи без инвойса
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')

    @property
    def total_balance_debits(self):
        """Общая сумма всех списаний с баланса клиента"""
        from django.db.models import Sum
        return Payment.objects.filter(
            from_client=self
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')

    @property
    def real_balance(self):
        """Инвойс-баланс: инвойсы - платежи (положительный = долг, отрицательный = переплата)"""
        return self.total_invoiced_amount - self.total_paid_amount

    @property
    def balance_status(self):
        """Статус баланса для отображения"""
        balance = self.real_balance
        if balance > 0:
            return "ДОЛГ"
        elif balance < 0:
            return "ПЕРЕПЛАТА"
        else:
            return "БАЛАНС"

    @property
    def balance_color(self):
        """Цвет для отображения баланса"""
        balance = self.real_balance
        if balance > 0:
            return "#dc3545"  # красный для долга
        elif balance < 0:
            return "#28a745"  # зеленый для переплаты
        else:
            return "#6c757d"  # серый для нуля

    def get_balance_summary(self):
        """Получить сводку по всем балансам"""
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.invoice_balance + self.cash_balance + self.card_balance,
            'real_balance': self.real_balance,
            'balance_status': self.balance_status,
            'balance_color': self.balance_color
        }

    def update_balance_from_invoices(self):
        """Обновляет инвойс-баланс на основе реальных инвойсов и платежей"""
        self.invoice_balance = self.real_balance
        self.save(update_fields=['invoice_balance'])
    
    def sync_balance_fields(self):
        """Синхронизирует поля баланса с реальными данными"""
        # Обновляем invoice_balance на основе реальных инвойсов
        self.invoice_balance = self.real_balance
        
        # Обновляем наличный и безналичный балансы
        from django.db.models import Sum
        
        # Сумма всех входящих наличных платежей (клиент получает деньги)
        cash_incoming = Payment.objects.filter(
            to_client=self,
            payment_type='CASH',
            invoice__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих безналичных платежей (клиент получает деньги)
        card_incoming = Payment.objects.filter(
            to_client=self,
            payment_type='CARD',
            invoice__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Сумма всех исходящих наличных платежей (клиент отправляет деньги)
        cash_outgoing = Payment.objects.filter(
            from_client=self,
            payment_type='CASH'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Сумма всех исходящих безналичных платежей (клиент отправляет деньги)
        card_outgoing = Payment.objects.filter(
            from_client=self,
            payment_type='CARD'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        self.cash_balance = cash_incoming - cash_outgoing
        self.card_balance = card_incoming - card_outgoing
        
        self.save()

    def balance_details(self):
        """Детальная информация о балансах"""
        summary = self.get_balance_summary()
        
        # Дополнительная информация
        from django.db.models import Sum, Q
        from django.utils import timezone
        
        # Последние платежи
        recent_payments = Payment.objects.filter(
            from_client=self
        ).order_by('-date')[:5]
        
        # Неоплаченные инвойсы
        unpaid_invoices = self.invoice_set.filter(
            Q(paid=False) | Q(paid_amount__lt=models.F('total_amount'))
        ).order_by('issue_date')
        
        # Просроченные инвойсы
        overdue_invoices = unpaid_invoices.filter(
            issue_date__lt=timezone.now().date() - timedelta(days=30)
        )
        
        return {
            **summary,
            'recent_payments': [
                {
                    'amount': p.amount,
                    'type': p.get_payment_type_display(),
                    'date': p.date,
                    'description': p.description
                } for p in recent_payments
            ],
            'unpaid_invoices_count': unpaid_invoices.count(),
            'overdue_invoices_count': overdue_invoices.count(),
            'total_unpaid_amount': unpaid_invoices.aggregate(
                total=Sum('total_amount')
            )['total'] or Decimal('0.00')
        }

    def can_pay_from_balance(self, amount, payment_type, from_cash_balance):
        """Проверяет, может ли клиент оплатить с баланса"""
        if from_cash_balance:
            return self.cash_balance >= amount
        else:
            return self.card_balance >= amount

    def recalculate_balance(self):
        """Пересчитывает все балансы клиента"""
        logger.info(f"Recalculating balance for client {self.name}")
        
        # Синхронизируем поля баланса
        self.sync_balance_fields()
        
        # Обновляем устаревшие поля для совместимости
        self.debt = self.invoice_balance
        
        self.save()
        
        logger.info(f"Client {self.name} balance recalculated: invoice={self.invoice_balance}, cash={self.cash_balance}, card={self.card_balance}")

    objects = OptimizedClientManager()
    
    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

class Warehouse(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название склада")
    
    # Балансы склада
    invoice_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Инвойс-баланс")
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Безнал")

    # Цены на услуги
    default_unloading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена за разгрузку")
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка за сутки",validators=[MinValueValidator(0)])
    complex_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Комплекс",validators=[MinValueValidator(0)])
    delivery_to_warehouse = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Доставка до склада")
    loading_on_trawl = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Погрузка на трал")
    documents_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Документы")
    transfer_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Плата за передачу")
    transit_declaration = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Транзитная декл.")
    export_declaration = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Экспортная декл.")
    additional_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Доп.расходы")

    def __str__(self):
        return self.name

    @property
    def balance(self):
        """Общий баланс склада (сумма всех типов)"""
        return self.invoice_balance + self.cash_balance + self.card_balance
    
    def get_balance(self, balance_type):
        """Получить баланс определенного типа"""
        if balance_type == 'INVOICE':
            return self.invoice_balance
        elif balance_type == 'CASH':
            return self.cash_balance
        elif balance_type == 'CARD':
            return self.card_balance
        return Decimal('0.00')
    
    def update_balance(self, balance_type, amount):
        """Обновить баланс определенного типа"""
        if balance_type == 'INVOICE':
            self.invoice_balance += amount
        elif balance_type == 'CASH':
            self.cash_balance += amount
        elif balance_type == 'CARD':
            self.card_balance += amount
        self.save()
    
    def get_balance_summary(self):
        """Получить сводку по всем балансам"""
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.balance
        }
    
    def update_balance_from_invoices(self):
        """Обновляет инвойс-баланс на основе реальных инвойсов и платежей"""
        from django.db.models import Sum
        from decimal import Decimal
        
        # Сумма всех исходящих инвойсов (мы выставляем счета)
        outgoing_invoices = Invoice.objects.filter(
            from_entity_type='WAREHOUSE',
            from_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих инвойсов (нам выставляют счета)
        incoming_invoices = Invoice.objects.filter(
            to_entity_type='WAREHOUSE',
            to_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Инвойс-баланс = входящие - исходящие
        self.invoice_balance = incoming_invoices - outgoing_invoices
        self.save(update_fields=['invoice_balance'])

    def balance_details(self):
        """Детальная информация о балансах склада"""
        summary = self.get_balance_summary()
        
        # Получаем кастомные услуги склада
        warehouse_services = WarehouseService.objects.filter(warehouse=self, is_active=True)
        services_dict = {}
        for service in warehouse_services:
            services_dict[service.name.lower().replace(' ', '_')] = service.default_price
        
        # Дополнительная информация о складе
        return {
            **summary,
            'services': services_dict
        }

    objects = OptimizedWarehouseManager()

# Контейнеры
class ContainerManager(BaseManager):
    def update_related(self, instance):
        cars = instance.container_cars.all()
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

    objects = OptimizedContainerManager()
    # Сохраняем старый менеджер для совместимости
    legacy_objects = ContainerManager()

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

    def sync_cars_after_warehouse_change(self):
        """
        Применяет новый склад ко всем авто контейнера:
        - ставит warehouse
        - жёстко перезаписывает все складские поля дефолтами нового склада
        - пересчитывает хранение и суммы
        """
        # Проверяем, что у экземпляра есть первичный ключ
        if not self.pk:
            return
            
        for car in self.container_cars.all():
            car.warehouse = self.warehouse
            car.apply_warehouse_defaults(force=True)  # перезаписать rate/free_days и прочее
            # если в контейнере уже стоит дата разгрузки — подтянем и её
            if self.unload_date and not car.unload_date:
                car.unload_date = self.unload_date
            car.update_days_and_storage()
            car.calculate_total_price()
            car.save()

    def sync_cars_after_edit(self):
        """
        Обновляет поля машин после изменения контейнера:
        — проставляет склад/дату разгрузки/клиента, если у авто они пустые,
        — подтягивает дефолты склада (rate/free_days/и т.д.) при пустых/дефолтных значениях,
        — пересчитывает хранение и цены.
        """
        # Проверяем, что у экземпляра есть первичный ключ
        if not self.pk:
            return
            
        from .models import Car  # если файл общий, импорт не обязателен
        for car in self.container_cars.all():
            changed = False

            # базовые связки
            if not car.warehouse and self.warehouse:
                car.warehouse = self.warehouse
                changed = True
            if not car.client and self.client:
                car.client = self.client
                changed = True
            if not car.unload_date and self.unload_date:
                car.unload_date = self.unload_date
                changed = True

            # подтянуть дефолты со склада (перезаписать только пустые/дефолтные)
            if car.warehouse:
                before_rate = car.rate
                before_free = car.free_days
                car.apply_warehouse_defaults(override_on_defaults=True)
                changed = changed or (car.rate != before_rate or car.free_days != before_free)

            # пересчёт
            car.update_days_and_storage()
            car.calculate_total_price()

            if changed:
                car.save()  # сохранит и отправит WS-обновление, если у тебя это в save()
            else:
                # всё равно сохраним, если изменилась стоимость/дни из-за новой даты
                car.save(update_fields=['storage_cost', 'days', 'current_price', 'total_price'])

    def check_and_update_status_from_cars(self):
        """Проверяет статус всех автомобилей в контейнере и обновляет статус контейнера"""
        if not self.pk:
            return
            
        cars = self.container_cars.all()
        if not cars.exists():
            return
            
        # Проверяем, все ли автомобили имеют статус "Передан"
        all_transferred = all(car.status == 'TRANSFERRED' for car in cars)
        
        if all_transferred and self.status != 'TRANSFERRED':
            self.status = 'TRANSFERRED'
            self.save(update_fields=['status'])
            logger.info(f"Container {self.number} status automatically changed to TRANSFERRED")

    def __str__(self):
        return self.number

    class Meta:
        verbose_name = "Контейнер"
        verbose_name_plural = "Контейнеры"
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['client', 'status']),
            models.Index(fields=['warehouse', 'status']),
            models.Index(fields=['line']),
            models.Index(fields=['eta']),
            models.Index(fields=['unload_date']),
        ]

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
    line = models.ForeignKey('Line', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Линия")
    carrier = models.ForeignKey('Carrier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Перевозчик")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    transfer_date = models.DateField(null=True, blank=True, verbose_name="Дата передачи")
    has_title = models.BooleanField(default=False, verbose_name="Тайтл у нас")
    title_notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания к тайтлу")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Итоговая цена")
    current_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Текущая цена")
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Складирование")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    container = models.ForeignKey('Container', on_delete=models.CASCADE, related_name="container_cars", verbose_name="Контейнер")  # Временно изменено для диагностики

    # Расходы
    ths = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Оплата линиям", validators=[MinValueValidator(0)])
    unload_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена за разгрузку", validators=[MinValueValidator(0)])
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Доставка до склада", validators=[MinValueValidator(0)])
    loading_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Погрузка на трал", validators=[MinValueValidator(0)])
    docs_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Документы", validators=[MinValueValidator(0)])
    transfer_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Плата за передачу", validators=[MinValueValidator(0)])
    transit_declaration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транзитная декл.", validators=[MinValueValidator(0)])
    export_declaration = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Экспортная декл.", validators=[MinValueValidator(0)])
    extra_costs = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Доп.расходы", validators=[MinValueValidator(0)])
    dekl = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Декларация", validators=[MinValueValidator(0)])
    proft = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('20.00'), null=True, blank=True, verbose_name="Наценка", validators=[MinValueValidator(0)])
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=5, verbose_name="Ставка за сутки", validators=[MinValueValidator(0)])
    complex_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Комплекс", validators=[MinValueValidator(0)])
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена", validators=[MinValueValidator(0)])
    auction_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Аукционный сбор", validators=[MinValueValidator(0)])
    transport_usa = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транспорт США", validators=[MinValueValidator(0)])
    ocean_freight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Океанский фрахт", validators=[MinValueValidator(0)])
    transport_kz = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Транспорт КЗ", validators=[MinValueValidator(0)])
    broker_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Брокерский сбор", validators=[MinValueValidator(0)])
    additional_expenses = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Дополнительные расходы", validators=[MinValueValidator(0)])

    objects = OptimizedCarManager()
    # Сохраняем старый менеджер для совместимости
    legacy_objects = CarManager()

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, '#3a8c3d')

    def apply_warehouse_defaults(self, force: bool = False):
        """
        Копирует дефолты со склада в авто из кастомных услуг.
        force=True — перезаписывает ВСЕ соответствующие поля значениями склада.
        force=False — перезаписывает только если поле пустое или равно дефолту модели.
        """
        if not self.warehouse:
            return

        from decimal import Decimal

        # Получаем кастомные услуги склада
        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)
        
        # Маппинг названий услуг на поля автомобиля
        service_mapping = {
            'Цена за разгрузку': 'unload_fee',
            'Доставка до склада': 'delivery_fee', 
            'Погрузка на трал': 'loading_fee',
            'Документы': 'docs_fee',
            'Плата за передачу': 'transfer_fee',
            'Транзитная декл.': 'transit_declaration',
            'Экспортная декл.': 'export_declaration',
            'Доп.расходы': 'extra_costs',
            'Комплекс': 'complex_fee',
            'Ставка за сутки': 'rate',
            'Бесплатные дни': 'free_days',
        }

        for service in warehouse_services:
            car_field = service_mapping.get(service.name)
            if car_field:
                wh_val = service.default_price or 0
                cur_val = getattr(self, car_field, None)

                if force:
                    setattr(self, car_field, wh_val)
                else:
                    if cur_val is None:
                        setattr(self, car_field, wh_val)
                    else:
                        try:
                            cur = Decimal(str(cur_val))
                            if cur == 0:
                                setattr(self, car_field, wh_val)
                            else:
                                model_default = self._meta.get_field(car_field).default
                                mdl = Decimal(str(model_default or 0))
                                if cur == mdl:
                                    setattr(self, car_field, wh_val)
                        except Exception:
                            setattr(self, car_field, wh_val)



    def warehouse_details(self):
        """Возвращает дефолтные цены на услуги из склада из кастомных услуг."""
        if not self.warehouse:
            return {"message": "Склад не назначен"}
        
        # Получаем кастомные услуги склада
        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)
        
        details = {"Название": self.warehouse.name}
        for service in warehouse_services:
            details[service.name] = str(service.default_price)
        
        return details

    def set_initial_warehouse_values(self):
        """Подтягивает дефолты со склада при создании авто.
        Если текущее значение = модельному дефолту (например, rate=5) или 0 — берём значение со склада.
        """
        if not self.warehouse:
            return

        initializing = self._state.adding

        def _override(cur_val, field_name, wh_val):
            if cur_val is None:
                return wh_val
            if initializing:
                # перетираем, если 0 или равно модельному дефолту
                try:
                    cur = Decimal(str(cur_val))
                    if cur == 0:
                        return wh_val
                    model_default = self._meta.get_field(field_name).default
                    mdl = Decimal(str(model_default or 0))
                    if cur == mdl:
                        return wh_val
                except Exception:
                    return wh_val
            return cur_val

        # ключевые поля
        self.rate = _override(self.rate, 'rate', self.warehouse.rate or 0)
        self.free_days = _override(self.free_days, 'free_days', self.warehouse.free_days or 0)

        # заодно остальные складские услуги, если нужно
        self.unload_fee = _override(self.unload_fee, 'unload_fee', self.warehouse.default_unloading_fee or 0)
        self.delivery_fee = _override(self.delivery_fee, 'delivery_fee', self.warehouse.delivery_to_warehouse or 0)
        self.loading_fee = _override(self.loading_fee, 'loading_fee', self.warehouse.loading_on_trawl or 0)
        self.docs_fee = _override(self.docs_fee, 'docs_fee', self.warehouse.documents_fee or 0)
        self.transfer_fee = _override(self.transfer_fee, 'transfer_fee', self.warehouse.transfer_fee or 0)
        self.transit_declaration = _override(self.transit_declaration, 'transit_declaration',
                                             self.warehouse.transit_declaration or 0)
        self.export_declaration = _override(self.export_declaration, 'export_declaration',
                                            self.warehouse.export_declaration or 0)
        self.extra_costs = _override(self.extra_costs, 'extra_costs', self.warehouse.additional_expenses or 0)
        self.complex_fee = _override(self.complex_fee, 'complex_fee', self.warehouse.complex_fee or 0)

    def calculate_total_price(self):
        """Пересчитывает текущую и итоговую цену используя только новую систему услуг."""
        # Получаем суммы по поставщикам из новой системы
        line_total = self.get_services_total_by_provider('LINE')
        carrier_total = self.get_services_total_by_provider('CARRIER')
        
        # Склад - разделяем хранение и услуги
        try:
            storage_cost = self.calculate_storage_cost()
            # Получаем только услуги склада (без хранения)
            warehouse_services_only = self.get_warehouse_services_total()
        except Exception as e:
            storage_cost = Decimal('0.00')
            warehouse_services_only = Decimal('0.00')
        
        warehouse_total = storage_cost + warehouse_services_only
        
        # Наценка Caromoto Lithuania из поля proft автомобиля
        markup_amount = self.proft or Decimal('0.00')
        
        # Общая сумма всех услуг (без наценки)
        services_total = line_total + warehouse_total + carrier_total
        
        # Добавляем наценку
        total_with_markup = services_total + markup_amount

        self.update_days_and_storage()

        if self.status == 'TRANSFERRED' and self.transfer_date:
            self.total_price = Decimal(str(total_with_markup)) + Decimal(str(self.storage_cost or 0))
            self.current_price = Decimal('0.00')
        else:
            self.current_price = Decimal(str(total_with_markup)) + Decimal(str(self.storage_cost or 0))
            self.total_price = Decimal('0.00')

        return self.current_price, self.total_price

    def update_days_and_storage(self):
        """Обновляет платные дни и стоимость хранения для автомобиля."""
        if not self.unload_date or not self.warehouse:
            self.days = 0
            self.storage_cost = Decimal('0.00')
            return

        # используем значения из склада, а не из Car
        free_days = int(self.warehouse.free_days or 0)
        rate = Decimal(str(self.warehouse.rate or 0))

        end_date = self.transfer_date if self.status == 'TRANSFERRED' and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1
        self.days = max(0, total_days - free_days)
        self.storage_cost = Decimal(str(self.days)) * rate

    def sync_with_container(self, container, ths_per_car):
        """Синхронизирует данные автомобиля с контейнером."""
        self.status = container.status
        self.warehouse = container.warehouse
        self.unload_date = container.unload_date
        self.transfer_date = timezone.now().date() if container.status == 'TRANSFERRED' else None
        self.ths = ths_per_car
        self.dekl = container.dekl
        self.proft = container.proft
        self.set_initial_warehouse_values()
        self.update_days_and_storage()
        self.calculate_total_price()

    WAREHOUSE_FEE_FIELDS = (
        'unload_fee',  # цена за разгрузку
        'delivery_fee',  # доставка до склада
        'loading_fee',  # погрузка на трал
        'docs_fee',  # документы
        'transfer_fee',  # плата за передачу
        'transit_declaration',  # транзитная декл.
        'export_declaration',  # экспортная декл.
        'extra_costs',  # доп.расходы
        'complex_fee',  # комплекс
    )

    def warehouse_payment_amount(self) -> Decimal:
        """Сколько должны складу за услуги (без учёта хранения) - использует новую систему услуг."""
        return self.get_warehouse_services_total()

    @property
    def warehouse_payment(self) -> Decimal:
        # удобное свойство, если где-то понадобится
        return self.warehouse_payment_amount()
    
    def get_line_services(self):
        """Получает услуги линии для этого автомобиля"""
        if not self.line or not self.pk:
            return self.car_services.none()
        # Получаем ID услуг линии
        line_service_ids = LineService.objects.only('id').filter(line=self.line).values_list('id', flat=True)
        return self.car_services.filter(service_type='LINE', service_id__in=line_service_ids)
    
    def get_carrier_services(self):
        """Получает услуги перевозчика для этого автомобиля"""
        if not self.carrier or not self.pk:
            return self.car_services.none()
        # Получаем ID услуг перевозчика
        carrier_service_ids = CarrierService.objects.only('id').filter(carrier=self.carrier).values_list('id', flat=True)
        return self.car_services.filter(service_type='CARRIER', service_id__in=carrier_service_ids)
    
    def get_warehouse_services(self):
        """Получает услуги склада для этого автомобиля"""
        if not self.warehouse or not self.pk:
            return self.car_services.none()
        # Получаем ID услуг склада
        warehouse_service_ids = WarehouseService.objects.only('id').filter(warehouse=self.warehouse).values_list('id', flat=True)
        return self.car_services.filter(service_type='WAREHOUSE', service_id__in=warehouse_service_ids)
    
    def get_services_total_by_provider(self, provider_type):
        """Получает общую стоимость услуг по типу поставщика"""
        total = Decimal('0.00')
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return total
        
        if provider_type == 'WAREHOUSE':
            # Для склада добавляем стоимость хранения на основе дней и ставки
            storage_cost = self.calculate_storage_cost()
            total += storage_cost
            
        for service in services:
            # Считаем все услуги как обычные
            total += Decimal(str(service.final_price))
        return total
    
    def get_warehouse_services_total(self):
        """Получает стоимость только услуг склада (без хранения)"""
        if not self.warehouse:
            return Decimal('0.00')
        
        services = self.get_warehouse_services()
        total = Decimal('0.00')
        
        for service in services:
            total += Decimal(str(service.final_price))
        
        return total
    
    def calculate_storage_cost(self):
        """Рассчитывает стоимость хранения на складе на основе дней и ставки"""
        if not self.warehouse or not self.unload_date:
            return Decimal('0.00')
        
        # Получаем ставку и бесплатные дни со склада
        daily_rate = self.warehouse.rate or Decimal('0.00')
        free_days = self.warehouse.free_days or 0
        
        # Рассчитываем общее количество дней хранения
        # Включаем день разгрузки и день забора авто
        end_date = self.transfer_date if self.status == 'TRANSFERRED' and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1
        
        # Рассчитываем платные дни (общие дни минус бесплатные)
        chargeable_days = max(0, total_days - free_days)
        
        # Рассчитываем стоимость
        storage_cost = daily_rate * chargeable_days
        
        return storage_cost

    def get_rates_by_provider(self, provider_type):
        """Получает ставки по типу поставщика"""
        rates = []
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return rates
            
        # Пока возвращаем пустой список (когда добавим service_type, изменим логику)
        return rates

    def get_parameters_by_provider(self, provider_type):
        """Получает параметры расчета по типу поставщика"""
        parameters = []
        if provider_type == 'LINE' and self.line:
            services = self.get_line_services()
        elif provider_type == 'CARRIER' and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == 'WAREHOUSE' and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return parameters
            
        # Пока возвращаем пустой список (когда добавим service_type, изменим логику)
        return parameters

    def save(self, *args, **kwargs):
        # подхватить данные с контейнера ДО копирования дефолтов
        # Проверяем, что у контейнера есть первичный ключ
        if not self.warehouse and self.container and self.container.pk and self.container.warehouse:
            self.warehouse = self.container.warehouse
        if not self.unload_date and self.container and self.container.pk and self.container.unload_date:
            self.unload_date = self.container.unload_date

        if self.transfer_date and self.status != 'TRANSFERRED':
            self.status = 'TRANSFERRED'

        # на создании — тянем дефолты склада (в т.ч. rate/free_days) ДО первого save()
        if self.pk is None and self.warehouse:
            try:
                self.set_initial_warehouse_values()
            except Exception as e:
                logger.error(f"Failed to set initial warehouse values for car {self.vin}: {e}")

        if self.status == 'TRANSFERRED' and not self.transfer_date:
            from django.utils import timezone
            self.transfer_date = timezone.now().date()

        # Сохраняем объект сначала, чтобы получить pk
        super().save(*args, **kwargs)
        
        # пересчёт ПОСЛЕ сохранения, когда у объекта уже есть pk
        try:
            old_current_price = self.current_price
            old_total_price = self.total_price
            
            self.calculate_total_price()
            
            # Сохраняем еще раз с пересчитанными ценами, только если цены изменились
            if (self.current_price != old_current_price or self.total_price != old_total_price):
                super().save(update_fields=['current_price', 'total_price'])
        except Exception as e:
            logger.error(f"Failed to calculate total price for car {self.vin}: {e}")

        # Обновляем связанные объекты только если у автомобиля есть первичный ключ
        if self.pk:
            try:
                Car.objects.update_related(self)
            except Exception as e:
                logger.error(f"Failed to update related objects for car {self.id}: {e}")
            
            # Проверяем и обновляем статус контейнера, если все автомобили переданы
            if self.container and self.container.pk:
                try:
                    self.container.check_and_update_status_from_cars()
                except Exception as e:
                    logger.error(f"Failed to check container status for car {self.id}: {e}")

        def _notify():
            try:
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    "updates",
                    {
                        "type": "data_update",
                        "data": {
                            "model": "Car",
                            "id": self.id,
                            "status": self.status,
                            "storage_cost": str(self.storage_cost),
                            "days": self.days,
                            "current_price": str(self.current_price),
                            "total_price": str(self.total_price),
                        },
                    },
                )
            except Exception as e:
                logger.error(f"Failed to send WebSocket notification for car {self.id}: {e}")
        transaction.on_commit(_notify)

    def __str__(self):
        return f"{self.brand} ({self.vin})"

    class Meta:
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        indexes = [
            models.Index(fields=['vin']),
            models.Index(fields=['status']),
            models.Index(fields=['unload_date', 'transfer_date']),
            # Дополнительные индексы для оптимизации запросов
            models.Index(fields=['client', 'status']),
            models.Index(fields=['warehouse', 'status']),
            models.Index(fields=['line']),
            models.Index(fields=['carrier']),
            models.Index(fields=['container']),
            models.Index(fields=['unload_date']),
            models.Index(fields=['transfer_date']),
        ]


# Инвойсы
class Invoice(models.Model):
    ENTITY_TYPES = [
        ('CLIENT', 'Клиент'),
        ('WAREHOUSE', 'Склад'),
        ('LINE', 'Линия'),
        ('CARRIER', 'Перевозчик'),
        ('COMPANY', 'Компания'),
    ]
    
    SERVICE_TYPES = [
        ('WAREHOUSE_SERVICES', 'Услуги склада'),
        ('LINE_SERVICES', 'Услуги линий'),
        ('CARRIER_SERVICES', 'Услуги перевозчиков'),
        ('TRANSPORT_SERVICES', 'Транспортные услуги'),
        ('OTHER_SERVICES', 'Прочие услуги'),
    ]
    
    number = models.CharField(max_length=20, unique=True, verbose_name="Номер инвойса")
    
    # Новые поля для отправителя и получателя инвойса
    from_entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES, default='COMPANY', verbose_name="Тип отправителя")
    from_entity_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID отправителя")
    
    to_entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES, default='CLIENT', verbose_name="Тип получателя")
    to_entity_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID получателя")
    
    # Тип услуг, за которые выставляется инвойс
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPES, default='WAREHOUSE_SERVICES', verbose_name="Тип услуг")
    
    # Устаревшие поля (оставляем для совместимости)
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент (устарело)")
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад (устарело)")
    
    cars = models.ManyToManyField('Car', blank=True, verbose_name="Автомобили")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Сумма")
    issue_date = models.DateField(auto_now_add=True, verbose_name="Дата выпуска")
    paid = models.BooleanField(default=False, verbose_name="Оплачен")
    is_outgoing = models.BooleanField(default=False, verbose_name="Нужно оплатить")

    def update_total_amount(self):
        try:
            total = Decimal('0.00')
            # Проверяем, что у экземпляра есть первичный ключ
            if not self.pk:
                return
            if self.cars.exists():  # Это поле ManyToMany в Invoice, не меняем
                for car in self.cars.all():
                    cur, tot = car.calculate_total_price()
                    
                    if self.service_type == 'WAREHOUSE_SERVICES':
                        # Услуги склада: только стоимость хранения
                        total += Decimal(str(car.storage_cost or 0))
                    elif self.service_type == 'LINE_SERVICES':
                        # Услуги линий: стоимость перевозки + THS сбор
                        total += Decimal(str(car.ocean_freight or 0))
                        total += Decimal(str(car.ths or 0))
                    elif self.service_type == 'CARRIER_SERVICES':
                        # Услуги перевозчиков: доставка до склада + перевозка по Казахстану
                        total += Decimal(str(car.delivery_fee or 0)) + Decimal(str(car.transport_kz or 0))
                    elif self.service_type == 'TRANSPORT_SERVICES':
                        # Транспортные услуги: доставка до склада + перевозка по Казахстану
                        total += Decimal(str(car.delivery_fee or 0)) + Decimal(str(car.transport_kz or 0))
                    else:
                        # Прочие услуги или клиентский инвойс: полная стоимость
                        if not self.is_outgoing:
                            # клиентский инвойс: если авто передано — берём итог, иначе текущую
                            total += tot if (tot and tot > 0) else (cur or Decimal('0.00'))
                        else:
                            # исходящий: считаем фактическое хранение
                            total += Decimal(str(car.storage_cost or 0))
                            
            self.total_amount = total
            # Сохраняем в базу данных
            Invoice.objects.filter(pk=self.pk).update(total_amount=self.total_amount)
            logger.info(f"Updated total_amount for invoice {self.number} (service_type: {self.service_type}): {self.total_amount}")
        except Exception as e:
            logger.error(f"Error calculating total_amount for invoice {self.number}: {e}")
            raise

    @property
    def from_entity(self):
        """Получает объект отправителя инвойса"""
        if not self.from_entity_id:
            return None
        
        try:
            if self.from_entity_type == 'CLIENT':
                return Client.objects.get(id=self.from_entity_id)
            elif self.from_entity_type == 'WAREHOUSE':
                return Warehouse.objects.get(id=self.from_entity_id)
            elif self.from_entity_type == 'LINE':
                return Line.objects.get(id=self.from_entity_id)
            elif self.from_entity_type == 'COMPANY':
                return Company.objects.get(id=self.from_entity_id)
        except (Client.DoesNotExist, Warehouse.DoesNotExist, Line.DoesNotExist, Company.DoesNotExist):
            return None
        return None
    
    @property
    def to_entity(self):
        """Получает объект получателя инвойса"""
        if not self.to_entity_id:
            return None
        
        try:
            if self.to_entity_type == 'CLIENT':
                return Client.objects.get(id=self.to_entity_id)
            elif self.to_entity_type == 'WAREHOUSE':
                return Warehouse.objects.get(id=self.to_entity_id)
            elif self.to_entity_type == 'LINE':
                return Line.objects.get(id=self.to_entity_id)
            elif self.to_entity_type == 'COMPANY':
                return Company.objects.get(id=self.to_entity_id)
        except (Client.DoesNotExist, Warehouse.DoesNotExist, Line.DoesNotExist, Company.DoesNotExist):
            return None
        return None
    
    @property
    def from_entity_name(self):
        """Получает имя отправителя инвойса"""
        entity = self.from_entity
        if entity:
            return str(entity)
        return f"{self.get_from_entity_type_display()} #{self.from_entity_id}" if self.from_entity_id else "Не указан"
    
    @property
    def to_entity_name(self):
        """Получает имя получателя инвойса"""
        entity = self.to_entity
        if entity:
            return str(entity)
        return f"{self.get_to_entity_type_display()} #{self.to_entity_id}" if self.to_entity_id else "Не указан"
    
    @property
    def paid_amount(self):
        return Payment.objects.filter(invoice=self).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def balance(self):
        paid = self.paid_amount or Decimal('0.00')
        total = self.total_amount or Decimal('0.00')
        return paid - total

    @property
    def due_date(self):
        try:
            return (self.issue_date or timezone.now().date()) + timedelta(days=14)
        except Exception:
            return None

    @property
    def is_overdue(self):
        try:
            return (not self.paid) and self.due_date and self.due_date < timezone.now().date()
        except Exception:
            return False

    @property
    def is_partial(self) -> bool:
        try:
            total = Decimal(str(self.total_amount or 0))
            paid = Decimal(str(self.paid_amount or 0))
            return paid > 0 and paid < total
        except Exception:
            return False

    @property
    def payment_status(self) -> str:
        if self.paid:
            return 'PAID'
        if self.is_partial:
            return 'PARTIAL'
        return 'UNPAID'

    def save(self, *args, **kwargs):
        logger.info(
            f"Saving invoice {self.number}, total_amount={self.total_amount}, from={self.from_entity_name}, to={self.to_entity_name}")
        
        # Автоматически устанавливаем компанию Caromoto Lithuania как отправителя по умолчанию
        if not self.from_entity_id:
            try:
                default_company = Company.objects.get(name="Caromoto Lithuania")
                self.from_entity_type = 'COMPANY'
                self.from_entity_id = default_company.id
                logger.info(f"Автоматически установлена компания по умолчанию: {default_company.name}")
            except Company.DoesNotExist:
                logger.warning("Компания Caromoto Lithuania не найдена")
        
        # Автоматически заполняем устаревшие поля для совместимости
        if self.from_entity_type == 'CLIENT' and self.from_entity_id:
            self.client = Client.objects.get(id=self.from_entity_id)
        elif self.to_entity_type == 'CLIENT' and self.to_entity_id:
            self.client = Client.objects.get(id=self.from_entity_id)
        
        if self.from_entity_type == 'WAREHOUSE' and self.from_entity_id:
            self.warehouse = Warehouse.objects.get(id=self.from_entity_id)
        elif self.to_entity_type == 'WAREHOUSE' and self.to_entity_id:
            self.warehouse = Warehouse.objects.get(id=self.to_entity_id)
        
        with transaction.atomic():
            old_invoice = None
            if self.pk:
                try:
                    old_invoice = Invoice.objects.get(pk=self.pk)
                    logger.info(
                        f"Old invoice found: total_amount={old_invoice.total_amount}, from={old_invoice.from_entity_name}, to={old_invoice.to_entity_name}")
                except Invoice.DoesNotExist:
                    logger.warning(f"No old invoice found for id={self.pk}")
            
            if self.is_outgoing:
                self.client = None
            
            # Сначала сохраняем инвойс, чтобы получить id
            super().save(*args, **kwargs)
            
            # Теперь можем обновить сумму на основе автомобилей
            self.update_total_amount()
            
            # Пересчитываем баланс получателя на основе реальных инвойсов и платежей
            if self.to_entity and hasattr(self.to_entity, 'update_balance_from_invoices'):
                self.to_entity.update_balance_from_invoices()
            
            # Пометим оплачено только при наличии платежей >= суммы
            paid_amt = Decimal(str(self.paid_amount or 0))
            total_amt = Decimal(str(self.total_amount or 0))
            self.paid = paid_amt >= total_amt and total_amt > 0
            # Обновляем только поле paid, чтобы избежать рекурсии
            Invoice.objects.filter(pk=self.pk).update(paid=self.paid)

        def _notify():
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
        transaction.on_commit(_notify)

    def recalculate_client_balance(self):
        """Пересчитывает баланс клиента на основе реальных инвойсов и платежей"""
        if not self.client:
            return
            
        from django.db.models import Sum
        
        # Сумма всех входящих инвойсов клиента
        total_invoiced = Invoice.objects.filter(
            client=self.client, 
            is_outgoing=False
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Сумма всех платежей клиента по инвойсам (включая списания с баланса)
        total_paid = Payment.objects.filter(
            from_client=self.client,
            invoice__isnull=False  # Только платежи по инвойсам
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Реальный долг = инвойсы - платежи
        real_debt = total_invoiced - total_paid
        
        # Обновляем поле debt
        if self.client.debt != real_debt:
            logger.info(f"Updating client {self.client.name} debt from {self.client.debt} to {real_debt}")
            self.client.debt = real_debt
            self.client.save()

    def __str__(self):
        direction = "Нужно оплатить" if self.is_outgoing else "Ждём оплату"
        return f"{self.number} ({direction})"

    objects = OptimizedInvoiceManager()
    
    class Meta:
        verbose_name = "Инвойс"
        verbose_name_plural = "Инвойсы"
        indexes = [
            models.Index(fields=['is_outgoing', 'paid']),
            # Дополнительные индексы для оптимизации запросов
            models.Index(fields=['from_entity_type', 'from_entity_id']),
            models.Index(fields=['to_entity_type', 'to_entity_id']),
            models.Index(fields=['issue_date', 'paid']),
            models.Index(fields=['service_type']),
            models.Index(fields=['client']),
            models.Index(fields=['warehouse']),
        ]

class Company(models.Model):
    """Модель для логистической компании Caromoto Lithuania"""
    
    name = models.CharField(max_length=100, default="Caromoto Lithuania", verbose_name="Название компании")
    
    # Балансы компании
    invoice_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Инвойс-баланс")
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Наличные")
    card_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Безнал")
    
    # Метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    objects = OptimizedCompanyManager()
    
    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"
    
    def __str__(self):
        return self.name
    
    def get_balance(self, balance_type):
        """Получить баланс определенного типа"""
        if balance_type == 'INVOICE':
            return self.invoice_balance
        elif balance_type == 'CASH':
            return self.cash_balance
        elif balance_type == 'CARD':
            return self.card_balance
        return Decimal('0.00')
    
    def update_balance(self, balance_type, amount):
        """Обновить баланс определенного типа"""
        if balance_type == 'INVOICE':
            self.invoice_balance += amount
        elif balance_type == 'CASH':
            self.cash_balance += amount
        elif balance_type == 'CARD':
            self.card_balance += amount
        self.save()
    
    def get_balance_summary(self):
        """Получить сводку по всем балансам"""
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.invoice_balance + self.cash_balance + self.card_balance
        }
    
    def update_balance_from_invoices(self):
        """Обновляет инвойс-баланс на основе реальных инвойсов и платежей"""
        from django.db.models import Sum
        from decimal import Decimal
        
        # Сумма всех исходящих инвойсов (мы выставляем счета)
        outgoing_invoices = Invoice.objects.filter(
            from_entity_type='COMPANY',
            from_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих инвойсов (нам выставляют счета)
        incoming_invoices = Invoice.objects.filter(
            to_entity_type='COMPANY',
            to_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Инвойс-баланс = входящие - исходящие
        self.invoice_balance = incoming_invoices - outgoing_invoices
        self.save(update_fields=['invoice_balance'])
    
    def balance_details(self):
        """Детальная информация о балансах компании"""
        summary = self.get_balance_summary()
        
        # Дополнительная информация о компании
        return {
            **summary,
            'company_info': {
                'name': self.name,
                'created_at': self.created_at,
                'updated_at': self.updated_at
            }
        }

# Платежи
class Payment(models.Model):
    PAYMENT_TYPES = [
        ('CASH', 'Наличные (пополнение/списание наличного баланса)'),
        ('CARD', 'Безналичные (пополнение/списание безналичного баланса)'),
        ('INVOICE', 'Инвойс (оплата счета)'),
    ]
    
    # Основная информация
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Сумма")
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPES, verbose_name="Тип платежа")
    description = models.TextField(blank=True, verbose_name="Описание", help_text="Например: 'Пополнение баланса', 'Оплата инвойса №123', 'Перевод средств'")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    
    # Новые прямые связи вместо GenericForeignKey
    from_client = models.ForeignKey('Client', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_sent')
    from_warehouse = models.ForeignKey('Warehouse', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_sent')
    from_line = models.ForeignKey('Line', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_sent')
    from_company = models.ForeignKey('Company', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_sent')
    
    to_client = models.ForeignKey('Client', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_received')
    to_warehouse = models.ForeignKey('Warehouse', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_received')
    to_line = models.ForeignKey('Line', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_received')
    to_company = models.ForeignKey('Company', on_delete=models.CASCADE, null=True, blank=True, related_name='payments_received')
    
    invoice = models.ForeignKey('Invoice', on_delete=models.CASCADE, null=True, blank=True, verbose_name="Инвойс")
    created_by = models.CharField(max_length=100, blank=True, verbose_name="Создано пользователем")
    
    @property
    def sender(self):
        """Возвращает отправителя платежа"""
        if self.from_client: return self.from_client
        if self.from_warehouse: return self.from_warehouse
        if self.from_line: return self.from_line
        if self.from_company: return self.from_company
        return None
    
    @property
    def recipient(self):
        """Возвращает получателя платежа"""
        if self.to_client: return self.to_client
        if self.to_warehouse: return self.to_warehouse
        if self.to_line: return self.to_line
        if self.to_company: return self.to_company
        return None
    
    @property
    def payer(self):
        """Совместимость со старой системой"""
        return self.sender
    
    @property
    def is_correction(self):
        """Проверяет, является ли платеж корректировкой"""
        return 'correction' in self.description.lower() or 'корректировка' in self.description.lower()
    
    def save(self, *args, **kwargs):
        is_new = not self.pk
        if is_new:  # Только для новых платежей
            self.created_by = get_current_user()
        
        super().save(*args, **kwargs)
        
        # Балансы обновляются через Django сигналы, поэтому здесь ничего не делаем
        # if is_new and not getattr(self, '_skip_balance_update', False):
        #     self.update_balances()
    
    def update_balances(self):
        """Обновляет балансы отправителя и получателя"""
        try:
            # Проверяем, является ли это пополнением собственного баланса
            is_self_payment = (self.sender == self.recipient and 
                              self.sender is not None and 
                              self.payment_type in ['CASH', 'CARD'])
            
            if is_self_payment:
                # Пополнение собственного баланса - только увеличиваем
                sender = self.sender
                if hasattr(sender, 'cash_balance') and hasattr(sender, 'card_balance'):
                    if self.payment_type == 'CASH':
                        sender.cash_balance += self.amount
                    elif self.payment_type == 'CARD':
                        sender.card_balance += self.amount
                    sender.save()
                    logger.info(f"Пополнен {self.payment_type} баланс для {sender}: +{self.amount}")
            else:
                # Обычный перевод между разными участниками
                # Обновляем баланс отправителя (уменьшаем)
                sender = self.sender
                if sender:
                    if hasattr(sender, 'cash_balance') and hasattr(sender, 'card_balance'):
                        if self.payment_type == 'CASH':
                            sender.cash_balance -= self.amount
                        elif self.payment_type == 'CARD':
                            sender.card_balance -= self.amount
                        sender.save()
                        logger.info(f"Списан {self.payment_type} баланс для {sender}: -{self.amount}")
                
                # Обновляем баланс получателя (увеличиваем)
                recipient = self.recipient
                if recipient:
                    if hasattr(recipient, 'cash_balance') and hasattr(recipient, 'card_balance'):
                        if self.payment_type == 'CASH':
                            recipient.cash_balance += self.amount
                        elif self.payment_type == 'CARD':
                            recipient.card_balance += self.amount
                        recipient.save()
                        logger.info(f"Зачислен {self.payment_type} баланс для {recipient}: +{self.amount}")
                    
        except Exception as e:
            # Логируем ошибку, но не прерываем сохранение платежа
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка обновления балансов для платежа {self.id}: {e}")
    
    @classmethod
    def recalculate_all_balances(cls):
        """Пересчитывает все балансы на основе существующих платежей"""
        from django.db import transaction
        
        with transaction.atomic():
            # Сбрасываем все балансы
            for model_class in [Client, Warehouse, Line, Company]:
                model_class.objects.all().update(
                    cash_balance=0,
                    card_balance=0
                )
            
            # Пересчитываем балансы на основе платежей
            for payment in cls.objects.all().order_by('date'):
                # Временно отключаем автоматическое обновление балансов
                # чтобы избежать двойного обновления
                payment._skip_balance_update = True
                payment.update_balances()
                payment._skip_balance_update = False
    
    @classmethod
    def reset_all_balances(cls):
        """Полностью обнуляет все балансы всех партнеров и компаний"""
        from django.db import transaction
        
        with transaction.atomic():
            # Обнуляем все балансы
            for model_class in [Client, Warehouse, Line, Company]:
                model_class.objects.all().update(
                    cash_balance=0,
                    card_balance=0,
                    invoice_balance=0
                )
            
            # Также обнуляем устаревшие поля для совместимости
            Client.objects.all().update(
                debt=0
            )
            
            logger.info("Все балансы партнеров и компаний обнулены")
            
        return {
            'message': 'Все балансы успешно обнулены',
            'affected_models': ['Client', 'Warehouse', 'Line', 'Company'],
            'timestamp': timezone.now()
        }

    def __str__(self):
        sender_str = str(self.sender) if self.sender else "Неизвестно"
        recipient_str = str(self.recipient) if self.recipient else "Неизвестно"
        return f"Платеж {self.amount} от {sender_str} к {recipient_str}"

    objects = OptimizedPaymentManager()
    
    class Meta:
        verbose_name = "Платеж"
        verbose_name_plural = "Платежи"
        ordering = ['-date']
        indexes = [
            models.Index(fields=['invoice', 'date']),
            models.Index(fields=['from_client']),
            models.Index(fields=['from_warehouse']),
            models.Index(fields=['from_line']),
            models.Index(fields=['from_company']),
            models.Index(fields=['to_client']),
            models.Index(fields=['to_warehouse']),
            models.Index(fields=['to_line']),
            models.Index(fields=['to_company']),
            models.Index(fields=['payment_type']),
            # Дополнительные составные индексы
            models.Index(fields=['date', 'payment_type']),
            models.Index(fields=['from_client', 'date']),
            models.Index(fields=['to_client', 'date']),
        ]

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


# Новые модели для системы услуг



class LineService(models.Model):
    """Услуги морских линий"""
    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='services', verbose_name="Линия")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    def __str__(self):
        return f"{self.line.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга линии"
        verbose_name_plural = "Услуги линий"


class CarrierService(models.Model):
    """Услуги перевозчиков"""
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name='services', verbose_name="Перевозчик")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    def __str__(self):
        return f"{self.carrier.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга перевозчика"
        verbose_name_plural = "Услуги перевозчиков"


class WarehouseService(models.Model):
    """Услуги складов"""
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='services', verbose_name="Склад")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    def __str__(self):
        return f"{self.warehouse.name} - {self.name}"
    
    class Meta:
        verbose_name = "Услуга склада"
        verbose_name_plural = "Услуги складов"


class DeletedCarService(models.Model):
    """Отслеживание удаленных пользователем услуг для конкретного автомобиля"""
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='deleted_services', verbose_name="Автомобиль")
    service_type = models.CharField(max_length=20, choices=[
        ('LINE', 'Линия'),
        ('CARRIER', 'Перевозчик'),
        ('WAREHOUSE', 'Склад'),
    ], verbose_name="Тип поставщика")
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    deleted_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата удаления")
    
    class Meta:
        unique_together = ['car', 'service_type', 'service_id']
        verbose_name = "Удаленная услуга автомобиля"
        verbose_name_plural = "Удаленные услуги автомобилей"


class CarService(models.Model):
    """Связь автомобиля с услугами и их ценами"""
    SERVICE_TYPES = [
        ('LINE', 'Линия'),
        ('CARRIER', 'Перевозчик'),
        ('WAREHOUSE', 'Склад'),
    ]
    
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='car_services', verbose_name="Автомобиль")
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPES, verbose_name="Тип поставщика")
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    custom_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Индивидуальная цена")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    notes = models.TextField(blank=True, verbose_name="Примечания")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    def __str__(self):
        return f"{self.car.vin} - {self.get_service_name()}: {self.final_price}"
    
    def get_service_name(self):
        """Получает название услуги"""
        if self.service_type == 'LINE':
            try:
                service = LineService.objects.get(id=self.service_id)
                return service.name
            except LineService.DoesNotExist:
                return "Услуга не найдена"
        elif self.service_type == 'CARRIER':
            try:
                service = CarrierService.objects.get(id=self.service_id)
                return service.name
            except CarrierService.DoesNotExist:
                return "Услуга не найдена"
        elif self.service_type == 'WAREHOUSE':
            try:
                service = WarehouseService.objects.get(id=self.service_id)
                return service.name
            except WarehouseService.DoesNotExist:
                return "Услуга не найдена"
        return "Неизвестная услуга"
    
    def get_default_price(self):
        """Получает цену по умолчанию"""
        if self.service_type == 'LINE':
            try:
                service = LineService.objects.get(id=self.service_id)
                return service.default_price
            except LineService.DoesNotExist:
                return 0
        elif self.service_type == 'CARRIER':
            try:
                service = CarrierService.objects.get(id=self.service_id)
                return service.default_price
            except CarrierService.DoesNotExist:
                return 0
        elif self.service_type == 'WAREHOUSE':
            try:
                service = WarehouseService.objects.get(id=self.service_id)
                return service.default_price
            except WarehouseService.DoesNotExist:
                return 0
        return 0
    
    @property
    def final_price(self):
        """Итоговая цена с учетом количества"""
        price = self.custom_price if self.custom_price else self.get_default_price()
        return price * self.quantity
    
    class Meta:
        verbose_name = "Услуга автомобиля"
        verbose_name_plural = "Услуги автомобилей"
        unique_together = ('car', 'service_type', 'service_id')
        indexes = [
            models.Index(fields=['car', 'service_type']),
            models.Index(fields=['service_type', 'service_id']),
            models.Index(fields=['car']),
        ]


# Сигналы для автоматического пересчета текущей цены при изменении услуг
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=CarService)
def recalculate_car_price_on_service_save(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при сохранении услуги"""
    try:
        instance.car.calculate_total_price()
        instance.car.save(update_fields=['current_price', 'total_price'])
    except Exception as e:
        print(f"Ошибка пересчета цены при сохранении услуги: {e}")


@receiver(post_delete, sender=CarService)
def recalculate_car_price_on_service_delete(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при удалении услуги"""
    try:
        instance.car.calculate_total_price()
        instance.car.save(update_fields=['current_price', 'total_price'])
    except Exception as e:
        print(f"Ошибка пересчета цены при удалении услуги: {e}")


@receiver(post_save, sender=Car)
def recalculate_car_price_on_car_save(sender, instance, **kwargs):
    """Пересчитывает текущую цену автомобиля при изменении полей, влияющих на расчет"""
    try:
        # Пересчитываем только если изменились поля, влияющие на расчет
        if hasattr(instance, '_state') and instance._state.adding:
            return  # Пропускаем для новых объектов
        
        # Проверяем, изменились ли поля, влияющие на расчет
        if hasattr(instance, '_state') and hasattr(instance._state, 'db'):
            # Получаем старую версию объекта из базы
            try:
                old_instance = Car.objects.get(pk=instance.pk)
                fields_to_check = ['status', 'transfer_date', 'unload_date', 'warehouse', 'proft']
                
                for field in fields_to_check:
                    if getattr(old_instance, field) != getattr(instance, field):
                        instance.calculate_total_price()
                        instance.save(update_fields=['current_price', 'total_price'])
                        break
            except Car.DoesNotExist:
                pass
    except Exception as e:
        print(f"Ошибка пересчета цены при изменении автомобиля: {e}")


# ==============================================================================
# 🎉 НОВАЯ СИСТЕМА ИНВОЙСОВ И ПЛАТЕЖЕЙ
# ==============================================================================
# Импортируем новые модели, чтобы Django их видел

from .models_billing import (
    NewInvoice,
    InvoiceItem,
    Transaction,
    SimpleBalanceMixin
)