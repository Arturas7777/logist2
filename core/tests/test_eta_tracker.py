"""Тесты обновления ETA контейнеров из Track & Trace API линий (DCSA).

Сеть не используется: адаптеры линий подменяются monkeypatch'ем,
парсер DCSA-событий тестируется на готовых payload'ах.

Запуск: pytest core/tests/test_eta_tracker.py
"""

from __future__ import annotations

from datetime import date

import pytest

from core.models import Container, Line
from core.services import eta_tracker
from core.services.eta_tracker import extract_eta_from_events, update_container_eta


def _transport_event(dt: str, *, type_code: str = "ARRI", classifier: str = "PLN") -> dict:
    return {
        "eventType": "TRANSPORT",
        "transportEventTypeCode": type_code,
        "eventClassifierCode": classifier,
        "eventDateTime": dt,
    }


# ── extract_eta_from_events ────────────────────────────────────────────────


def test_extract_eta_takes_latest_planned_arrival():
    # Трансшипмент: промежуточное прибытие + конечное. Берём позднее.
    payload = [
        _transport_event("2026-08-15T10:00:00Z"),
        _transport_event("2026-08-28T06:00:00+00:00"),
        _transport_event("2026-08-20T10:00:00Z", type_code="DEPA"),  # отход — не ETA
        _transport_event("2026-08-30T10:00:00Z", classifier="ACT"),  # факт — не план
        {"eventType": "EQUIPMENT", "eventDateTime": "2026-09-01T00:00:00Z"},
    ]
    assert extract_eta_from_events(payload) == date(2026, 8, 28)


def test_extract_eta_accepts_estimated_and_dict_payload():
    payload = {"events": [_transport_event("2026-09-03T12:00:00Z", classifier="EST")]}
    assert extract_eta_from_events(payload) == date(2026, 9, 3)


def test_extract_eta_none_for_empty_or_garbage():
    assert extract_eta_from_events([]) is None
    assert extract_eta_from_events(None) is None
    assert extract_eta_from_events([{"eventType": "TRANSPORT"}, "мусор"]) is None
    assert extract_eta_from_events([_transport_event("не дата")]) is None


# ── update_container_eta ───────────────────────────────────────────────────


@pytest.fixture
def maersk_container(db):
    line = Line.objects.create(name="MAERSK")
    return Container.objects.create(number="MSKU1234567", status="FLOATING", line=line)


def test_update_container_eta_sets_new_date(maersk_container, monkeypatch):
    payload = [_transport_event("2026-08-25T08:00:00Z")]
    monkeypatch.setitem(eta_tracker.LINE_FETCHERS, "MAERSK", lambda number: (payload, ""))

    result = update_container_eta(maersk_container)

    maersk_container.refresh_from_db()
    assert result["updated"] is True
    assert maersk_container.eta == date(2026, 8, 25)
    assert result["new_eta"] == "2026-08-25"


def test_update_container_eta_unchanged(maersk_container, monkeypatch):
    maersk_container.eta = date(2026, 8, 25)
    maersk_container.save(update_fields=["eta"])
    payload = [_transport_event("2026-08-25T23:00:00Z")]
    monkeypatch.setitem(eta_tracker.LINE_FETCHERS, "MAERSK", lambda number: (payload, ""))

    result = update_container_eta(maersk_container)

    assert result["updated"] is False
    assert result["message"] == "ETA не изменился"


def test_update_container_eta_unsupported_line(db):
    line = Line.objects.create(name="HAPPAG")
    container = Container.objects.create(number="HLXU1234567", status="FLOATING", line=line)

    result = update_container_eta(container)

    assert result["updated"] is False
    assert "не поддерживается" in result["message"]


def test_update_container_eta_missing_key(maersk_container, settings):
    # Без ключа адаптер честно сообщает причину и ничего не меняет.
    settings.MAERSK_CONSUMER_KEY = ""

    result = update_container_eta(maersk_container)

    assert result["updated"] is False
    assert "MAERSK_CONSUMER_KEY" in result["message"]


def test_update_container_eta_msc_waits_for_onboarding(db, settings):
    settings.MSC_API_BASE_URL = ""
    settings.MSC_API_KEY = ""
    line = Line.objects.create(name="MSC")
    container = Container.objects.create(number="MSDU1234567", status="FLOATING", line=line)

    result = update_container_eta(container)

    assert result["updated"] is False
    assert "MSC_API" in result["message"]


def test_update_container_eta_fetch_error(maersk_container, monkeypatch):
    import requests

    def boom(number):
        raise requests.ConnectionError("сеть недоступна")

    monkeypatch.setitem(eta_tracker.LINE_FETCHERS, "MAERSK", boom)

    result = update_container_eta(maersk_container)

    assert result["updated"] is False
    assert "ошибка запроса" in result["message"]
