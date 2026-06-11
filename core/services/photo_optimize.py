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

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_LONG_SIDE = 2560
JPEG_QUALITY = 85
_JPEG_EXTS = (".jpg", ".jpeg")


def compress_image_bytes(
    data: bytes,
    *,
    max_long_side: int = MAX_LONG_SIDE,
    quality: int = JPEG_QUALITY,
) -> bytes | None:
    """Пережимает байты изображения. Возвращает новые байты либо None, если
    трогать не нужно (уже маленькое / не JPEG / ошибка)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            fmt = (im.format or "").upper()
            if fmt not in ("JPEG", "MPO"):
                return None
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            if max(w, h) <= max_long_side:
                return None
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((max_long_side, max_long_side), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            out = buf.getvalue()
            if len(out) >= len(data):
                return None
            return out
    except (UnidentifiedImageError, OSError, ValueError) as e:
        logger.warning("photo_optimize: не удалось пережать (%s)", e)
        return None


# --- Картинки моделей авто (CarModelImage) -------------------------------
# Единый стандарт для иллюстрации в карточке авто: все изображения
# приводятся к одному прозрачному канвасу 16:9 и кодируются в WebP, чтобы
# любая загруженная картинка (разной ширины/высоты/пропорций) выглядела
# одинаково ровно по центру.
CAR_MODEL_CANVAS = (800, 450)  # 16:9
CAR_MODEL_PADDING = 0.04  # небольшое поле ПОСЛЕ обрезки прозрачных краёв
WEBP_QUALITY = 88


def normalize_car_model_image_bytes(
    data: bytes,
    *,
    canvas: tuple[int, int] = CAR_MODEL_CANVAS,
    padding: float = CAR_MODEL_PADDING,
    quality: int = WEBP_QUALITY,
) -> bytes | None:
    """Вписывает изображение в прозрачный канвас фикс. размера и отдаёт WebP.

    Сохраняет пропорции исходника, центрирует, добавляет поля. Возвращает
    None при ошибке (тогда вызывающий оставляет файл как есть).
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGBA":
                im = im.convert("RGBA")

            # Обрезаем прозрачные поля вокруг машины по альфа-каналу — иначе
            # «воздух», заложенный в исходный PNG, делает авто мелким и
            # смещённым в канвасе. После обрезки авто заполняет канвас целиком.
            bbox = im.getchannel("A").getbbox()
            if bbox:
                im = im.crop(bbox)

            cw, ch = canvas
            max_w = int(cw * (1 - 2 * padding))
            max_h = int(ch * (1 - 2 * padding))
            # Масштабируем под рамку (contain), РАЗРЕШАЯ увеличение — thumbnail()
            # только уменьшает, из-за чего мелкие исходники оставались мелкими.
            bw, bh = im.size
            if bw and bh:
                scale = min(max_w / bw, max_h / bh)
                im = im.resize((max(1, round(bw * scale)), max(1, round(bh * scale))), Image.LANCZOS)

            board = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            x = (cw - im.width) // 2
            y = (ch - im.height) // 2
            board.paste(im, (x, y), im)

            buf = io.BytesIO()
            board.save(buf, "WEBP", quality=quality, method=6)
            return buf.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        logger.warning("normalize_car_model_image: ошибка обработки (%s)", e)
        return None


def normalize_car_model_image_field(instance, field_name: str = "image") -> bool:
    """Нормализует поле картинки модели авто под единый канвас (WebP).

    Возвращает True, если файл был переписан. Идемпотентность обеспечивается
    тем, что результат всегда .webp фикс. размера — повторная обработка даёт
    тот же канвас (визуально стабильно)."""
    field = getattr(instance, field_name, None)
    if not field or not getattr(field, "name", ""):
        return False

    try:
        field.open("rb")
        try:
            raw = field.read()
        finally:
            try:
                field.close()
            except Exception:
                pass
    except (OSError, ValueError) as e:
        logger.warning("normalize_car_model_image: не удалось прочитать %s: %s", field.name, e)
        return False

    new_bytes = normalize_car_model_image_bytes(raw)
    if not new_bytes:
        return False

    old_name = field.name
    base = os.path.splitext(os.path.basename(old_name))[0] + ".webp"
    storage = field.storage
    try:
        if storage.exists(old_name):
            storage.delete(old_name)
    except (OSError, ValueError) as e:
        logger.warning("normalize_car_model_image: не удалось удалить оригинал %s: %s", old_name, e)

    field.save(base, ContentFile(new_bytes), save=False)
    logger.info("normalize_car_model_image: %s → %d KB (canvas %dx%d)", base, len(new_bytes) // 1024, *CAR_MODEL_CANVAS)
    return True


def maybe_compress_image_field(
    instance,
    field_name: str = "photo",
    *,
    max_long_side: int = MAX_LONG_SIDE,
    quality: int = JPEG_QUALITY,
) -> bool:
    """Если поле указывает на JPEG, чей long-side > max_long_side, переписывает
    его сжатым вариантом (in memory) под тем же именем. Возвращает True, если
    поле было изменено."""
    field = getattr(instance, field_name, None)
    if not field or not getattr(field, "name", ""):
        return False

    name = field.name.lower()
    if not name.endswith(_JPEG_EXTS):
        return False

    try:
        field.open("rb")
        try:
            raw = field.read()
        finally:
            try:
                field.close()
            except Exception:
                pass
    except (OSError, ValueError) as e:
        logger.warning("photo_optimize: не удалось прочитать %s: %s", field.name, e)
        return False

    new_bytes = compress_image_bytes(
        raw,
        max_long_side=max_long_side,
        quality=quality,
    )
    if not new_bytes:
        return False

    # ВАЖНО: до записи сжатого варианта удаляем оригинал из storage,
    # иначе FieldFile.save() через get_available_name() добавит к имени
    # случайный суффикс (IMG_xxx_RANDOM.jpg) — и оригинал останется на
    # диске сиротой, удваивая занятое место. Этот баг копил orphan-файлы
    # для каждого фото, прошедшего через photo.save() (особенно для
    # google_drive_sync, где файл сначала пишется на диск целиком).
    old_name = field.name
    base_name = os.path.basename(old_name)
    storage = field.storage
    try:
        if storage.exists(old_name):
            storage.delete(old_name)
    except (OSError, ValueError) as e:
        logger.warning("photo_optimize: не удалось удалить оригинал %s: %s", old_name, e)

    field.save(base_name, ContentFile(new_bytes), save=False)

    logger.info(
        "photo_optimize: %s → %d KB (было %d KB)",
        base_name,
        len(new_bytes) // 1024,
        len(raw) // 1024,
    )
    return True
