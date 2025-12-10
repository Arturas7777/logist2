"""
Миксины для моделей Django
"""

from django.db import models
from decimal import Decimal
import logging

logger = logging.getLogger('django')


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
