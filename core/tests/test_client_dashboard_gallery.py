"""Галерея фото контейнера в списке авто кабинета клиента."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Car, Client, Container
from core.models.website import ClientUser, ContainerPhoto

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_login(client):
    owner = Client.objects.create(name="Gallery Client")
    user = User.objects.create_user(username="gallery-client", password="secret123")
    ClientUser.objects.create(user=user, client=owner, is_verified=True)
    client.force_login(user)
    return client, owner


def test_dashboard_gallery_link_when_container_has_photos(portal_login):
    http, owner = portal_login
    container = Container.objects.create(number="GALLERY0011", status="IN_PORT")
    Car.objects.create(
        year=2022,
        brand="BMW",
        vin="GALLERYVIN0000001",
        status="UNLOADED",
        client=owner,
        container=container,
    )
    upload = SimpleUploadedFile("u.jpg", b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        ContainerPhoto.objects.create(container=container, photo=upload, is_public=True, photo_type="UNLOADING")

    response = http.get(reverse("website:dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "GALLERY0011" in html
    assert "?track=GALLERY0011&amp;photos=1" in html or "?track=GALLERY0011&photos=1" in html
    assert "bi-camera" in html


def test_dashboard_hides_gallery_icon_without_photos(portal_login):
    http, owner = portal_login
    container = Container.objects.create(number="NOPHOTO0011", status="IN_PORT")
    Car.objects.create(
        year=2022,
        brand="Audi",
        vin="NOPHOTOVIN0000001",
        status="UNLOADED",
        client=owner,
        container=container,
    )
    response = http.get(reverse("website:dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "NOPHOTO0011" in html
    assert "photos=1" not in html
