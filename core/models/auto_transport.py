"""Автовоз — формирование рейса (загрузка + клиенты)."""

from django.db import models
from django.utils import timezone

from .carriers import Carrier, CarrierDriver, CarrierTruck
from .clients import Client


class AutoTransport(models.Model):
    """Автовоз на загрузку - формирование рейса"""

    STATUS_CHOICES = [
        ("DRAFT", "Черновик"),
        ("FORMED", "Сформирован"),
        ("LOADED", "Загружен"),
        ("IN_TRANSIT", "В пути"),
        ("DELIVERED", "Доставлен"),
        ("CANCELLED", "Отменен"),
    ]

    # Основная информация
    number = models.CharField(
        max_length=50, unique=True, verbose_name="Номер автовоза", help_text="Уникальный номер рейса"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT", verbose_name="Статус")

    # Перевозчик и его данные
    carrier = models.ForeignKey(
        Carrier, on_delete=models.PROTECT, related_name="auto_transports", verbose_name="Перевозчик"
    )
    eori_code = models.CharField(
        max_length=50, blank=True, verbose_name="EORI код", help_text="Автоматически подтягивается из перевозчика"
    )

    # Автовоз (тягач + прицеп)
    truck = models.ForeignKey(
        CarrierTruck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transports",
        verbose_name="Автовоз",
    )
    truck_number_manual = models.CharField(
        max_length=20, blank=True, verbose_name="Номер тягача (вручную)", help_text="Если автовоза нет в списке"
    )
    trailer_number_manual = models.CharField(
        max_length=20, blank=True, verbose_name="Номер прицепа (вручную)", help_text="Если автовоза нет в списке"
    )

    # Водитель
    driver = models.ForeignKey(
        CarrierDriver,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transports",
        verbose_name="Водитель",
    )
    driver_name_manual = models.CharField(
        max_length=100, blank=True, verbose_name="ФИО водителя (вручную)", help_text="Если водителя нет в списке"
    )
    driver_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Телефон водителя",
        help_text="Автоматически подтягивается из водителя или вводится вручную",
    )

    # Граница пересечения
    border_crossing = models.CharField(
        max_length=100, blank=True, verbose_name="Граница пересечения", help_text="Название пункта пересечения границы"
    )

    # Автомобили в автовозе
    cars = models.ManyToManyField("Car", related_name="auto_transports", blank=True, verbose_name="Автомобили")

    # Даты
    loading_date = models.DateField(null=True, blank=True, verbose_name="Дата загрузки")
    departure_date = models.DateField(null=True, blank=True, verbose_name="Дата отправления")
    estimated_delivery_date = models.DateField(null=True, blank=True, verbose_name="Планируемая дата доставки")
    actual_delivery_date = models.DateField(null=True, blank=True, verbose_name="Фактическая дата доставки")

    # Дополнительная информация
    notes = models.TextField(blank=True, verbose_name="Примечания")

    # Технические поля
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    created_by = models.CharField(max_length=100, blank=True, verbose_name="Создал")

    class Meta:
        verbose_name = "Автовоз на загрузку"
        verbose_name_plural = "Автовозы на загрузку"
        ordering = ["-created_at"]
        indexes = [
            # Фильтрация по статусу — частая операция в сигналах и
            # автогенерации инвойсов (см. _queue_or_run_generate_invoices).
            models.Index(fields=["status"], name="autotransport_status_idx"),
            models.Index(fields=["carrier", "status"], name="autotransport_carr_st_idx"),
        ]

    def __str__(self):
        return f"Автовоз {self.number} - {self.carrier.name}"

    # LOADED → DELIVERED разрешён намеренно: админы часто отмечают
    # «доставлен» пакетным действием, минуя промежуточный IN_TRANSIT
    # (его в реальном процессе не всегда фиксируют в системе).
    ALLOWED_TRANSITIONS = {
        "DRAFT": {"FORMED", "CANCELLED"},
        "FORMED": {"LOADED", "DRAFT", "CANCELLED"},
        "LOADED": {"IN_TRANSIT", "DELIVERED", "FORMED", "CANCELLED"},
        "IN_TRANSIT": {"DELIVERED", "LOADED", "CANCELLED"},
        "DELIVERED": set(),
        "CANCELLED": {"DRAFT"},
    }

    def save(self, *args, **kwargs):
        if not self.eori_code and self.carrier:
            self.eori_code = self.carrier.eori_code or ""

        if not self.number:
            from django.db import transaction as db_transaction

            with db_transaction.atomic():
                self.number = self._generate_number()

        if self.driver and not self.driver_phone:
            self.driver_phone = self.driver.phone

        if self.pk:
            old_status = AutoTransport.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old_status and old_status != self.status:
                allowed = self.ALLOWED_TRANSITIONS.get(old_status, set())
                if self.status not in allowed:
                    from django.core.exceptions import ValidationError

                    raise ValidationError(
                        f"Недопустимый переход статуса автовоза: {old_status} → {self.status}. "
                        f"Допустимые: {', '.join(sorted(allowed)) or 'нет'}"
                    )

        super().save(*args, **kwargs)

    @staticmethod
    def _generate_number():
        """Generate unique number using select_for_update to prevent duplicates."""
        date_str = timezone.now().strftime("%Y%m%d")
        prefix = f"AT-{date_str}"
        last = AutoTransport.objects.filter(number__startswith=prefix).select_for_update().order_by("-number").first()
        if last:
            try:
                last_num = int(last.number.split("-")[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        return f"{prefix}-{next_num:03d}"

    @property
    def truck_full_number(self):
        """Полный номер автовоза"""
        if self.truck:
            return self.truck.full_number
        elif self.truck_number_manual:
            if self.trailer_number_manual:
                return f"{self.truck_number_manual} / {self.trailer_number_manual}"
            return self.truck_number_manual
        return "Не указан"

    @property
    def driver_full_name(self):
        """Полное имя водителя"""
        if self.driver:
            return self.driver.full_name
        return self.driver_name_manual or "Не указан"

    @property
    def cars_count(self):
        """Количество автомобилей в автовозе"""
        return self.cars.count()

    def get_clients(self):
        """Получить список уникальных клиентов автомобилей в автовозе"""
        return Client.objects.filter(car__in=self.cars.all()).distinct()

    def emails_for_panel(self):
        """Агрегирующая вьюха: все письма, привязанные к машинам рейса.

        Отдельной таблицы для AutoTransport нет — переиспользуем
        ``CarEmailLink`` через ``cars``. ``is_read_here`` = True только
        если ВСЕ ссылки письма на машины этого рейса прочитаны; иначе
        False (есть непрочитанные), чтобы бейдж «непрочитанное» показывался
        до тех пор, пока хотя бы одна машина имеет непрочитанный link.
        """
        from django.db.models import Exists, OuterRef

        from core.models_email import CarEmailLink, ContainerEmail

        car_ids = list(self.cars.values_list("id", flat=True))
        if not car_ids:
            return ContainerEmail.objects.none()
        has_unread = Exists(
            CarEmailLink.objects.filter(
                email=OuterRef("pk"),
                car_id__in=car_ids,
                is_read=False,
            )
        )
        return (
            ContainerEmail.objects.filter(cars__id__in=car_ids)
            .annotate(is_read_here=~has_unread)
            .distinct()
            .order_by("-received_at")
        )

    def generate_invoices(self):
        """Создать/обновить инвойсы для клиентов.

        Skips invoices that are already PAID or CANCELLED to avoid corrupting
        finalized financial records.
        """
        from django.utils import timezone

        from core.models_billing import NewInvoice

        from .company import Company

        clients = self.get_clients()
        created_invoices = []

        for client in clients:
            client_cars = self.cars.filter(client=client)

            if not client_cars.exists():
                continue

            existing_invoice = (
                NewInvoice.objects.filter(auto_transport=self, recipient_client=client)
                .exclude(status__in=["PAID", "CANCELLED"])
                .first()
            )

            if existing_invoice:
                existing_invoice.cars.set(client_cars)
                existing_invoice.regenerate_items_from_cars()
                created_invoices.append(existing_invoice)
            else:
                has_finalized = NewInvoice.objects.filter(
                    auto_transport=self, recipient_client=client, status__in=["PAID", "CANCELLED"]
                ).exists()
                if has_finalized:
                    continue

                company = Company.get_default()

                if company:
                    invoice = NewInvoice.objects.create(
                        issuer_company=company,
                        recipient_client=client,
                        auto_transport=self,
                        document_type="PROFORMA_BLC",
                        status="DRAFT",
                        date=timezone.now().date(),
                    )
                    invoice.cars.set(client_cars)
                    invoice.regenerate_items_from_cars()
                    created_invoices.append(invoice)

        return created_invoices
