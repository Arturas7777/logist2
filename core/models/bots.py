"""Доступ ботов к CRM: кто, что изменил, очередь работы, проформы.

Боты больше не ходят в базу под root. Команда ``manage.py bot`` проверяет
токен, роль и пишет журнал. Секрет токена хранится только как SHA-256.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q


class BotPrincipal(models.Model):
    ROLE_CHIEF = "chief"
    ROLE_ACCOUNTANT = "accountant"
    ROLE_NETO = "neto"
    ROLE_HANDOVER = "handover"
    ROLE_PARSER = "parser"
    ROLE_CHOICES = [
        (ROLE_CHIEF, "Главный бот"),
        (ROLE_ACCOUNTANT, "Бухгалтер"),
        (ROLE_NETO, "Заявки в Neto"),
        (ROLE_HANDOVER, "Передача контейнера"),
        (ROLE_PARSER, "Парсер контейнеров"),
    ]

    name = models.CharField(max_length=64, unique=True, verbose_name="Имя")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, verbose_name="Роль")
    token_hash = models.CharField(max_length=64, unique=True, verbose_name="Хеш токена")
    token_prefix = models.CharField(max_length=8, blank=True, default="", verbose_name="Начало токена")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Бот"
        verbose_name_plural = "Боты"

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"


class BotAuditEntry(models.Model):
    principal_name = models.CharField(max_length=64, verbose_name="Кто")
    role = models.CharField(max_length=20, blank=True, default="")
    command = models.CharField(max_length=64, verbose_name="Команда")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    source_email_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="Письмо-источник")
    dry_run = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        verbose_name = "Журнал бота"
        verbose_name_plural = "Журнал ботов"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.principal_name} {self.command}"


class BotCommandReceipt(models.Model):
    """Повтор того же ключа возвращает прошлый результат и ничего не пишет."""

    principal = models.ForeignKey(BotPrincipal, on_delete=models.CASCADE, related_name="receipts")
    idempotency_key = models.CharField(max_length=80)
    command = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["principal", "idempotency_key"], name="uniq_bot_receipt_key"),
        ]


class BotLock(models.Model):
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64)
    principal = models.ForeignKey(BotPrincipal, on_delete=models.CASCADE, related_name="locks")
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target_type", "target_id"], name="uniq_bot_lock_target"),
        ]


class BotJob(models.Model):
    STATUS_NEW = "NEW"
    STATUS_CLAIMED = "CLAIMED"
    STATUS_DONE = "DONE"
    STATUS_CHOICES = [
        (STATUS_NEW, "Новое"),
        (STATUS_CLAIMED, "Взято"),
        (STATUS_DONE, "Сделано"),
    ]

    job_type = models.CharField(max_length=40, db_index=True)
    role = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    source_email = models.ForeignKey(
        "ContainerEmail", null=True, blank=True, on_delete=models.SET_NULL, related_name="bot_jobs"
    )
    claimed_by = models.ForeignKey(BotPrincipal, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Задание боту"
        verbose_name_plural = "Задания ботам"
        constraints = [
            models.UniqueConstraint(
                fields=["source_email", "job_type"],
                condition=Q(source_email__isnull=False),
                name="uniq_botjob_email_type",
            ),
        ]


class BotEmailMark(models.Model):
    """Письмо обработано этим ботом. Курсор next_after_id боту больше не нужен."""

    email = models.ForeignKey("ContainerEmail", on_delete=models.CASCADE, related_name="bot_marks")
    principal = models.ForeignKey(BotPrincipal, on_delete=models.CASCADE, related_name="email_marks")
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["email", "principal"], name="uniq_bot_email_mark"),
        ]


class EtaHistory(models.Model):
    container = models.ForeignKey("Container", on_delete=models.CASCADE, related_name="eta_history")
    eta = models.DateField()
    source = models.CharField(max_length=20, blank=True, default="")
    previous_eta = models.DateField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "История ETA"
        verbose_name_plural = "История ETA"
        ordering = ["-recorded_at", "-id"]


class CaromotoClientMap(models.Model):
    office = models.CharField(max_length=128, blank=True, default="", verbose_name="Офис")
    caromoto_client_id = models.CharField(max_length=64, verbose_name="ID клиента Caromoto")
    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="caromoto_maps")
    priority = models.PositiveSmallIntegerField(default=0, verbose_name="Приоритет")

    class Meta:
        verbose_name = "Клиент Caromoto"
        verbose_name_plural = "Клиенты Caromoto"
        ordering = ["-priority", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["office", "caromoto_client_id"],
                name="uniq_caromoto_client_office",
            ),
        ]

    def __str__(self):
        return f"{self.office} / {self.caromoto_client_id} → {self.client_id}"


class CaromotoImportBatch(models.Model):
    """Сохранённый dry-run. Применение идёт по id партии, не по файлу повторно."""

    created_at = models.DateTimeField(auto_now_add=True)
    principal_name = models.CharField(max_length=64, blank=True, default="")
    diff = models.JSONField(default=dict)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Импорт Caromoto"
        ordering = ["-created_at"]


class Proforma(models.Model):
    KIND_OFFICIAL = "OFFICIAL"
    KIND_UNOFFICIAL = "UNOFFICIAL"
    KIND_CHOICES = [
        (KIND_OFFICIAL, "Официальная AV"),
        (KIND_UNOFFICIAL, "Неофициальная AV"),
    ]
    STATUS_OPEN = "OPEN"
    STATUS_PARTIAL = "PARTIAL"
    STATUS_PAID = "PAID"
    STATUS_OVERDUE = "OVERDUE"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Открыта"),
        (STATUS_PARTIAL, "Частично"),
        (STATUS_PAID, "Оплачена"),
        (STATUS_OVERDUE, "Просрочена"),
    ]

    number = models.CharField(max_length=40, unique=True, verbose_name="Номер")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_UNOFFICIAL)
    client = models.ForeignKey("Client", null=True, blank=True, on_delete=models.PROTECT, related_name="proformas")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")
    bank_transactions = models.ManyToManyField("BankTransaction", blank=True, related_name="proformas")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Проформа"
        verbose_name_plural = "Проформы"

    def refresh_status(self, today=None):
        from decimal import Decimal

        from django.utils import timezone

        today = today or timezone.localdate()
        paid = self.paid_amount or Decimal("0")
        if paid >= self.amount:
            self.status = self.STATUS_PAID
        elif paid > 0:
            self.status = self.STATUS_PARTIAL
        elif self.due_date and self.due_date < today:
            self.status = self.STATUS_OVERDUE
        else:
            self.status = self.STATUS_OPEN

    def __str__(self):
        return f"{self.number} {self.status}"


class LedgerEntry(models.Model):
    """Доход или расход не привязанный к машине: +10 €/авто TRAL, BMW не из CRM."""

    KIND_INCOME = "INCOME"
    KIND_EXPENSE = "EXPENSE"
    KIND_CHOICES = [
        (KIND_INCOME, "Доход"),
        (KIND_EXPENSE, "Расход"),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    category = models.ForeignKey(
        "ExpenseCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_entries"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    occurred_on = models.DateField()
    description = models.CharField(max_length=255)
    car = models.ForeignKey("Car", null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_entries")
    bank_transaction = models.ForeignKey(
        "BankTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_entries"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Проводка вне машины"
        verbose_name_plural = "Проводки вне машины"
        ordering = ["-occurred_on", "-id"]


class NetoDispatchCounter(models.Model):
    day = models.DateField()
    warehouse_code = models.CharField(max_length=20)
    seq = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["day", "warehouse_code"], name="uniq_neto_dispatch_day"),
        ]
