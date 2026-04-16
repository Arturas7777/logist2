"""
Новая упрощенная система инвойсов, платежей и балансов
=========================================================

Основные принципы:
- Простота и понятность
- Прямые связи вместо generic
- Один баланс вместо трех
- Транзакционная безопасность
- Полная история операций

Авторы: AI Assistant
Дата: 30 сентября 2025
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
import logging

from .service_codes import is_storage_service

logger = logging.getLogger(__name__)
User = get_user_model()


# ============================================================================
# КАТЕГОРИИ РАСХОДОВ/ДОХОДОВ
# ============================================================================

class ExpenseCategory(models.Model):
    """Категория расхода/дохода для классификации инвойсов и транзакций"""
    
    CATEGORY_TYPE_CHOICES = [
        ('OPERATIONAL', 'Операционные'),
        ('ADMINISTRATIVE', 'Административные'),
        ('SALARY', 'Зарплаты'),
        ('MARKETING', 'Маркетинг'),
        ('TAX', 'Налоги и сборы'),
        ('PERSONAL', 'Личные расходы'),
        ('OTHER', 'Прочие'),
    ]
    
    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Название",
        help_text="Название категории (напр. Аренда, Логистика)"
    )
    short_name = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Сокращение",
        help_text="Короткое название для отчётов"
    )
    category_type = models.CharField(
        max_length=20,
        choices=CATEGORY_TYPE_CHOICES,
        default='OTHER',
        verbose_name="Тип категории"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна"
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Порядок",
        help_text="Порядок отображения в списке (меньше = выше)"
    )
    
    class Meta:
        verbose_name = "Категория расходов"
        verbose_name_plural = "Категории расходов"
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name


# ============================================================================
# БАЗОВЫЙ МИКСИН ДЛЯ БАЛАНСОВ
# ============================================================================

class SimpleBalanceMixin(models.Model):
    """
    Простой миксин для балансов - ОДИН баланс вместо трех!
    
    Разделение по способам оплаты происходит через историю транзакций,
    а не через отдельные поля баланса.
    """
    
    balance = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        verbose_name="Баланс",
        help_text="Текущий баланс (положительный = переплата, отрицательный = долг)"
    )
    
    balance_updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Дата обновления баланса"
    )
    
    class Meta:
        abstract = True
    
    def get_balance_breakdown(self):
        """
        Получить разбивку баланса по способам оплаты из истории транзакций
        
        Returns:
            dict: {'cash': Decimal, 'card': Decimal, 'transfer': Decimal, 'total': Decimal}
        """
        from django.db.models import Sum, Q
        
        # Определяем тип сущности для фильтрации транзакций
        model_name = self.__class__.__name__.lower()
        
        # Фильтры для входящих и исходящих транзакций
        incoming_filter = Q(**{f'to_{model_name}': self})
        outgoing_filter = Q(**{f'from_{model_name}': self})
        
        # Получаем транзакции
        from .models_billing import Transaction
        transactions = Transaction.objects.filter(incoming_filter | outgoing_filter)
        
        # Разбивка по способам оплаты
        breakdown = {}
        for method in ['CASH', 'CARD', 'TRANSFER']:
            incoming = transactions.filter(
                incoming_filter, 
                method=method
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            
            outgoing = transactions.filter(
                outgoing_filter, 
                method=method
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            
            breakdown[method.lower()] = incoming - outgoing
        
        breakdown['total'] = self.balance
        return breakdown
    
    def get_balance_info(self):
        """
        Получить информацию о балансе в понятном виде
        
        Returns:
            dict: Информация о балансе с статусом и цветом
        """
        balance = self.balance
        
        if balance > 0:
            status = "ПЕРЕПЛАТА"
            color = "#28a745"  # зеленый
            description = f"Переплата {balance:.2f}"
        elif balance < 0:
            status = "ДОЛГ"
            color = "#dc3545"  # красный
            description = f"Долг {abs(balance):.2f}"
        else:
            status = "БАЛАНС"
            color = "#6c757d"  # серый
            description = "Баланс нулевой"
        
        return {
            'balance': balance,
            'status': status,
            'color': color,
            'description': description,
            'breakdown': self.get_balance_breakdown()
        }


# ============================================================================
# НОВАЯ МОДЕЛЬ ИНВОЙСА
# ============================================================================

class NewInvoice(models.Model):
    """
    Упрощенная модель инвойса с прямыми связями
    
    Основные улучшения:
    - Прямые ForeignKey вместо generic связей
    - Понятные статусы
    - Автоматический расчет сумм
    - История изменений
    """
    
    # Статусы инвойса
    STATUS_CHOICES = [
        ('DRAFT', 'Черновик'),
        ('ISSUED', 'Выставлен'),
        ('PARTIALLY_PAID', 'Частично оплачен'),
        ('PAID', 'Оплачен'),
        ('OVERDUE', 'Просрочен'),
        ('CANCELLED', 'Отменен'),
    ]

    # Тип документа
    DOCUMENT_TYPE_CHOICES = [
        ('INVOICE', 'Счёт-фактура (PARDP)'),
        ('PROFORMA', 'Коммерческое предложение (AV)'),
        ('INVOICE_BLC', 'Неофициальный счёт (PARBLC)'),
        ('PROFORMA_BLC', 'Неофициальное предложение (AVBLC)'),
        ('INVOICE_FACT', 'Входящий счёт от контрагента (FACT)'),
        ('INVOICE_INCBLC', 'Входящий неофициальный счёт (INCBLC)'),
    ]

    DOCTYPE_PREFIX_MAP = {
        'INVOICE': 'PARDP',
        'PROFORMA': 'AV',
        'INVOICE_BLC': 'PARBLC',
        'PROFORMA_BLC': 'AVBLC',
        'INVOICE_FACT': 'FACT',
        'INVOICE_INCBLC': 'INCBLC',
    }

    # Серии, которые всегда оплачиваются наличными и не пушатся в site.pro
    CASH_DOCUMENT_TYPES = frozenset({'INVOICE_BLC', 'INVOICE_INCBLC'})
    
    # ========================================================================
    # ИДЕНТИФИКАЦИЯ
    # ========================================================================

    document_type = models.CharField(
        max_length=15,
        choices=DOCUMENT_TYPE_CHOICES,
        default='PROFORMA',
        verbose_name="Тип документа",
        help_text="PARDP — официальный счёт (site.pro). AV — коммерческое предложение. "
                  "PARBLC — исходящий неофициальный счёт (нал). AVBLC — неофиц. предложение. "
                  "FACT — входящий официальный счёт от контрагента. "
                  "INCBLC — входящий неофициальный счёт (нал, не в site.pro)."
    )
    
    number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Номер документа",
        help_text="Уникальный номер (генерируется автоматически по серии)"
    )
    
    external_number = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="Номер счёта контрагента",
        help_text="Номер с бумажного/PDF счёта от поставщика (для входящих инвойсов)"
    )
    
    date = models.DateField(
        default=timezone.now,
        verbose_name="Дата выставления"
    )
    
    due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Срок оплаты",
        help_text="Дата, до которой должен быть оплачен инвойс (автоматически +14 дней)"
    )
    
    # ========================================================================
    # КТО ВЫСТАВИЛ (может быть любая сущность!)
    # ========================================================================
    
    issuer_company = models.ForeignKey(
        'Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='issued_invoices_new',
        verbose_name="Компания-выставитель"
    )
    
    issuer_warehouse = models.ForeignKey(
        'Warehouse',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='issued_invoices_new',
        verbose_name="Склад-выставитель"
    )
    
    issuer_line = models.ForeignKey(
        'Line',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='issued_invoices_new',
        verbose_name="Линия-выставитель"
    )
    
    issuer_carrier = models.ForeignKey(
        'Carrier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='issued_invoices_new',
        verbose_name="Перевозчик-выставитель"
    )
    
    # ========================================================================
    # КОМУ ВЫСТАВЛЕН (прямые связи - ТОЛЬКО ОДНА заполнена!)
    # ========================================================================
    
    recipient_client = models.ForeignKey(
        'Client',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_invoices_new',
        verbose_name="Клиент-получатель"
    )
    
    recipient_warehouse = models.ForeignKey(
        'Warehouse',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_invoices_new',
        verbose_name="Склад-получатель"
    )
    
    recipient_line = models.ForeignKey(
        'Line',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_invoices_new',
        verbose_name="Линия-получатель"
    )
    
    recipient_carrier = models.ForeignKey(
        'Carrier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_invoices_new',
        verbose_name="Перевозчик-получатель"
    )
    
    recipient_company = models.ForeignKey(
        'Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_invoices_new',
        verbose_name="Компания-получатель"
    )
    
    # ========================================================================
    # ВАЛЮТА
    # ========================================================================

    CURRENCY_CHOICES = [
        ('EUR', 'EUR'),
        ('USD', 'USD'),
        ('GBP', 'GBP'),
    ]

    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='EUR',
        verbose_name="Валюта"
    )

    # ========================================================================
    # ФИНАНСЫ
    # ========================================================================
    
    subtotal = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Подытог",
        help_text="Сумма всех позиций без дополнительных сборов"
    )
    
    discount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Скидка"
    )
    
    tax = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Налог"
    )
    
    total = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Итого к оплате",
        help_text="Итоговая сумма инвойса"
    )
    
    paid_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Оплачено",
        help_text="Сумма, которая уже оплачена"
    )
    
    # ========================================================================
    # СТАТУС И МЕТАДАННЫЕ
    # ========================================================================
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='DRAFT',
        verbose_name="Статус"
    )
    
    notes = models.TextField(
        blank=True,
        verbose_name="Примечания",
        help_text="Дополнительная информация об инвойсе"
    )
    
    # Связь с автомобилями для автоматического формирования позиций
    cars = models.ManyToManyField(
        'Car',
        blank=True,
        related_name='invoices_new',
        verbose_name="Выбранные автомобили",
        help_text="Выберите автомобили - позиции создадутся автоматически из их услуг"
    )
    
    # Связь с автовозом (если инвойс создан для автовоза)
    auto_transport = models.ForeignKey(
        'AutoTransport',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
        verbose_name="Автовоз",
        help_text="Автовоз, для которого создан этот инвойс"
    )

    # ========================================================================
    # СВЯЗАННЫЙ СЧЁТ (пара реальный ↔ официальный)
    # ========================================================================

    linked_invoice = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='linked_from',
        verbose_name="Связанный счёт",
        help_text="Пара: реальный BLC-счёт ↔ официальный счёт на ту же сумму"
    )
    
    # ========================================================================
    # КАТЕГОРИЗАЦИЯ И ВЛОЖЕНИЯ
    # ========================================================================
    
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
        verbose_name="Категория",
        help_text="Категория для учёта доходов/расходов (напр. Логистика, Аренда)"
    )
    
    skip_ai_comparison = models.BooleanField(
        default=False,
        verbose_name="Без сверки с базой",
        help_text="AI извлечёт данные из PDF, но не будет сверять с расходами в системе"
    )

    attachment = models.FileField(
        upload_to='invoices/attachments/%Y/%m/',
        null=True,
        blank=True,
        verbose_name="Вложение",
        help_text="PDF или фото счёта/инвойса"
    )
    
    # Аудит
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_invoices_new',
        verbose_name="Создал"
    )
    
    # Служебное поле для отслеживания обновления баланса
    _balance_updated = models.BooleanField(default=False, editable=False)
    
    class Meta:
        verbose_name = "Инвойс"
        verbose_name_plural = "Инвойсы"
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['number']),
            models.Index(fields=['status', 'date']),
            models.Index(fields=['due_date', 'status']),
            models.Index(fields=['recipient_client', 'status']),
            models.Index(fields=['recipient_warehouse', 'status']),
            models.Index(fields=['recipient_line', 'status']),
            models.Index(fields=['recipient_carrier', 'status']),
            models.Index(fields=['recipient_company', 'status']),
            models.Index(fields=['issuer_company', 'status']),
            models.Index(fields=['issuer_warehouse', 'status']),
            models.Index(fields=['issuer_line', 'status']),
            models.Index(fields=['issuer_carrier', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(issuer_company__isnull=False, issuer_warehouse__isnull=True, issuer_line__isnull=True, issuer_carrier__isnull=True) |
                    models.Q(issuer_company__isnull=True, issuer_warehouse__isnull=False, issuer_line__isnull=True, issuer_carrier__isnull=True) |
                    models.Q(issuer_company__isnull=True, issuer_warehouse__isnull=True, issuer_line__isnull=False, issuer_carrier__isnull=True) |
                    models.Q(issuer_company__isnull=True, issuer_warehouse__isnull=True, issuer_line__isnull=True, issuer_carrier__isnull=False)
                ),
                name='invoice_exactly_one_issuer',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(recipient_client__isnull=False, recipient_warehouse__isnull=True, recipient_line__isnull=True, recipient_carrier__isnull=True, recipient_company__isnull=True) |
                    models.Q(recipient_client__isnull=True, recipient_warehouse__isnull=False, recipient_line__isnull=True, recipient_carrier__isnull=True, recipient_company__isnull=True) |
                    models.Q(recipient_client__isnull=True, recipient_warehouse__isnull=True, recipient_line__isnull=False, recipient_carrier__isnull=True, recipient_company__isnull=True) |
                    models.Q(recipient_client__isnull=True, recipient_warehouse__isnull=True, recipient_line__isnull=True, recipient_carrier__isnull=False, recipient_company__isnull=True) |
                    models.Q(recipient_client__isnull=True, recipient_warehouse__isnull=True, recipient_line__isnull=True, recipient_carrier__isnull=True, recipient_company__isnull=False)
                ),
                name='invoice_exactly_one_recipient',
            ),
        ]
    
    def __str__(self):
        return f"Инвойс {self.number} ({self.get_status_display()})"
    
    # ========================================================================
    # СВОЙСТВА
    # ========================================================================
    
    @property
    def issuer(self):
        """Получить выставителя инвойса"""
        if self.issuer_company:
            return self.issuer_company
        elif self.issuer_warehouse:
            return self.issuer_warehouse
        elif self.issuer_line:
            return self.issuer_line
        elif self.issuer_carrier:
            return self.issuer_carrier
        return None
    
    @property
    def issuer_name(self):
        """Получить имя выставителя"""
        issuer = self.issuer
        return str(issuer) if issuer else "Не указан"
    
    DIRECTION_OUTGOING = 'OUTGOING'
    DIRECTION_INCOMING = 'INCOMING'
    DIRECTION_INTERNAL = 'INTERNAL'
    
    @property
    def direction(self):
        """
        Определить направление инвойса:
        - OUTGOING: мы (Caromoto Lithuania, Company id=1) выставили кому-то
        - INCOMING: нам выставили (мы получатель)
        - INTERNAL: прочие комбинации
        """
        from .models import Company
        default_id = Company.get_default_id()
        if self.issuer_company_id == default_id:
            return self.DIRECTION_OUTGOING
        if self.recipient_company_id == default_id:
            return self.DIRECTION_INCOMING
        return self.DIRECTION_INTERNAL
    
    @property
    def direction_display(self):
        """Отображение направления для админки"""
        labels = {
            self.DIRECTION_OUTGOING: 'Исходящий',
            self.DIRECTION_INCOMING: 'Входящий',
            self.DIRECTION_INTERNAL: 'Внутренний',
        }
        return labels.get(self.direction, 'Неизвестно')
    
    @property
    def recipient(self):
        """Получить получателя инвойса"""
        if self.recipient_client:
            return self.recipient_client
        elif self.recipient_warehouse:
            return self.recipient_warehouse
        elif self.recipient_line:
            return self.recipient_line
        elif self.recipient_carrier:
            return self.recipient_carrier
        elif self.recipient_company:
            return self.recipient_company
        return None
    
    @property
    def recipient_name(self):
        """Получить имя получателя"""
        recipient = self.recipient
        return str(recipient) if recipient else "Не указан"
    
    @property
    def remaining_amount(self):
        """Остаток к оплате"""
        return max(Decimal('0.00'), self.total - self.paid_amount)
    
    @property
    def is_overdue(self):
        """Просрочен ли инвойс"""
        if self.status in ['PAID', 'CANCELLED']:
            return False
        if not self.due_date:
            return False
        return self.due_date < timezone.now().date()
    
    @property
    def days_until_due(self):
        """Количество дней до срока оплаты"""
        if not self.due_date:
            return 0
        delta = self.due_date - timezone.now().date()
        return delta.days
    
    # ========================================================================
    # МЕТОДЫ
    # ========================================================================
    
    def calculate_totals(self):
        """Пересчитать итоговые суммы на основе позиций"""
        items = self.items.all()
        self.subtotal = sum(item.total_price for item in items)
        self.total = self.subtotal - self.discount + self.tax
        return self.total

    def recalculate_paid_amount(self):
        """Пересчитать paid_amount из реальных COMPLETED-транзакций привязанных к этому инвойсу."""
        from django.db.models import Sum
        payments = self.transactions.filter(
            type='PAYMENT', status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        refunds = self.transactions.filter(
            type='REFUND', status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        calculated = payments - refunds
        if calculated < Decimal('0.00'):
            logger.warning(
                "Invoice %s: paid_amount would be negative (%s). "
                "Payments=%s, Refunds=%s. Clamping to 0.",
                self.number, calculated, payments, refunds,
            )
        self.paid_amount = max(Decimal('0.00'), calculated)
        self.update_status()
        self.save(update_fields=['paid_amount', 'status', 'updated_at'])
    
    def get_items_pivot_table(self):
        """
        Возвращает данные для табличного отображения инвойса:
        строки = авто, столбцы = группы услуг (short_name), крайний правый = итого.
        Для входящих инвойсов каждая ячейка содержит и цену из инвойса, и цену для клиента.
        """
        from collections import OrderedDict

        items = self.items.all().select_related('car').order_by('order')

        if not items.exists():
            return None

        is_incoming = self.direction == 'INCOMING'
        has_client_prices = is_incoming and items.filter(client_price__isnull=False).exists()

        columns = []
        seen_cols = set()
        car_rows = OrderedDict()

        for item in items:
            raw_desc = item.description or ''
            col_name = raw_desc.split(':')[0].strip() if ':' in raw_desc else raw_desc
            if col_name not in seen_cols:
                columns.append(col_name)
                seen_cols.add(col_name)

            car_key = item.car_id or f'nocar_{item.pk}'
            if car_key not in car_rows:
                if item.car:
                    car_label = f"{item.car.brand}, {item.car.vin}"
                else:
                    car_label = item.description or 'Без авто'
                car_rows[car_key] = {
                    'car': item.car,
                    'car_label': car_label,
                    'services': {},
                    'client_services': {},
                    'total': Decimal('0'),
                    'client_total': Decimal('0'),
                }

            car_rows[car_key]['services'][col_name] = item.unit_price
            car_rows[car_key]['total'] += item.total_price
            if item.client_price is not None:
                car_rows[car_key]['client_services'][col_name] = item.client_price
                car_rows[car_key]['client_total'] += item.client_price

        single_cols = set()
        if has_client_prices:
            for col in columns:
                has_any_client = any(
                    col in row['client_services'] for row in car_rows.values()
                )
                if not has_any_client:
                    single_cols.add(col)

        columns_info = [
            {'name': col, 'single': col in single_cols} for col in columns
        ]

        column_totals = {}
        client_column_totals = {}
        for col in columns:
            column_totals[col] = sum(
                row['services'].get(col, Decimal('0')) for row in car_rows.values()
            )
            if has_client_prices:
                client_column_totals[col] = sum(
                    row['client_services'].get(col, Decimal('0')) for row in car_rows.values()
                )

        grand_total = sum(row['total'] for row in car_rows.values())
        client_grand_total = sum(row['client_total'] for row in car_rows.values()) if has_client_prices else None

        rows = []
        for car_data in car_rows.values():
            cells = []
            for col in columns:
                val = car_data['services'].get(col, None)
                is_single = col in single_cols
                if has_client_prices:
                    client_val = car_data['client_services'].get(col, None)
                    profit = None
                    if client_val is not None and val is not None:
                        profit = client_val - val
                    cells.append({
                        'invoice': val, 'client': client_val,
                        'profit': profit, 'single': is_single,
                    })
                else:
                    cells.append(val)

            row_profit = None
            if has_client_prices:
                row_profit = car_data['client_total'] - car_data['total']

            rows.append({
                'car_label': car_data['car_label'],
                'cells': cells,
                'total': car_data['total'],
                'client_total': car_data['client_total'] if has_client_prices else None,
                'profit': row_profit,
            })

        col_totals_list = [column_totals[col] for col in columns]

        if has_client_prices:
            col_totals_paired = []
            for i, col in enumerate(columns):
                inv = column_totals[col]
                cli = client_column_totals.get(col, Decimal('0'))
                col_totals_paired.append({
                    'invoice': inv, 'client': cli, 'profit': cli - inv,
                    'single': col in single_cols,
                })
            profit_grand = client_grand_total - grand_total
        else:
            col_totals_paired = None
            profit_grand = None

        return {
            'columns': columns_info,
            'rows': rows,
            'col_totals': col_totals_list,
            'col_totals_paired': col_totals_paired,
            'grand_total': grand_total,
            'client_grand_total': client_grand_total,
            'profit_grand': profit_grand,
            'has_client_prices': has_client_prices,
        }
    
    def update_status(self):
        """Обновить статус на основе оплаты"""
        if self.status == 'CANCELLED':
            return
        if self.total > 0 and self.paid_amount >= self.total:
            self.status = 'PAID'
        elif self.total == 0 and self.paid_amount >= 0:
            if self.status not in ('DRAFT', 'ISSUED'):
                self.status = 'ISSUED'
        elif self.paid_amount > 0 and self.total > 0:
            self.status = 'PARTIALLY_PAID'
        elif self.total > 0 and self.paid_amount == 0:
            if self.is_overdue:
                self.status = 'OVERDUE'
            elif self.status not in ('DRAFT', 'ISSUED'):
                self.status = 'ISSUED'
    
    def generate_number(self):
        """Сгенерировать уникальный номер документа.

        Серия определяется по document_type через DOCTYPE_PREFIX_MAP:
        PARDP-NNNNNN, AV-NNNNNN, PARBLC-NNNNNN, AVBLC-NNNNNN, FACT-NNNNNN, INCBLC-NNNNNN.
        """
        prefix = self.DOCTYPE_PREFIX_MAP.get(self.document_type, 'AV')
        pad = 6

        last_invoice = (
            NewInvoice.objects
            .filter(number__startswith=f'{prefix}-')
            .select_for_update()
            .order_by('-number')
            .first()
        )

        if last_invoice:
            try:
                last_num = int(last_invoice.number.split('-', 1)[1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1

        return f"{prefix}-{next_num:0{pad}d}"
    
    def change_series(self, new_document_type, created_by=None):
        """Перевести инвойс в другую серию с автоматическим перенумерованием.

        При переходе на любую кассовую серию (INVOICE_BLC / INVOICE_INCBLC)
        автоматически создаётся кассовый платёж (CASH) на оставшуюся сумму
        и инвойс закрывается. При уходе с кассовой серии на некассовую —
        кассовые платежи отменяются. Переход между кассовыми сериями
        (PARBLC ↔ INCBLC) не трогает платежи, только меняет номер.

        Returns the old number for logging purposes.
        """
        from django.db import transaction as db_transaction

        if new_document_type == self.document_type:
            return self.number

        old_number = self.number
        old_type = self.document_type
        self.document_type = new_document_type
        with db_transaction.atomic():
            self.number = self.generate_number()
            self.save(update_fields=['document_type', 'number', 'updated_at'])

            was_cash = old_type in self.CASH_DOCUMENT_TYPES
            is_cash = new_document_type in self.CASH_DOCUMENT_TYPES

            if is_cash and not was_cash and self.remaining_amount > 0:
                self._register_cash_payment(created_by=created_by)
            elif was_cash and not is_cash:
                self._reverse_cash_payments(created_by=created_by)

        return old_number

    def _register_cash_payment(self, created_by=None):
        """Create a CASH PAYMENT transaction for the remaining amount."""
        from django.db import transaction as db_transaction

        remaining = self.remaining_amount
        if remaining <= 0:
            return

        payer = self.recipient
        issuer = self.issuer
        if not payer or not issuer:
            return

        payer_field = f'from_{payer.__class__.__name__.lower()}'
        issuer_field = f'to_{issuer.__class__.__name__.lower()}'

        trx = Transaction(
            type='PAYMENT',
            method='CASH',
            invoice=self,
            amount=remaining,
            currency=self.currency or 'EUR',
            description=f"Оплата наличными ({self.number})",
            created_by=created_by,
            status='COMPLETED',
        )
        setattr(trx, payer_field, payer)
        setattr(trx, issuer_field, issuer)
        trx.save()
        logger.info('Auto cash payment %s: %s for invoice %s', trx.number, remaining, self.number)

    def _reverse_cash_payments(self, created_by=None):
        """Reverse auto-created cash payments when moving away from PARBLC."""
        cash_payments = self.transactions.filter(
            type='PAYMENT', method='CASH', status='COMPLETED',
            description__contains='Оплата наличными',
        )
        for trx in cash_payments:
            trx.status = 'CANCELLED'
            trx._skip_balance_recalc = True
            trx.save(update_fields=['status'])
        if cash_payments.exists():
            self.recalculate_paid_amount()
            logger.info('Reversed %d cash payments for invoice %s', cash_payments.count(), self.number)

    def regenerate_items_from_cars(self):
        """
        Автоматически создает позиции инвойса из услуг выбранных автомобилей.

        Табличный формат (06.02.2026):
        - Одна позиция на каждую группу услуг (по short_name) для каждого авто
        - Услуги с одинаковым short_name суммируются (напр. Разгрузка+Погрузка+Декларация → "Порт")
        - Хранение — отдельная группа "Хран"
        - description = short_name (для группировки в таблице)
        """
        from collections import OrderedDict
        from django.db import transaction

        with transaction.atomic():
            self._regenerate_items_from_cars_inner()

    def _regenerate_items_from_cars_inner(self):
        from collections import OrderedDict

        # Удаляем старые позиции
        self.items.all().delete()
        
        issuer = self.issuer
        if not issuer:
            return
        
        issuer_type = issuer.__class__.__name__
        is_company = (issuer_type == 'Company')
        
        order = 0
        for car in self.cars.prefetch_related('car_services').select_related('warehouse').all():
            # Пересчитываем хранение и стоимость перед генерацией позиций
            car.update_days_and_storage()
            car.calculate_total_price()
            
            # Определяем набор услуг в зависимости от типа выставителя
            if issuer_type == 'Warehouse':
                services = car.get_warehouse_services()
            elif issuer_type == 'Line':
                services = car.get_line_services()
            elif issuer_type == 'Carrier':
                services = car.get_carrier_services()
            elif issuer_type == 'Company':
                services = car.car_services.all()
            else:
                continue
            
            # === Группируем услуги по short_name ===
            # OrderedDict сохраняет порядок добавления
            groups = OrderedDict()
            
            for service in services:
                service_name = service.get_service_name()
                
                # Пропускаем битые услуги
                if service_name == "Услуга не найдена":
                    continue
                
                if is_storage_service(service):
                    continue
                
                short = service.get_service_short_name()
                
                # Рассчитываем цену
                if is_company:
                    price = (service.custom_price if service.custom_price is not None else service.get_default_price()) + (service.markup_amount if service.markup_amount is not None else Decimal('0'))
                else:
                    price = service.custom_price if service.custom_price is not None else service.get_default_price()
                
                amount = price * service.quantity
                
                if short in groups:
                    groups[short] += amount
                else:
                    groups[short] = amount
            
            # === Добавляем хранение как отдельную группу ===
            if (is_company or issuer_type == 'Warehouse'):
                if car.storage_cost and car.storage_cost > 0 and car.days and car.days > 0:
                    daily_rate = car._get_storage_daily_rate() if car.warehouse else Decimal('0')
                    storage_total = daily_rate * car.days
                    groups['Хран'] = storage_total
            
            # === Создаём InvoiceItem для каждой группы ===
            for short_name, amount in groups.items():
                InvoiceItem.objects.create(
                    invoice=self,
                    description=short_name,
                    car=car,
                    quantity=1,
                    unit_price=amount,
                    order=order
                )
                order += 1
        
        # Пересчитываем итоги
        self.calculate_totals()
        self.save(update_fields=['subtotal', 'total'])
    
    def clean(self):
        """Валидация инвойса перед сохранением."""
        from django.core.exceptions import ValidationError
        errors = {}
        
        issuers = [
            self.issuer_company_id, self.issuer_warehouse_id,
            self.issuer_line_id, self.issuer_carrier_id,
        ]
        issuer_count = sum(1 for f in issuers if f)
        if issuer_count == 0:
            errors['__all__'] = "Необходимо указать ровно одного выставителя инвойса."
        elif issuer_count > 1:
            errors['__all__'] = "Можно указать только одного выставителя инвойса."
        
        recipients = [
            self.recipient_company_id, self.recipient_client_id,
            self.recipient_warehouse_id, self.recipient_line_id,
            self.recipient_carrier_id,
        ]
        recipient_count = sum(1 for f in recipients if f)
        if recipient_count == 0:
            errors.setdefault('__all__', '')
            errors['__all__'] = (errors['__all__'] + " Необходимо указать ровно одного получателя инвойса.").strip()
        elif recipient_count > 1:
            errors.setdefault('__all__', '')
            errors['__all__'] = (errors['__all__'] + " Можно указать только одного получателя инвойса.").strip()
        
        if self.issuer_company_id and self.recipient_company_id:
            if self.issuer_company_id == self.recipient_company_id:
                errors['recipient_company'] = "Выставитель и получатель не могут быть одной компанией."
        
        if self.issuer_warehouse_id and self.recipient_warehouse_id:
            if self.issuer_warehouse_id == self.recipient_warehouse_id:
                errors['recipient_warehouse'] = "Выставитель и получатель не могут быть одним складом."
        
        if self.due_date and self.date and self.due_date < self.date:
            errors['due_date'] = "Срок оплаты не может быть раньше даты выставления."
        
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, force=False, **kwargs):
        if not force:
            if self.status == 'PAID':
                raise ValidationError("Нельзя удалить оплаченный инвойс. Используйте отмену.")
            if self.paid_amount > 0:
                raise ValidationError(
                    "Нельзя удалить инвойс с зарегистрированными платежами. "
                    "Сначала оформите возврат."
                )
        return super().delete(*args, **kwargs)
    
    def save(self, *args, **kwargs):
        """Переопределяем save для автоматической генерации номера и обновления статуса"""
        from django.db import transaction as db_transaction

        if not self.number:
            with db_transaction.atomic():
                self.number = self.generate_number()

        if not self.due_date:
            self.due_date = timezone.now().date() + timezone.timedelta(days=14)

        self.update_status()

        super().save(*args, **kwargs)


# ============================================================================
# ПОЗИЦИЯ В ИНВОЙСЕ
# ============================================================================

class InvoiceItem(models.Model):
    """
    Позиция (строка) в инвойсе
    
    Может быть связана с автомобилем или быть произвольной услугой
    """
    
    # Связь с инвойсом
    invoice = models.ForeignKey(
        NewInvoice,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="Инвойс"
    )
    
    # Связь с автомобилем (опционально)
    car = models.ForeignKey(
        'Car',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_items_new',
        verbose_name="Автомобиль"
    )
    
    # Описание услуги/товара
    description = models.CharField(
        max_length=500,
        verbose_name="Описание",
        help_text="Например: 'Хранение авто VIN12345 (10 дней)'"
    )
    
    # Количество и цена
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1,
        validators=[MinValueValidator(0)],
        verbose_name="Количество"
    )
    
    unit_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Цена за единицу"
    )
    
    total_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Сумма",
        help_text="Автоматически рассчитывается: количество × цена"
    )

    client_price = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True,
        verbose_name="Цена для клиента",
        help_text="Цена из CarService (для сравнения во входящих инвойсах)"
    )

    # Порядок отображения
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Порядок"
    )
    
    class Meta:
        verbose_name = "Позиция инвойса"
        verbose_name_plural = "Позиции инвойса"
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['invoice', 'order']),
            models.Index(fields=['car']),
        ]
    
    def __str__(self):
        return f"{self.description} - {self.total_price}"
    
    def calculate_total(self):
        """Рассчитать итоговую сумму позиции"""
        self.total_price = self.quantity * self.unit_price
        return self.total_price
    
    def save(self, *args, **kwargs):
        """Переопределяем save для автоматического расчета суммы"""
        self.calculate_total()
        super().save(*args, **kwargs)
        
        # Обновляем итоги инвойса
        if self.invoice_id:
            self.invoice.calculate_totals()
            self.invoice.save(update_fields=['subtotal', 'total', 'updated_at'])


# ============================================================================
# ТРАНЗАКЦИЯ (ПЛАТЕЖ/ВОЗВРАТ/ПЕРЕВОД)
# ============================================================================

class Transaction(models.Model):
    """
    Универсальная модель для всех финансовых операций
    
    Заменяет старую модель Payment и включает все типы операций:
    - Платежи по инвойсам
    - Пополнение баланса
    - Возвраты
    - Переводы между сущностями
    - Корректировки
    """
    
    # Типы транзакций
    TYPE_CHOICES = [
        ('PAYMENT', 'Платеж'),
        ('REFUND', 'Возврат'),
        ('ADJUSTMENT', 'Корректировка'),
        ('TRANSFER', 'Перевод'),
        ('BALANCE_TOPUP', 'Пополнение баланса'),
    ]
    
    # Способы оплаты
    METHOD_CHOICES = [
        ('CASH', 'Наличные'),
        ('CARD', 'Банковская карта'),
        ('TRANSFER', 'Банковский перевод'),
        ('BALANCE', 'Списание с баланса'),
        ('OTHER', 'Другое'),
    ]
    
    # Статусы транзакции
    STATUS_CHOICES = [
        ('PENDING', 'В ожидании'),
        ('COMPLETED', 'Завершена'),
        ('FAILED', 'Ошибка'),
        ('CANCELLED', 'Отменена'),
    ]
    
    # ========================================================================
    # ИДЕНТИФИКАЦИЯ
    # ========================================================================
    
    number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Номер транзакции"
    )
    
    date = models.DateTimeField(
        default=timezone.now,
        verbose_name="Дата и время"
    )
    
    # ========================================================================
    # ТИП И СПОСОБ
    # ========================================================================
    
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        verbose_name="Тип операции"
    )
    
    method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        verbose_name="Способ оплаты"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='COMPLETED',
        verbose_name="Статус"
    )
    
    # ========================================================================
    # ОТКУДА (отправитель) - ТОЛЬКО ОДНО поле заполнено!
    # ========================================================================
    
    from_client = models.ForeignKey(
        'Client',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_sent_new',
        verbose_name="От клиента"
    )
    
    from_warehouse = models.ForeignKey(
        'Warehouse',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_sent_new',
        verbose_name="От склада"
    )
    
    from_line = models.ForeignKey(
        'Line',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_sent_new',
        verbose_name="От линии"
    )
    
    from_carrier = models.ForeignKey(
        'Carrier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_sent_new',
        verbose_name="От перевозчика"
    )
    
    from_company = models.ForeignKey(
        'Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_sent_new',
        verbose_name="От компании"
    )
    
    # ========================================================================
    # КУДА (получатель) - ТОЛЬКО ОДНО поле заполнено!
    # ========================================================================
    
    to_client = models.ForeignKey(
        'Client',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_received_new',
        verbose_name="Клиенту"
    )
    
    to_warehouse = models.ForeignKey(
        'Warehouse',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_received_new',
        verbose_name="Складу"
    )
    
    to_line = models.ForeignKey(
        'Line',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_received_new',
        verbose_name="Линии"
    )
    
    to_carrier = models.ForeignKey(
        'Carrier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_received_new',
        verbose_name="Перевозчику"
    )
    
    to_company = models.ForeignKey(
        'Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transactions_received_new',
        verbose_name="Компании"
    )
    
    # ========================================================================
    # СВЯЗЬ С ИНВОЙСОМ
    # ========================================================================
    
    invoice = models.ForeignKey(
        NewInvoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
        verbose_name="Инвойс",
        help_text="Если это оплата инвойса, указываем его здесь"
    )
    
    # ========================================================================
    # ВАЛЮТА
    # ========================================================================

    CURRENCY_CHOICES = [
        ('EUR', 'EUR'),
        ('USD', 'USD'),
        ('GBP', 'GBP'),
    ]

    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='EUR',
        verbose_name="Валюта"
    )

    # ========================================================================
    # СУММА И ОПИСАНИЕ
    # ========================================================================
    
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        verbose_name="Сумма"
    )
    
    description = models.TextField(
        verbose_name="Описание",
        help_text="Подробное описание операции"
    )
    
    # ========================================================================
    # КАТЕГОРИЗАЦИЯ И ВЛОЖЕНИЯ
    # ========================================================================
    
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
        verbose_name="Категория",
        help_text="Категория расхода/дохода. При привязке к инвойсу берётся автоматически."
    )
    
    attachment = models.FileField(
        upload_to='transactions/attachments/%Y/%m/',
        null=True,
        blank=True,
        verbose_name="Вложение",
        help_text="Чек, квитанция или подтверждение оплаты"
    )
    
    receipt_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Данные чека",
        help_text="AI-распарсенные данные из фото чека (магазин, товары, суммы)"
    )
    
    # ========================================================================
    # МЕТАДАННЫЕ
    # ========================================================================
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_transactions_new',
        verbose_name="Создал"
    )
    
    class Meta:
        verbose_name = "Транзакция"
        verbose_name_plural = "Транзакции"
        ordering = ['-date']
        indexes = [
            models.Index(fields=['number']),
            models.Index(fields=['date', 'type']),
            models.Index(fields=['invoice']),
            models.Index(fields=['from_client', 'date']),
            models.Index(fields=['to_client', 'date']),
            models.Index(fields=['from_warehouse', 'date']),
            models.Index(fields=['to_warehouse', 'date']),
            models.Index(fields=['from_line', 'date']),
            models.Index(fields=['to_line', 'date']),
            models.Index(fields=['from_carrier', 'date']),
            models.Index(fields=['to_carrier', 'date']),
            models.Index(fields=['from_company', 'date']),
            models.Index(fields=['to_company', 'date']),
            models.Index(fields=['status', 'date']),
        ]
    
    def __str__(self):
        return f"{self.number}: {self.get_type_display()} {self.amount}"
    
    # ========================================================================
    # СВОЙСТВА
    # ========================================================================
    
    @property
    def sender(self):
        """Получить отправителя"""
        if self.from_client:
            return self.from_client
        elif self.from_warehouse:
            return self.from_warehouse
        elif self.from_line:
            return self.from_line
        elif self.from_carrier:
            return self.from_carrier
        elif self.from_company:
            return self.from_company
        return None
    
    @property
    def recipient(self):
        """Получить получателя"""
        if self.to_client:
            return self.to_client
        elif self.to_warehouse:
            return self.to_warehouse
        elif self.to_line:
            return self.to_line
        elif self.to_carrier:
            return self.to_carrier
        elif self.to_company:
            return self.to_company
        return None
    
    @property
    def sender_name(self):
        """Имя отправителя"""
        sender = self.sender
        return str(sender) if sender else "Не указан"
    
    @property
    def recipient_name(self):
        """Имя получателя"""
        recipient = self.recipient
        return str(recipient) if recipient else "Не указан"
    
    # ========================================================================
    # МЕТОДЫ
    # ========================================================================
    
    # ========================================================================
    # ВАЛИДАЦИЯ
    # ========================================================================

    def clean(self):
        """Валидация: ровно один отправитель и ровно один получатель, совпадение валют."""
        errors = {}
        from_fields = [
            self.from_client_id, self.from_warehouse_id,
            self.from_line_id, self.from_carrier_id, self.from_company_id,
        ]
        to_fields = [
            self.to_client_id, self.to_warehouse_id,
            self.to_line_id, self.to_carrier_id, self.to_company_id,
        ]
        from_count = sum(1 for f in from_fields if f)
        to_count = sum(1 for f in to_fields if f)

        if self.type == 'BALANCE_TOPUP':
            if to_count != 1:
                errors['__all__'] = "Пополнение баланса: укажите ровно одного получателя."
        elif self.type == 'ADJUSTMENT':
            if (from_count + to_count) != 1:
                errors['__all__'] = "Корректировка: укажите ровно одну сторону (отправитель ИЛИ получатель)."
        else:
            if from_count > 1:
                errors['__all__'] = "Укажите не более одного отправителя."
            if to_count > 1:
                errors.setdefault('__all__', '')
                errors['__all__'] += " Укажите не более одного получателя."
                errors['__all__'] = errors['__all__'].strip()

        if self.invoice_id and self.invoice and self.currency != self.invoice.currency:
            errors['currency'] = (
                f"Валюта транзакции ({self.currency}) не совпадает "
                f"с валютой инвойса ({self.invoice.currency})."
            )

        if errors:
            raise ValidationError(errors)

    # ========================================================================
    # ПЕРЕСЧЁТ БАЛАНСА СУЩНОСТИ
    # ========================================================================

    @staticmethod
    def recalculate_entity_balance(entity):
        """Пересчитать баланс entity строго по COMPLETED-транзакциям из БД."""
        if entity is None or not hasattr(entity, 'balance'):
            return
        model_name = entity.__class__.__name__.lower()
        incoming = Transaction.objects.filter(
            status='COMPLETED', **{f'to_{model_name}': entity}
        ).aggregate(s=models.Sum('amount'))['s'] or Decimal('0.00')
        outgoing = Transaction.objects.filter(
            status='COMPLETED', **{f'from_{model_name}': entity}
        ).aggregate(s=models.Sum('amount'))['s'] or Decimal('0.00')
        new_balance = incoming - outgoing
        if entity.balance != new_balance:
            entity.balance = new_balance
            entity.save(update_fields=['balance', 'balance_updated_at'])

    def generate_number(self):
        """Сгенерировать уникальный номер транзакции.
        Использует select_for_update для предотвращения дублирования.
        """
        from django.utils.timezone import now

        date = now()
        prefix = f"TRX-{date.year}{date.month:02d}{date.day:02d}"

        last_transaction = (
            Transaction.objects
            .filter(number__startswith=prefix)
            .select_for_update()
            .order_by('-number')
            .first()
        )

        if last_transaction:
            try:
                last_num = int(last_transaction.number.split('-')[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1

        return f"{prefix}-{next_num:05d}"
    
    def delete(self, *args, force=False, **kwargs):
        if not force and self.status == 'COMPLETED':
            raise ValidationError(
                "Нельзя удалить завершённую транзакцию. "
                "Для корректировки создайте возврат или корректировку."
            )
        return super().delete(*args, **kwargs)
    
    def save(self, *args, **kwargs):
        """Переопределяем save для автоматической генерации номера"""
        from django.db import transaction as db_transaction

        if not self.number:
            with db_transaction.atomic():
                self.number = self.generate_number()

        super().save(*args, **kwargs)


# ============================================================================
# ЛИЧНЫЕ КАРТЫ
# ============================================================================

class PersonalCard(models.Model):
    """Личная банковская карта для учёта личных финансов"""

    name = models.CharField(
        max_length=100,
        verbose_name="Название",
        help_text="Например: Revolut, SEB, Swedbank"
    )
    last_four = models.CharField(
        max_length=4,
        blank=True,
        default='',
        verbose_name="Последние 4 цифры",
    )
    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name="Баланс",
    )
    color = models.CharField(
        max_length=7,
        default='#6366f1',
        verbose_name="Цвет",
        help_text="HEX-цвет для отображения на дашборде"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Порядок",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создана",
    )

    class Meta:
        verbose_name = "Личная карта"
        verbose_name_plural = "Личные карты"
        ordering = ['order', 'name']

    def __str__(self):
        label = self.name
        if self.last_four:
            label += f" ·{self.last_four}"
        return label

    @property
    def display_name(self):
        return str(self)


# ============================================================================
# ПЕРЕВОДЫ МЕЖДУ КАРТАМИ / НАЛИЧНЫМИ
# ============================================================================

class PersonalTransfer(models.Model):
    """Перевод средств между наличными и личными картами"""

    TRANSFER_TYPE_CHOICES = [
        ('CASH_TO_CARD', 'Наличные → Карта'),
        ('CARD_TO_CASH', 'Карта → Наличные'),
        ('CARD_TO_CARD', 'Карта → Карта'),
        ('CARD_INCOME', 'Поступление на карту'),
        ('CARD_EXPENSE', 'Расход с карты'),
    ]

    transfer_type = models.CharField(
        max_length=20,
        choices=TRANSFER_TYPE_CHOICES,
        verbose_name="Тип операции",
    )
    from_card = models.ForeignKey(
        PersonalCard,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transfers_out',
        verbose_name="Карта-источник",
    )
    to_card = models.ForeignKey(
        PersonalCard,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='transfers_in',
        verbose_name="Карта-получатель",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Сумма",
    )
    description = models.TextField(
        blank=True,
        default='',
        verbose_name="Описание",
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='personal_transfers',
        verbose_name="Категория",
        help_text="Для расходов с карты"
    )
    date = models.DateTimeField(
        default=timezone.now,
        verbose_name="Дата",
    )
    linked_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='personal_transfers',
        verbose_name="Связанная транзакция",
        help_text="Транзакция в кассе (для переводов нал↔карта)"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='personal_transfers',
        verbose_name="Создал",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создана",
    )

    class Meta:
        verbose_name = "Личный перевод"
        verbose_name_plural = "Личные переводы"
        ordering = ['-date']

    def __str__(self):
        return f"{self.get_transfer_type_display()} — {self.amount}"

    def clean(self):
        errors = {}
        tt = self.transfer_type

        if tt == 'CASH_TO_CARD' and not self.to_card_id:
            errors['to_card'] = "Укажите карту-получатель"
        if tt == 'CARD_TO_CASH' and not self.from_card_id:
            errors['from_card'] = "Укажите карту-источник"
        if tt == 'CARD_TO_CARD':
            if not self.from_card_id:
                errors['from_card'] = "Укажите карту-источник"
            if not self.to_card_id:
                errors['to_card'] = "Укажите карту-получатель"
            if self.from_card_id and self.to_card_id and self.from_card_id == self.to_card_id:
                errors['to_card'] = "Карта-источник и карта-получатель должны быть разными"
        if tt == 'CARD_INCOME' and not self.to_card_id:
            errors['to_card'] = "Укажите карту-получатель"
        if tt == 'CARD_EXPENSE' and not self.from_card_id:
            errors['from_card'] = "Укажите карту-источник"

        if errors:
            raise ValidationError(errors)

    def execute(self, company=None):
        """
        Выполнить перевод: обновить балансы карт и создать Transaction для кассы.
        Вызывается из view после создания объекта.
        """
        from django.db import transaction as db_transaction

        tt = self.transfer_type

        with db_transaction.atomic():
            if tt in ('CASH_TO_CARD', 'CARD_INCOME'):
                card = PersonalCard.objects.select_for_update().get(pk=self.to_card_id)
                card.balance += self.amount
                card.save(update_fields=['balance'])

            if tt in ('CARD_TO_CASH', 'CARD_EXPENSE'):
                card = PersonalCard.objects.select_for_update().get(pk=self.from_card_id)
                card.balance -= self.amount
                card.save(update_fields=['balance'])

            if tt == 'CARD_TO_CARD':
                src = PersonalCard.objects.select_for_update().get(pk=self.from_card_id)
                dst = PersonalCard.objects.select_for_update().get(pk=self.to_card_id)
                src.balance -= self.amount
                dst.balance += self.amount
                src.save(update_fields=['balance'])
                dst.save(update_fields=['balance'])

            if tt == 'CASH_TO_CARD' and company:
                tx = Transaction.objects.create(
                    type='ADJUSTMENT',
                    method='CASH',
                    amount=self.amount,
                    currency='EUR',
                    from_company=company,
                    description=self.description or f'Перевод на карту {self.to_card}',
                    status='COMPLETED',
                    date=self.date,
                    created_by=self.created_by,
                )
                self.linked_transaction = tx
                self.save(update_fields=['linked_transaction'])

            if tt == 'CARD_TO_CASH' and company:
                tx = Transaction.objects.create(
                    type='ADJUSTMENT',
                    method='CASH',
                    amount=self.amount,
                    currency='EUR',
                    to_company=company,
                    description=self.description or f'Снятие с карты {self.from_card}',
                    status='COMPLETED',
                    date=self.date,
                    created_by=self.created_by,
                )
                self.linked_transaction = tx
                self.save(update_fields=['linked_transaction'])
