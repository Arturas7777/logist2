"""Публичные эндпоинты выдачи фото по подписанным URL'ам (H5a).

Поток:

1. ``GET  /api/container-photos/<num>/`` — отдаёт метаданные фото
   контейнера и **container_token** (подпись номера контейнера).
   Каждой фотографии присваиваются подписанные ссылки
   ``/photo/s/<token>/`` со сроком жизни 1 час.
2. ``POST /api/download-photos-archive/`` — собирает ZIP из выбранных
   фото. **Требует валидный container_token**, иначе отдаёт 400. Это
   защита от подбора ``photo_ids`` сторонним скриптом.
3. ``GET  /photo/s/<token>/`` — отдаёт сам файл по подписанному токену.
   Throttle снят, потому что в галерее одного контейнера живут сотни
   превью и глобальный ``AnonRateThrottle`` (30/min) ломал UX.

Все детали подписи — в :mod:`core.services.signed_urls`.
"""

import logging
import os
import zipfile
from io import BytesIO

from django.core.cache import cache as django_cache
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import Container
from core.models_website import CarPhoto, ContainerPhoto
from core.services.signed_urls import (
    BadSignature,
    SignatureExpired,
    make_container_token,
    make_photo_token,
    parse_container_token,
    parse_photo_token,
)
from core.throttles import PhotoDownloadThrottle

logger = logging.getLogger(__name__)


def _build_signed_photo_url(request, kind, photo_id, variant):
    """Абсолютный URL ``/photo/s/<token>/`` для отдачи фотографии.

    Подпись живёт ``PHOTO_URL_TTL`` (1 час). TTL/SALT — в
    :mod:`core.services.signed_urls`.
    """
    token = make_photo_token(kind, photo_id, variant)
    path = reverse("website:serve_signed_photo", kwargs={"token": token})
    return request.build_absolute_uri(path) if request else path


def _attach_signed_urls(request, photo_raw):
    """Дополняет «сырые» поля фото подписанными URL'ами.

    Возвращает новый словарь — исходный кэш не мутируется.
    """
    out = dict(photo_raw)
    photo_id = out["id"]
    out["url"] = _build_signed_photo_url(request, "container", photo_id, "full")
    out["thumbnail_url"] = (
        _build_signed_photo_url(request, "container", photo_id, "thumb")
        if out.pop("has_thumbnail", False)
        else out["url"]
    )
    return out


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PhotoDownloadThrottle])
def get_container_photos(request, container_number):
    """Список публичных фото контейнера + signed-токены для скачивания.

    Изменено в рамках H5a:

    * ``url`` / ``thumbnail_url`` больше не указывают на ``/media/``
      напрямую, а на ``/photo/s/<token>/``. Токен живёт 1 час (см.
      :mod:`core.services.signed_urls`).
    * Дополнительно возвращается ``container_token`` — подпись номера
      контейнера. Без неё :func:`download_photos_archive` отклонит запрос
      на ZIP (раньше можно было дёрнуть архив с любыми ``photo_ids``).

    Throttle: 30 req/min на IP. Кэш 15 мин снимает нагрузку с БД, но
    URL и ``container_token`` пересобираются под каждый запрос — они
    содержат timestamp и кэшировать их нельзя.
    """
    cache_key = f"container_photos:{container_number}"
    cached_payload = django_cache.get(cache_key)
    if cached_payload is not None:
        result = dict(cached_payload)
        result["photos"] = [_attach_signed_urls(request, p) for p in cached_payload["photos"]]
        result["container_token"] = make_container_token(cached_payload["container_number"])
        return Response(result)

    try:
        container = Container.objects.get(number=container_number)
        photos = ContainerPhoto.objects.filter(container=container, is_public=True)

        type_order = {"UNLOADING": 0, "GENERAL": 1, "IN_CONTAINER": 2}
        photos_list = list(photos)
        photos_list.sort(
            key=lambda p: (
                type_order.get(p.photo_type or "GENERAL", 1),
                p.photo.name if p.photo else "",
            )
        )

        photos_raw = []
        type_counts = {"IN_CONTAINER": 0, "UNLOADING": 0, "GENERAL": 0}

        for photo in photos_list:
            photo_type = photo.photo_type or "GENERAL"
            type_counts[photo_type] = type_counts.get(photo_type, 0) + 1

            photos_raw.append(
                {
                    "id": photo.id,
                    "has_thumbnail": bool(photo.thumbnail),
                    "description": photo.description,
                    "photo_type": photo.get_photo_type_display(),
                    "photo_type_code": photo_type,
                    "uploaded_at": photo.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                    "filename": photo.filename,
                }
            )

        cached_payload = {
            "success": True,
            "container_number": container.number,
            "photos": photos_raw,
            "photos_count": len(photos_raw),
            "type_counts": type_counts,
        }
        django_cache.set(cache_key, cached_payload, 60 * 15)

        result = dict(cached_payload)
        result["photos"] = [_attach_signed_urls(request, p) for p in photos_raw]
        result["container_token"] = make_container_token(container.number)
        return Response(result)

    except Container.DoesNotExist:
        return Response(
            {"success": False, "error": "Контейнер не найден"},
            status=404,
        )
    except Exception as e:
        logger.exception(
            "get_container_photos failed for %s: %s",
            container_number,
            e,
        )
        return Response(
            {"success": False, "error": "Внутренняя ошибка сервера."},
            status=500,
        )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PhotoDownloadThrottle])
def download_photos_archive(request):
    """Скачать ZIP с выбранными фото контейнера.

    H5a: теперь обязательно передавать ``container_token``, который выдаёт
    :func:`get_container_photos`. Это «proof of view» — подтверждение, что
    клиент действительно открывал галерею данного контейнера, а не
    перебирает ``photo_ids`` сторонним скриптом. Дополнительно
    ``photo_ids`` фильтруются по ``container_token.container_number``: в
    ZIP попадают только фото именно этого контейнера.

    Throttle: 30 req/min на IP — каждая операция строит ZIP в памяти.
    """
    photo_ids = request.data.get("photo_ids", [])
    container_token = request.data.get("container_token", "")

    if not photo_ids:
        return Response({"success": False, "error": "Не выбраны фотографии"}, status=400)

    if not container_token:
        logger.warning(
            "download_photos_archive: missing container_token, ip=%s",
            request.META.get("REMOTE_ADDR"),
        )
        return Response(
            {"success": False, "error": "Не передан container_token. Откройте галерею контейнера заново."},
            status=400,
        )

    try:
        container_number = parse_container_token(container_token)
    except SignatureExpired:
        return Response(
            {"success": False, "error": "Ссылка устарела. Откройте галерею контейнера заново."},
            status=410,
        )
    except (BadSignature, ValueError):
        logger.warning(
            "download_photos_archive: bad container_token from ip=%s",
            request.META.get("REMOTE_ADDR"),
        )
        return Response({"success": False, "error": "Недопустимая ссылка"}, status=403)

    try:
        photos = ContainerPhoto.objects.filter(
            id__in=photo_ids,
            is_public=True,
            container__number=container_number,
        ).select_related("container")

        if not photos.exists():
            return Response({"success": False, "error": "Фотографии не найдены"}, status=404)

        photo_ids_found = list(photos.values_list("id", flat=True))

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for photo in photos:
                if photo.photo and os.path.exists(photo.photo.path):
                    zip_file.write(
                        photo.photo.path,
                        f"{photo.container.number}_{photo.filename}",
                    )

        zip_buffer.seek(0)

        logger.info(
            "download_photos_archive: container=%s ids=%s ip=%s size=%d",
            container_number,
            photo_ids_found,
            request.META.get("REMOTE_ADDR"),
            zip_buffer.getbuffer().nbytes,
        )

        response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="container_photos_{container_number}.zip"'
        return response

    except Exception:
        logger.exception(
            "download_photos_archive: container=%s ids=%s",
            container_number,
            photo_ids,
        )
        return Response(
            {"success": False, "error": "Внутренняя ошибка сервера."},
            status=500,
        )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])  # отключаем глобальный AnonRateThrottle (30/min)
def serve_signed_photo(request, token):
    """Отдаёт media-файл фотографии по подписанному токену.

    URL генерируется :func:`make_photo_token` ``(kind, photo_id, variant)``.
    Подпись проверяется по ``SECRET_KEY`` с TTL ``PHOTO_URL_TTL``
    (по умолчанию 1 час). Без валидного токена скачать файл невозможно —
    даже если кто-то знает ``photo_id``.

    Throttle на этом view намеренно отключён (``@throttle_classes([])``):
    ``DEFAULT_THROTTLE_CLASSES`` в ``settings/base.py`` глобально применяет
    ``AnonRateThrottle`` (30/min) ко всем DRF views, а в галерее одного
    контейнера живут сотни превью — lazy-load быстро выедает лимит и
    ломает UX (раньше ``/media/...`` отдавал nginx без лимитов).

    Парсинг ограничен на этапе **выдачи подписей** —
    :func:`get_container_photos` под ``PhotoDownloadThrottle`` (30/min),
    а сами подписи живут только 1 час.

    Логирование: каждый скачанный файл записывается в ``logger.info(...)``
    с client_ip, photo_id, container/car_id — для аудита массовых
    выгрузок через Sentry / ``grep`` по journalctl.
    """
    try:
        kind, photo_id, variant = parse_photo_token(token)
    except SignatureExpired:
        return Response({"error": "Ссылка устарела"}, status=410)
    except (BadSignature, ValueError):
        logger.warning(
            "serve_signed_photo: bad token from ip=%s",
            request.META.get("REMOTE_ADDR"),
        )
        return Response({"error": "Недопустимая ссылка"}, status=403)

    if kind == "container":
        photo = get_object_or_404(
            ContainerPhoto.objects.select_related("container"),
            id=photo_id,
            is_public=True,
        )
        parent_id = photo.container_id
    elif kind == "car":
        photo = get_object_or_404(
            CarPhoto.objects.select_related("car"),
            id=photo_id,
            is_public=True,
        )
        parent_id = photo.car_id
    else:
        raise Http404

    if variant == "thumb" and getattr(photo, "thumbnail", None):
        file_field = photo.thumbnail
    else:
        file_field = photo.photo

    if not file_field or not os.path.exists(file_field.path):
        raise Http404

    logger.info(
        "serve_signed_photo: kind=%s id=%s variant=%s parent=%s ip=%s",
        kind,
        photo_id,
        variant,
        parent_id,
        request.META.get("REMOTE_ADDR"),
    )

    return FileResponse(file_field.open("rb"))
