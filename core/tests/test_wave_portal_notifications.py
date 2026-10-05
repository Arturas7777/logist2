"""Уведомления клиента о событиях кабинета (C5) и напоминания о просрочке (B8).

Клиенту на email и в Telegram уходят только планируемая разгрузка и разгрузка.
События кабинета (фото, счёт, статус заявки, передача авто, напоминания)
письмо не создают. История статусов авто и дело менеджеру по долгой
просрочке остаются.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import Car, CarStatusHistory, Client, Company, Container, Task, Warehouse
from core.models.billing import InvoiceItem, NewInvoice
from core.models.website import CarPhoto, ClientUser, ContainerPhoto, NotificationLog, TransportRequest
from core.services import client_notifications as cn
from core.tasks import check_overdue_invoices, escalate_overdue_invoices, send_invoice_due_reminders

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return Client.objects.create(name="Notify Client", email="notify@example.com")


@pytest.fixture
def portal_user(owner):
    user = User.objects.create_user(username="notify-client", password="secret123")
    return ClientUser.objects.create(user=user, client=owner, is_verified=True, language="ru")


def _car(owner, vin="NOTIFYVIN00000001", status="UNLOADED", **kwargs):
    return Car.objects.create(year=2022, brand="BMW", vin=vin, status=status, client=owner, **kwargs)


def _photo_upload(name="p.jpg"):
    return SimpleUploadedFile(name, b"\xff\xd8\xff\xd9" * 32, content_type="image/jpeg")


# ── Фото готовы ─────────────────────────────────────────────────────────────


def test_photos_ready_sent_once_per_container(owner, django_capture_on_commit_callbacks):
    container = Container.objects.create(number="PHOTOSRDY0001", status="IN_PORT")
    _car(owner, container=container)

    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        with django_capture_on_commit_callbacks(execute=True):
            ContainerPhoto.objects.create(container=container, photo=_photo_upload(), is_public=True)
            ContainerPhoto.objects.create(container=container, photo=_photo_upload("q.jpg"), is_public=True)

    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="PHOTOS_READY", container=container).exists()

    cn.notify_photos_ready(container=container)
    assert mail.outbox == []


def test_photos_ready_for_car_without_container(owner, django_capture_on_commit_callbacks):
    car = _car(owner, vin="CARPHOTOVIN000001")
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        with django_capture_on_commit_callbacks(execute=True):
            CarPhoto.objects.create(car=car, photo=_photo_upload(), is_public=True)
    assert mail.outbox == []


def test_private_photo_does_not_notify(owner, django_capture_on_commit_callbacks):
    container = Container.objects.create(number="PHOTOSPRIV001", status="IN_PORT")
    _car(owner, container=container)
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        with django_capture_on_commit_callbacks(execute=True):
            ContainerPhoto.objects.create(container=container, photo=_photo_upload(), is_public=False)
    assert mail.outbox == []


# ── Подписки ────────────────────────────────────────────────────────────────


def test_prefs_disable_email_channel(owner, portal_user):
    portal_user.notification_prefs = {"PHOTOS_READY": {"email": False, "telegram": True}}
    portal_user.save()
    container = Container.objects.create(number="PHOTOSPREF001", status="IN_PORT")
    _car(owner, container=container)
    with patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False):
        ContainerPhoto.objects.create(container=container, photo=_photo_upload(), is_public=True)

    cn.notify_photos_ready(container=container)
    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="PHOTOS_READY").exists()


def test_notification_settings_page_saves_prefs(client, owner, portal_user):
    client.force_login(portal_user.user)
    url = reverse("website:notification_settings")
    html = client.get(url).content.decode()
    assert "планируемая разгрузка" in html
    assert "PHOTOS_READY__email" not in html
    assert "CAR_TRANSFERRED" not in html


# ── Инвойс выставлен ────────────────────────────────────────────────────────


def _invoice(owner, company, amount="150.00", **kwargs):
    kwargs.setdefault("document_type", "INVOICE")
    kwargs.setdefault("status", "DRAFT")
    issue_date = timezone.now().date()
    due_date = kwargs.get("due_date")
    if due_date and due_date < issue_date:
        # Дата выставления не может быть позже срока оплаты.
        issue_date = due_date - timedelta(days=14)
    invoice = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=owner,
        date=issue_date,
        **kwargs,
    )
    InvoiceItem.objects.create(invoice=invoice, description="Услуги", quantity=1, unit_price=Decimal(amount))
    invoice.refresh_from_db()
    return invoice


def test_invoice_issued_notifies_once_with_pdf(owner, django_capture_on_commit_callbacks):
    company = Company.objects.create(name="Caromoto Lithuania")
    invoice = _invoice(owner, company)
    invoice.attachment.save("inv.pdf", SimpleUploadedFile("inv.pdf", b"%PDF-1.4 test", content_type="application/pdf"))

    with patch("core.tasks.push_invoice_to_sitepro_task.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            invoice.status = "ISSUED"
            invoice.save()

    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="INVOICE_ISSUED").exists()

    with patch("core.tasks.push_invoice_to_sitepro_task.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            invoice.notes = "edit"
            invoice.save()
    cn.notify_invoice_issued(invoice)
    assert mail.outbox == []


def test_proforma_issue_does_not_notify(owner, django_capture_on_commit_callbacks):
    company = Company.objects.create(name="Caromoto Lithuania")
    invoice = _invoice(owner, company, document_type="PROFORMA")
    with django_capture_on_commit_callbacks(execute=True):
        invoice.status = "ISSUED"
        invoice.save()
    assert mail.outbox == []


# ── Заявка сменила статус ───────────────────────────────────────────────────


def test_request_status_change_notifies_once(owner, portal_user, django_capture_on_commit_callbacks):
    tr = TransportRequest.objects.create(client=owner, truck_number="ABC123", status="SUBMITTED")
    with django_capture_on_commit_callbacks(execute=True):
        tr.status = "ACCEPTED"
        tr.save()
    assert mail.outbox == []

    with django_capture_on_commit_callbacks(execute=True):
        tr.staff_comment = "ok"
        tr.save()
    cn.notify_request_status(tr)
    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="REQUEST_STATUS", transport_request=tr).exists()


def test_new_request_does_not_notify(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        TransportRequest.objects.create(client=owner, truck_number="ABC123", status="SUBMITTED")
    assert mail.outbox == []


# ── Авто передано + история статусов ────────────────────────────────────────


def test_car_transferred_notifies_once_and_records_history(owner, django_capture_on_commit_callbacks):
    warehouse = Warehouse.objects.create(name="WH")
    car = _car(owner, vin="TRANSFERVIN000001", status="UNLOADED", warehouse=warehouse)
    assert list(CarStatusHistory.objects.filter(car=car).values_list("status", flat=True)) == ["UNLOADED"]

    with django_capture_on_commit_callbacks(execute=True):
        car.status = "TRANSFERRED"
        car.save()

    assert mail.outbox == []
    history = list(CarStatusHistory.objects.filter(car=car).order_by("changed_at", "id"))
    assert [h.status for h in history] == ["UNLOADED", "TRANSFERRED"]
    assert history[-1].previous_status == "UNLOADED"

    # Повторное сохранение без смены статуса — ни письма, ни записи.
    with django_capture_on_commit_callbacks(execute=True):
        car.save(update_fields=["transfer_date"])
    cn.notify_car_transferred(car)
    assert mail.outbox == []
    assert CarStatusHistory.objects.filter(car=car).count() == 2


def test_car_detail_timeline_uses_history_dates(client, owner, portal_user):
    car = _car(owner, vin="HISTORYVIN0000001", status="FLOATING")
    car.status = "IN_PORT"
    car.save()
    client.force_login(portal_user.user)
    html = client.get(reverse("website:car_detail", args=[car.id])).content.decode()
    today = timezone.localdate().strftime("%d.%m.%Y")
    assert "status-timeline__step is-done" in html
    assert today in html


# ── B8: напоминания и эскалация ─────────────────────────────────────────────


def test_due_soon_reminder_sent_once(owner):
    company = Company.objects.create(name="Caromoto Lithuania")
    today = timezone.now().date()
    invoice = _invoice(owner, company, status="ISSUED", due_date=today + timedelta(days=3))
    # Не-PARDP и другие сроки не трогаем.
    _invoice(owner, company, status="ISSUED", due_date=today + timedelta(days=3), document_type="PROFORMA")
    _invoice(owner, company, status="ISSUED", due_date=today + timedelta(days=5))

    stats = send_invoice_due_reminders.apply().get()
    assert stats["due_soon"] == 0
    assert mail.outbox == []
    assert invoice.number

    stats = send_invoice_due_reminders.apply().get()
    assert stats["due_soon"] == 0
    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="INVOICE_DUE_SOON").exists()


def test_overdue_notification_and_escalation(owner):
    company = Company.objects.create(name="Caromoto Lithuania")
    today = timezone.now().date()
    old = _invoice(owner, company, status="ISSUED", due_date=today - timedelta(days=20))
    fresh = _invoice(owner, company, status="ISSUED", due_date=today - timedelta(days=2))
    # Статусы проставляет штатная задача — так же, как на проде перед напоминаниями.
    assert check_overdue_invoices.apply().get() == 2
    assert NewInvoice.objects.filter(pk__in=[old.pk, fresh.pk], status="OVERDUE").count() == 2

    stats = send_invoice_due_reminders.apply().get()
    assert stats["overdue"] == 0
    assert mail.outbox == []

    created = escalate_overdue_invoices.apply().get()
    assert created == 1
    task = Task.objects.get(title__contains=old.number)
    assert task.priority == "HIGH"
    assert not task.is_completed
    assert not Task.objects.filter(title__contains=fresh.number).exists()

    # Повторный запуск — ни писем, ни дел.
    assert escalate_overdue_invoices.apply().get() == 0
    stats = send_invoice_due_reminders.apply().get()
    assert stats["overdue"] == 0
    assert mail.outbox == []


def test_reminders_respect_prefs(owner, portal_user):
    portal_user.notification_prefs = {"INVOICE_DUE_SOON": {"email": False, "telegram": False}}
    portal_user.save()
    company = Company.objects.create(name="Caromoto Lithuania")
    _invoice(owner, company, status="ISSUED", due_date=timezone.now().date() - timedelta(days=1))
    check_overdue_invoices.apply().get()
    send_invoice_due_reminders.apply().get()
    assert mail.outbox == []


# ── Telegram-канал ──────────────────────────────────────────────────────────


def test_telegram_channel_sends_and_logs(owner, settings):
    settings.TELEGRAM_BOT_TOKEN = "123:abc"
    settings.TELEGRAM_NOTIFICATIONS_ENABLED = True
    owner.telegram_chat_id = "42"
    owner.save()
    car = _car(owner, vin="TGTRANSFERVIN0001", status="TRANSFERRED", transfer_date=date(2026, 10, 1))

    with patch("core.services.client_notifications.send_telegram_message", return_value=(True, "")) as tg:
        result = cn.notify_car_transferred(car)
    assert result == {"email": 0, "telegram": 0}
    assert tg.call_count == 0
    assert mail.outbox == []
    assert not NotificationLog.objects.filter(notification_type="CAR_TRANSFERRED").exists()


# ── Регистрация: дело менеджеру ─────────────────────────────────────────────


def test_registration_creates_manager_task_and_onboarding_banner(client):
    response = client.post(
        reverse("website:register"),
        {
            "name": "New Buyer LLC",
            "email": "buyer@example.com",
            "phone": "+37060000000",
            "username": "newbuyer",
            "password1": "Str0ngPassw0rd!",
            "password2": "Str0ngPassw0rd!",
        },
    )
    assert response.status_code == 302
    task = Task.objects.get(title__contains="New Buyer LLC")
    assert "buyer@example.com" in task.description
    html = client.get(reverse("website:dashboard")).content.decode()
    assert "Кабинет будет активирован менеджером" in html


def test_password_reset_flow_sends_email(client):
    User.objects.create_user(username="reset-me", email="reset@example.com", password="secret123")
    html = client.get(reverse("website:login")).content.decode()
    assert reverse("website:password_reset") in html

    response = client.post(reverse("website:password_reset"), {"email": "reset@example.com"})
    assert response.status_code == 302
    assert response.url == reverse("website:password_reset_done")
    assert len(mail.outbox) == 1
    assert "password-reset/" in mail.outbox[0].body
    assert mail.outbox[0].alternatives, "должна быть HTML-версия письма"
