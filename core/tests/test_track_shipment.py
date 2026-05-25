"""Тесты для публичного эндпоинта ``/api/track/``.

Покрытие (H7):

* валидный JSON, контейнер найден → 200 + ``type='container'``;
* валидный JSON, ничего не найдено → 404 с человекочитаемой ошибкой;
* пустой ``tracking_number`` → 400;
* слишком короткий ``tracking_number`` (защита от перебора) → 400;
* **битый JSON** → 400 (раньше падало в 500 из-за широкого ``except
  Exception`` в ``track_shipment``).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Container


@pytest.fixture
def tracked_container(db):
    return Container.objects.create(number="CARU1234567", status="IN_PORT")


def test_track_shipment_finds_container(client, tracked_container):
    url = reverse("website:track_shipment")
    response = client.post(
        url,
        data={"tracking_number": tracked_container.number},
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "container"
    assert payload["data"]["number"] == tracked_container.number


def test_track_shipment_not_found(client, db):
    url = reverse("website:track_shipment")
    response = client.post(
        url,
        data={"tracking_number": "ZZZZ9999999"},
        content_type="application/json",
    )
    assert response.status_code == 404
    assert "Груз не найден" in response.json()["error"]


def test_track_shipment_empty_number(client, db):
    url = reverse("website:track_shipment")
    response = client.post(
        url,
        data={"tracking_number": ""},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "укажите номер" in response.json()["error"]


def test_track_shipment_too_short(client, db):
    """VIN >= 17 символов, контейнер ~11 — короче 8 однозначно невалидно."""
    url = reverse("website:track_shipment")
    response = client.post(
        url,
        data={"tracking_number": "ABC123"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "слишком короткий" in response.json()["error"]


def test_track_shipment_broken_json_returns_400(client, db):
    """H7 regression: битый JSON должен давать 400 (DRF ParseError),
    а не 500 — иначе фронт не видит причину, а Sentry заваливается
    ложными 500-ками.
    """
    url = reverse("website:track_shipment")
    response = client.post(
        url,
        data="{not valid json,,,",
        content_type="application/json",
    )
    assert response.status_code == 400
    # DRF возвращает {"detail": "JSON parse error - ..."} для ParseError
    assert "detail" in response.json()


def test_track_shipment_wrong_content_type_returns_400(client, db):
    """multipart/form-data без поля tracking_number → 400 (пустой номер),
    не 500.
    """
    url = reverse("website:track_shipment")
    response = client.post(url, data={"foo": "bar"})  # default form-data
    assert response.status_code == 400
