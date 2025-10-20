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
    
    class Meta:
        verbose_name = "Инвойс (новая система)"
        verbose_name_plural = "Инвойсы (новая система)"
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
    
    def update_status(self):
        """Обновить статус на основе оплаты"""
        if self.paid_amount >= self.total:
            self.status = 'PAID'
        elif self.paid_amount > 0:
            self.status = 'PARTIALLY_PAID'
        elif self.is_overdue:
            self.status = 'OVERDUE'
        elif self.status == 'DRAFT':
            pass  # Остается черновиком
        else:
            self.status = 'ISSUED'
    
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
        Автоматически создает позиции инвойса из услуг выбранных автомобилей
        """
        # Удаляем старые позиции
        self.items.all().delete()
        
        issuer = self.issuer
        if not issuer:
            return
        
        issuer_type = issuer.__class__.__name__
        
        order = 0
        for car in self.cars.all():
            # ВАЖНО! Пересчитываем хранение и стоимость перед генерацией позиций
            car.update_days_and_storage()
            car.calculate_total_price()
            
            # Защита от рекурсии - не триггерим сигналы при сохранении
            car._updating_invoices = True
            car.save(update_fields=['storage_cost', 'days', 'current_price', 'total_price'])
            car._updating_invoices = False
            # Определяем какие услуги брать в зависимости от типа выставителя
            if issuer_type == 'Warehouse':
                services = car.get_warehouse_services()
                prefix = 'Склад'
                
                # ВАЖНО! Добавляем хранение как отдельную позицию
                if car.storage_cost and car.storage_cost > 0:
                    InvoiceItem.objects.create(
                        invoice=self,
                        description=f"Хранение - {car.brand} {car.vin} ({car.days} дн.)",
                        car=car,
                        quantity=car.days,
                        unit_price=car.warehouse.rate if car.warehouse else Decimal('0'),
                        order=order
                    )
                    order += 1
                    
            elif issuer_type == 'Line':
                services = car.get_line_services()
                prefix = 'Линия'
            elif issuer_type == 'Carrier':
                services = car.get_carrier_services()
                prefix = 'Перевозчик'
            elif issuer_type == 'Company':
                # Компания выставляет клиенту - все услуги + хранение + наценка
                services = car.car_services.all()
                prefix = 'Все услуги'
                
                # Определяем статус для описания
                status_note = ""
                if car.status == 'TRANSFERRED' and car.transfer_date:
                    status_note = f" [Передан {car.transfer_date}]"
                else:
                    from django.utils import timezone
                    status_note = f" [Текущее хранение на {timezone.now().date()}]"
                
                # Добавляем хранение для клиентских инвойсов
                if car.storage_cost and car.storage_cost > 0:
                    InvoiceItem.objects.create(
                        invoice=self,
                        description=f"Хранение - {car.brand} {car.vin} ({car.days} дн.){status_note}",
                        car=car,
                        quantity=car.days,
                        unit_price=car.warehouse.rate if car.warehouse else Decimal('0'),
                        order=order
                    )
                    order += 1
                
                # Добавляем наценку Caromoto Lithuania как отдельную позицию
                if car.proft and car.proft > 0:
                    InvoiceItem.objects.create(
                        invoice=self,
                        description=f"Наценка Caromoto Lithuania - {car.brand} {car.vin}",
                        car=car,
                        quantity=1,
                        unit_price=car.proft,
                        order=order
                    )
                    order += 1
            else:
                continue
            
            # Создаем позиции из услуг
            for service in services:
                service_name = service.get_service_name()
                price = service.custom_price if service.custom_price else service.get_default_price()
                
                InvoiceItem.objects.create(
                    invoice=self,
                    description=f"{prefix}: {service_name} - {car.brand} {car.vin}",
                    car=car,
                    quantity=service.quantity,
                    unit_price=price,
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
