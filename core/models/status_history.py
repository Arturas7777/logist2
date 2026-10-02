"""История статусов авто (C2, IMPROVEMENT_PLAN_2026-10).

Лёгкий журнал: одна строка на каждую смену ``Car.status``. Заполняется
сигналом ``core.signals.client_notifications`` (сравнение старого и нового
статуса через ``pre_save``-кэш). Используется таймлайном карточки авто в
кабинете клиента: даты шагов берутся отсюда, если они есть.

Массовые ``QuerySet.update(status=...)`` (например каскад статуса контейнера
в ``container_lifecycle_service``) сигналы не вызывают — такие переходы в
журнал не попадают; таймлайн тогда падает обратно на ``unload_date`` /
``transfer_date``.
"""

from django.db import models


class CarStatusHistory(models.Model):
    SOURCE_ADMIN = "ADMIN"
    SOURCE_SIGNAL = "SIGNAL"
    SOURCE_IMPORT = "IMPORT"
    SOURCE_CHOICES = [
        (SOURCE_ADMIN, "Админка / пользователь"),
        (SOURCE_SIGNAL, "Автоматически (сигнал)"),
        (SOURCE_IMPORT, "Импорт / миграция данных"),
    ]

    car = models.ForeignKey(
        "Car",
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="Авто",
    )
    status = models.CharField(max_length=20, verbose_name="Статус")
    previous_status = models.CharField(max_length=20, blank=True, default="", verbose_name="Предыдущий статус")
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Когда")
    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
        default=SOURCE_SIGNAL,
        verbose_name="Источник",
    )
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="Примечание")

    class Meta:
        verbose_name = "История статуса авто"
        verbose_name_plural = "История статусов авто"
        ordering = ["-changed_at", "-id"]
        indexes = [
            models.Index(fields=["car", "changed_at"], name="car_status_hist_car_at_idx"),
        ]

    def __str__(self):
        return f"{self.car_id}: {self.previous_status or '—'} → {self.status} ({self.changed_at:%d.%m.%Y %H:%M})"
