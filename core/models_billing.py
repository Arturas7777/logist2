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
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
import logging

logger = logging.getLogger('django')
User = get_user_model()


# ============================================================================
# КАТЕГОРИИ РАСХОДОВ/ДОХОДОВ
# ============================================================================

class ExpenseCategory(models.Model):
    """Категория расхода/дохода для классификации инвойсов и транзакций"""
    
    CATEGORY_TYPE_CHOICES = [
        ('OPERATIONAL', 'Операционные'),       # склады, линии, перевозчики
        ('ADMINISTRATIVE', 'Административные'), # аренда, коммунальные
        ('SALARY', 'Зарплаты'),
        ('MARKETING', 'Маркетинг'),
        ('TAX', 'Налоги и сборы'),
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
    
    # ========================================================================
    # ИДЕНТИФИКАЦИЯ
    # ========================================================================
    
    number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Номер инвойса",
        help_text="Уникальный номер инвойса (генерируется автоматически)"
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
        # Caromoto Lithuania — Company с id=1
        if self.issuer_company_id == 1:
            return self.DIRECTION_OUTGOING
        if self.recipient_company_id == 1:
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
    
    def get_items_pivot_table(self):
        """
        Возвращает данные для табличного отображения инвойса:
        строки = авто, столбцы = группы услуг (short_name), крайний правый = итого
        """
        from collections import OrderedDict
        
        items = self.items.all().select_related('car').order_by('order')
        
        if not items.exists():
            return None
        
        # Собираем уникальные заголовки столбцов (в порядке появления)
        columns = []
        seen_cols = set()
        
        # Группируем по авто
        car_rows = OrderedDict()
        
        for item in items:
            col_name = item.description
            if col_name not in seen_cols:
                columns.append(col_name)
                seen_cols.add(col_name)
            
            car_key = item.car_id or 0
            if car_key not in car_rows:
                car_label = ''
                if item.car:
                    car_label = f"{item.car.brand}, {item.car.vin}"
                else:
                    car_label = 'Без авто'
                car_rows[car_key] = {
                    'car': item.car,
                    'car_label': car_label,
                    'services': {},
                    'total': Decimal('0'),
                }
            
            car_rows[car_key]['services'][col_name] = item.unit_price
            car_rows[car_key]['total'] += item.total_price
        
        # Считаем итоги по столбцам
        column_totals = {}
        for col in columns:
            column_totals[col] = sum(
                row['services'].get(col, Decimal('0')) for row in car_rows.values()
            )
        
        grand_total = sum(row['total'] for row in car_rows.values())
        
        # Формируем строки с ячейками в порядке столбцов
        rows = []
        for car_data in car_rows.values():
            cells = []
            for col in columns:
                val = car_data['services'].get(col, None)
                cells.append(val)
            rows.append({
                'car_label': car_data['car_label'],
                'cells': cells,
                'total': car_data['total'],
            })
        
        # Итого по столбцам
        col_totals_list = [column_totals[col] for col in columns]
        
        return {
            'columns': columns,
            'rows': rows,
            'col_totals': col_totals_list,
            'grand_total': grand_total,
        }
    
    def update_status(self):
        """Обновить статус на основе оплаты"""
        # Не меняем статус если total = 0 (инвойс без позиций)
        if self.total > 0 and self.paid_amount >= self.total:
            self.status = 'PAID'
        elif self.paid_amount > 0 and self.total > 0:
            self.status = 'PARTIALLY_PAID'
        elif self.is_overdue:
            self.status = 'OVERDUE'
        elif self.status == 'DRAFT':
            pass  # Остается черновиком
        elif self.status == 'PAID' and self.total == 0:
            # Если был PAID но теперь total=0, сбрасываем на ISSUED
            self.status = 'ISSUED'
        # Если уже установлен валидный статус - не меняем
    
    def generate_number(self):
        """Сгенерировать уникальный номер инвойса"""
        from django.utils.timezone import now
        date = now()
        prefix = f"INV-{date.year}{date.month:02d}"
        
        # Находим последний номер за текущий месяц
        last_invoice = NewInvoice.objects.filter(
            number__startswith=prefix
        ).order_by('-number').first()
        
        if last_invoice:
            # Извлекаем номер и увеличиваем
            try:
                last_num = int(last_invoice.number.split('-')[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        
        return f"{prefix}-{next_num:04d}"
    
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
        for car in self.cars.all():
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
                
                # Хранение обрабатывается отдельно (не через CarService)
                if service_name == 'Хранение':
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
    
    def save(self, *args, **kwargs):
        """Переопределяем save для автоматической генерации номера и обновления статуса"""
        # Генерируем номер для новых инвойсов
        if not self.number:
            self.number = self.generate_number()
        
        # Устанавливаем срок оплаты, если не указан
        if not self.due_date:
            self.due_date = timezone.now().date() + timezone.timedelta(days=14)
        
        # Обновляем статус
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
        validators=[MinValueValidator(0)],
        verbose_name="Цена за единицу"
    )
    
    total_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        verbose_name="Сумма",
        help_text="Автоматически рассчитывается: количество × цена"
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
    
    def generate_number(self):
        """Сгенерировать уникальный номер транзакции"""
        from django.utils.timezone import now
        date = now()
        prefix = f"TRX-{date.year}{date.month:02d}{date.day:02d}"
        
        # Находим последнюю транзакцию за текущий день
        last_transaction = Transaction.objects.filter(
            number__startswith=prefix
        ).order_by('-number').first()
        
        if last_transaction:
            try:
                last_num = int(last_transaction.number.split('-')[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        
        return f"{prefix}-{next_num:05d}"
    
    def save(self, *args, **kwargs):
        """Переопределяем save для автоматической генерации номера"""
        if not self.number:
            self.number = self.generate_number()

        super().save(*args, **kwargs)
