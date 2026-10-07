"""Кабинет клиента, волна «Клиент» (IMPROVEMENT_PLAN_2026-10: Q10, C1, C3, C6, C7).

* Q10 — дефолтный фильтр показывает все активные статусы (включая FLOATING),
  чипы статусов над списком.
* C1 — таймлайн статусов в карточках авто/контейнера и ETA в ``/api/track/``.
* C3 — страница «Контейнеры» без N+1; на главной кабинета списка нет.
* C6/C7 — цены в EUR, фото с lazy-loading и единым lightbox.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Car, Client, Container, Warehouse
from core.models.website import CarPhoto, ClientUser, ContainerPhoto

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal(client):
    owner = Client.objects.create(name="Wave Client")
    user = User.objects.create_user(username="wave-client", password="secret123")
    ClientUser.objects.create(user=user, client=owner, is_verified=True)
    client.force_login(user)
    return client, owner


def _car(owner, vin, status, **kwargs):
    return Car.objects.create(year=2022, brand="BMW", vin=vin, status=status, client=owner, **kwargs)


# ── Q10 ─────────────────────────────────────────────────────────────────────


def test_dashboard_floating_sorted_by_eta_soonest_first(portal):
    """«В пути»: ближайший ETA выше, без даты — внизу. Разгрузка остаётся над ними."""
    http, owner = portal
    late = Container.objects.create(number="ETALATE00001", status="FLOATING", eta=date(2026, 12, 20))
    soon = Container.objects.create(number="ETASOON00001", status="FLOATING", eta=date(2026, 10, 15))
    missing = Container.objects.create(number="ETANONE00001", status="FLOATING")
    _car(owner, "ETALATEVIN0000001", "FLOATING", container=late)
    _car(owner, "ETASOONVIN0000001", "FLOATING", container=soon)
    _car(owner, "ETANONEVIN0000001", "FLOATING", container=missing)
    _car(owner, "ETAUNLDVIN0000001", "UNLOADED", unload_date=date(2026, 9, 1))

    html = http.get(reverse("website:dashboard")).content.decode()
    assert html.index("ETAUNLDVIN0000001") < html.index("ETASOONVIN0000001")
    assert html.index("ETASOONVIN0000001") < html.index("ETALATEVIN0000001")
    assert html.index("ETALATEVIN0000001") < html.index("ETANONEVIN0000001")


def test_dashboard_default_shows_floating_cars(portal):
    http, owner = portal
    _car(owner, "FLOATDEFAULT00001", "FLOATING")
    _car(owner, "UNLOADDEFAULT0001", "UNLOADED")
    _car(owner, "TRANSFDEFAULT0001", "TRANSFERRED", transfer_date=date(2026, 1, 1))

    html = http.get(reverse("website:dashboard")).content.decode()
    assert "FLOATDEFAULT00001" in html
    assert "UNLOADDEFAULT0001" in html
    assert "TRANSFDEFAULT0001" not in html


def test_dashboard_status_chips_present_and_work(portal):
    http, owner = portal
    _car(owner, "CHIPTRANSFER00001", "TRANSFERRED", transfer_date=date(2026, 1, 1))
    _car(owner, "CHIPUNLOADED00001", "UNLOADED")

    html = http.get(reverse("website:dashboard")).content.decode()
    assert 'class="status-chips' in html
    assert "status-chip is-active" in html
    assert "?status=TRANSFERRED" in html

    html = http.get(reverse("website:dashboard"), {"status": "TRANSFERRED"}).content.decode()
    assert "CHIPTRANSFER00001" in html
    assert "CHIPUNLOADED00001" not in html


# ── C1 ──────────────────────────────────────────────────────────────────────


def test_car_detail_has_timeline_with_eta(portal):
    http, owner = portal
    container = Container.objects.create(number="TLCONT000001", status="FLOATING", eta=date(2026, 11, 20))
    car = _car(owner, "TIMELINEVIN000001", "FLOATING", container=container)

    html = http.get(reverse("website:car_detail", args=[car.id])).content.decode()
    assert 'class="status-timeline"' in html
    assert "status-timeline__step is-current" in html
    assert "ETA 20.11.2026" in html


def test_container_detail_timeline_marks_done_steps(portal):
    http, owner = portal
    warehouse = Warehouse.objects.create(name="Klaipėda WH")
    container = Container.objects.create(
        number="TLCONT000002",
        status="UNLOADED",
        client=owner,
        unload_date=date(2026, 9, 1),
        warehouse=warehouse,
    )
    _car(owner, "TIMELINEVIN000002", "UNLOADED", container=container, warehouse=warehouse)

    html = http.get(reverse("website:container_detail", args=[container.id])).content.decode()
    assert html.count("status-timeline__step is-done") == 2
    assert html.count("status-timeline__step is-current") == 1
    assert "01.09.2026" in html
    # Весь файл под i18n — русского хардкода «Информация о контейнере» без trans больше нет,
    # но на ru-локали текст остаётся.
    assert "Информация о контейнере" in html


def test_track_api_returns_eta_for_car(client):
    container = Container.objects.create(number="TRACKETA00001", status="FLOATING", eta=date(2026, 12, 5))
    Car.objects.create(year=2021, brand="Audi", vin="TRACKETAVIN000001", status="FLOATING", container=container)

    response = client.post(
        reverse("website:track_shipment"),
        data={"tracking_number": "TRACKETAVIN000001"},
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "car"
    assert payload["data"]["eta"] == "2026-12-05"


def test_home_page_exposes_tracking_i18n(client):
    html = client.get(reverse("website:home")).content.decode()
    assert 'id="tracking-i18n"' in html
    assert "renderTimeline" in html
    assert "toLocaleDateString('ru-RU')" not in html


# ── C6 / C7 ─────────────────────────────────────────────────────────────────


def test_car_detail_prices_in_eur_and_lazy_photos(portal):
    http, owner = portal
    car = _car(owner, "PRICEVIN000000001", "UNLOADED", total_price=120, storage_cost=20, days=4)
    upload = SimpleUploadedFile("p.jpg", b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        CarPhoto.objects.create(car=car, photo=upload, is_public=True)

    html = http.get(reverse("website:car_detail", args=[car.id])).content.decode()
    assert "$" not in html.split("<main>")[1].split("</main>")[0]
    assert "EUR" in html
    assert "current_price" not in html
    assert 'loading="lazy"' in html
    assert 'id="photoLightbox"' in html
    assert "photoModal" not in html


# ── C3 ──────────────────────────────────────────────────────────────────────


def test_containers_page_lists_client_containers_without_n_plus_one(portal):
    http, owner = portal
    containers = []
    for i in range(4):
        ctr = Container.objects.create(
            number=f"MYCONT0000{i}", status="FLOATING", client=owner, eta=date(2026, 11, 1 + i)
        )
        containers.append(ctr)
        _car(owner, f"MYCONTVIN0000000{i}", "FLOATING", container=ctr)
    # Контейнер без FK client, но с авто клиента — тоже «мой».
    orphan = Container.objects.create(number="MYCONTORPHAN", status="IN_PORT")
    _car(owner, "MYCONTORPHANVIN01", "IN_PORT", container=orphan)
    # Переданный контейнер в блок не попадает.
    done = Container.objects.create(number="MYCONTDONE01", status="TRANSFERRED", client=owner)
    _car(owner, "MYCONTDONEVIN0001", "TRANSFERRED", container=done, transfer_date=date(2026, 1, 1))
    upload = SimpleUploadedFile("c.jpg", b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        ContainerPhoto.objects.create(container=containers[0], photo=upload, is_public=True)

    dashboard = http.get(reverse("website:dashboard"))
    assert dashboard.status_code == 200
    dashboard_html = dashboard.content.decode()
    assert "Мои контейнеры" not in dashboard_html
    assert reverse("website:containers") in dashboard_html
    for ctr in containers:
        assert reverse("website:container_detail", args=[ctr.id]) not in dashboard_html

    with CaptureQueriesContext(connection) as ctx:
        response = http.get(reverse("website:containers"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Контейнеры" in html
    for ctr in containers:
        assert reverse("website:container_detail", args=[ctr.id]) in html
    assert "MYCONTORPHAN" in html
    assert reverse("website:container_detail", args=[done.id]) not in html
    assert "01.11.2026" in html
    assert len(ctx.captured_queries) <= 12, "\n".join(q["sql"] for q in ctx.captured_queries)
