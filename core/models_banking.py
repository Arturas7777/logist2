"""
Модели для интеграции с банковскими API (Revolut и др.)
========================================================

Поддерживает:
- BankConnection — подключение к банковскому API
- BankAccount — кэшированные данные счетов
- BankTransaction — кэшированные последние транзакции

Авторы: AI Assistant
Дата: Февраль 2026
"""

from django.db import models
from django.utils import timezone
from cryptography.fernet import Fernet
from django.conf import settings
import base64
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encryption helpers — используем SECRET_KEY для Fernet-шифрования токенов
# ---------------------------------------------------------------------------

def _get_fernet():
    """Создаёт Fernet-ключ из Django SECRET_KEY (детерминированно)."""
    import hashlib
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_value(plain_text: str) -> str:
    """Шифрует строку и возвращает base64-текст для хранения в БД."""
    if not plain_text:
        return ''
    return _get_fernet().encrypt(plain_text.encode()).decode()


def decrypt_value(cipher_text: str) -> str:
    """Дешифрует строку из БД."""
    if not cipher_text:
        return ''
    try:
        return _get_fernet().decrypt(cipher_text.encode()).decode()
    except Exception:
        logger.warning('Не удалось расшифровать значение — возвращаю пустую строку')
        return ''


# ============================================================================
# BANK CONNECTION
# ============================================================================

class BankConnection(models.Model):
    """Подключение к банковскому API (Revolut, и др. в будущем)."""

    BANK_TYPE_CHOICES = [
        ('REVOLUT', 'Revolut Business'),
        ('OTHER', 'Другой банк'),
    ]

    bank_type = models.CharField(
        max_length=20, choices=BANK_TYPE_CHOICES, default='REVOLUT',
        verbose_name='Тип банка',
    )
    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE,
        related_name='bank_connections', verbose_name='Компания',
    )
    name = models.CharField(
        max_length=100, verbose_name='Название подключения',
        help_text='Например: Revolut Business EUR',
    )

    # --- Credentials (зашифрованы) ---
    _client_id = models.TextField(blank=True, default='', db_column='client_id',
                                  verbose_name='Client ID (encrypted)')
    _refresh_token = models.TextField(blank=True, default='', db_column='refresh_token',
                                      verbose_name='Refresh Token (encrypted)')
    _access_token = models.TextField(blank=True, default='', db_column='access_token',
                                     verbose_name='Access Token (encrypted)')
    access_token_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Access Token истекает',
    )
    _jwt_assertion = models.TextField(blank=True, default='', db_column='jwt_assertion',
                                      verbose_name='JWT Assertion (encrypted)')

    # --- Настройки ---
    is_active = models.BooleanField(default=True, verbose_name='Активно')
    use_sandbox = models.BooleanField(default=False, verbose_name='Sandbox-режим')
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name='Последняя синхронизация')
    last_error = models.TextField(blank=True, default='', verbose_name='Последняя ошибка')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Банковское подключение'
        verbose_name_plural = 'Банковские подключения'

    def __str__(self):
        return f'{self.get_bank_type_display()} — {self.name}'

    # --- Property-обёртки для шифрования ---
    @property
    def client_id(self):
        return decrypt_value(self._client_id)

    @client_id.setter
    def client_id(self, value):
        self._client_id = encrypt_value(value)

    @property
    def refresh_token(self):
        return decrypt_value(self._refresh_token)

    @refresh_token.setter
    def refresh_token(self, value):
        self._refresh_token = encrypt_value(value)

    @property
    def access_token(self):
        return decrypt_value(self._access_token)

    @access_token.setter
    def access_token(self, value):
        self._access_token = encrypt_value(value)

    @property
    def jwt_assertion(self):
        return decrypt_value(self._jwt_assertion)

    @jwt_assertion.setter
    def jwt_assertion(self, value):
        self._jwt_assertion = encrypt_value(value)

    @property
    def is_token_expired(self):
        if not self.access_token_expires_at:
            return True
        return timezone.now() >= self.access_token_expires_at

    @property
    def base_url(self):
        if self.use_sandbox:
            return 'https://sandbox-b2b.revolut.com'
        return 'https://b2b.revolut.com'


# ============================================================================
# BANK ACCOUNT (кэшированные данные из API)
# ============================================================================

class BankAccount(models.Model):
    """Банковский счёт — данные из API, обновляются при синхронизации."""

    connection = models.ForeignKey(
        BankConnection, on_delete=models.CASCADE,
        related_name='accounts', verbose_name='Подключение',
    )
    external_id = models.CharField(
        max_length=100, verbose_name='ID в банке',
        help_text='UUID счёта в Revolut',
    )
    name = models.CharField(max_length=200, blank=True, default='', verbose_name='Название счёта')
    currency = models.CharField(max_length=10, verbose_name='Валюта')
    balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name='Баланс',
    )
    state = models.CharField(
        max_length=20, default='active', verbose_name='Статус',
        help_text='active / inactive',
    )
    last_updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Банковский счёт'
        verbose_name_plural = 'Банковские счета'
        unique_together = ('connection', 'external_id')

    def __str__(self):
        return f'{self.name} ({self.currency}) — {self.balance} {self.currency}'


# ============================================================================
# BANK TRANSACTION (кэшированные последние транзакции)
# ============================================================================

class BankTransaction(models.Model):
    """Банковская транзакция — кэш последних операций из API."""

    TRANSACTION_TYPES = [
        ('card_payment', 'Оплата картой'),
        ('card_refund', 'Возврат по карте'),
        ('transfer', 'Перевод'),
        ('exchange', 'Обмен валют'),
        ('topup', 'Пополнение'),
        ('fee', 'Комиссия'),
        ('atm', 'Снятие в банкомате'),
        ('refund', 'Возврат'),
        ('tax', 'Налог'),
        ('other', 'Другое'),
    ]

    connection = models.ForeignKey(
        BankConnection, on_delete=models.CASCADE,
        related_name='transactions', verbose_name='Подключение',
    )
    external_id = models.CharField(
        max_length=100, verbose_name='ID транзакции в банке',
    )
    transaction_type = models.CharField(
        max_length=30, choices=TRANSACTION_TYPES, default='other',
        verbose_name='Тип',
    )
    amount = models.DecimalField(
        max_digits=15, decimal_places=2, verbose_name='Сумма',
    )
    currency = models.CharField(max_length=10, verbose_name='Валюта')
    description = models.TextField(blank=True, default='', verbose_name='Описание')
    counterparty_name = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Контрагент',
    )
    state = models.CharField(
        max_length=20, default='completed', verbose_name='Статус',
        help_text='pending / completed / declined / failed / reverted',
    )
    created_at = models.DateTimeField(verbose_name='Дата транзакции')
    fetched_at = models.DateTimeField(auto_now=True, verbose_name='Загружено')

    class Meta:
        verbose_name = 'Банковская транзакция'
        verbose_name_plural = 'Банковские транзакции'
        unique_together = ('connection', 'external_id')
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f'{self.created_at:%d.%m.%Y} {sign}{self.amount} {self.currency} — {self.description[:50]}'
