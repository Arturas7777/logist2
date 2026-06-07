"""Автомобиль — крупнейшая бизнес-сущность."""

import logging
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from core.constants import STATUS_COLORS
from core.managers import OptimizedCarManager
from core.service_codes import ServiceCode

from ._vehicle_types import VEHICLE_TYPE_CHOICES
from .containers import Container
from .warehouses import Warehouse

logger = logging.getLogger(__name__)


class Car(models.Model):
    """Автомобиль — крупнейшая бизнес-сущность.

    DEPRECATED FIELDS (оставлены для совместимости с историческими данными
    и старыми отчётами; в новой логике расчёт идёт через ``CarService``):

      * ``ths``                — было «оплата линиям», заменено CarService(LINE).
      * ``unload_fee``         — заменено CarService(WAREHOUSE, code=unloading).
      * ``delivery_fee``       — заменено CarService(WAREHOUSE, code=delivery).
      * ``loading_fee``        — заменено CarService(WAREHOUSE, code=loading).
      * ``docs_fee``           — заменено CarService(WAREHOUSE, code=documents).
      * ``transfer_fee``       — заменено CarService(WAREHOUSE, code=transfer).
      * ``transit_declaration``— заменено CarService(WAREHOUSE, code=transit_declaration).
      * ``export_declaration`` — заменено CarService(WAREHOUSE, code=export_declaration).
      * ``extra_costs``        — заменено CarService(WAREHOUSE, code=extra_costs).
      * ``complex_fee``        — заменено CarService(WAREHOUSE, code=complex).
      * ``declaration_fee``    — устарело, не используется.
      * ``markup``             — устарело, наценка хранится в CarService.markup_amount.
      * ``free_days``/``rate`` — переехали в WarehouseService(code=free_days/daily_rate).
      * ``price``,``auction_fee``,``transport_usa``,``ocean_freight``,
        ``transport_kz``,``broker_fee``,``additional_expenses`` — расходы
        на покупку авто (аукцион), используются только в отчётах и
        client-dashboard; перенос на отдельную модель PurchaseCost
        в TODO (см. план).

    Удаление этих колонок из БД отложено: исторические инвойсы и отчёты
    могут на них ссылаться. Удалять только после полного аудита данных
    + миграции переноса значений в соответствующие CarService.
    """

    # Ссылка на единый список типов ТС (определён на уровне модуля)
    VEHICLE_TYPE_CHOICES = VEHICLE_TYPE_CHOICES

    year = models.PositiveIntegerField(verbose_name="Год выпуска")
    brand = models.CharField(max_length=50, verbose_name="Марка")
    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, default="SEDAN", verbose_name="Тип ТС")
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    client = models.ForeignKey("Client", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    status = models.CharField(max_length=20, choices=Container.STATUS_CHOICES, verbose_name="Статус")
    warehouse = models.ForeignKey("Warehouse", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Склад")
    unload_site = models.SmallIntegerField(choices=Warehouse.SITE_CHOICES, default=1, verbose_name="Адрес")
    line = models.ForeignKey("Line", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Линия")
    carrier = models.ForeignKey("Carrier", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Перевозчик")
    unload_date = models.DateField(null=True, blank=True, verbose_name="Дата разгрузки")
    transfer_date = models.DateField(null=True, blank=True, verbose_name="Дата передачи")
    has_title = models.BooleanField(default=False, verbose_name="Т")
    title_notes = models.CharField(max_length=200, blank=True, verbose_name="Примечания к тайтлу")
    # Общие примечания и флаг «важное» (см. core.models.Task — авто-создание дела
    # при is_important=True; снятие галочки = ручное завершение дела).
    notes = models.TextField(
        blank=True,
        verbose_name="Примечания",
        help_text="Свободные примечания к авто. Видны во всплывающей подсказке у красного значка в списке авто.",
    )
    is_important = models.BooleanField(
        default=False,
        verbose_name="Важное",
        help_text="Пометить авто как требующее внимания. Пока галочка стоит — "
        "нельзя менять статус и нельзя добавлять авто в автовоз. "
        "Авто с этой пометкой автоматически попадает в раздел «Дела».",
    )
    # Скан физического титула (US car title), загруженный с принтера/сканера и
    # обработанный AI (см. core.services.scan_extractor / scan_applier).
    # При прикреплении автоматически выставляется has_title=True.
    title_scan = models.FileField(
        upload_to="title_scans/%Y/%m/",
        null=True,
        blank=True,
        verbose_name="Скан тайтла (PDF)",
        help_text="Сканированная копия физического титула. Привязывается автоматически "
        "при обработке скана через AI или вручную.",
    )
    # Масса авто (в кг), извлечённая из Dock Receipt. Обычно в lbs на оригинале —
    # храним сразу в kg (1 lb = 0.4535924 kg), чтобы не плодить путаницу.
    weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Масса (кг)",
        validators=[MinValueValidator(0)],
        help_text="Масса автомобиля в килограммах. При импорте из Dock Receipt (US) "
        "значение в lbs конвертируется автоматически (1 lb = 0.4535924 kg).",
    )
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Цена")
    # УДАЛЕНО: current_price - теперь используется только total_price
    storage_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Складирование")
    days = models.PositiveIntegerField(default=0, verbose_name="Платные дни")
    container = models.ForeignKey(
        "Container",
        on_delete=models.CASCADE,
        related_name="container_cars",
        null=True,
        blank=True,
        verbose_name="Контейнер",
    )

    # Расходы
    ths = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Оплата линиям",
        validators=[MinValueValidator(0)],
    )
    unload_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Цена за разгрузку",
        validators=[MinValueValidator(0)],
    )
    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Доставка до склада",
        validators=[MinValueValidator(0)],
    )
    loading_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Погрузка на трал",
        validators=[MinValueValidator(0)],
    )
    docs_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Документы",
        validators=[MinValueValidator(0)],
    )
    transfer_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Плата за передачу",
        validators=[MinValueValidator(0)],
    )
    transit_declaration = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Транзитная декл.",
        validators=[MinValueValidator(0)],
    )
    export_declaration = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Экспортная декл.",
        validators=[MinValueValidator(0)],
    )
    extra_costs = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Доп.расходы",
        validators=[MinValueValidator(0)],
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
        max_digits=10,
        decimal_places=2,
        default=Decimal("20.00"),
        null=True,
        blank=True,
        verbose_name="Наценка",
        validators=[MinValueValidator(0)],
    )
    hide_markup_in = models.ForeignKey(
        "CarService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cars_with_hidden_markup",
        verbose_name="Скрыть наценку в услуге",
        help_text="Выберите услугу, в которую добавить наценку (вместо отдельной строки в инвойсе)",
    )
    free_days = models.PositiveIntegerField(default=0, verbose_name="Бесплатные дни")
    rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=5, verbose_name="Ставка за сутки", validators=[MinValueValidator(0)]
    )
    complex_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Комплекс",
        validators=[MinValueValidator(0)],
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена", validators=[MinValueValidator(0)]
    )
    auction_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Аукционный сбор",
        validators=[MinValueValidator(0)],
    )
    transport_usa = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Транспорт США",
        validators=[MinValueValidator(0)],
    )
    ocean_freight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Океанский фрахт",
        validators=[MinValueValidator(0)],
    )
    transport_kz = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Транспорт КЗ",
        validators=[MinValueValidator(0)],
    )
    broker_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Брокерский сбор",
        validators=[MinValueValidator(0)],
    )
    additional_expenses = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Дополнительные расходы",
        validators=[MinValueValidator(0)],
    )

    objects = OptimizedCarManager()

    def get_status_color(self):
        return STATUS_COLORS.get(self.status, "#3a8c3d")

    def get_unload_address(self):
        """Возвращает (name, address) выбранной площадки разгрузки"""
        if self.warehouse:
            return self.warehouse.get_site_address(self.unload_site)
        return ("", "")

    def apply_warehouse_defaults(self, force: bool = False):
        """
        Копирует дефолты со склада в авто из кастомных услуг.
        force=True — перезаписывает ВСЕ соответствующие поля значениями склада.
        force=False — перезаписывает только если поле пустое или равно дефолту модели.

        .. deprecated::
            Метод пишет в legacy-поля Car (``unload_fee``, ``delivery_fee``,
            ``loading_fee``, ``docs_fee``, ``transfer_fee``,
            ``transit_declaration``, ``export_declaration``, ``extra_costs``,
            ``complex_fee``, ``free_days``, ``rate``). Эти поля больше не
            используются в ``calculate_total_price()`` — расчёт идёт через
            ``CarService``. Метод оставлен для совместимости со старой
            админкой и историческими отчётами; новый код **не должен**
            на него полагаться. Будет удалён после переноса всех читателей
            legacy полей на CarService (см. план в docstring класса Car).
        """
        if not self.warehouse:
            return

        import warnings

        warnings.warn(
            "Car.apply_warehouse_defaults() writes to deprecated legacy fields. "
            "Use CarService records via car_service_manager instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        from decimal import Decimal

        from .services import WarehouseService

        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)

        from core.service_codes import NAME_TO_CODE, ServiceCode

        code_to_car_field = {
            ServiceCode.UNLOADING: "unload_fee",
            ServiceCode.DELIVERY: "delivery_fee",
            ServiceCode.LOADING: "loading_fee",
            ServiceCode.DOCUMENTS: "docs_fee",
            ServiceCode.TRANSFER: "transfer_fee",
            ServiceCode.TRANSIT_DECLARATION: "transit_declaration",
            ServiceCode.EXPORT_DECLARATION: "export_declaration",
            ServiceCode.EXTRA_COSTS: "extra_costs",
            ServiceCode.COMPLEX: "complex_fee",
            ServiceCode.DAILY_RATE: "rate",
            ServiceCode.FREE_DAYS: "free_days",
        }

        for service in warehouse_services:
            svc_code = service.code if service.code else NAME_TO_CODE.get(service.name)
            car_field = code_to_car_field.get(svc_code) if svc_code else None
            if car_field:
                wh_val = service.default_price or 0
                cur_val = getattr(self, car_field, None)

                if force:
                    setattr(self, car_field, wh_val)
                else:
                    if cur_val is None:
                        setattr(self, car_field, wh_val)
                    else:
                        try:
                            cur = Decimal(str(cur_val))
                            if cur == 0:
                                setattr(self, car_field, wh_val)
                            else:
                                model_default = self._meta.get_field(car_field).default
                                mdl = Decimal(str(model_default or 0))
                                if cur == mdl:
                                    setattr(self, car_field, wh_val)
                        except Exception:
                            setattr(self, car_field, wh_val)

    def warehouse_details(self):
        """Возвращает дефолтные цены на услуги из склада из кастомных услуг."""
        if not self.warehouse:
            return {"message": "Склад не назначен"}

        from .services import WarehouseService

        warehouse_services = WarehouseService.objects.filter(warehouse=self.warehouse, is_active=True)

        details = {"Название": self.warehouse.name}
        for service in warehouse_services:
            details[service.name] = str(service.default_price)

        return details

    def set_initial_warehouse_values(self):
        """Подтягивает дефолты со склада при создании авто.
        Если текущее значение = модельному дефолту (например, rate=5) или 0 — берём значение со склада.

        .. deprecated::
            Пишет в legacy-поля Car (free_days, unload_fee, delivery_fee и т.д.).
            При создании нового авто эти поля больше не нужны — расчёт идёт
            через CarService, который автоматически создаётся signal'ом
            ``car_post_save`` → ``_create_car_services_if_needed`` →
            ``apply_warehouse_services_to_car`` из ``car_service_manager``.
            Метод оставлен для совместимости со старыми отчётами, читающими
            ``car.unload_fee`` и др. напрямую.
        """
        if not self.warehouse:
            return

        initializing = self._state.adding

        def _override(cur_val, field_name, wh_val):
            if cur_val is None:
                return wh_val
            if initializing:
                # перетираем, если 0 или равно модельному дефолту
                try:
                    cur = Decimal(str(cur_val))
                    if cur == 0:
                        return wh_val
                    model_default = self._meta.get_field(field_name).default
                    mdl = Decimal(str(model_default or 0))
                    if cur == mdl:
                        return wh_val
                except Exception:
                    return wh_val
            return cur_val

        # ключевые поля (rate удалён - ставка теперь берётся из услуги "Хранение")
        self.free_days = _override(self.free_days, "free_days", self.warehouse.free_days or 0)

        # заодно остальные складские услуги, если нужно
        self.unload_fee = _override(self.unload_fee, "unload_fee", self.warehouse.default_unloading_fee or 0)
        self.delivery_fee = _override(self.delivery_fee, "delivery_fee", self.warehouse.delivery_to_warehouse or 0)
        self.loading_fee = _override(self.loading_fee, "loading_fee", self.warehouse.loading_on_trawl or 0)
        self.docs_fee = _override(self.docs_fee, "docs_fee", self.warehouse.documents_fee or 0)
        self.transfer_fee = _override(self.transfer_fee, "transfer_fee", self.warehouse.transfer_fee or 0)
        self.transit_declaration = _override(
            self.transit_declaration, "transit_declaration", self.warehouse.transit_declaration or 0
        )
        self.export_declaration = _override(
            self.export_declaration, "export_declaration", self.warehouse.export_declaration or 0
        )
        self.extra_costs = _override(self.extra_costs, "extra_costs", self.warehouse.additional_expenses or 0)
        self.complex_fee = _override(self.complex_fee, "complex_fee", self.warehouse.complex_fee or 0)

    def calculate_total_price(self):
        """Пересчитывает цену используя систему услуг CarService.

        Цена = сумма всех услуг + сумма всех скрытых наценок.

        ВАЖНО: total_price включает скрытую наценку (markup_amount)!
        Это полная сумма, которую заплатит клиент.

        Порядок: обновить дни/хранение -> записать цену хранения в CarService ->
        -> сбросить prefetch-кэш -> просуммировать все CarService.
        """
        # 1. Обновляем дни и storage_cost (поля на модели Car)
        self.update_days_and_storage()

        # 2. Записываем рассчитанную цену хранения в CarService (через .update(),
        #    минуя сигналы -- итог всё равно будет учтён ниже при суммировании)
        self._update_storage_service_price()

        # 3. Сбрасываем prefetch-кэш чтобы получить актуальные услуги из БД
        if hasattr(self, "_prefetched_objects_cache"):
            self._prefetched_objects_cache.pop("car_services", None)

        # 4. Один проход по car_services: итоговая цена = sum(invoice_price)
        #    invoice_price = (base_price + markup) * quantity — учитывает наценку корректно
        total = Decimal("0.00")
        for svc in self.car_services.all():
            total += Decimal(str(svc.invoice_price))

        self.total_price = total
        return self.total_price

    def update_days_and_storage(self):
        """Обновляет платные дни и стоимость хранения для автомобиля.

        Цена за день берётся из услуги "Хранение" в списке услуг склада.
        Стоимость = платные_дни × цена_за_день.

        НЕ обновляет CarService и НЕ пересчитывает total_price.
        Для полного пересчёта используйте calculate_total_price().
        """
        if not self.unload_date or not self.warehouse:
            self.days = 0
            self.storage_cost = Decimal("0.00")
            return

        free_days = int(self.warehouse.free_days or 0)

        end_date = self.transfer_date if self.status == "TRANSFERRED" and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1
        self.days = max(0, total_days - free_days)

        daily_rate = self._get_storage_daily_rate()
        self.storage_cost = Decimal(str(self.days)) * daily_rate

    def _get_storage_daily_rate(self):
        """Получает ставку хранения за день из услуги 'Хранение' склада.
        Кэшируется на экземпляре для избежания повторных запросов в рамках одного save().
        """
        if not self.warehouse:
            return Decimal("0.00")

        cache_attr = "_cached_storage_rate"
        cached_wh = "_cached_storage_rate_wh_id"
        if hasattr(self, cache_attr) and getattr(self, cached_wh, None) == self.warehouse_id:
            return getattr(self, cache_attr)

        # Ставка может быть предзагружена аннотацией в списке админки
        # (один Subquery на весь queryset вместо запроса на каждую строку,
        # см. CarAdmin.get_queryset). Используем её только если аннотация
        # посчитана для того же склада (защита от смены склада на форме).
        ann_wh = getattr(self, "_storage_daily_rate_ann_wh", None)
        if ann_wh is not None and ann_wh == self.warehouse_id:
            rate = Decimal(str(getattr(self, "_storage_daily_rate_ann", 0) or 0))
            setattr(self, cache_attr, rate)
            setattr(self, cached_wh, self.warehouse_id)
            return rate

        rate = Decimal("0.00")
        try:
            from .services import WarehouseService

            storage_service = (
                WarehouseService.objects.filter(
                    warehouse=self.warehouse,
                    is_active=True,
                )
                .filter(
                    Q(code=ServiceCode.STORAGE) | Q(name="Хранение"),
                )
                .first()
            )
            if storage_service:
                rate = Decimal(str(storage_service.default_price or 0))
        except Exception:
            pass

        setattr(self, cache_attr, rate)
        setattr(self, cached_wh, self.warehouse_id)
        return rate

    def _update_storage_service_price(self):
        """Обновляет цену услуги 'Хранение' в CarService.

        Цена = платные_дни × ставка_за_день (из WarehouseService)

        ВАЖНО: markup_amount НЕ обновляется автоматически!
        Наценка устанавливается только при создании услуги (из default_markup)
        или вручную пользователем в админке.
        """
        if not self.pk or not self.warehouse:
            return

        try:
            from .services import CarService, WarehouseService

            storage_service = (
                WarehouseService.objects.filter(
                    warehouse=self.warehouse,
                    is_active=True,
                )
                .filter(
                    Q(code=ServiceCode.STORAGE) | Q(name="Хранение"),
                )
                .first()
            )

            if storage_service:
                days = Decimal(str(self.days))
                # Стоимость = платные_дни × цена_за_день
                storage_price = days * Decimal(str(storage_service.default_price or 0))

                # Обновляем ТОЛЬКО цену в CarService (markup_amount не трогаем!)
                # Наценка устанавливается при создании услуги или вручную в админке
                CarService.objects.filter(car=self, service_type="WAREHOUSE", service_id=storage_service.id).update(
                    custom_price=storage_price
                )

                # Сбрасываем prefetch кэш чтобы получить актуальные данные
                if hasattr(self, "_prefetched_objects_cache"):
                    self._prefetched_objects_cache.pop("car_services", None)
        except Exception:
            pass  # Игнорируем ошибки - модель может быть ещё не сохранена

    def sync_with_container(self, container, ths_per_car):
        """Синхронизирует данные автомобиля с контейнером."""
        self.status = container.status
        self.warehouse = container.warehouse
        self.unload_date = container.unload_date
        self.transfer_date = timezone.now().date() if container.status == "TRANSFERRED" else None
        self.ths = ths_per_car
        self.declaration_fee = container.declaration_fee
        self.markup = container.markup
        self.set_initial_warehouse_values()
        self.update_days_and_storage()
        self.calculate_total_price()

    WAREHOUSE_FEE_FIELDS = (
        "unload_fee",  # цена за разгрузку
        "delivery_fee",  # доставка до склада
        "loading_fee",  # погрузка на трал
        "docs_fee",  # документы
        "transfer_fee",  # плата за передачу
        "transit_declaration",  # транзитная декл.
        "export_declaration",  # экспортная декл.
        "extra_costs",  # доп.расходы
        "complex_fee",  # комплекс
    )

    def warehouse_payment_amount(self) -> Decimal:
        """Сколько должны складу за услуги (без учёта хранения) - использует новую систему услуг."""
        return self.get_warehouse_services_total()

    @property
    def warehouse_payment(self) -> Decimal:
        # удобное свойство, если где-то понадобится
        return self.warehouse_payment_amount()

    def get_line_services(self):
        """Получает услуги линии для этого автомобиля"""
        if not self.line or not self.pk:
            return self.car_services.none()
        from .services import LineService

        line_service_ids = LineService.objects.only("id").filter(line=self.line).values_list("id", flat=True)
        return self.car_services.filter(service_type="LINE", service_id__in=line_service_ids)

    def get_carrier_services(self):
        """Получает услуги перевозчика для этого автомобиля"""
        if not self.carrier or not self.pk:
            return self.car_services.none()
        from .services import CarrierService

        carrier_service_ids = (
            CarrierService.objects.only("id").filter(carrier=self.carrier).values_list("id", flat=True)
        )
        return self.car_services.filter(service_type="CARRIER", service_id__in=carrier_service_ids)

    def get_company_services(self):
        """Получает услуги компаний для этого автомобиля"""
        if not self.pk:
            return self.car_services.none()
        return self.car_services.filter(service_type="COMPANY")

    def get_warehouse_services(self):
        """Получает все услуги складов для этого автомобиля (включая услуги от других складов)"""
        if not self.pk:
            return self.car_services.none()
        # Получаем ВСЕ услуги складов, привязанные к этому автомобилю
        return self.car_services.filter(service_type="WAREHOUSE")

    def get_services_total_by_provider(self, provider_type):
        """Получает общую стоимость услуг по типу поставщика.

        Для склада: стоимость хранения уже включена в услугу "Хранение" (CarService).
        """
        total = Decimal("0.00")
        if provider_type == "LINE" and self.line:
            services = self.get_line_services()
        elif provider_type == "CARRIER" and self.carrier:
            services = self.get_carrier_services()
        elif provider_type == "COMPANY":
            services = self.get_company_services()
        elif provider_type == "WAREHOUSE" and self.warehouse:
            services = self.get_warehouse_services()
        else:
            return total

        for service in services:
            total += Decimal(str(service.final_price))
        return total

    def get_warehouse_services_total(self):
        """Получает стоимость только услуг склада (без хранения)"""
        if not self.warehouse:
            return Decimal("0.00")

        services = self.get_warehouse_services()
        total = Decimal("0.00")

        for service in services:
            total += Decimal(str(service.final_price))

        return total

    def calculate_storage_cost(self):
        """Рассчитывает стоимость хранения на складе.

        Ставка берётся из услуги "Хранение" в списке услуг склада.
        """
        if not self.warehouse or not self.unload_date:
            return Decimal("0.00")

        # Получаем ставку из услуги "Хранение" и бесплатные дни со склада
        daily_rate = self._get_storage_daily_rate()
        free_days = self.warehouse.free_days or 0

        # Рассчитываем общее количество дней хранения
        # Включаем день разгрузки и день забора авто
        end_date = self.transfer_date if self.status == "TRANSFERRED" and self.transfer_date else timezone.now().date()
        total_days = (end_date - self.unload_date).days + 1

        # Рассчитываем платные дни (общие дни минус бесплатные)
        chargeable_days = max(0, total_days - free_days)

        # Рассчитываем стоимость
        storage_cost = daily_rate * chargeable_days

        return storage_cost

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.vin and len(self.vin) != 17:
            errors["vin"] = "VIN должен содержать ровно 17 символов."
        if self.year and (self.year < 1900 or self.year > timezone.now().year + 2):
            errors["year"] = f"Год выпуска должен быть между 1900 и {timezone.now().year + 2}."
        if self.transfer_date and self.unload_date and self.transfer_date < self.unload_date:
            errors["transfer_date"] = "Дата передачи не может быть раньше даты разгрузки."
        if errors:
            raise ValidationError(errors)

    def _inherit_from_container(self):
        """Наследует данные из контейнера (склад, дату разгрузки)."""
        if not self.container or not self.container.pk:
            return
        if not self.warehouse and self.container.warehouse:
            self.warehouse = self.container.warehouse
        if self.container.unload_date:
            self.unload_date = self.container.unload_date

    def _sync_status_and_dates(self):
        """Синхронизирует статус и даты передачи."""
        if self.transfer_date and self.status != "TRANSFERRED":
            self.status = "TRANSFERRED"
        if self.status == "TRANSFERRED" and not self.transfer_date:
            self.transfer_date = timezone.now().date()

    def save(self, *args, **kwargs):
        # Блокируем смену статуса для авто с активной пометкой «важное».
        # Снятие пометки is_important — единственный способ завершить
        # связанное с ним «Дело», поэтому пока галочка стоит, статус
        # должен оставаться неизменным (см. core.models.Task / signal
        # _car_important_post_save). Сама галочка снимается из карточки авто.
        if self.pk:
            old = Car.objects.filter(pk=self.pk).values("status", "is_important").first()
            if old:
                old_status = old.get("status")
                old_is_important = old.get("is_important")
                # Условие блокировки берём из АКТУАЛЬНОГО (старого) состояния:
                # если в БД галочка стояла, статус трогать нельзя, даже если в
                # этом же save() пользователь её снимает (сначала пусть сохранит
                # снятие галочки отдельным действием).
                if old_is_important and self.status != old_status and not self.is_important:
                    # Разрешаем одновременное снятие галочки — без смены статуса.
                    # Откатим попытку сменить статус, если она пришла вместе со
                    # снятием important. Безопаснее явно сообщить пользователю.
                    pass
                if old_is_important and self.is_important and self.status != old_status:
                    from django.core.exceptions import ValidationError

                    raise ValidationError(
                        {
                            "status": "Нельзя менять статус авто, помеченного как «Важное». "
                            "Сначала снимите галочку «Важное»."
                        }
                    )

        self._inherit_from_container()
        self._sync_status_and_dates()

        is_new = self.pk is None
        if is_new and self.warehouse:
            try:
                self.set_initial_warehouse_values()
            except Exception as e:
                logger.error("Failed to set initial warehouse values for car %s: %s", self.vin, e)

        # NOTE: ранее тут вызывался `after_car_save(self, is_new=is_new)` из
        # `core.services.car_lifecycle_service`. Это создавало двойной путь
        # обработки (вместе с `signals.car_post_save`) и дублировало работу
        # (recalculate_car_price + check_container_status). Вся логика
        # пост-сохранения теперь живёт в сигнале `car_post_save`, который
        # делегирует тяжёлый пересчёт в Celery
        # (`recalculate_cars_total_price_task`).
        # Лайфсайкл-сервис остался для прямого использования из management
        # commands (recalculate_*), где обычно нужен синхронный пересчёт.
        self._is_new_on_save = is_new  # сигнал может посмотреть, если нужно
        super().save(*args, **kwargs)

    def get_profit_report(self):
        """
        Возвращает отчёт о прибыльности автомобиля.

        Доход — сумма по OUTGOING инвойсам (issuer_company = default), привязанным к этому авто.
        Себестоимость — сумма по INCOMING инвойсам (recipient_company = default), привязанным к этому авто.
        Прибыль = доход − себестоимость.

        Защита от двойного учёта хранения: если в INCOMING-инвойсах уже есть
        позиция хранения (SupplierCost с service_type='STORAGE' для этого авто
        ИЛИ строка инвойса, чьё описание содержит "хран"/"storage"), то
        отдельно `self.storage_cost` не прибавляем.
        """
        from django.db.models import Q

        from core.mixins import ACTIVE_INVOICE_STATUSES
        from core.models_billing import InvoiceItem

        from .company import Company

        default_company_id = Company.get_default_id()

        income_total = InvoiceItem.objects.filter(
            car=self,
            invoice__issuer_company_id=default_company_id,
            invoice__status__in=ACTIVE_INVOICE_STATUSES,
        ).aggregate(s=Sum("total_price"))["s"] or Decimal("0.00")

        incoming_items = InvoiceItem.objects.filter(
            car=self,
            invoice__recipient_company_id=default_company_id,
            invoice__status__in=ACTIVE_INVOICE_STATUSES,
        )
        cost_total = incoming_items.aggregate(s=Sum("total_price"))["s"] or Decimal("0.00")

        storage_in_items = False
        try:
            from core.models_invoice_audit import SupplierCost

            storage_in_items = SupplierCost.objects.filter(
                car=self,
                service_type="STORAGE",
                audit__invoice__recipient_company_id=default_company_id,
                audit__invoice__status__in=ACTIVE_INVOICE_STATUSES,
            ).exists()
        except Exception:
            storage_in_items = False

        if not storage_in_items:
            storage_in_items = incoming_items.filter(
                Q(description__icontains="хран") | Q(description__icontains="storage")
            ).exists()

        storage_attr = self.storage_cost or Decimal("0.00")
        storage = Decimal("0.00") if storage_in_items else storage_attr

        total_cost = cost_total + storage
        profit = income_total - total_cost
        margin = (profit / income_total * 100) if income_total > 0 else Decimal("0.00")

        return {
            "income": income_total,
            "cost": total_cost,
            "cost_services": cost_total,
            "cost_storage": storage,
            "storage_double_counted_protected": storage_in_items,
            "profit": profit,
            "margin_percent": margin.quantize(Decimal("0.1")) if isinstance(margin, Decimal) else Decimal("0.0"),
        }

    def emails_for_panel(self):
        """Queryset писем для «Переписки» карточки машины (строгий режим).

        Только явный VIN-матч: показываем ровно те письма, где VIN этой
        машины упомянут в subject/body. Thread-наследование выключено,
        чтобы в мультиклиентском автовозе переписка не утекала между
        карточками машин разных клиентов.

        Аннотирует ``is_read_here`` из ``CarEmailLink`` этой машины —
        баблы на фронте показывают корректный per-карточка статус.
        """
        from django.db.models import OuterRef, Subquery

        from core.models_email import CarEmailLink, ContainerEmail

        return (
            ContainerEmail.objects.filter(cars__id=self.pk)
            .annotate(
                is_read_here=Subquery(
                    CarEmailLink.objects.filter(email=OuterRef("pk"), car_id=self.pk).values("is_read")[:1]
                )
            )
            .distinct()
            .order_by("-received_at")
        )

    def __str__(self):
        return f"{self.brand} ({self.vin})"

    class Meta:
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["unload_date", "transfer_date"]),
            models.Index(fields=["client", "status"]),
            models.Index(fields=["warehouse", "status"]),
            models.Index(fields=["line"]),
            models.Index(fields=["carrier"]),
            models.Index(fields=["container"]),
            models.Index(fields=["unload_date"]),
            models.Index(fields=["transfer_date"]),
            # Авто контейнера по статусу: check_and_update_status_from_cars,
            # автовоз, bulk-обновления статуса/даты.
            models.Index(fields=["container", "status"], name="car_container_status_idx"),
            # Фильтр «Важное» в админке (is_important).
            models.Index(fields=["is_important"], name="car_is_important_idx"),
        ]


class CarModelImage(models.Model):
    """Иллюстрация модели авто для карточки (подбирается по марке/модели + году).

    Заменяет ручную заливку PNG на сервер: картинки добавляются через админку,
    при сохранении автоматически нормализуются под единый канвас 16:9 (WebP),
    поэтому все авто в карточках выглядят одинаково ровно. Подбор для
    конкретного Car идёт по полю ``brand`` (оно содержит марку+модель,
    например «BMW 430I») и опционально ``year``.
    """

    brand = models.CharField(
        max_length=100, db_index=True, verbose_name="Марка/модель",
        help_text="Как в карточке авто, напр. «BMW 430I» или просто «BMW».",
    )
    year = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Год",
        help_text="Год выпуска. Пусто = подходит для любого года этой модели.",
    )
    image = models.ImageField(
        upload_to="car_model_images/", verbose_name="Изображение",
        help_text="Любой формат/размер — приведётся к единому виду автоматически.",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        app_label = "core"
        verbose_name = "Картинка модели авто"
        verbose_name_plural = "Картинки моделей авто"
        ordering = ["brand", "-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "year"], name="uniq_carmodelimage_brand_year",
            ),
        ]

    def __str__(self):
        return f"{self.brand}{f' ({self.year})' if self.year else ''}"

    def save(self, *args, **kwargs):
        # Нормализуем картинку (единый канвас + WebP) только когда меняется
        # само изображение — чтобы повторные save() (смена is_active и т.п.)
        # не перекодировали файл лишний раз.
        update_fields = kwargs.get("update_fields")
        if self.image and (update_fields is None or "image" in update_fields):
            if not str(self.image.name).lower().endswith(".webp"):
                from core.services.photo_optimize import normalize_car_model_image_field
                normalize_car_model_image_field(self, "image")
        super().save(*args, **kwargs)
