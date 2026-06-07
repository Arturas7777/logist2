"""Скачивание фотографий для авторизованных клиентов (по сессии).

В отличие от signed-photos (см. :mod:`.signed_photos`), эти эндпоинты
требуют логин и проверяют ``photo.car/container.client == request.user``.
Используются ссылками в личном кабинете.
"""

import logging
import os
import tempfile
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Car
from core.models_website import CarPhoto, ClientUser, ContainerPhoto

logger = logging.getLogger(__name__)


@login_required
def download_car_photo(request, photo_id):
    """Скачать одну фотографию автомобиля."""
    try:
        client_user = request.user.clientuser
        photo = get_object_or_404(
            CarPhoto.objects.select_related("car"),
            id=photo_id,
            car__client=client_user.client,
            is_public=True,
        )

        if photo.photo and os.path.exists(photo.photo.path):
            response = FileResponse(photo.photo.open("rb"))
            response["Content-Disposition"] = f'attachment; filename="{photo.filename}"'
            return response
        raise Http404("Фото не найдено")
    except ClientUser.DoesNotExist:
        raise Http404("Доступ запрещен")


@login_required
def download_container_photo(request, photo_id):
    """Скачать одну фотографию контейнера."""
    try:
        client_user = request.user.clientuser
        photo = get_object_or_404(
            ContainerPhoto.objects.select_related("container"),
            id=photo_id,
            container__client=client_user.client,
            is_public=True,
        )

        if photo.photo and os.path.exists(photo.photo.path):
            response = FileResponse(photo.photo.open("rb"))
            response["Content-Disposition"] = f'attachment; filename="{photo.filename}"'
            return response
        raise Http404("Фото не найдено")
    except ClientUser.DoesNotExist:
        raise Http404("Доступ запрещен")


@login_required
@api_view(["GET"])
def download_all_car_photos(request, car_id):
    """Скачать все фотографии автомобиля одним ZIP-архивом."""
    try:
        client_user = request.user.clientuser
        car = get_object_or_404(Car, id=car_id, client=client_user.client)
        photos = CarPhoto.objects.filter(car=car, is_public=True)

        if not photos.exists():
            return Response(
                {"error": "Фотографии не найдены"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # SpooledTemporaryFile держит маленькие архивы в памяти, а большие
        # (> 16 МБ) сбрасывает на диск — без риска OOM на контейнерах/авто
        # с большим числом фото. FileResponse стримит чанками и закрывает
        # файл (а с ним temp) после отдачи.
        zip_buffer = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for photo in photos:
                if photo.photo and os.path.exists(photo.photo.path):
                    zip_file.write(
                        photo.photo.path,
                        arcname=f"{photo.get_photo_type_display()}_{photo.filename}",
                    )

        zip_buffer.seek(0)
        response = FileResponse(zip_buffer, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{car.vin}_photos.zip"'
        return response

    except ClientUser.DoesNotExist:
        return Response(
            {"error": "Доступ запрещен"},
            status=status.HTTP_403_FORBIDDEN,
        )
