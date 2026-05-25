"""«Дела» (Task) — to-do по работе с авто/контейнерами."""

from django.db import models
from django.utils import timezone


class Task(models.Model):
    """Задача / напоминание сотрудника.

    Дела бывают двух типов:

      * **Авто-созданные** (``auto_created=True``) — порождаются установкой
        галочки «Важное» в карточке авто. Связаны с конкретной машиной через
        FK ``car``. Снятие галочки ``Car.is_important`` автоматически
        закрывает соответствующее дело (помечает ``is_completed=True``).
      * **Ручные** (``auto_created=False``) — заводятся пользователем
        вручную через раздел «Дела». Могут быть привязаны к авто/контейнеру
        или быть отдельными напоминаниями. Закрываются ТОЛЬКО вручную.

    Поле ``deadline`` опциональное — без него дело просто висит в списке
    как напоминание без срока.
    """

    PRIORITY_CHOICES = [
        ("LOW", "Низкий"),
        ("MEDIUM", "Средний"),
        ("HIGH", "Высокий"),
    ]

    title = models.CharField(max_length=200, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    deadline = models.DateTimeField(
        null=True, blank=True, verbose_name="Дедлайн", help_text="Опционально. Без дедлайна — обычное напоминание."
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="MEDIUM", verbose_name="Приоритет")

    # Связи. Можно привязать дело к конкретному авто и/или контейнеру.
    # Оба поля опциональные — для «общих» дел без привязки.
    car = models.ForeignKey(
        "Car", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks", verbose_name="Авто"
    )
    container = models.ForeignKey(
        "Container", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks", verbose_name="Контейнер"
    )

    # Состояние выполнения. Закрытие — ТОЛЬКО ручное (через админку или
    # снятие галочки is_important у привязанного авто для auto_created).
    is_completed = models.BooleanField(default=False, verbose_name="Выполнено")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Выполнено в")
    completed_by = models.CharField(max_length=100, blank=True, verbose_name="Выполнил")

    # Метаданные источника.
    auto_created = models.BooleanField(
        default=False,
        verbose_name="Создано автоматически",
        help_text="True — дело создано из чекбокса «Важное» в карточке авто.",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    created_by = models.CharField(max_length=100, blank=True, verbose_name="Создал")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Дело"
        verbose_name_plural = "Дела"
        ordering = ["is_completed", "deadline", "-created_at"]
        indexes = [
            models.Index(fields=["is_completed"]),
            models.Index(fields=["deadline"]),
            models.Index(fields=["car"]),
            models.Index(fields=["container"]),
        ]

    def __str__(self):
        suffix = " ✓" if self.is_completed else ""
        return f"{self.title}{suffix}"

    @property
    def is_overdue(self) -> bool:
        """Просрочено: есть дедлайн в прошлом и дело не закрыто."""
        if self.is_completed or not self.deadline:
            return False
        return self.deadline < timezone.now()

    def mark_completed(self, by: str = "") -> None:
        """Ручное закрытие дела."""
        if self.is_completed:
            return
        self.is_completed = True
        self.completed_at = timezone.now()
        if by:
            self.completed_by = by
        self.save(update_fields=["is_completed", "completed_at", "completed_by", "updated_at"])

    def reopen(self) -> None:
        """Откатить ручное закрытие (на случай ошибочного нажатия)."""
        self.is_completed = False
        self.completed_at = None
        self.completed_by = ""
        self.save(update_fields=["is_completed", "completed_at", "completed_by", "updated_at"])
