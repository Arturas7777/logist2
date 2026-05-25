"""Контейнеры — основная единица входящей логистики."""

import logging

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.constants import STATUS_COLORS
from core.managers import OptimizedContainerManager

from .warehouses import Warehouse

logger = logging.getLogger(__name__)


class Container(models.Model):
    STATUS_CHOICES = [
        ("FLOATING", "В пути"),
        ("IN_PORT", "В порту"),
        ("UNLOADED", "Разгружен"),
        ("TRANSFERRED", "Передан"),
    ]

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, "#3a8c3d")  # Темнее зелёного по умолчанию

    CUSTOMS_PROCEDURE_CHOICES = (
        ("TRANSIT", "Транзит"),
        ("IMPORT", "Импорт"),
        ("REEXPORT", "Реэкспорт"),
        ("EXPORT", "Экспорт"),
    )

    number = models.CharField(max_length=100, unique=True, verbose_name="Номер контейнера")
    booking_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Номер букинга",
        help_text="Booking number (букинг) — используется для сопоставления писем с контейнером, "
        "когда номер контейнера ещё не известен.",
    )
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="FLOATING", verbose_name="Статус")
    line = models.ForeignKey("Line", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Морская линия")
    eta = models.DateField(null=True, blank=True, verbose_name="ETA")
    client = models.ForeignKey("Client", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    customs_procedure = models.CharField(
        max_length=20, choices=CUSTOMS_PROCEDURE_CHOICES, null=True, blank=True, verbose_name="Таможенная процедура"
    )
    THS_PAYER_CHOICES = [
        ("LINE", "Напрямую линии"),
        ("WAREHOUSE", "Через склад"),
    ]

    ths = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Оплата линиям",
        validators=[MinValueValidator(0)],
    )
    ths_payer = models.CharField(
        max_length=20,
        choices=THS_PAYER_CHOICES,
        default="LINE",
        verbose_name="Оплата THS через",
        help_text="От чьего имени записать расход THS в карточках ТС: напрямую линии или через склад",
    )
    warehouse_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=160, verbose_name="Оплата складу", validators=[MinValueValidator(0)]
    )
    declaration_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Декларация",
        validators=[MinValueValidator(0)],
    )
    markup = models.DecimalField(
        max_digits=10, decimal_places=2, default=20, verbose_name="Наценка", validators=[MinValueValidator(0)]
    )
    warehouse = models.ForeignKey("Warehouse", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_site = models.SmallIntegerField(choices=Warehouse.SITE_CHOICES, default=1, verbose_name="Адрес")
    planned_unload_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Будем разгружать",
        help_text="Укажите когда планируете разгружать контейнер (клиенты получат уведомление)",
    )
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    unloaded_status_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Статус 'Разгружен' с",
        help_text="Когда контейнер получил статус 'Разгружен' (для задержки синхронизации фото)",
    )
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=5, verbose_name="Ставка", validators=[MinValueValidator(0)]
    )
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Складирование")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания")

    # Google Drive integration
    google_drive_folder_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Google Drive папка",
        help_text="Прямая ссылка на папку с фотографиями контейнера в Google Drive",
    )
    # Скан Dock Receipt (US shipping document) — обычно от Atlantic Express и
    # подобных. Привязывается автоматически при AI-обработке через
    # core.services.scan_extractor / scan_applier.
    dock_receipt_scan = models.FileField(
        upload_to="dock_receipts/%Y/%m/",
        null=True,
        blank=True,
        verbose_name="Скан Dock Receipt (PDF)",
        help_text="Сканированная копия Dock Receipt. AI извлекает container_number, "
        "booking_number, VIN-ы машин и их массу.",
    )

    labels_printed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Наклейки напечатаны",
        help_text="Дата последней печати наклеек для этого контейнера. "
        "Автоматически проставляется при открытии листа печати.",
    )

    objects = OptimizedContainerManager()

    def update_days_and_storage(self):
        if self.status == "UNLOADED" and self.unload_date:
            total_days = (timezone.now().date() - self.unload_date).days + 1
            self.days = max(0, total_days - self.free_days)
            self.storage_cost = self.days * (self.rate or 0)
        else:
            self.days = 0
            self.storage_cost = 0

    def sync_cars(self):
        self.update_days_and_storage()
        Container.objects.update_related(self)

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.status == "UNLOADED":
            if not self.warehouse:
                errors["warehouse"] = "Для статуса 'Разгружен' обязателен склад."
            if not self.unload_date:
                errors["unload_date"] = "Для статуса 'Разгружен' обязательна дата разгрузки."
        if self.unload_date and self.eta and self.unload_date < self.eta:
            errors["unload_date"] = "Дата разгрузки не может быть раньше ETA."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Не вызываем clean() при partial-update через ``update_fields``:
        # такие сохранения делают bulk-операции (пересчёт хранения, набор
        # цены), и им нужен только UPDATE одного поля. Полная валидация
        # (с проверкой склада/даты разгрузки) уместна только при
        # классическом ``container.save()`` со всеми полями.
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            self.clean()
        super().save(*args, **kwargs)

    def sync_cars_after_warehouse_change(self):
        """
        Применяет новый склад ко всем авто контейнера:
        - ставит warehouse
        - жёстко перезаписывает все складские поля дефолтами нового склада
        - дата разгрузки ВСЕГДА наследуется из контейнера (принудительно)
        - пересчитывает хранение и суммы
        Использует bulk_update для минимизации запросов.
        """
        if not self.pk:
            return

        cars = list(self.container_cars.select_related("warehouse").all())
        if not cars:
            return

        update_fields = [
            "warehouse",
            "unload_date",
            "rate",
            "free_days",
            "storage_cost",
            "days",
            "total_price",
            "unload_fee",
            "delivery_fee",
            "loading_fee",
            "docs_fee",
            "transfer_fee",
            "transit_declaration",
            "export_declaration",
            "extra_costs",
            "complex_fee",
        ]

        from .cars import Car

        with transaction.atomic():
            for car in cars:
                car.warehouse = self.warehouse
                car.apply_warehouse_defaults(force=True)
                if self.unload_date:
                    car.unload_date = self.unload_date
                    logger.debug(f"Car {car.vin}: forced unload_date={self.unload_date} from container {self.number}")
                car.update_days_and_storage()
                car.calculate_total_price()

            Car.objects.bulk_update(cars, update_fields, batch_size=50)

    def sync_cars_after_edit(self):
        """
        Обновляет поля машин после изменения контейнера:
        — проставляет склад/клиента, если у авто они пустые,
        — дата разгрузки ВСЕГДА берется из контейнера (принудительное наследование),
        — подтягивает дефолты склада (rate/free_days/и т.д.) при пустых/дефолтных значениях,
        — пересчитывает хранение и цены.
        Использует bulk_update для минимизации запросов.
        """
        if not self.pk:
            return

        cars = list(self.container_cars.select_related("warehouse").all())
        if not cars:
            return

        cars_to_bulk_update = []
        update_fields = {
            "warehouse",
            "client",
            "unload_date",
            "rate",
            "free_days",
            "storage_cost",
            "days",
            "total_price",
            "unload_fee",
            "delivery_fee",
            "loading_fee",
            "docs_fee",
            "transfer_fee",
            "transit_declaration",
            "export_declaration",
            "extra_costs",
            "complex_fee",
        }

        from .cars import Car

        with transaction.atomic():
            for car in cars:
                if not car.warehouse and self.warehouse:
                    car.warehouse = self.warehouse
                if not car.client and self.client:
                    car.client = self.client

                if self.unload_date and car.unload_date != self.unload_date:
                    car.unload_date = self.unload_date
                    logger.info(
                        f"Car {car.vin}: forced unload_date update to {self.unload_date} from container {self.number}"
                    )

                if car.warehouse:
                    car.apply_warehouse_defaults(override_on_defaults=True)

                car.update_days_and_storage()
                car.calculate_total_price()
                cars_to_bulk_update.append(car)

            if cars_to_bulk_update:
                Car.objects.bulk_update(cars_to_bulk_update, list(update_fields), batch_size=50)

    def check_and_update_status_from_cars(self):
        """Если ВСЕ авто в контейнере уже TRANSFERRED — обновить статус контейнера.

        Единственный источник правды для логики «контейнер передан».
        Раньше дублировалось в трёх местах:
        ``Container.check_and_update_status_from_cars`` (2 exists()-запроса),
        ``signals._update_container_status_if_all_transferred`` (1 aggregate)
        и ``car_lifecycle_service.check_container_status`` (обёртка).
        Теперь все обёртки делегируют сюда.

        Один SQL-запрос (aggregate) вместо двух (exists + exists).
        """
        if not self.pk:
            return
        if self.status == "TRANSFERRED":
            return

        from django.db.models import Count, Q

        stats = self.container_cars.aggregate(
            total=Count("id"),
            transferred=Count("id", filter=Q(status="TRANSFERRED")),
        )
        total = stats["total"] or 0
        if total == 0 or stats["transferred"] != total:
            return

        self.status = "TRANSFERRED"
        self.save(update_fields=["status"])
        logger.info(
            "Container %s -> TRANSFERRED (all %d cars transferred)",
            self.number,
            total,
        )

    def get_unload_address(self):
        """Возвращает (name, address) выбранной площадки разгрузки"""
        if self.warehouse:
            return self.warehouse.get_site_address(self.unload_site)
        return ("", "")

    def emails_for_panel(self):
        """Queryset писем для «Переписки» карточки, с per-карточка is_read.

        Аннотирует ``is_read_here`` из ``ContainerEmailLink`` для текущего
        контейнера, чтобы бабблы на фронте показывали корректное состояние
        именно в этой карточке (а не глобальное по письму).
        """
        from django.db.models import OuterRef, Subquery

        from core.models_email import ContainerEmail, ContainerEmailLink

        return (
            ContainerEmail.objects.filter(containers__id=self.pk)
            .annotate(
                is_read_here=Subquery(
                    ContainerEmailLink.objects.filter(email=OuterRef("pk"), container_id=self.pk).values("is_read")[:1]
                )
            )
            .distinct()
            .order_by("-received_at")
        )

    def __str__(self):
        return self.number

    class Meta:
        verbose_name = "Контейнер"
        verbose_name_plural = "Контейнеры"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["client", "status"]),
            models.Index(fields=["warehouse", "status"]),
            models.Index(fields=["line"]),
            models.Index(fields=["eta"]),
            models.Index(fields=["unload_date"]),
            # Celery send_planned_notifications_task фильтрует контейнеры
            # по planned_unload_date с горизонтом 3 дня, без индекса даёт
            # full scan по всей таблице.
            models.Index(fields=["planned_unload_date"]),
            # photo sync wait-window: unloaded_status_at >= now - N days.
            models.Index(fields=["unloaded_status_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(status="UNLOADED")
                | (models.Q(warehouse__isnull=False) & models.Q(unload_date__isnull=False)),
                name="container_unloaded_requires_warehouse_and_date",
            ),
        ]
