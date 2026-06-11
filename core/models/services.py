"""Услуги поставщиков и привязка их к авто.

Содержит:

- абстрактный ``BaseService`` (общие поля);
- ``CompanyService`` / ``LineService`` / ``CarrierService`` /
  ``WarehouseService`` — справочники услуг по контрагентам;
- ``CarService`` — m2m авто↔услуга с индивидуальной ценой и наценкой;
- ``DeletedCarService`` — память пользовательских удалений (чтобы автоматика
  не возвращала их обратно).
"""

from decimal import Decimal

from django.db import models

from .carriers import Carrier
from .cars import Car
from .company import Company
from .lines import Line
from .warehouses import Warehouse


class BaseService(models.Model):
    """Абстрактная базовая модель для всех типов услуг."""

    name = models.CharField(max_length=200, verbose_name="Название услуги")
    code = models.SlugField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Код",
        help_text="Машинный идентификатор услуги (напр. unloading, storage, ths). Используется для программной привязки.",
    )
    short_name = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Сокращённо",
        help_text="Короткое название для инвойсов и таблиц (напр. THS, Порт, Хран)",
    )
    description = models.TextField(blank=True, verbose_name="Описание")
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена по умолчанию")
    default_markup = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Наценка по умолчанию",
        help_text="Скрытая наценка, которая будет автоматически добавлена при создании услуги для авто",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    add_by_default = models.BooleanField(
        default=False,
        verbose_name="Добавлять по умолчанию",
        help_text="Автоматически добавлять эту услугу при создании автомобиля",
    )

    def save(self, *args, **kwargs):
        if self.code is None:
            self.code = ""
        if self.short_name is None:
            self.short_name = ""
        super().save(*args, **kwargs)

    class Meta:
        abstract = True


class CompanyService(BaseService):
    """Услуги компаний"""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="services", verbose_name="Компания")

    def __str__(self):
        return f"{self.company.name} - {self.name}"

    class Meta:
        verbose_name = "Услуга компании"
        verbose_name_plural = "Услуги компаний"


class LineService(BaseService):
    """Услуги морских линий"""

    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name="services", verbose_name="Линия")

    def __str__(self):
        return f"{self.line.name} - {self.name}"

    class Meta:
        verbose_name = "Услуга линии"
        verbose_name_plural = "Услуги линий"
        indexes = [
            # Используется в signals и car_service_manager: фильтр
            # `LineService.objects.filter(line=..., is_active=True, add_by_default=True)`.
            models.Index(
                fields=["line", "is_active", "add_by_default"],
                name="line_svc_active_default_idx",
            ),
        ]


class CarrierService(BaseService):
    """Услуги перевозчиков"""

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="services", verbose_name="Перевозчик")

    def __str__(self):
        return f"{self.carrier.name} - {self.name}"

    class Meta:
        verbose_name = "Услуга перевозчика"
        verbose_name_plural = "Услуги перевозчиков"
        indexes = [
            models.Index(
                fields=["carrier", "is_active", "add_by_default"],
                name="carrier_svc_active_default_idx",
            ),
        ]


class WarehouseService(BaseService):
    """Услуги складов"""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="services", verbose_name="Склад")

    def __str__(self):
        return f"{self.warehouse.name} - {self.name}"

    class Meta:
        verbose_name = "Услуга склада"
        verbose_name_plural = "Услуги складов"
        indexes = [
            # Главный композитный — для signals.update_cars_on_warehouse_service_change
            # и car_service_manager.find_warehouse_services_for_car.
            models.Index(
                fields=["warehouse", "is_active", "add_by_default"],
                name="wh_svc_active_default_idx",
            ),
        ]


class DeletedCarService(models.Model):
    """Отслеживание удаленных пользователем услуг для конкретного автомобиля"""

    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="deleted_services", verbose_name="Автомобиль")
    service_type = models.CharField(
        max_length=20,
        choices=[
            ("LINE", "Линия"),
            ("CARRIER", "Перевозчик"),
            ("WAREHOUSE", "Склад"),
            ("COMPANY", "Компания"),
        ],
        verbose_name="Тип поставщика",
    )
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    deleted_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата удаления")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["car", "service_type", "service_id"], name="unique_deleted_car_service"),
        ]
        verbose_name = "Удаленная услуга автомобиля"
        verbose_name_plural = "Удаленные услуги автомобилей"


class CarService(models.Model):
    """Связь автомобиля с услугами и их ценами"""

    SERVICE_TYPES = [
        ("LINE", "Линия"),
        ("CARRIER", "Перевозчик"),
        ("WAREHOUSE", "Склад"),
        ("COMPANY", "Компания"),
    ]

    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="car_services", verbose_name="Автомобиль")
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPES, verbose_name="Тип поставщика")
    service_id = models.PositiveIntegerField(verbose_name="ID услуги")
    custom_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Индивидуальная цена"
    )
    markup_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Скрытая наценка",
        help_text="Сумма наценки, которая будет добавлена к цене услуги в инвойсе (скрыто от клиента)",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    notes = models.TextField(blank=True, verbose_name="Примечания")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return f"{self.car.vin} - {self.get_service_name()}: {self.final_price}"

    SERVICE_MODEL_MAP = {
        "LINE": LineService,
        "CARRIER": CarrierService,
        "WAREHOUSE": WarehouseService,
        "COMPANY": CompanyService,
    }

    def _get_service_obj(self):
        """Получает объект услуги с кэшированием через Django cache."""
        from django.core.cache import cache

        cache_key = f"svc:{self.service_type}:{self.service_id}"
        result = cache.get(cache_key)
        if result is not None:
            return result if result != "_none_" else None
        model_class = CarService.SERVICE_MODEL_MAP.get(self.service_type)
        if model_class:
            try:
                obj = model_class.objects.get(id=self.service_id)
                cache.set(cache_key, obj, 300)
                return obj
            except model_class.DoesNotExist:
                cache.set(cache_key, "_none_", 300)
                return None
        cache.set(cache_key, "_none_", 300)
        return None

    def get_service_name(self):
        """Получает название услуги"""
        service = self._get_service_obj()
        if service:
            return service.name
        if self.service_type in CarService.SERVICE_MODEL_MAP:
            return "Услуга не найдена"
        return "Неизвестная услуга"

    def get_service_short_name(self):
        """Получает сокращённое название услуги (для инвойсов и таблиц)"""
        service = self._get_service_obj()
        if service:
            return service.short_name or service.name[:10]
        return "?"

    def get_default_price(self):
        """Получает цену по умолчанию"""
        service = self._get_service_obj()
        if service:
            return service.default_price
        return 0

    @property
    def final_price(self):
        """Итоговая цена с учетом количества (БЕЗ скрытой наценки - для внутреннего учёта)"""
        # Используем custom_price если он задан (даже если 0), иначе default_price
        price = self.custom_price if self.custom_price is not None else self.get_default_price()
        return price * self.quantity

    @property
    def invoice_price(self):
        """Цена для инвойса (С учётом скрытой наценки)"""
        # Используем custom_price если он задан (даже если 0), иначе default_price
        base_price = self.custom_price if self.custom_price is not None else self.get_default_price()
        markup = self.markup_amount if self.markup_amount is not None else Decimal("0")
        return (base_price + markup) * self.quantity

    def get_total_distributed_markup(self):
        """Возвращает сумму распределённой наценки для этого авто"""
        if not self.car_id:
            return Decimal("0")
        return CarService.objects.filter(car_id=self.car_id).aggregate(total=models.Sum("markup_amount"))[
            "total"
        ] or Decimal("0")

    class Meta:
        verbose_name = "Услуга автомобиля"
        verbose_name_plural = "Услуги автомобилей"
        constraints = [
            models.UniqueConstraint(fields=["car", "service_type", "service_id"], name="unique_car_service"),
        ]
        indexes = [
            models.Index(fields=["car", "service_type"]),
            models.Index(fields=["service_type", "service_id"]),
            models.Index(fields=["car"]),
        ]


def prefetch_service_objects(car_services, timeout: int = 300) -> int:
    """Батч-резолвинг каталога услуг (P2, AUDIT_ROUND3).

    ``CarService.invoice_price`` резолвит каталог псевдо-generic FK
    (``service_type`` + ``service_id``) через ``_get_service_obj`` —
    по одному cache-lookup'у (и DB-запросу на промахе) на услугу. При
    массовом пересчёте (``recalculate_cars_total_price_task``) это даёт
    тысячи запросов.

    Здесь собираем все (service_type, service_id), выбираем каталоги
    максимум 4 запросами ``in_bulk`` и прогреваем cache-ключи
    ``svc:<type>:<id>`` одним ``set_many`` — последующие
    ``_get_service_obj()`` в БД уже не ходят.

    Returns:
        число прогретых ключей.
    """
    from collections import defaultdict

    from django.core.cache import cache

    by_type: dict = defaultdict(set)
    for svc in car_services:
        if svc.service_type in CarService.SERVICE_MODEL_MAP:
            by_type[svc.service_type].add(svc.service_id)

    to_cache: dict = {}
    for stype, ids in by_type.items():
        model_class = CarService.SERVICE_MODEL_MAP[stype]
        found = model_class.objects.in_bulk(ids)
        for sid in ids:
            to_cache[f"svc:{stype}:{sid}"] = found.get(sid) or "_none_"

    if to_cache:
        cache.set_many(to_cache, timeout)
    return len(to_cache)
