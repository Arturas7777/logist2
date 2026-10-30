"""Массовое действие «Скопировать» в списке контейнеров: «Линия - номер - ETA дата»."""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.admin.container import container_copy_line, containers_copy_text
from core.models import Container, Line

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    user = get_user_model().objects.create_user(
        username="copy-container-staff", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    return client


def test_copy_text_is_line_number_and_eta():
    rows = [
        ("MSC", "MSMU7700595", date(2026, 11, 5)),
        (" MAERSK ", " MRKU1234567 ", date(2026, 12, 1)),
    ]

    assert containers_copy_text(rows) == ("MSC - MSMU7700595 - ETA 2026-11-05\nMAERSK - MRKU1234567 - ETA 2026-12-01")
    assert container_copy_line("MSC", "MSMU7700595", None) == "MSC - MSMU7700595"
    assert container_copy_line("", "MSMU7700595", date(2026, 11, 5)) == "MSMU7700595 - ETA 2026-11-05"
    assert containers_copy_text([]) == ""


def test_changelist_rows_carry_copy_line(staff_client):
    line = Line.objects.create(name="MSC")
    Container.objects.create(number="MSMU7700595", status="IN_PORT", line=line, eta=date(2026, 11, 5))

    body = staff_client.get(reverse("admin:core_container_changelist")).content.decode()

    assert 'data-copy-line="MSC - MSMU7700595 - ETA 2026-11-05"' in body
    assert 'value="copy_selected_containers"' in body


def test_server_fallback_page_lists_selected_containers(staff_client):
    msc = Line.objects.create(name="MSC")
    maersk = Line.objects.create(name="MAERSK")
    first = Container.objects.create(number="MSMU7700595", status="IN_PORT", line=msc, eta=date(2026, 11, 5))
    second = Container.objects.create(number="MRKU1234567", status="IN_PORT", line=maersk, eta=date(2026, 12, 1))
    Container.objects.create(number="CMAU0000001", status="IN_PORT", line=Line.objects.create(name="CMA"))

    response = staff_client.post(
        reverse("admin:core_container_changelist"),
        {"action": "copy_selected_containers", "_selected_action": [first.pk, second.pk], "index": 0},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "MSC - MSMU7700595 - ETA 2026-11-05" in body
    assert "MAERSK - MRKU1234567 - ETA 2026-12-01" in body
    assert "CMAU0000001" not in body
