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

import logging
from datetime import UTC

from django.db import models
from django.utils import timezone

# Реэкспорт шифрования из выделенного модуля — он же используется
# command'ом `rotate_encryption_key` и в `models_accounting.py`.
# Старый импорт `from core.models_banking import encrypt_value` продолжает
# работать благодаря этим реэкспортам.
from core.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)


# ============================================================================
# BANK CONNECTION
# ============================================================================


class BankConnection(models.Model):
    """Подключение к банковскому API (Revolut, и др. в будущем)."""

    BANK_TYPE_CHOICES = [
        ("REVOLUT", "Revolut Business"),
        ("PAYSERA", "Paysera"),
        ("OTHER", "Другой банк"),
    ]

    bank_type = models.CharField(
        max_length=20,
        choices=BANK_TYPE_CHOICES,
        default="REVOLUT",
        verbose_name="Тип банка",
    )
    company = models.ForeignKey(
        "core.Company",
        on_delete=models.CASCADE,
        related_name="bank_connections",
        verbose_name="Компания",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Название подключения",
        help_text="Например: Revolut Business EUR",
    )

    # --- Credentials (зашифрованы) ---
    _client_id = models.TextField(blank=True, default="", db_column="client_id", verbose_name="Client ID (encrypted)")
    _refresh_token = models.TextField(
        blank=True, default="", db_column="refresh_token", verbose_name="Refresh Token (encrypted)"
    )
    _access_token = models.TextField(
        blank=True, default="", db_column="access_token", verbose_name="Access Token (encrypted)"
    )
    access_token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Access Token истекает",
    )
    _jwt_assertion = models.TextField(
        blank=True, default="", db_column="jwt_assertion", verbose_name="JWT Assertion (encrypted)"
    )

    # --- Настройки ---
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    use_sandbox = models.BooleanField(default=False, verbose_name="Sandbox-режим")
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="Последняя синхронизация")
    last_error = models.TextField(blank=True, default="", verbose_name="Последняя ошибка")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Банковское подключение"
        verbose_name_plural = "Банковские подключения"
        indexes = [
            # `BankConnection.objects.filter(bank_type='REVOLUT', is_active=True)`
            # — типичный запрос Celery-задач (sync_bank_and_reconcile,
            # check_revolut_jwt_expiry, sync_bank_accounts).
            models.Index(fields=["bank_type", "is_active"], name="bank_conn_type_active_idx"),
        ]

    def __str__(self):
        return f"{self.get_bank_type_display()} — {self.name}"

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
            return "https://sandbox-b2b.revolut.com"
        return "https://b2b.revolut.com"

    @property
    def jwt_expires_at(self):
        """Декодирует payload JWT-assertion и возвращает дату истечения (UTC).

        Revolut JWT (client_assertion) подписывается приватным ключом и имеет
        срок жизни, заданный при генерации (по умолчанию 90 дней — см.
        `setup_revolut.py::_generate_jwt`). После истечения refresh_token-flow
        возвращает 401 Unauthorized — синхронизация падает.

        Возвращает `None`, если JWT отсутствует, не парсится или не содержит `exp`.
        """
        if self.bank_type != "REVOLUT":
            return None
        jwt = self.jwt_assertion
        if not jwt or jwt.count(".") != 2:
            return None
        try:
            import base64
            import json
            from datetime import datetime

            payload_b64 = jwt.split(".")[1]
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            exp = payload.get("exp")
            if exp is None:
                return None
            return datetime.fromtimestamp(int(exp), tz=UTC)
        except Exception:
            logger.warning("Не удалось декодировать payload JWT-assertion для %s", self)
            return None

    @property
    def jwt_days_until_expiry(self):
        """Сколько дней осталось до истечения JWT-assertion. None если JWT нет.

        Отрицательное значение = JWT уже просрочен (синхронизация не работает).
        """
        exp = self.jwt_expires_at
        if not exp:
            return None
        delta = exp - timezone.now()
        return delta.days


# ============================================================================
# BANK ACCOUNT (кэшированные данные из API)
# ============================================================================


class BankAccount(models.Model):
    """Банковский счёт — данные из API, обновляются при синхронизации."""

    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name="accounts",
        verbose_name="Подключение",
    )
    external_id = models.CharField(
        max_length=100,
        verbose_name="ID в банке",
        help_text="UUID счёта в Revolut",
    )
    name = models.CharField(max_length=200, blank=True, default="", verbose_name="Название счёта")
    currency = models.CharField(max_length=10, verbose_name="Валюта")
    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name="Баланс",
    )
    state = models.CharField(
        max_length=20,
        default="active",
        verbose_name="Статус",
        help_text="active / inactive",
    )
    last_updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Банковский счёт"
        verbose_name_plural = "Банковские счета"
        constraints = [
            models.UniqueConstraint(fields=["connection", "external_id"], name="unique_bank_account"),
        ]

    def __str__(self):
        return f"{self.name} ({self.currency}) — {self.balance} {self.currency}"


# ============================================================================
# BANK TRANSACTION (кэшированные последние транзакции)
# ============================================================================


class BankTransaction(models.Model):
    """Банковская транзакция — кэш последних операций из API."""

    TRANSACTION_TYPES = [
        ("card_payment", "Оплата картой"),
        ("card_refund", "Возврат по карте"),
        ("transfer", "Перевод"),
        ("exchange", "Обмен валют"),
        ("topup", "Пополнение"),
        ("fee", "Комиссия"),
        ("atm", "Снятие в банкомате"),
        ("refund", "Возврат"),
        ("tax", "Налог"),
        ("other", "Другое"),
    ]

    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Подключение",
    )
    external_id = models.CharField(
        max_length=100,
        verbose_name="ID транзакции в банке",
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=TRANSACTION_TYPES,
        default="other",
        verbose_name="Тип",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Сумма",
    )
    currency = models.CharField(max_length=10, verbose_name="Валюта")
    description = models.TextField(blank=True, default="", verbose_name="Описание")
    counterparty_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="Контрагент",
    )
    state = models.CharField(
        max_length=20,
        default="completed",
        verbose_name="Статус",
        help_text="pending / completed / declined / failed / reverted",
    )
    created_at = models.DateTimeField(verbose_name="Дата транзакции")
    fetched_at = models.DateTimeField(auto_now=True, verbose_name="Загружено")

    # ── Сопоставление с внутренними операциями ──
    matched_transaction = models.ForeignKey(
        "core.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_transactions",
        verbose_name="Связанная транзакция",
        help_text="Внутренняя транзакция (оплата), соответствующая этой банковской операции",
    )
    matched_invoice = models.ForeignKey(
        "core.NewInvoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_transactions",
        verbose_name="Связанный инвойс",
        help_text="Инвойс, к которому относится эта банковская операция",
    )
    reconciliation_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Заметка сопоставления",
        help_text="Комментарий при ручном сопоставлении",
    )
    reconciliation_skipped = models.BooleanField(
        default=False,
        verbose_name="Не требует привязки",
        help_text="Отметьте для операций, не связанных с инвойсами (комиссии, обмены валют и т.д.)",
    )

    # ── Revolut Expenses API: чек и категория из приложения ──
    expense_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Revolut Expense ID",
        help_text="ID связанного expense в Revolut Business (для подгрузки чеков)",
    )
    receipt_file = models.FileField(
        upload_to="bank_receipts/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="Чек из Revolut",
        help_text="Файл чека, автоматически скачанный из Revolut Expenses API",
    )
    receipt_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Чек загружен",
    )
    revolut_category = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Категория Revolut",
        help_text="Категория, назначенная в приложении Revolut (labels/category)",
    )

    class Meta:
        verbose_name = "Банковская транзакция"
        verbose_name_plural = "Банковские транзакции"
        constraints = [
            models.UniqueConstraint(fields=["connection", "external_id"], name="unique_bank_transaction"),
        ]
        ordering = ["-created_at"]
        indexes = [
            # Основные фильтры админки: сопоставление + дата.
            models.Index(fields=["matched_invoice"], name="bt_matched_invoice_idx"),
            models.Index(fields=["matched_transaction"], name="bt_matched_tx_idx"),
            models.Index(fields=["reconciliation_skipped", "created_at"], name="bt_skipped_created_idx"),
            # Сортировка + фильтр по валюте — частый запрос дашбордов.
            models.Index(fields=["currency", "created_at"], name="bt_ccy_created_idx"),
        ]

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"{self.created_at:%d.%m.%Y} {sign}{self.amount} {self.currency} — {self.description[:50]}"

    @property
    def is_reconciled(self):
        """Сопоставлена ли банковская операция с инвойсом, транзакцией, или помечена как не требующая привязки"""
        return (
            self.matched_transaction_id is not None
            or self.matched_invoice_id is not None
            or self.reconciliation_skipped
        )
