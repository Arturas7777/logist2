"""
Scan processing pipeline (Title / Dock Receipt) — модели очереди обработки.

Архитектура:

  upload (admin action)
       │
       ▼
  ScanProcessingJob (status=PENDING, original_file=<PDF>)
       │  ◀─ Celery: core.tasks.process_scan_job
       ▼
  AI извлечение (Claude Sonnet 4 Vision)
       │
       ▼
  status=NEEDS_REVIEW, extracted_data=<JSON>
       │  ◀─ admin "Применить"
       ▼
  status=APPLIED, linked_car/linked_container установлены,
                  файл скопирован в Car.title_scan / Container.dock_receipt_scan

При ошибке: status=ERROR, error_message заполнен.
"""

from django.conf import settings
from django.db import models


class ScanProcessingJob(models.Model):
    """Задача AI-обработки одного отсканированного PDF (титул или Dock Receipt)."""

    SCAN_TYPE_TITLE = "TITLE"
    SCAN_TYPE_DOCK_RECEIPT = "DOCK_RECEIPT"
    SCAN_TYPE_CHOICES = [
        (SCAN_TYPE_TITLE, "Title (титул авто)"),
        (SCAN_TYPE_DOCK_RECEIPT, "Dock Receipt"),
    ]

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
    STATUS_APPLIED = "APPLIED"
    STATUS_ERROR = "ERROR"
    STATUS_IGNORED = "IGNORED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает обработки"),
        (STATUS_PROCESSING, "Обрабатывается AI"),
        (STATUS_NEEDS_REVIEW, "Ожидает проверки"),
        (STATUS_APPLIED, "Применено"),
        (STATUS_ERROR, "Ошибка"),
        (STATUS_IGNORED, "Проигнорировано"),
    ]

    scan_type = models.CharField(max_length=20, choices=SCAN_TYPE_CHOICES, db_index=True, verbose_name="Тип скана")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, verbose_name="Статус"
    )

    original_file = models.FileField(
        upload_to="scan_jobs/%Y/%m/",
        verbose_name="Исходный PDF",
        help_text="PDF, загруженный пользователем (один документ = одна задача).",
    )

    # Что AI извлёк (raw):
    extracted_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Извлечённые данные (AI)",
        help_text="JSON, полученный от Claude Vision API.",
    )

    # Что в итоге было применено (для аудита):
    applied_changes = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Применённые изменения",
        help_text="Что именно изменилось в Car/Container после нажатия 'Применить'.",
    )

    # Привязанные сущности — заполняются после применения.
    linked_car = models.ForeignKey(
        "core.Car",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_jobs",
        verbose_name="Связанный автомобиль",
    )
    linked_container = models.ForeignKey(
        "core.Container",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_jobs",
        verbose_name="Связанный контейнер",
    )

    # Признак того, что задача создала новые сущности — чтобы было видно
    # в списке "вот тут AI создал контейнер с нуля".
    created_new_car = models.BooleanField(default=False, verbose_name="Создан новый Car")
    created_new_container = models.BooleanField(default=False, verbose_name="Создан новый Container")

    error_message = models.TextField(blank=True, verbose_name="Сообщение об ошибке")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создано")
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Обработано AI")
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name="Применено")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_jobs_created",
        verbose_name="Загрузил",
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_jobs_applied",
        verbose_name="Применил",
    )

    class Meta:
        verbose_name = "AI-обработка скана"
        verbose_name_plural = "AI-обработка сканов"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["scan_type", "status"]),
        ]

    def __str__(self):
        label = dict(self.SCAN_TYPE_CHOICES).get(self.scan_type, self.scan_type)
        return f"#{self.pk} {label} — {self.get_status_display()}"

    @property
    def is_terminal(self):
        return self.status in (self.STATUS_APPLIED, self.STATUS_IGNORED)

    @property
    def can_apply(self):
        return self.status == self.STATUS_NEEDS_REVIEW
