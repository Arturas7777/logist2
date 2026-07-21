"""Перевозчики, их транспорт и водители."""

from django.db import models

from core.mixins import BalanceMethodsMixin

from .requisites import CounterpartyRequisitesMixin


# Carrier получает только регистрационные реквизиты: телефон / email / EORI
# у него существовали до миксинов как собственные поля.
class Carrier(BalanceMethodsMixin, CounterpartyRequisitesMixin, models.Model):
    name = models.CharField(max_length=100, verbose_name="Название перевозчика", db_index=True)
    short_name = models.CharField(max_length=20, blank=True, null=True, verbose_name="Короткое название")
    contact_person = models.CharField(max_length=100, blank=True, null=True, verbose_name="Контактное лицо")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, null=True, verbose_name="Email")
    eori_code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="EORI код",
    )

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.00,
        verbose_name="Баланс",
        help_text="Положительный = нам должны, отрицательный = мы должны",
    )
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    transport_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость перевозки (за км)"
    )
    loading_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость погрузки")
    unloading_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Стоимость разгрузки"
    )
    fuel_surcharge = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Топливная надбавка"
    )
    additional_fees = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, verbose_name="Дополнительные сборы"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Перевозчик"
        verbose_name_plural = "Перевозчики"

    def __str__(self):
        return self.name


class CarrierTruck(models.Model):
    """Автовоз перевозчика (тягач + прицеп)"""

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="trucks", verbose_name="Перевозчик")
    truck_number = models.CharField(max_length=20, verbose_name="Номер тягача", help_text="Номер головы автовоза")
    trailer_number = models.CharField(
        max_length=20, blank=True, verbose_name="Номер прицепа", help_text="Номер прицепа (опционально)"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    notes = models.TextField(blank=True, verbose_name="Примечания")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Автовоз перевозчика"
        verbose_name_plural = "Автовозы перевозчиков"
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "truck_number", "trailer_number"], name="unique_carrier_truck_trailer"
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        if self.trailer_number:
            return f"{self.truck_number} / {self.trailer_number}"
        return self.truck_number

    @property
    def full_number(self):
        """Полный номер автовоза в формате XXXXX / XXXXX"""
        if self.trailer_number:
            return f"{self.truck_number} / {self.trailer_number}"
        return self.truck_number


class CarrierDriver(models.Model):
    """Водитель перевозчика"""

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="drivers", verbose_name="Перевозчик")
    first_name = models.CharField(max_length=50, verbose_name="Имя")
    last_name = models.CharField(max_length=50, verbose_name="Фамилия")
    phone = models.CharField(max_length=20, verbose_name="Телефон")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    notes = models.TextField(blank=True, verbose_name="Примечания")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Водитель перевозчика"
        verbose_name_plural = "Водители перевозчиков"
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        """Полное имя водителя"""
        return f"{self.first_name} {self.last_name}"
