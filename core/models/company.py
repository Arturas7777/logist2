"""Caromoto Lithuania — наша компания."""

from django.db import models

from core.managers import OptimizedCompanyManager
from core.mixins import BalanceMethodsMixin


class Company(BalanceMethodsMixin, models.Model):
    """Модель для логистической компании Caromoto Lithuania"""

    name = models.CharField(
        max_length=100, default="Caromoto Lithuania", verbose_name="Название компании", db_index=True
    )

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.00,
        verbose_name="Баланс",
        help_text="Положительный = нам должны, отрицательный = мы должны",
    )
    balance_updated_at = models.DateTimeField(auto_now=True, verbose_name="Баланс обновлен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    objects = OptimizedCompanyManager()

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"

    def __str__(self):
        return self.name

    @classmethod
    def get_default(cls):
        """Возвращает компанию по умолчанию (из settings.COMPANY_NAME).

        Используйте вместо Company.objects.get(pk=1) или
        Company.objects.filter(name='Caromoto Lithuania').first()
        """
        from django.conf import settings

        name = getattr(settings, "COMPANY_NAME", "Caromoto Lithuania")
        return cls.objects.filter(name=name).first()

    @classmethod
    def get_default_id(cls):
        """Возвращает ID компании по умолчанию. Кэширует через Django cache (TTL 5 мин)."""
        from django.core.cache import cache

        cache_key = "company:default_id"
        result = cache.get(cache_key)
        if result is not None:
            return result if result != "__none__" else None
        company = cls.get_default()
        value = company.pk if company else None
        cache.set(cache_key, value if value is not None else "__none__", 300)
        return value
