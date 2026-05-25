"""Тесты для signed-URL фотографий (H5a).

Покрытие:
- round-trip make/parse photo_token,
- round-trip make/parse container_token,
- невалидные подписи / просроченные подписи / битый payload,
- view serve_signed_photo (200/410/403/404),
- download_photos_archive отвергает запрос без container_token,
  с битым / просроченным / неподходящим (для другого контейнера) токеном.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import Container
from core.models_website import ContainerPhoto
from core.services.signed_urls import (
    BadSignature,
    SignatureExpired,
    make_container_token,
    make_photo_token,
    parse_container_token,
    parse_photo_token,
)

# ---------------------------------------------------------------------------
# Round-trip pure-Python (без БД)
# ---------------------------------------------------------------------------


def test_photo_token_round_trip():
    token = make_photo_token("container", 42, "full")
    kind, pid, variant = parse_photo_token(token)
    assert (kind, pid, variant) == ("container", 42, "full")


def test_photo_token_thumb_variant():
    token = make_photo_token("car", 7, "thumb")
    assert parse_photo_token(token) == ("car", 7, "thumb")


def test_photo_token_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_photo_token("warehouse", 1)


def test_photo_token_rejects_unknown_variant():
    with pytest.raises(ValueError):
        make_photo_token("container", 1, "micro")


def test_photo_token_bad_signature():
    token = make_photo_token("container", 42, "full")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(BadSignature):
        parse_photo_token(tampered)


def test_photo_token_expired():
    token = make_photo_token("container", 42, "full")
    # Подменяем timezone.now на «через 2 часа», чтобы подпись «постарела».
    future = timezone.now() + timedelta(hours=2)
    with patch("django.core.signing.time.time", return_value=future.timestamp()):
        with pytest.raises(SignatureExpired):
            parse_photo_token(token)


def test_container_token_round_trip():
    token = make_container_token("CARU1234567")
    assert parse_container_token(token) == "CARU1234567"


def test_container_token_bad_signature():
    token = make_container_token("CARU1234567")
    with pytest.raises(BadSignature):
        parse_container_token(token + "x")


def test_container_token_expired():
    token = make_container_token("CARU1234567")
    future = timezone.now() + timedelta(hours=2)
    with patch("django.core.signing.time.time", return_value=future.timestamp()):
        with pytest.raises(SignatureExpired):
            parse_container_token(token)


# ---------------------------------------------------------------------------
# View serve_signed_photo
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_photo_compression():
    """Отключаем `maybe_compress_image_field` на время теста.

    Эта функция в проде делает `field.open('rb')` + `field.close()`, что
    конфликтует с SimpleUploadedFile (он остаётся «закрытым», и
    последующий super().save() падает с
    `I/O operation on closed file.`). В тестах сжатие нам не нужно — мы
    проверяем именно подпись и доступ, а не оптимизацию JPEG.

    Импорт в моделях ленивый (`from .services.photo_optimize import …`
    внутри save()), поэтому патчим именно исходный модуль.
    """
    with patch(
        "core.services.photo_optimize.maybe_compress_image_field",
        return_value=False,
    ):
        yield


@pytest.fixture
def container_photo(db):
    """ContainerPhoto с реальным (не пустым) файлом на диске."""
    container = Container.objects.create(number="CARU9999999", status="IN_PORT")
    # Достаточно любого ненулевого содержимого: serve_signed_photo
    # только проверяет os.path.exists и стримит байты.
    upload = SimpleUploadedFile("test.jpg", b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")
    photo = ContainerPhoto.objects.create(
        container=container,
        photo=upload,
        is_public=True,
    )
    return photo


def test_serve_signed_photo_ok(client, container_photo):
    token = make_photo_token("container", container_photo.id, "full")
    url = reverse("website:serve_signed_photo", kwargs={"token": token})
    response = client.get(url)
    assert response.status_code == 200


def test_serve_signed_photo_bad_token(client, container_photo):
    url = reverse("website:serve_signed_photo", kwargs={"token": "definitely-not-a-token"})
    response = client.get(url)
    assert response.status_code == 403


def test_serve_signed_photo_expired(client, container_photo):
    token = make_photo_token("container", container_photo.id, "full")
    url = reverse("website:serve_signed_photo", kwargs={"token": token})
    future = timezone.now() + timedelta(hours=2)
    with patch("django.core.signing.time.time", return_value=future.timestamp()):
        response = client.get(url)
    assert response.status_code == 410


def test_serve_signed_photo_unknown_id(client, container_photo, db):
    # Используем id, который точно не существует
    fake_token = make_photo_token("container", container_photo.id + 9999, "full")
    url = reverse("website:serve_signed_photo", kwargs={"token": fake_token})
    response = client.get(url)
    assert response.status_code == 404


def test_serve_signed_photo_not_public(client, container_photo):
    container_photo.is_public = False
    container_photo.save(update_fields=["is_public"])
    token = make_photo_token("container", container_photo.id, "full")
    url = reverse("website:serve_signed_photo", kwargs={"token": token})
    response = client.get(url)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# download_photos_archive — требует container_token, фильтрует по контейнеру
# ---------------------------------------------------------------------------


def test_download_archive_requires_container_token(client, container_photo):
    url = reverse("website:download_photos_archive")
    response = client.post(
        url,
        data={"photo_ids": [container_photo.id]},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_download_archive_with_valid_token(client, container_photo):
    token = make_container_token(container_photo.container.number)
    url = reverse("website:download_photos_archive")
    response = client.post(
        url,
        data={"photo_ids": [container_photo.id], "container_token": token},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


def test_download_archive_rejects_bad_token(client, container_photo):
    url = reverse("website:download_photos_archive")
    response = client.post(
        url,
        data={
            "photo_ids": [container_photo.id],
            "container_token": "not-a-real-token",
        },
        content_type="application/json",
    )
    assert response.status_code == 403


def test_download_archive_filters_by_container(client, container_photo, db):
    """photo_ids чужого контейнера не должны попадать в архив."""
    # Создаём фото в другом контейнере
    other = Container.objects.create(number="OTHER1234567", status="IN_PORT")
    other_photo = ContainerPhoto.objects.create(
        container=other,
        photo=SimpleUploadedFile("o.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"),
        is_public=True,
    )

    # Токен подписан под номер первого контейнера
    token = make_container_token(container_photo.container.number)
    url = reverse("website:download_photos_archive")

    # В photo_ids — только id чужого фото
    response = client.post(
        url,
        data={"photo_ids": [other_photo.id], "container_token": token},
        content_type="application/json",
    )
    # Чужое фото не должно найтись в выборке этого контейнера
    assert response.status_code == 404
