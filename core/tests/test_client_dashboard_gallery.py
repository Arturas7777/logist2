"""Галерея фото контейнера в списке авто кабинета клиента."""

from __future__ import annotations

from datetime import date
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
    assert 'data-container-photos="GALLERY0011"' in html
    assert "bi-camera" in html
    assert 'id="photosModal"' in html
    assert "?track=" not in html


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
    assert "data-container-photos" not in html


def test_dashboard_transferred_gallery_newest_first(portal_login):
    """Фильтр «Передан»: свежие с фото сверху, иконка галереи есть."""
    http, owner = portal_login
    old_ctr = Container.objects.create(number="OLDTRANS001", status="TRANSFERRED")
    new_ctr = Container.objects.create(number="NEWTRANS001", status="TRANSFERRED")
    Car.objects.create(
        year=2021,
        brand="Kia",
        vin="OLDTRANSVIN000001",
        status="TRANSFERRED",
        client=owner,
        container=old_ctr,
        transfer_date=date(2025, 8, 1),
        unload_date=date(2025, 8, 1),
    )
    Car.objects.create(
        year=2024,
        brand="BMW",
        vin="NEWTRANSVIN000001",
        status="TRANSFERRED",
        client=owner,
        container=new_ctr,
        transfer_date=date(2026, 8, 10),
        unload_date=date(2026, 8, 1),
    )
    upload = SimpleUploadedFile("n.jpg", b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        ContainerPhoto.objects.create(container=new_ctr, photo=upload, is_public=True, photo_type="UNLOADING")

    response = http.get(reverse("website:dashboard"), {"status": "TRANSFERRED"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "NEWTRANSVIN000001" in html
    assert "OLDTRANSVIN000001" in html
    assert html.index("NEWTRANSVIN000001") < html.index("OLDTRANSVIN000001")
    assert 'data-container-photos="NEWTRANS001"' in html
    assert 'data-container-photos="OLDTRANS001"' not in html


def test_dashboard_title_preview_when_scan_uploaded(portal_login):
    http, owner = portal_login
    car = Car.objects.create(
        year=2023,
        brand="Ford",
        vin="TITLESCANVIN00001",
        status="UNLOADED",
        client=owner,
    )
    car.title_scan.save(
        "title.pdf",
        SimpleUploadedFile("title.pdf", b"%PDF-1.4 title", content_type="application/pdf"),
        save=True,
    )

    response = http.get(reverse("website:dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "TITLESCANVIN00001" in html
    assert "title-tile doc-preview-btn" in html
    assert "bi-file-earmark-text" in html
    assert 'id="docPreviewModal"' in html
    assert car.title_scan.url in html
    assert "bi-exclamation-triangle-fill" not in html


def test_dashboard_title_warning_when_scan_missing(portal_login):
    http, owner = portal_login
    Car.objects.create(
        year=2023,
        brand="Jeep",
        vin="NOTITLEVIN0000001",
        status="UNLOADED",
        client=owner,
    )
    response = http.get(reverse("website:dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "NOTITLEVIN0000001" in html
    assert "bi-exclamation-triangle-fill" in html
    assert "title-tile is-missing" in html
    assert "title-tile doc-preview-btn" not in html


def test_dashboard_hides_request_checkbox_for_floating(portal_login):
    http, owner = portal_login
    Car.objects.create(
        year=2024,
        brand="Kia",
        vin="FLOATDASHVIN00001",
        status="FLOATING",
        client=owner,
    )
    response = http.get(reverse("website:dashboard"), {"status": "FLOATING"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "FLOATDASHVIN00001" in html
    assert 'class="form-check-input car-select"' not in html
