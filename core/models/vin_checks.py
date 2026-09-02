"""Кэш результатов проверки VIN (контрольная цифра + NHTSA).

Зачем отдельная таблица, а не запрос в NHTSA по месту:

  * форма сохранения машины не должна ждать сеть — оператор вводит VIN
    и жмёт «Сохранить», а не наблюдает пятисекундный таймаут;
  * аудит контейнера из пяти машин иначе делал бы пять HTTP-запросов
    на каждую отрисовку карточки.

Ответ NHTSA по конкретному VIN не меняется, поэтому удачная проверка
кэшируется навсегда. Перепроверяем только записи, где ``nhtsa_ok=False``
— там мог быть недоступен API, а не неверный VIN.
"""

from django.db import models
from django.utils import timezone


class VinCheck(models.Model):
    """Снимок проверки одного VIN — общий для форм, сканов и аудита."""

    # Как долго живёт неудачная проверка, прежде чем её стоит повторить.
    STALE_AFTER_DAYS = 7

    vin = models.CharField(max_length=17, unique=True, db_index=True, verbose_name="VIN")

    length_ok = models.BooleanField(default=False, verbose_name="17 символов")
    checksum_ok = models.BooleanField(default=False, verbose_name="Контрольная цифра сходится")
    is_north_american = models.BooleanField(
        default=False,
        verbose_name="Североамериканский VIN",
        help_text="Начинается с 1-5 — для таких контрольная цифра обязана сходиться.",
    )

    nhtsa_ok = models.BooleanField(default=False, verbose_name="NHTSA распознал VIN")
    nhtsa_make = models.CharField(max_length=100, blank=True, default="", verbose_name="Марка (NHTSA)")
    nhtsa_model = models.CharField(max_length=100, blank=True, default="", verbose_name="Модель (NHTSA)")
    nhtsa_year = models.PositiveIntegerField(null=True, blank=True, verbose_name="Год (NHTSA)")
    nhtsa_vehicle_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Тип ТС (NHTSA)",
        help_text="Сырое значение Vehicle Type из NHTSA, например MOTORCYCLE.",
    )
    error_text = models.CharField(max_length=255, blank=True, default="", verbose_name="Ответ NHTSA")

    checked_at = models.DateTimeField(default=timezone.now, verbose_name="Проверено")

    class Meta:
        verbose_name = "Проверка VIN"
        verbose_name_plural = "Проверки VIN"
        ordering = ("-checked_at",)

    def __str__(self):
        return f"{self.vin} — {'NHTSA ok' if self.nhtsa_ok else 'не подтверждён'}"

    @property
    def is_stale(self) -> bool:
        """Пора ли перепроверить: неудачные проверки протухают, удачные — нет."""
        if self.nhtsa_ok:
            return False
        return (timezone.now() - self.checked_at).days >= self.STALE_AFTER_DAYS

    @property
    def nhtsa_summary(self) -> str:
        """«TOYOTA CAMRY 2021» — то, что показываем оператору под полем VIN."""
        parts = [self.nhtsa_make, self.nhtsa_model, str(self.nhtsa_year) if self.nhtsa_year else ""]
        return " ".join(p for p in parts if p).strip()
