"""P1 / Q6 — отдача фото: Cache-Control, X-Accel-Redirect, защита от traversal.

Покрытие:
- флаг ``PHOTO_SERVE_VIA_NGINX`` выключен → ``FileResponse`` с телом и
  ``Cache-Control: private, max-age=<TTL подписи>``, без ``X-Accel-Redirect``;
- флаг включён → пустое тело, ``X-Accel-Redirect`` на путь относительно
  ``MEDIA_ROOT`` под ``PHOTO_ACCEL_PREFIX``, тот же ``Cache-Control``;
- скачивание из портала (``download_container_photo``) — ``Content-Disposition:
  attachment`` в обоих режимах, чужое фото → 404;
- путь вне ``MEDIA_ROOT`` / ``..`` в имени → 404 (traversal запрещён).
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import quote

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.urls import reverse

from core.models import Client as CrmClient
from core.models import Container
from core.models_website import ClientUser, ContainerPhoto
from core.services.photo_response import build_photo_response, resolve_media_file
from core.services.signed_urls import make_photo_token, photo_url_ttl

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _disable_photo_compression():
    """Как в test_signed_photos: сжатие конфликтует с SimpleUploadedFile."""
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        yield


@pytest.fixture
def crm_client(db):
    return CrmClient.objects.create(name="Photo Client")


@pytest.fixture
def container_photo(db, crm_client):
    container = Container.objects.create(number="PERF9999999", status="IN_PORT", client=crm_client)
    upload = SimpleUploadedFile("perf test.jpg", b"\xff\xd8\xff\xd9" * 16, content_type="image/jpeg")
    return ContainerPhoto.objects.create(container=container, photo=upload, is_public=True)


@pytest.fixture
def portal_client(db, client, crm_client):
    user = User.objects.create_user("portal-perf", password="x")
    ClientUser.objects.create(user=user, client=crm_client)
    client.force_login(user)
    return client


def _signed_url(photo):
    return reverse("website:serve_signed_photo", kwargs={"token": make_photo_token("container", photo.id, "full")})


# ---------------------------------------------------------------------------
# serve_signed_photo
# ---------------------------------------------------------------------------


def test_signed_photo_fileresponse_when_nginx_disabled(client, container_photo, settings):
    settings.PHOTO_SERVE_VIA_NGINX = False
    response = client.get(_signed_url(container_photo))

    assert response.status_code == 200
    assert response.streaming
    assert b"".join(response.streaming_content) == b"\xff\xd8\xff\xd9" * 16
    assert response["Cache-Control"] == f"private, max-age={photo_url_ttl()}"
    assert response["Content-Type"] == "image/jpeg"
    assert not response.has_header("X-Accel-Redirect")


def test_signed_photo_x_accel_when_nginx_enabled(client, container_photo, settings):
    settings.PHOTO_SERVE_VIA_NGINX = True
    settings.PHOTO_ACCEL_PREFIX = "/_protected_media"
    response = client.get(_signed_url(container_photo))

    assert response.status_code == 200
    assert not response.streaming
    assert response.content == b""
    assert response["X-Accel-Redirect"] == "/_protected_media/" + quote(container_photo.photo.name)
    # имя с пробелом → percent-encoded, nginx разэкранирует сам
    assert " " not in response["X-Accel-Redirect"]
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == f"private, max-age={photo_url_ttl()}"


def test_signed_photo_respects_custom_prefix(client, container_photo, settings):
    settings.PHOTO_SERVE_VIA_NGINX = True
    settings.PHOTO_ACCEL_PREFIX = "/internal/media/"  # лишний слэш на конце допустим
    response = client.get(_signed_url(container_photo))
    assert response["X-Accel-Redirect"].startswith("/internal/media/container_photos/")


def test_signed_photo_missing_file_404_in_both_modes(client, container_photo, settings):
    container_photo.photo.storage.delete(container_photo.photo.name)
    for enabled in (False, True):
        settings.PHOTO_SERVE_VIA_NGINX = enabled
        assert client.get(_signed_url(container_photo)).status_code == 404


# ---------------------------------------------------------------------------
# Портал: download_container_photo
# ---------------------------------------------------------------------------


def test_portal_download_sets_attachment_disposition(portal_client, container_photo, settings):
    url = reverse("website:download_container_photo", kwargs={"photo_id": container_photo.id})

    settings.PHOTO_SERVE_VIA_NGINX = False
    response = portal_client.get(url)
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert container_photo.filename.split(".")[-1] in response["Content-Disposition"]
    assert response["Cache-Control"] == f"private, max-age={photo_url_ttl()}"

    settings.PHOTO_SERVE_VIA_NGINX = True
    response = portal_client.get(url)
    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == "/_protected_media/" + quote(container_photo.photo.name)
    assert response["Content-Disposition"].startswith("attachment;")


def test_portal_download_foreign_photo_404(portal_client, container_photo, settings):
    settings.PHOTO_SERVE_VIA_NGINX = True
    other = CrmClient.objects.create(name="Other Client")
    container_photo.container.client = other
    container_photo.container.save(update_fields=["client"])
    url = reverse("website:download_container_photo", kwargs={"photo_id": container_photo.id})
    assert portal_client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def test_resolve_rejects_dotdot_in_name(container_photo):
    container_photo.photo.name = "../../etc/passwd"
    with pytest.raises(Http404):
        resolve_media_file(container_photo.photo)


def test_resolve_rejects_path_outside_media_root(container_photo, tmp_path):
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"x")

    class _OutsideStorage:
        def path(self, name):
            return str(outside)

    field = container_photo.photo
    field.storage = _OutsideStorage()
    with pytest.raises(Http404):
        resolve_media_file(field)


def test_resolve_rejects_empty_field(container_photo):
    container_photo.photo.name = ""
    with pytest.raises(Http404):
        resolve_media_file(container_photo.photo)


def test_build_response_relative_path_uses_forward_slashes(container_photo, settings):
    settings.PHOTO_SERVE_VIA_NGINX = True
    response = build_photo_response(container_photo.photo, max_age=10)
    assert "\\" not in response["X-Accel-Redirect"]
    assert response["Cache-Control"] == "private, max-age=10"
