"""Клиенты, их тарифы и email-уведомления."""

from django.db import models

from core.managers import OptimizedClientManager
from core.mixins import BalanceMethodsMixin

from ._vehicle_types import VEHICLE_TYPE_CHOICES


class Client(BalanceMethodsMixin, models.Model):
    TARIFF_CHOICES = [
        ("NONE", "Без тарифа"),
        ("FIXED", "Фикс. цена (не зависит от кол-ва)"),
        ("FLEXIBLE", "Гибкая цена (зависит от кол-ва авто)"),
    ]

    name = models.CharField(max_length=100, verbose_name="Имя клиента", db_index=True)
    email = models.EmailField(
        blank=True,
        null=True,
        verbose_name="Email 1",
        help_text="Основной email для уведомлений о разгрузке контейнеров",
    )
    email2 = models.EmailField(
        blank=True, null=True, verbose_name="Email 2", help_text="Дополнительный email для уведомлений"
    )
    email3 = models.EmailField(
        blank=True, null=True, verbose_name="Email 3", help_text="Дополнительный email для уведомлений"
    )
    email4 = models.EmailField(
        blank=True, null=True, verbose_name="Email 4", help_text="Дополнительный email для уведомлений"
    )
    notification_enabled = models.BooleanField(
        default=True, verbose_name="Получать уведомления", help_text="Отправлять email-уведомления о контейнерах"
    )
    tariff_type = models.CharField(
        max_length=10,
        choices=TARIFF_CHOICES,
        default="NONE",
        verbose_name="Тип тарифа",
        help_text="NONE=обычные наценки, FIXED=фикс.цена (не зависит от кол-ва), FLEXIBLE=цена зависит от кол-ва авто в контейнере",
    )

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.00,
        verbose_name="Баланс",
        help_text="Положительный = переплата, отрицательный = долг",
    )
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    objects = OptimizedClientManager()

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return self.name

    def get_notification_emails(self):
        """
        Возвращает список всех заполненных email-адресов для уведомлений.
        Пустые и None значения исключаются.
        """
        emails = []
        for field in [self.email, self.email2, self.email3, self.email4]:
            if field and field.strip():
                emails.append(field.strip())
        return emails

    def has_notification_emails(self):
        """Проверяет, есть ли хотя бы один email для уведомлений"""
        return len(self.get_notification_emails()) > 0

    @property
    def open_invoices_debt(self):
        """Сумма остатков по открытым (не оплаченным) инвойсам клиента.

        Считаются статусы ISSUED / OVERDUE / PARTIALLY_PAID — то есть все
        выставленные документы, по которым клиент ещё нам должен.
        Возвращает положительное число (или 0).
        """
        from decimal import Decimal

        from django.db.models import F, Sum

        from core.mixins import OPEN_INVOICE_STATUSES
        from core.models_billing import NewInvoice

        total = NewInvoice.objects.filter(
            recipient_client=self,
            status__in=OPEN_INVOICE_STATUSES,
        ).aggregate(s=Sum(F("total") - F("paid_amount")))["s"] or Decimal("0.00")
        return total

    @property
    def total_balance(self):
        """Полный баланс клиента: сальдо транзакций минус долг по открытым инвойсам.

        Показывает реальное состояние взаиморасчётов:
        - отрицательное = клиент нам должен (с учётом ещё не оплаченных инвойсов);
        - положительное = у клиента на счету аванс;
        - ноль = всё сведено.
        """
        return self.balance - self.open_invoices_debt

    @property
    def balance_status(self):
        """Статус баланса для отображения (с учётом открытых инвойсов)."""
        tb = self.total_balance
        if tb > 0:
            return "ПЕРЕПЛАТА"
        elif tb < 0:
            return "ДОЛГ"
        return "БАЛАНС"

    @property
    def balance_color(self):
        """Цвет для отображения баланса (с учётом открытых инвойсов)."""
        tb = self.total_balance
        if tb > 0:
            return "#28a745"  # зеленый для переплаты
        elif tb < 0:
            return "#dc3545"  # красный для долга
        return "#6c757d"  # серый для нуля


class ClientTariffRate(models.Model):
    """
    Тариф клиента: общая согласованная цена за авто (все услуги кроме хранения).

    FIXED: цена не зависит от кол-ва авто в контейнере.
    FLEXIBLE: цена зависит от общего кол-ва ТС в контейнере (диапазоны min_cars/max_cars).

    Примеры:
      FIXED:    (SEDAN, min=1, max=None, price=300) — легковой всегда 300€
      FLEXIBLE: (SEDAN, min=3, max=3, price=290)    — легковой при 3 ТС → 290€
                (SEDAN, min=4, max=4, price=265)    — легковой при 4 ТС → 265€
                (SEDAN, min=5, max=None, price=240)  — легковой при 5+ ТС → 240€
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="tariff_rates", verbose_name="Клиент")
    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, verbose_name="Тип ТС")
    min_cars = models.PositiveIntegerField(default=1, verbose_name="От (ТС в контейнере)")
    max_cars = models.PositiveIntegerField(null=True, blank=True, verbose_name="До (пусто = и более)")
    agreed_total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Цена за авто (€)",
        help_text="Общая согласованная цена за все услуги (кроме хранения) для этого типа ТС",
    )

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы по типам ТС"
        ordering = ["vehicle_type", "min_cars"]

    def __str__(self):
        vtype = dict(VEHICLE_TYPE_CHOICES).get(self.vehicle_type, self.vehicle_type)
        if self.max_cars:
            return f"{vtype}, {self.min_cars}-{self.max_cars} ТС → {self.agreed_total_price}€"
        return f"{vtype}, {self.min_cars}+ ТС → {self.agreed_total_price}€"


class ClientEmail(models.Model):
    """Email address for client notifications."""

    client = models.ForeignKey(
        "Client", on_delete=models.CASCADE, related_name="notification_emails", verbose_name="Клиент"
    )
    email = models.EmailField(verbose_name="Email")
    is_primary = models.BooleanField(default=False, verbose_name="Основной")

    class Meta:
        verbose_name = "Email клиента"
        verbose_name_plural = "Email-адреса клиентов"
        constraints = [
            models.UniqueConstraint(fields=["client", "email"], name="unique_client_email"),
        ]

    def __str__(self):
        return f"{self.client.name} — {self.email}"
