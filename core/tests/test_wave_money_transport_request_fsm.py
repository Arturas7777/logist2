"""T8 (B10): FSM статусов ``TransportRequest``.

Запуск: pytest core/tests/test_wave_money_transport_request_fsm.py
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Car, Client
from core.models.website import TransportRequest


@pytest.fixture
def portal_client(db):
    return Client.objects.create(name="FSM Client")


@pytest.fixture
def car(db, portal_client):
    return Car.objects.create(year=2023, brand="Kia", vin="TRFSMCAR000000001", status="UNLOADED", client=portal_client)


def _request(client, status="DRAFT", **extra):
    return TransportRequest.objects.create(
        client=client,
        carrier_name="Carrier",
        truck_number="TRK1",
        driver_name="Driver",
        status=status,
        **extra,
    )


@pytest.mark.django_db
class TestTransitions:
    @pytest.mark.parametrize(
        "old, new",
        [
            ("DRAFT", "SUBMITTED"),
            ("DRAFT", "IN_PROGRESS"),  # письмо складу из черновика
            ("DRAFT", "CANCELLED"),
            ("SUBMITTED", "DRAFT"),  # корзина на доске
            ("SUBMITTED", "ACCEPTED"),
            ("SUBMITTED", "IN_PROGRESS"),
            ("SUBMITTED", "COMPLETED"),
            ("ACCEPTED", "IN_PROGRESS"),
            ("IN_PROGRESS", "COMPLETED"),
            ("IN_PROGRESS", "DRAFT"),
            ("COMPLETED", "IN_PROGRESS"),
            ("CANCELLED", "DRAFT"),
        ],
    )
    def test_allowed_transitions_save(self, portal_client, old, new):
        tr = _request(portal_client, status=old)
        tr.status = new
        tr.save(update_fields=["status", "updated_at"])
        tr.refresh_from_db()
        assert tr.status == new

    @pytest.mark.parametrize(
        "old, new",
        [
            ("DRAFT", "ACCEPTED"),
            ("DRAFT", "COMPLETED"),
            ("COMPLETED", "DRAFT"),
            ("COMPLETED", "SUBMITTED"),
            ("COMPLETED", "CANCELLED"),
            ("CANCELLED", "SUBMITTED"),
            ("CANCELLED", "COMPLETED"),
            ("IN_PROGRESS", "SUBMITTED"),
        ],
    )
    def test_forbidden_jumps_raise(self, portal_client, old, new):
        tr = _request(portal_client, status=old)
        tr.status = new
        with pytest.raises(ValidationError) as exc:
            tr.save()
        assert "Недопустимый переход" in str(exc.value)
        tr.refresh_from_db()
        assert tr.status == old

    def test_same_status_save_is_noop(self, portal_client):
        tr = _request(portal_client, status="COMPLETED")
        tr.staff_comment = "x"
        tr.save()  # статус не менялся — FSM не вмешивается
        assert TransportRequest.objects.get(pk=tr.pk).status == "COMPLETED"

    def test_update_fields_without_status_skips_fsm(self, portal_client):
        tr = _request(portal_client, status="COMPLETED")
        tr.status = "DRAFT"  # в памяти, но в БД не пишем
        tr.save(update_fields=["staff_comment"])
        assert TransportRequest.objects.get(pk=tr.pk).status == "COMPLETED"

    def test_creation_with_any_status_is_allowed(self, portal_client):
        tr = _request(portal_client, status="COMPLETED")
        assert tr.pk and tr.status == "COMPLETED"


@pytest.mark.django_db
class TestHelpers:
    def test_allowed_next_statuses_in_choice_order(self, portal_client):
        tr = _request(portal_client, status="SUBMITTED")
        assert tr.allowed_next_statuses() == ["DRAFT", "ACCEPTED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
        assert tr.can_transition("ACCEPTED") is True
        assert tr.can_transition("SUBMITTED") is True
        completed = _request(portal_client, status="COMPLETED")
        assert completed.can_transition("DRAFT") is False

    def test_status_choices_for_ui_contains_current_and_allowed(self, portal_client):
        tr = _request(portal_client, status="COMPLETED")
        assert [code for code, _ in tr.status_choices_for_ui()] == ["COMPLETED", "IN_PROGRESS"]

    def test_transition_problems_soft_gates(self, portal_client, car):
        tr = _request(portal_client, status="DRAFT")
        assert "нет автомобилей" in " ".join(tr.transition_problems("SUBMITTED"))
        tr.cars.add(car)
        assert tr.transition_problems("SUBMITTED") == []
        assert "автовоз" in " ".join(tr.transition_problems("COMPLETED"))

    def test_completed_without_autotransport_saves_with_warning_only(self, portal_client):
        """Гейт COMPLETED — мягкий: рейс могли вести вне системы."""
        tr = _request(portal_client, status="IN_PROGRESS")
        tr.status = "COMPLETED"
        tr.save(update_fields=["status", "updated_at"])
        assert TransportRequest.objects.get(pk=tr.pk).status == "COMPLETED"


@pytest.mark.django_db
class TestBoardStatusEndpoint:
    @pytest.fixture
    def staff_client(self, client):
        user = User.objects.create_user(username="fsm-staff", password="x", is_staff=True, is_superuser=True)
        client.force_login(user)
        return client

    def test_forbidden_jump_returns_400(self, staff_client, portal_client):
        tr = _request(portal_client, status="COMPLETED")
        response = staff_client.post(reverse("admin_request_status_set", args=[tr.pk]), {"status": "DRAFT"})
        assert response.status_code == 400
        assert "Недопустимый переход" in response.json()["error"]
        tr.refresh_from_db()
        assert tr.status == "COMPLETED"

    def test_allowed_change_returns_allowed_statuses_and_warnings(self, staff_client, portal_client):
        tr = _request(portal_client, status="IN_PROGRESS")
        response = staff_client.post(reverse("admin_request_status_set", args=[tr.pk]), {"status": "COMPLETED"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "COMPLETED"
        assert data["allowed_statuses"] == ["IN_PROGRESS"]
        assert any("автовоз" in w for w in data["warnings"])

    def test_card_select_offers_only_allowed_statuses(self, staff_client, portal_client, car):
        tr = _request(portal_client, status="COMPLETED")
        tr.cars.add(car)
        body = staff_client.get(reverse("admin_request_card", args=[tr.pk])).content.decode()
        assert 'value="IN_PROGRESS"' in body
        assert 'value="SUBMITTED"' not in body
