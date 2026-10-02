"""U2: пилот HTMX — статус контейнера, фильтры авто, монитор."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Container

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    user = get_user_model().objects.create_user(
        username="htmx-staff", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    return client


def test_container_status_htmx_switches_and_rejects(staff_client):
    container = Container.objects.create(number="HTMX-1", status="FLOATING")
    url = reverse("admin:core_container_set_status", args=[container.pk])
    ok = staff_client.post(url, {"status": "IN_PORT"})
    assert ok.status_code == 200
    assert "В порту" in ok.content.decode()
    container.refresh_from_db()
    assert container.status == "IN_PORT"

    bad = staff_client.post(url, {"status": "NOPE"})
    assert bad.status_code == 400
    container.refresh_from_db()
    assert container.status == "IN_PORT"


def test_car_changelist_and_monitor_declare_htmx(staff_client):
    cars = staff_client.get(reverse("admin:core_car_changelist"))
    assert cars.status_code == 200
    body = cars.content.decode()
    assert "hx-get" in body
    assert "hx-target" in body

    monitor = staff_client.get(reverse("system_monitor"))
    assert monitor.status_code == 200
    page = monitor.content.decode()
    assert 'hx-trigger="every 30s, click"' in page
    assert "smApplySnapshot" in page
