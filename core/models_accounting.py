"""
Модели для интеграции с бухгалтерским сервисом site.pro (бывший b1.lt)
=====================================================================

Поддерживает:
- SiteProConnection — подключение к site.pro API
- SiteProInvoiceSync — лог синхронизации инвойсов

Авторы: AI Assistant
Дата: Февраль 2026
"""

from django.db import models
from django.utils import timezone
from .models_banking import encrypt_value, decrypt_value
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# SITE.PRO CONNECTION
# ============================================================================

class SiteProConnection(models.Model):
    """Подключение к site.pro (b1.lt) бухгалтерскому API."""

    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE,
        related_name='sitepro_connections', verbose_name='Компания',
    )
    name = models.CharField(
        max_length=100, verbose_name='Название подключения',
        help_text='Например: Site.pro Caromoto Lithuania',
    )

    # --- Credentials (зашифрованы через Fernet) ---
    _username = models.TextField(
        blank=True, default='', db_column='sp_username',
        verbose_name='Username (encrypted)',
    )
    _password = models.TextField(
        blank=True, default='', db_column='sp_password',
        verbose_name='Password (encrypted)',
    )
    _access_token = models.TextField(
        blank=True, default='', db_column='sp_access_token',
        verbose_name='Access Token (encrypted)',
    )
    access_token_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Access Token истекает',
    )
    sitepro_user_id = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name='SitePro User ID',
        help_text='ID пользователя в site.pro (из ответа /token)',
    )
    sitepro_company_id = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name='SitePro Company ID',
        help_text='ID компании в site.pro (spcoid из ответа /token)',
    )

    # --- Настройки ---
    is_active = models.BooleanField(default=True, verbose_name='Активно')
    auto_push_on_issue = models.BooleanField(
        default=False, verbose_name='Авто-отправка при выставлении',
        help_text='Автоматически отправлять инвойс в site.pro при смене статуса на ISSUED',
    )
    default_vat_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name='Ставка НДС по умолчанию (%)',
        help_text='Ставка НДС для позиций инвойса (0 = без НДС)',
    )
    default_currency = models.CharField(
        max_length=3, default='EUR', verbose_name='Валюта по умолчанию',
    )
    invoice_series = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Серия инвойсов в site.pro',
        help_text='Серия нумерации инвойсов (например: CAR)',
    )

    # --- Статус ---
    last_synced_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Последняя синхронизация',
    )
    last_error = models.TextField(
        blank=True, default='', verbose_name='Последняя ошибка',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Подключение site.pro'
        verbose_name_plural = 'Подключения site.pro'

    def __str__(self):
        status = 'активно' if self.is_active else 'неактивно'
        return f'Site.pro — {self.name} ({status})'

    # --- Property-обёртки для шифрования ---
    @property
    def username(self):
        return decrypt_value(self._username)

    @username.setter
    def username(self, value):
        self._username = encrypt_value(value)

    @property
    def password(self):
        return decrypt_value(self._password)

    @password.setter
    def password(self, value):
        self._password = encrypt_value(value)

    @property
    def access_token(self):
        return decrypt_value(self._access_token)

    @access_token.setter
    def access_token(self, value):
        self._access_token = encrypt_value(value)

    @property
    def is_token_expired(self):
        if not self.access_token_expires_at:
            return True
        return timezone.now() >= self.access_token_expires_at

    @property
    def base_url(self):
        return 'https://api.sitepro.com'


# ============================================================================
# SITE.PRO INVOICE SYNC LOG
# ============================================================================

class SiteProInvoiceSync(models.Model):
    """Лог синхронизации инвойсов с site.pro."""

    SYNC_STATUS_CHOICES = [
        ('PENDING', 'Ожидает отправки'),
        ('SENT', 'Отправлен'),
        ('FAILED', 'Ошибка'),
        ('PDF_READY', 'PDF готов'),
    ]

    connection = models.ForeignKey(
        SiteProConnection, on_delete=models.CASCADE,
        related_name='invoice_syncs', verbose_name='Подключение',
    )
    invoice = models.ForeignKey(
        'core.NewInvoice', on_delete=models.CASCADE,
        related_name='sitepro_syncs', verbose_name='Инвойс',
    )

    # --- Данные из site.pro ---
    external_id = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='ID в site.pro',
        help_text='ID инвойса/продажи в site.pro',
    )
    external_number = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Номер в site.pro',
        help_text='Номер инвойса в site.pro',
    )
    pdf_url = models.URLField(
        blank=True, default='', verbose_name='PDF URL',
        help_text='Ссылка на PDF инвойса в site.pro',
    )

    # --- Статус ---
    sync_status = models.CharField(
        max_length=20, choices=SYNC_STATUS_CHOICES, default='PENDING',
        verbose_name='Статус синхронизации',
    )
    error_message = models.TextField(
        blank=True, default='', verbose_name='Ошибка',
    )
    last_synced_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Последняя синхронизация',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Синхронизация инвойса (site.pro)'
        verbose_name_plural = 'Синхронизация инвойсов (site.pro)'
        unique_together = ('connection', 'invoice')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.invoice.number} → site.pro ({self.get_sync_status_display()})'
