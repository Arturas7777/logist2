"""Инвалидация кэшей статистики/отчётов при изменении ключевых моделей.

Кэш ``company_stats``, ``client_stats``, ``warehouse_stats`` и
``payment_objects:*`` живёт 30 минут — без явной инвалидации после
изменения связанных данных пользователь видит устаревшие цифры.

Раньше :func:`invalidate_related_cache` был написан, но нигде не
вызывался — это «починен пункт #12 плана».

Подключение через :func:`connect_cache_invalidation_signals` — нужна
``apps.get_model``, поэтому вызывается из ``core/signals/__init__.py``
после загрузки всех submodules.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)


_CACHE_INVALIDATION_MODELS = {
    "Client",
    "Warehouse",
    "Company",
    "Line",
    "Carrier",
    "NewInvoice",
    "Transaction",
    "Car",
    "Container",
}


def _invalidate_stats_cache(sender, instance, **kwargs):
    model_name = sender.__name__
    if model_name not in _CACHE_INVALIDATION_MODELS:
        return
    try:
        from core.cache_utils import invalidate_related_cache

        # Откладываем до commit, чтобы инвалидация происходила после записи в БД.
        instance_id = getattr(instance, "pk", None)
        transaction.on_commit(lambda: invalidate_related_cache(model_name, instance_id))
    except Exception as exc:
        logger.debug("Cache invalidation skipped for %s: %s", model_name, exc)


def connect_cache_invalidation_signals():
    from django.apps import apps as _apps

    for model_name in _CACHE_INVALIDATION_MODELS:
        try:
            model = _apps.get_model("core", model_name)
        except LookupError:
            continue
        post_save.connect(
            _invalidate_stats_cache,
            sender=model,
            dispatch_uid=f"cache_invalidate_save_{model_name}",
            weak=False,
        )
        post_delete.connect(
            _invalidate_stats_cache,
            sender=model,
            dispatch_uid=f"cache_invalidate_delete_{model_name}",
            weak=False,
        )
