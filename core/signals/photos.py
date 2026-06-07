"""Инвалидация кэша публичной галереи фото контейнера.

Эндпоинт ``GET /api/container-photos/<num>/`` (см.
:mod:`core.views_website.signed_photos`) кэширует метаданные фото на
15 минут под ключом ``container_photos:<number>``. Раньше кэш никем не
инвалидировался — новые фото (загрузка в админке, синхронизация с Google
Drive) появлялись в галерее только после истечения TTL.

Здесь мы сбрасываем этот ключ при любом изменении ``ContainerPhoto``
(create/update/delete), на ``transaction.on_commit`` — чтобы читатель
кэша увидел уже закоммиченные данные.
"""

import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models_website import ContainerPhoto

logger = logging.getLogger(__name__)


def _invalidate_container_gallery_cache(container_id):
    if not container_id:
        return

    def _do():
        try:
            from core.models import Container

            number = (
                Container.objects.filter(pk=container_id)
                .values_list("number", flat=True)
                .first()
            )
            if number:
                cache.delete(f"container_photos:{number}")
        except Exception as e:  # инвалидация кэша не должна ронять основной flow
            logger.debug("Failed to invalidate container gallery cache for %s: %s", container_id, e)

    transaction.on_commit(_do)


@receiver(post_save, sender=ContainerPhoto)
def invalidate_gallery_on_photo_save(sender, instance, **kwargs):
    _invalidate_container_gallery_cache(instance.container_id)


@receiver(post_delete, sender=ContainerPhoto)
def invalidate_gallery_on_photo_delete(sender, instance, **kwargs):
    _invalidate_container_gallery_cache(instance.container_id)
