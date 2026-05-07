"""
Утилиты для оптимизации производительности и бизнес-логики
"""


import logging
import threading

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def round_up_to_5(value):
    """Округляет Decimal в большую сторону с шагом 5 EUR.

    Пример: 73.12 -> 75, 70.00 -> 70, 0 -> 0
    Использует чистую Decimal-арифметику (без потери точности через float).
    """
    remainder = value % 5
    if remainder == 0:
        return value
    return value + (5 - remainder)

logger = logging.getLogger(__name__)


class WebSocketBatcher:
    """Per-thread батчинг WebSocket уведомлений.

    ВАЖНО: раньше класс держал общий `_batch = []` на всех воркерах. Под
    `gthread`-gunicorn (workers=4, threads=4) это давало гонку — один поток
    мог отправить flush с чужими событиями или потерять часть. Теперь
    буфер хранится в `threading.local()` и не разделяется между потоками.
    """

    _local = threading.local()
    _max_batch_size = 50

    @classmethod
    def _get_batch(cls):
        bucket = getattr(cls._local, 'batch', None)
        if bucket is None:
            bucket = []
            cls._local.batch = bucket
        return bucket

    @classmethod
    def add(cls, model_name, obj_id, data):
        """Добавить обновление в пакет (буфер привязан к текущему потоку)."""
        batch = cls._get_batch()
        batch.append({
            'model': model_name,
            'id': obj_id,
            **data
        })

        if len(batch) >= cls._max_batch_size:
            cls.flush()

    @classmethod
    def flush(cls):
        """Отправить накопленные обновления текущего потока."""
        batch = cls._get_batch()
        if not batch:
            return

        # Снимаем копию и сразу очищаем локальный буфер, чтобы повторные
        # вызовы flush() (например, из разных on_commit-хуков) не дублировали
        # отправку.
        payload = batch[:]
        batch.clear()

        try:
            channel_layer = get_channel_layer()
            if channel_layer is None:
                logger.debug("No channel layer configured, dropping %d WS updates", len(payload))
                return
            async_to_sync(channel_layer.group_send)(
                "updates",
                {
                    "type": "data_update_batch",
                    "data": payload,
                }
            )
            logger.debug("Sent batch of %d WebSocket updates", len(payload))
        except Exception as e:
            logger.error("Failed to send WebSocket batch: %s", e)

    @classmethod
    def send_on_commit(cls, model_name, obj_id, data):
        """Добавить в пакет и отправить при коммите транзакции.

        on_commit вызовется в том же потоке, что и текущий запрос, поэтому
        thread-local буфер останется консистентным.
        """
        cls.add(model_name, obj_id, data)
        transaction.on_commit(cls.flush)


def batch_update_queryset(queryset, update_func, batch_size=100):
    """
    Массовое обновление queryset с функцией обновления

    Args:
        queryset: QuerySet для обновления
        update_func: Функция обновления (принимает объект, возвращает измененный объект)
        batch_size: Размер пакета для bulk_update

    Returns:
        int: Количество обновленных объектов
    """
    objects_to_update = []
    updated_count = 0

    for obj in queryset:
        updated_obj = update_func(obj)
        if updated_obj:
            objects_to_update.append(updated_obj)

        if len(objects_to_update) >= batch_size:
            # Массовое обновление
            queryset.model.objects.bulk_update(
                objects_to_update,
                [f.name for f in queryset.model._meta.fields if not f.primary_key],
                batch_size=batch_size
            )
            updated_count += len(objects_to_update)
            objects_to_update.clear()

    # Обновить остаток
    if objects_to_update:
        queryset.model.objects.bulk_update(
            objects_to_update,
            [f.name for f in queryset.model._meta.fields if not f.primary_key],
            batch_size=batch_size
        )
        updated_count += len(objects_to_update)

    return updated_count


def optimize_queryset_for_list(queryset, select_fields=None, prefetch_fields=None):
    """
    Оптимизирует queryset для отображения списка

    Args:
        queryset: QuerySet для оптимизации
        select_fields: Поля для select_related
        prefetch_fields: Поля для prefetch_related

    Returns:
        Оптимизированный QuerySet
    """
    if select_fields:
        queryset = queryset.select_related(*select_fields)

    if prefetch_fields:
        queryset = queryset.prefetch_related(*prefetch_fields)

    return queryset


def log_slow_queries(threshold_ms=100):
    """
    Декоратор для логирования медленных запросов

    Args:
        threshold_ms: Порог в миллисекундах
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            from time import time

            from django.db import connection, reset_queries

            reset_queries()
            start = time()

            result = func(*args, **kwargs)

            elapsed = (time() - start) * 1000
            queries_count = len(connection.queries)

            if elapsed > threshold_ms:
                logger.warning(
                    f"Slow function {func.__name__}: {elapsed:.2f}ms, "
                    f"{queries_count} queries"
                )
                # Логируем самые медленные запросы
                for query in connection.queries:
                    query_time = float(query['time']) * 1000
                    if query_time > threshold_ms / 2:
                        logger.warning(f"  Slow query ({query_time:.2f}ms): {query['sql'][:200]}")

            return result
        return wrapper
    return decorator
