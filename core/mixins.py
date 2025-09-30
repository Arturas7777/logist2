"""
Миксины для моделей Django
"""

from django.db import models
from decimal import Decimal
import logging

logger = logging.getLogger('django')


class BalanceMixin(models.Model):
    """
    Абстрактный миксин для управления балансами сущностей.
    Используется в моделях: Client, Warehouse, Line, Company, Carrier
    """
    
    invoice_balance = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0.00, 
        verbose_name="Инвойс-баланс"
    )
    cash_balance = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0.00, 
        verbose_name="Наличные"
    )
    card_balance = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0.00, 
        verbose_name="Безнал"
    )
    
    class Meta:
        abstract = True
    
    def get_balance(self, balance_type: str) -> Decimal:
        """
        Получить баланс определенного типа
        
        Args:
            balance_type: Тип баланса ('INVOICE', 'CASH', 'CARD')
            
        Returns:
            Decimal: Значение баланса
        """
        balance_map = {
            'INVOICE': 'invoice_balance',
            'CASH': 'cash_balance',
            'CARD': 'card_balance'
        }
        
        field_name = balance_map.get(balance_type.upper())
        if field_name:
            return getattr(self, field_name, Decimal('0.00'))
        return Decimal('0.00')
    
    def update_balance(self, balance_type: str, amount: Decimal):
        """
        Обновить баланс определенного типа
        
        Args:
            balance_type: Тип баланса ('INVOICE', 'CASH', 'CARD')
            amount: Сумма для добавления (может быть отрицательной)
        """
        balance_map = {
            'INVOICE': 'invoice_balance',
            'CASH': 'cash_balance',
            'CARD': 'card_balance'
        }
        
        field_name = balance_map.get(balance_type.upper())
        if field_name:
            current_balance = getattr(self, field_name)
            setattr(self, field_name, current_balance + Decimal(str(amount)))
            self.save(update_fields=[field_name])
            logger.info(f"Updated {balance_type} balance for {self}: {current_balance} -> {getattr(self, field_name)}")
    
    def get_balance_summary(self) -> dict:
        """
        Получить сводку по всем балансам
        
        Returns:
            dict: Словарь с балансами
        """
        return {
            'invoice_balance': self.invoice_balance,
            'cash_balance': self.cash_balance,
            'card_balance': self.card_balance,
            'total_balance': self.invoice_balance + self.cash_balance + self.card_balance
        }
    
    def update_balance_from_invoices(self):
        """
        Обновляет инвойс-баланс на основе реальных инвойсов и платежей
        Должна быть переопределена в дочерних классах, если нужна специфическая логика
        """
        from django.db.models import Sum
        from .models import InvoiceOLD as Invoice
        
        # Получаем тип модели для фильтрации
        model_name = self.__class__.__name__.upper()
        
        # Сумма всех исходящих инвойсов (мы выставляем счета)
        outgoing_invoices = Invoice.objects.filter(
            from_entity_type=model_name,
            from_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Сумма всех входящих инвойсов (нам выставляют счета)
        incoming_invoices = Invoice.objects.filter(
            to_entity_type=model_name,
            to_entity_id=self.id
        ).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        # Инвойс-баланс = входящие - исходящие
        self.invoice_balance = incoming_invoices - outgoing_invoices
        self.save(update_fields=['invoice_balance'])
        
        logger.debug(f"Updated invoice balance for {self}: {self.invoice_balance}")


class TimestampMixin(models.Model):
    """
    Абстрактный миксин для добавления временных меток
    """
    
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Дата создания"
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name="Дата обновления"
    )
    
    class Meta:
        abstract = True


class SoftDeleteMixin(models.Model):
    """
    Абстрактный миксин для мягкого удаления записей
    """
    
    is_deleted = models.BooleanField(
        default=False, 
        verbose_name="Удалено"
    )
    deleted_at = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name="Дата удаления"
    )
    
    class Meta:
        abstract = True
    
    def soft_delete(self):
        """Мягкое удаление записи"""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])
    
    def restore(self):
        """Восстановление записи"""
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])
