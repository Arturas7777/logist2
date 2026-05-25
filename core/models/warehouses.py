"""Склады и их площадки."""

from django.core.validators import MinValueValidator
from django.db import models

from core.managers import OptimizedWarehouseManager
from core.mixins import BalanceMethodsMixin


class Warehouse(BalanceMethodsMixin, models.Model):
    SITE_CHOICES = [
        (1, "Площадка 1"),
        (2, "Площадка 2"),
        (3, "Площадка 3"),
    ]

    name = models.CharField(max_length=100, verbose_name="Название склада", db_index=True)

    # Площадка 1
    address_name = models.CharField(max_length=100, blank=True, verbose_name="Название площадки 1")
    address = models.CharField(max_length=300, blank=True, verbose_name="Адрес площадки 1")

    # Площадка 2
    address2_name = models.CharField(max_length=100, blank=True, verbose_name="Название площадки 2")
    address2 = models.CharField(max_length=300, blank=True, verbose_name="Адрес площадки 2")

    # Площадка 3
    address3_name = models.CharField(max_length=100, blank=True, verbose_name="Название площадки 3")
    address3 = models.CharField(max_length=300, blank=True, verbose_name="Адрес площадки 3")

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.00,
        verbose_name="Баланс",
        help_text="Положительный = нам должны, отрицательный = мы должны",
    )
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    # Цены на услуги
    default_unloading_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Цена за разгрузку"
    )
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    complex_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Комплекс", validators=[MinValueValidator(0)]
    )
    delivery_to_warehouse = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Доставка до склада"
    )
    loading_on_trawl = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Погрузка на трал")
    documents_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Документы")
    transfer_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Плата за передачу")
    transit_declaration = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Транзитная декл."
    )
    export_declaration = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Экспортная декл."
    )
    additional_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Доп.расходы")

    objects = OptimizedWarehouseManager()

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"

    def __str__(self):
        return self.name

    def get_site_address(self, site_number):
        """Возвращает (name, address) для площадки 1/2/3"""
        if site_number == 2:
            return (self.address2_name, self.address2)
        elif site_number == 3:
            return (self.address3_name, self.address3)
        return (self.address_name, self.address)

    def get_available_sites(self):
        """Возвращает список (number, address) площадок с заполненным адресом"""
        sites = []
        if self.address:
            sites.append((1, self.address))
        if self.address2:
            sites.append((2, self.address2))
        if self.address3:
            sites.append((3, self.address3))
        return sites


class WarehouseSite(models.Model):
    """Individual site/location of a warehouse."""

    warehouse = models.ForeignKey("Warehouse", on_delete=models.CASCADE, related_name="sites", verbose_name="Склад")
    number = models.PositiveSmallIntegerField(verbose_name="Номер площадки")
    name = models.CharField(max_length=100, blank=True, verbose_name="Название площадки")
    address = models.CharField(max_length=300, blank=True, verbose_name="Адрес")

    class Meta:
        verbose_name = "Площадка склада"
        verbose_name_plural = "Площадки складов"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "number"], name="unique_warehouse_site_number"),
        ]
        ordering = ["warehouse", "number"]

    def __str__(self):
        label = self.name or f"Площадка {self.number}"
        return f"{self.warehouse.name} — {label}"
