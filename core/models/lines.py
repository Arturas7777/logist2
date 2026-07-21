"""Морские линии и связанные справочники."""

from django.db import models

from core.mixins import BalanceMethodsMixin

from ._vehicle_types import VEHICLE_TYPE_CHOICES
from .requisites import CounterpartyContactsMixin, CounterpartyRequisitesMixin


class Line(BalanceMethodsMixin, CounterpartyRequisitesMixin, CounterpartyContactsMixin, models.Model):
    name = models.CharField(max_length=100, verbose_name="Название линии", db_index=True)

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.00,
        verbose_name="Баланс (наличный)",
        help_text=(
            "НАЛИЧНЫЙ баланс контрагента. Положительный = нам должны, "
            "отрицательный = мы должны. Это техническое поле — для UI "
            "и расчётов используйте свойство ``total_balance``, которое "
            "учитывает безналичные транзакции и связанные инвойсы."
        ),
    )
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    # Услуги и цены
    ocean_freight_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за авто)"
    )
    documentation_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость документов"
    )
    handling_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость обработки"
    )
    ths_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="THS сбор (оплата линиям)"
    )
    additional_fees = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы"
    )

    class Meta:
        verbose_name = "Линия"
        verbose_name_plural = "Линии"

    def __str__(self):
        return self.name


class LineTHSCoefficient(models.Model):
    """Коэффициент THS для типа ТС у конкретной линии.

    Используется для распределения общей суммы THS контейнера между ТС
    пропорционально их "весу" (коэффициенту).

    Пример: Контейнер THS = 500 EUR
    3 машины: легковой(1.0) + джип(2.0) + мото(0.5) = сумма весов 3.5
    - Легковой: 500 × (1.0/3.5) = 143 EUR
    - Джип: 500 × (2.0/3.5) = 286 EUR
    - Мото: 500 × (0.5/3.5) = 71 EUR
    """

    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name="ths_coefficients", verbose_name="Линия")
    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, verbose_name="Тип ТС")
    coefficient = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=1.00,
        verbose_name="Коэффициент",
        help_text="Вес типа ТС при распределении THS (1.0 = стандарт, 2.0 = двойной, 0.5 = половина)",
    )

    class Meta:
        verbose_name = "Коэффициент THS для типа ТС"
        verbose_name_plural = "Коэффициенты THS для типов ТС"
        constraints = [
            models.UniqueConstraint(fields=["line", "vehicle_type"], name="unique_line_vehicle_type"),
        ]

    def __str__(self):
        return f"{self.line.name} - {self.get_vehicle_type_display()}: ×{self.coefficient}"
