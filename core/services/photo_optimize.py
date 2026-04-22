"""
Оптимизация загружаемых фотографий: ресайз до web-качества + перекодировка
в прогрессивный JPEG.

Мы вызываем `maybe_compress_image_field(instance, 'photo')` в `save()` моделей
`ContainerPhoto` и `CarPhoto` перед `super().save()`. Работает одинаково для:
  * InMemoryUploadedFile / TemporaryUploadedFile (админка, форма);
  * ContentFile с байтами (Google Drive sync, распаковка ZIP);
  * ImageFieldFile, уже привязанного к файлу на диске (редактирование существующей
    записи, миграционные прогонки).

Идемпотентно: если long-side исходника уже <= MAX_LONG_SIDE — ничего не делаем.
Безопасно: любое исключение логируем и оставляем исходный файл нетронутым.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_LONG_SIDE = 2560
JPEG_QUALITY = 85
_JPEG_EXTS = ('.jpg', '.jpeg')


def compress_image_bytes(
    data: bytes,
    *,
    max_long_side: int = MAX_LONG_SIDE,
    quality: int = JPEG_QUALITY,
) -> Optional[bytes]:
    """Пережимает байты изображения. Возвращает новые байты либо None, если
    трогать не нужно (уже маленькое / не JPEG / ошибка)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            fmt = (im.format or '').upper()
            if fmt not in ('JPEG', 'MPO'):
                return None
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            if max(w, h) <= max_long_side:
                return None
            if im.mode != 'RGB':
                im = im.convert('RGB')
            im.thumbnail((max_long_side, max_long_side), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
            out = buf.getvalue()
            if len(out) >= len(data):
                return None
            return out
    except (UnidentifiedImageError, OSError, ValueError) as e:
        logger.warning('photo_optimize: не удалось пережать (%s)', e)
        return None


def maybe_compress_image_field(
    instance,
    field_name: str = 'photo',
    *,
    max_long_side: int = MAX_LONG_SIDE,
    quality: int = JPEG_QUALITY,
) -> bool:
    """Если поле указывает на JPEG, чей long-side > max_long_side, переписывает
    его сжатым вариантом (in memory) под тем же именем. Возвращает True, если
    поле было изменено."""
    field = getattr(instance, field_name, None)
    if not field or not getattr(field, 'name', ''):
        return False

    name = field.name.lower()
    if not name.endswith(_JPEG_EXTS):
        return False

    try:
        field.open('rb')
        try:
            raw = field.read()
        finally:
            try:
                field.close()
            except Exception:
                pass
    except (OSError, ValueError) as e:
        logger.warning('photo_optimize: не удалось прочитать %s: %s',
                       field.name, e)
        return False

    new_bytes = compress_image_bytes(
        raw, max_long_side=max_long_side, quality=quality,
    )
    if not new_bytes:
        return False

    base_name = os.path.basename(field.name)
    field.save(base_name, ContentFile(new_bytes), save=False)

    logger.info(
        'photo_optimize: %s → %d KB (было %d KB)',
        base_name, len(new_bytes) // 1024, len(raw) // 1024,
    )
    return True
