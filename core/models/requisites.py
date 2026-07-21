"""Реквизиты контрагентов: общие поля + банковские счета.

``CounterpartyRequisitesMixin`` — регистрационные данные (Įm. k., НДС,
страна, адрес, сайт), применяется ко ВСЕМ контрагентам.

``CounterpartyContactsMixin`` — телефон / общая почта / EORI. Применяется к
Client / Company / Warehouse / Line; у Carrier эти поля существовали до
миксинов (``phone`` / ``email`` / ``eori_code``) и остаются как есть.

``CounterpartyBankAccount`` — банковский счёт контрагента (IBAN/SWIFT),
привязан через GenericFK как ``Contact``. Не путать с ``BankAccount``
(наши собственные счета Revolut/Paysera).
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class CounterpartyRequisitesMixin(models.Model):
    """Регистрационные реквизиты юрлица. Все поля необязательные."""

    imones_kodas = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Įm. k. (код предприятия)",
        help_text="Įmonės kodas — регистрационный код юрлица.",
    )
    vat_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Код НДС плательщика",
        help_text="PVM mokėtojo kodas, например LT100012345678.",
    )
    registration_country = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Страна регистрации",
    )
    physical_address = models.CharField(
        max_length=300,
        blank=True,
        default="",
        verbose_name="Физический адрес",
    )
    website = models.URLField(
        blank=True,
        default="",
        verbose_name="Сайт",
    )

    class Meta:
        abstract = True


class CounterpartyContactsMixin(models.Model):
    """Контактные реквизиты + EORI. Все поля необязательные.

    ``general_email`` при сохранении автоматически дублируется в модель
    «Контакты» (см. ``core/signals/partners.py``).
    """

    phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Телефон",
    )
    general_email = models.EmailField(
        blank=True,
        default="",
        verbose_name="Общая почта",
        help_text="Общий email компании — автоматически попадает в «Контакты».",
    )
    eori_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="EORI код",
        help_text="Код EORI для таможенного оформления.",
    )

    class Meta:
        abstract = True


class CounterpartyBankAccount(models.Model):
    """Банковский счёт контрагента (для платёжных реквизитов в документах)."""

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name="Тип контрагента",
    )
    object_id = models.PositiveIntegerField(verbose_name="ID контрагента")
    counterparty = GenericForeignKey("content_type", "object_id")

    bank_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Банк",
    )
    iban = models.CharField(
        max_length=50,
        verbose_name="IBAN / номер счёта",
    )
    swift = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="SWIFT / BIC",
    )
    currency = models.CharField(
        max_length=3,
        default="EUR",
        verbose_name="Валюта",
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name="Основной",
    )
    notes = models.CharField(
        max_length=300,
        blank=True,
        default="",
        verbose_name="Примечание",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Счёт контрагента"
        verbose_name_plural = "Счета контрагента"
        ordering = ["-is_primary", "bank_name", "iban"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        parts = [p for p in (self.bank_name, self.iban) if p]
        return " ".join(parts) or f"Счёт #{self.pk}"
