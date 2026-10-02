"""Кнопка «отметить все письма прочитанными»."""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Car, Client, Container
from core.models.email import CarEmailLink, ContainerEmail, ContainerEmailLink

pytestmark = pytest.mark.django_db


def test_mark_all_emails_read_clears_badges(client):
    user = get_user_model().objects.create_user(
        username="mail-read", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    owner = Client.objects.create(name="Mail Owner")
    container = Container.objects.create(number="MSCU9990001", status="FLOATING")
    car = Car.objects.create(
        vin="MAILREADVIN000001", client=owner, container=container, status="FLOATING", year=2020
    )
    email = ContainerEmail.objects.create(
        message_id="<mark-all-read@test>",
        thread_id="thread-mark-all",
        subject="hello",
        from_addr="a@b.c",
        direction="INCOMING",
        received_at=timezone.now(),
    )
    ContainerEmailLink.objects.create(email=email, container=container, is_read=False)
    CarEmailLink.objects.create(email=email, car=car, is_read=False)

    page = client.get("/admin/core/containeremail/mark-all-read/")
    assert page.status_code == 200
    assert "непрочитанных" in page.content.decode()

    done = client.post("/admin/core/containeremail/mark-all-read/")
    assert done.status_code == 302
    assert ContainerEmailLink.objects.filter(is_read=False).count() == 0
    assert CarEmailLink.objects.filter(is_read=False).count() == 0
