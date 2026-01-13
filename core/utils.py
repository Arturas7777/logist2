"""
Утилиты для оптимизации производительности
"""

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction
import logging

logger = logging.getLogger('django')


class WebSocketBatcher:
    """
    Батчинг WebSocket уведомлений для уменьшения трафика
    Вместо отправки 100 отдельных сообщений - отправляет 1 пакет
    """
    _batch = []
    _max_batch_size = 50
    
    @classmethod
    def add(cls, model_name, obj_id, data):
        """Добавить обновление в пакет"""
        cls._batch.append({
            'model': model_name,
            'id': obj_id,
            **data
        })
        
        # Автоматически отправляем при достижении лимита
        if len(cls._batch) >= cls._max_batch_size:
            cls.flush()
    
    @classmethod
    def flush(cls):
        """Отправить накопленные обновления"""
        if not cls._batch:
            return
        
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "updates",
                {
                    "type": "data_update_batch",
                    "data": cls._batch.copy()
                }
            )
            logger.info(f"Sent batch of {len(cls._batch)} WebSocket updates")
            cls._batch.clear()
        except Exception as e:
            logger.error(f"Failed to send WebSocket batch: {e}")
            cls._batch.clear()
    
    @classmethod
    def send_on_commit(cls, model_name, obj_id, data):
        """Добавить в пакет и отправить при коммите транзакции"""
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
            from django.db import connection, reset_queries
            from time import time
            
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
