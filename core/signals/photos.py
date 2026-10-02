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

from core.models_website import CarPhoto, ContainerPhoto

logger = logging.getLogger(__name__)

# «Фото готовы» (C5): задержка перед отправкой — фото обычно приходят пачкой
# (GDrive-синк, массовая загрузка), поэтому ждём и шлём одно письмо на всё.
PHOTOS_READY_DELAY_SEC = 600
_PHOTOS_READY_PENDING_KEY = "photos_ready_pending:{kind}:{pk}"


def _schedule_photos_ready(kind, obj_id):
    """Ставит задачу «фото готовы» один раз на пачку загрузок.

    ``cache.add`` атомарен: первое фото ставит задачу с countdown, остальные в
    пределах окна ничего не делают. Сама задача дедупится через NotificationLog,
    так что повторный запуск (или потеря кэша) лишнего письма не даст.
    """
    key = _PHOTOS_READY_PENDING_KEY.format(kind=kind, pk=obj_id)

    def _do():
        if not cache.add(key, "1", PHOTOS_READY_DELAY_SEC + 60):
            return
        try:
            from core.tasks import notify_photos_ready_task

            notify_photos_ready_task.apply_async((kind, obj_id), countdown=PHOTOS_READY_DELAY_SEC)
        except Exception as exc:  # брокер недоступен — не роняем сохранение фото
            cache.delete(key)
            logger.warning("Не удалось поставить задачу «фото готовы» для %s %s: %s", kind, obj_id, exc)

    transaction.on_commit(_do)


def _invalidate_container_gallery_cache(container_id):
    if not container_id:
        return

    def _do():
        try:
            from core.models import Container

            number = Container.objects.filter(pk=container_id).values_list("number", flat=True).first()
            if number:
                cache.delete(f"container_photos:{number}")
        except Exception as e:  # инвалидация кэша не должна ронять основной flow
            logger.debug("Failed to invalidate container gallery cache for %s: %s", container_id, e)

    transaction.on_commit(_do)


@receiver(post_save, sender=ContainerPhoto)
def invalidate_gallery_on_photo_save(sender, instance, **kwargs):
    _invalidate_container_gallery_cache(instance.container_id)


@receiver(post_save, sender=ContainerPhoto, dispatch_uid="client_notify_container_photos_ready")
def schedule_container_photos_ready(sender, instance, **kwargs):
    """C5: публичное фото контейнера → отложенное «фото готовы» клиентам."""
    if instance.is_public and instance.container_id:
        _schedule_photos_ready("container", instance.container_id)


@receiver(post_save, sender=CarPhoto, dispatch_uid="client_notify_car_photos_ready")
def schedule_car_photos_ready(sender, instance, **kwargs):
    """C5: публичное фото авто → отложенное «фото готовы» клиенту авто."""
    if instance.is_public and instance.car_id:
        _schedule_photos_ready("car", instance.car_id)


@receiver(post_delete, sender=ContainerPhoto)
def invalidate_gallery_on_photo_delete(sender, instance, **kwargs):
    _invalidate_container_gallery_cache(instance.container_id)
