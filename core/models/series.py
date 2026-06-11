"""Счётчик серий документов (B1, AUDIT_ROUND3).

Раньше ``generate_number`` у NewInvoice/Transaction/AutoTransport брал
последнюю строку серии через ``SELECT FOR UPDATE`` — это не лочит ничего
при пустой серии и допускает коллизии при параллельной вставке
(IntegrityError → 500). Таблица счётчиков сериализует выдачу номеров
одним атомарным upsert-стейтментом (``INSERT … ON CONFLICT … DO UPDATE …
RETURNING``), который поддерживают и PostgreSQL, и SQLite (тесты).
"""

from django.db import connection, models


class SeriesCounter(models.Model):
    """Последний выданный номер для серии документов (``prefix``)."""

    prefix = models.CharField(max_length=64, unique=True, verbose_name="Префикс серии")
    last_value = models.BigIntegerField(default=0, verbose_name="Последний номер")

    class Meta:
        verbose_name = "Счётчик серии"
        verbose_name_plural = "Счётчики серий"

    def __str__(self):
        return f"{self.prefix}: {self.last_value}"

    @classmethod
    def next_value(cls, prefix: str, seed: int = 0) -> int:
        """Атомарно выдать следующий номер серии.

        Если счётчика ещё нет — он создаётся со стартовым значением
        ``seed + 1`` (``seed`` = максимальный уже существующий номер,
        вычисленный вызывающим кодом). Один SQL-стейтмент, полная
        сериализация на уровне БД, без гонок и без SELECT FOR UPDATE.
        """
        table = cls._meta.db_table
        with connection.cursor() as cursor:
            # Имя таблицы берётся из _meta (не из пользовательского ввода),
            # значения передаются bind-параметрами — SQL-инъекция исключена.
            cursor.execute(
                f"INSERT INTO {table} (prefix, last_value) VALUES (%s, %s + 1) "  # nosec B608
                f"ON CONFLICT (prefix) DO UPDATE SET last_value = {table}.last_value + 1 "
                "RETURNING last_value",
                [prefix, seed],
            )
            return cursor.fetchone()[0]


def next_document_number(model, prefix: str, pad: int) -> str:
    """Вернуть следующий номер документа вида ``{prefix}-NNN…N``.

    При первом обращении к серии счётчик «засевается» максимальным уже
    существующим номером модели (важно для прода и восстановленных
    дампов). Гонка двух одновременных первых обращений безопасна:
    upsert в ``next_value`` гарантирует разные значения.
    """
    seed = 0
    if not SeriesCounter.objects.filter(prefix=prefix).exists():
        last = (
            model.objects
            .filter(number__startswith=f'{prefix}-')
            .order_by('-number')
            .values_list('number', flat=True)
            .first()
        )
        if last:
            try:
                seed = int(last.rsplit('-', 1)[1])
            except (ValueError, IndexError):
                seed = 0
    value = SeriesCounter.next_value(prefix, seed)
    return f"{prefix}-{value:0{pad}d}"
