"""L6: «ждём документы» открывает правку заявки, но не откат и не отмену.

Раньше при ``awaiting_client_docs`` клиент мог кнопкой «Сохранить
черновик» вернуть заявку «В процессе» в черновик или удалить её; для
«Оформлена» оба действия падали 500 (переход запрещён FSM).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.urls import reverse

from core.models import Client
from core.models.website import ClientUser, TransportRequest
from core.views_website.portal_transport import _save_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_client():
    return Client.objects.create(name="L6 Portal Client")


@pytest.fixture
def logged_client(client, portal_client):
    user = User.objects.create_user(username="l6portal", password="secret123")
    ClientUser.objects.create(user=user, client=portal_client, is_verified=True)
    client.force_login(user)
    return client


def _request(portal_client, status, **extra):
    tr = TransportRequest.objects.create(client=portal_client, truck_number="L6", status="DRAFT")
    if status != "DRAFT":
        TransportRequest.objects.filter(pk=tr.pk).update(status=status, **extra)
        tr.refresh_from_db()
    return tr


class _Form:
    def __init__(self, instance):
        self.instance = instance

    def save(self, commit=True):
        return self.instance

    def save_m2m(self):
        pass


@pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED"])
def test_save_draft_does_not_revert_request_in_work(portal_client, status):
    tr = _request(portal_client, status, awaiting_client_docs=True)
    request = RequestFactory().post("/", {"save_draft": "1"})

    saved = _save_request(request, _Form(tr), portal_client, instance=TransportRequest.objects.get(pk=tr.pk))

    saved.refresh_from_db()
    assert saved.status == status


def test_save_draft_still_works_for_draft(portal_client):
    tr = _request(portal_client, "DRAFT")
    request = RequestFactory().post("/", {"save_draft": "1"})

    saved = _save_request(request, _Form(tr), portal_client, instance=TransportRequest.objects.get(pk=tr.pk))

    assert saved.status == "DRAFT"


@pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED"])
def test_client_cannot_cancel_request_in_work(logged_client, portal_client, status):
    tr = _request(portal_client, status, awaiting_client_docs=True)
    assert tr.is_client_editable
    assert not tr.is_client_cancellable

    response = logged_client.post(reverse("website:transport_request_delete", args=[tr.pk]))

    assert response.status_code == 302
    tr.refresh_from_db()
    assert tr.status == status


def test_client_can_cancel_submitted(logged_client, portal_client):
    tr = _request(portal_client, "SUBMITTED")

    logged_client.post(reverse("website:transport_request_delete", args=[tr.pk]))

    tr.refresh_from_db()
    assert tr.status == "CANCELLED"
