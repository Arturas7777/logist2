"""Тесты администрирования заявок на автовоз.

Покрытие:

* доска заявок и карточка доступны сотруднику, закрыты для клиента;
* переписка: сообщение сотрудника, ответ клиента, счётчики непрочитанного;
* запрос документов включает ``awaiting_client_docs`` и открывает клиенту
  правку заявки, ушедшей в работу;
* чеклист полноты пакета: недостающие типы зависят от типа декларации;
* декларации заявки: разбивка машин по отдельным декларациям, переезд авто
  между ними, влияние типа на обязательные документы;
* адресаты склада (общая почта + email-контакты) и рендер LT-письма;
* матчинг писем: ответ склада в треде и письмо с номером заявки в теме
  привязываются к заявке, чужое письмо — нет;
* создание рейса из заявки (сопоставление перевозчика по EORI).
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Car, Client
from core.models.carriers import Carrier
from core.models.website import ClientUser, TransportRequest, TransportRequestMessage

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouse(db):
    from core.models.warehouses import Warehouse

    return Warehouse.objects.create(name="Klaipeda Terminal", general_email="terminal@example.lt")


@pytest.fixture
def portal_client(db):
    return Client.objects.create(name="Test Portal Client")


@pytest.fixture
def car(warehouse, portal_client):
    return Car.objects.create(
        year=2023,
        brand="Chevrolet Malibu",
        vin="1G1ZD5ST0PF171248",
        status="UNLOADED",
        client=portal_client,
        warehouse=warehouse,
    )


@pytest.fixture
def second_car(warehouse, portal_client, transport_request):
    car = Car.objects.create(
        year=2022,
        brand="Audi A6",
        vin="WAUZZZ4G0DN000001",
        status="UNLOADED",
        client=portal_client,
        warehouse=warehouse,
    )
    transport_request.cars.add(car)
    return car


@pytest.fixture
def transport_request(car, portal_client):
    tr = TransportRequest.objects.create(
        client=portal_client,
        carrier_name="MAXER TRANSPORT Sp. z.o.o.",
        carrier_eori="PL123456789",
        truck_number="ABC123",
        trailer_number="XYZ789",
        driver_name="Иванов Иван",
        driver_phone="+37060000000",
        border_crossing="Medininkai",
        planned_loading_date=datetime.date(2026, 9, 1),
        destination_country="BY",
        declaration_type="TRANSIT",
        status="SUBMITTED",
    )
    tr.cars.add(car)
    return tr


@pytest.fixture
def staff_client(client):
    user = User.objects.create_user(username="staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def portal_user(portal_client):
    user = User.objects.create_user(username="portal", password="secret123")
    ClientUser.objects.create(user=user, client=portal_client, is_verified=True)
    return user


@pytest.fixture
def logged_client(client, portal_user):
    client.force_login(portal_user)
    return client


# ---------------------------------------------------------------------------
# Доступ к доске
# ---------------------------------------------------------------------------


def test_board_available_for_staff(staff_client, transport_request):
    response = staff_client.get(reverse("admin_requests_board"))
    assert response.status_code == 200
    assert transport_request.number in response.content.decode()


def test_board_search_by_vin(staff_client, transport_request, car):
    response = staff_client.get(reverse("admin_requests_board"), {"tab": "all", "q": car.vin})
    assert response.status_code == 200
    assert transport_request.number in response.content.decode()


def test_board_closed_for_client(logged_client):
    response = logged_client.get(reverse("admin_requests_board"))
    assert response.status_code in (302, 403)


def test_card_available_for_staff(staff_client, transport_request):
    response = staff_client.get(reverse("admin_request_card", args=[transport_request.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert transport_request.number in body
    assert "Переписка со складом" in body
    assert "Общая декларация заявки" in body
    assert "Страна назначения" in body
    # Пакет одним файлом разбирается AI прямо из карточки.
    assert "Разобрать AI" in body


def test_card_shows_separate_declaration(staff_client, transport_request, car, second_car):
    from core.services import transport_declarations as decl

    decl.create_group(transport_request, "EXPORT", [second_car.pk], note="отдельно на экспорт")
    body = staff_client.get(reverse("admin_request_card", args=[transport_request.pk])).content.decode()

    assert "Отдельная декларация" in body
    assert "отдельно на экспорт" in body
    assert "№1 · Экспортная" in body


# ---------------------------------------------------------------------------
# Переписка и счётчики
# ---------------------------------------------------------------------------


def test_staff_message_and_unread_counters(staff_client, transport_request, monkeypatch):
    from core.services import transport_request_notify

    monkeypatch.setattr(
        transport_request_notify,
        "notify_client_about_message",
        lambda message, user=None: {"email": 1, "telegram": 1},
    )
    url = reverse("admin_request_message_send", args=[transport_request.pk])
    response = staff_client.post(url, {"body": "Не хватает паспорта покупателя."})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # Своё сообщение сотрудник уже «прочитал», клиент — ещё нет.
    assert transport_request.unread_messages_for_staff() == 0
    assert transport_request.unread_messages_for_client() == 1


def test_doc_request_enables_client_editing(staff_client, transport_request, monkeypatch):
    from core.services import transport_request_notify

    monkeypatch.setattr(
        transport_request_notify,
        "notify_client_about_message",
        lambda message, user=None: {"email": 0, "telegram": 0},
    )
    transport_request.status = "IN_PROGRESS"
    transport_request.save(update_fields=["status"])
    assert transport_request.is_client_editable is False

    staff_client.post(
        reverse("admin_request_message_send", args=[transport_request.pk]),
        {"body": "Нужен паспорт", "requested_doc_types": ["PASSPORT"]},
    )
    transport_request.refresh_from_db()
    assert transport_request.awaiting_client_docs is True
    assert transport_request.is_client_editable is True
    assert transport_request.pending_requested_doc_types() == ["PASSPORT"]


def test_client_reply_marks_staff_messages_read(logged_client, transport_request):
    TransportRequestMessage.objects.create(
        request=transport_request,
        author_kind=TransportRequestMessage.AUTHOR_STAFF,
        body="Поправьте номер прицепа",
    )
    assert transport_request.unread_messages_for_client() == 1

    response = logged_client.post(
        reverse("website:transport_request_message_send", args=[transport_request.pk]),
        {"body": "Исправил, спасибо"},
    )
    assert response.status_code == 200
    assert transport_request.unread_messages_for_client() == 0
    assert transport_request.unread_messages_for_staff() == 1


def test_card_view_marks_client_messages_read(staff_client, transport_request):
    TransportRequestMessage.objects.create(
        request=transport_request,
        author_kind=TransportRequestMessage.AUTHOR_CLIENT,
        body="Загрузил документы",
    )
    assert transport_request.unread_messages_for_staff() == 1
    staff_client.get(reverse("admin_request_card", args=[transport_request.pk]))
    assert transport_request.unread_messages_for_staff() == 0


# ---------------------------------------------------------------------------
# Полнота пакета документов
# ---------------------------------------------------------------------------


def test_transit_requires_full_package(transport_request, car):
    from core.services.transport_request_check import check_request

    readiness = check_request(transport_request)
    assert not readiness.is_complete
    missing = readiness.cars[0].missing
    assert "PASSPORT" in missing
    assert "CONTRACT" in missing


def test_export_requires_less_than_transit(transport_request, car):
    from core.services.transport_request_check import check_request

    transport_request.declaration_type = "EXPORT"
    transport_request.save(update_fields=["declaration_type"])
    readiness = check_request(transport_request)
    assert readiness.cars[0].missing == ["TITLE", "PASSPORT", "INVOICE"]


def test_separate_declaration_overrides_request_type(staff_client, transport_request, car):
    url = reverse("admin_request_declarations", args=[transport_request.pk])
    response = staff_client.post(url, {"action": "add", "declaration_type": "EXPORT", "cars": [car.pk]})
    assert response.status_code == 302
    assert transport_request.declaration_groups.count() == 1
    assert transport_request.declaration_types_by_car()[car.pk] == "EXPORT"


def test_declaration_group_moves_car_between_declarations(staff_client, transport_request, car, second_car):
    from core.services import transport_declarations as decl

    first = decl.create_group(transport_request, "EXPORT", [car.pk, second_car.pk])
    second = decl.create_group(transport_request, "TRANSIT", [second_car.pk])

    # Авто входит только в одну декларацию — из первой оно уехало.
    assert list(first.cars.values_list("pk", flat=True)) == [car.pk]
    assert list(second.cars.values_list("pk", flat=True)) == [second_car.pk]

    types = transport_request.declaration_types_by_car()
    assert types[car.pk] == "EXPORT"
    assert types[second_car.pk] == "TRANSIT"


def test_declaration_plan_groups_rest_under_request_type(transport_request, car, second_car):
    from core.services import transport_declarations as decl

    decl.create_group(transport_request, "EXPORT", [second_car.pk])
    plan = decl.declaration_plan(transport_request)

    assert len(plan) == 2
    assert [line.declaration_type for line in plan] == ["EXPORT", "TRANSIT"]
    assert plan[0].cars == [second_car]
    assert plan[1].is_default
    assert plan[1].cars == [car]


def test_delete_declaration_returns_cars_to_request_type(staff_client, transport_request, car):
    from core.services import transport_declarations as decl

    group = decl.create_group(transport_request, "EXPORT", [car.pk])
    url = reverse("admin_request_declarations", args=[transport_request.pk])
    response = staff_client.post(url, {"action": "delete", "group": group.pk})

    assert response.status_code == 302
    assert transport_request.declaration_groups.count() == 0
    assert transport_request.declaration_types_by_car()[car.pk] == "TRANSIT"


def test_declaration_type_changes_required_docs_per_car(transport_request, car, second_car):
    from core.services import transport_declarations as decl
    from core.services.transport_request_check import check_request

    decl.create_group(transport_request, "EXPORT", [second_car.pk])
    readiness = check_request(transport_request)
    by_vin = {status.car.vin: status for status in readiness.cars}

    assert "CONTRACT" in by_vin[car.vin].missing
    assert by_vin[second_car.vin].missing == ["TITLE", "PASSPORT", "INVOICE"]


def test_car_removed_from_request_leaves_declaration(staff_client, transport_request, car, second_car):
    from core.services import transport_declarations as decl

    group = decl.create_group(transport_request, "EXPORT", [car.pk, second_car.pk])
    staff_client.post(
        reverse("admin_request_car_toggle", args=[transport_request.pk]),
        {"action": "remove", "car": second_car.pk},
    )
    assert list(group.cars.values_list("pk", flat=True)) == [car.pk]


def test_client_form_offers_country_and_procedure(portal_client):
    from core.views_website.forms import TransportRequestForm

    form = TransportRequestForm(client=portal_client)
    procedure = form.fields["declaration_type"]
    country = form.fields["destination_country"]

    assert procedure.required and country.required
    assert [code for code, _label in procedure.choices] == ["", "TRANSIT", "EXPORT", "IMPORT", "REEXPORT"]
    assert [code for code, _label in country.choices] == ["", "BY", "MD", "UA"]
    # Процедуру по умолчанию подставляет JS — маппинг уходит в data-атрибут.
    assert '"UA": "REEXPORT"' in country.widget.attrs["data-default-procedures"]


def test_required_docs_come_from_admin_rule(transport_request, car):
    from core.models.website import TransportDocumentRule
    from core.services.transport_request_check import check_request

    transport_request.destination_country = "MD"
    transport_request.save(update_fields=["destination_country"])
    TransportDocumentRule.objects.update_or_create(
        country="MD",
        procedure="TRANSIT",
        defaults={"required_doc_types": ["PASSPORT", "CONTRACT"]},
    )

    readiness = check_request(transport_request)
    # Тайтл добавляется к любому правилу — без него пакет не примут.
    assert readiness.cars[0].missing == ["TITLE", "PASSPORT", "CONTRACT"]


def test_required_docs_fall_back_when_rule_missing(transport_request, car):
    from core.models.website import TransportDocumentRule
    from core.services.transport_request_check import check_request

    transport_request.destination_country = "UA"
    transport_request.save(update_fields=["destination_country"])
    TransportDocumentRule.objects.filter(country="UA", procedure="TRANSIT").delete()

    readiness = check_request(transport_request)
    assert "CONTRACT" in readiness.cars[0].missing


def test_missing_country_is_a_blocker(transport_request):
    from core.services.transport_request_check import check_request

    transport_request.destination_country = ""
    transport_request.save(update_fields=["destination_country"])
    readiness = check_request(transport_request)
    assert any("страна" in blocker.lower() for blocker in readiness.blockers)


def test_title_is_required_for_every_procedure():
    from core.services.transport_request_check import required_doc_types

    for procedure in ("TRANSIT", "EXPORT", "IMPORT", "REEXPORT", ""):
        assert "TITLE" in required_doc_types(procedure, "BY")


def test_title_from_car_closes_the_gap(transport_request, car, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.services.transport_request_check import check_request
    from core.views_website.portal_transport import _docs_context

    settings.MEDIA_ROOT = str(tmp_path)
    car.title_scan.save("title.pdf", SimpleUploadedFile("title.pdf", b"%PDF-1.4 fake"))
    car.save(update_fields=["title_scan"])

    assert "TITLE" in check_request(transport_request).cars[0].missing
    _docs_context(transport_request)  # открытие кабинета прикрепляет тайтл
    assert "TITLE" not in check_request(transport_request).cars[0].missing


def test_client_portal_offers_title_slot(logged_client, transport_request):
    body = logged_client.get(reverse("website:transport_requests")).content.decode()

    assert "docModal-TITLE" in body
    assert "Тайтл" in body


def test_missing_warehouse_is_a_blocker(transport_request, car):
    from core.services.transport_request_check import check_request

    car.warehouse = None
    car.save(update_fields=["warehouse"])
    readiness = check_request(transport_request)
    assert not readiness.can_send_to_warehouse
    assert any("склад" in blocker for blocker in readiness.blockers)


# ---------------------------------------------------------------------------
# Письмо складу
# ---------------------------------------------------------------------------


def test_warehouse_letter_page_renders(staff_client, transport_request, warehouse):
    response = staff_client.get(reverse("admin_request_warehouse_letter", args=[transport_request.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "terminal@example.lt" in body
    assert "tranzito deklaraciją (T1)" in body


def test_client_portal_shows_messages_block(logged_client, transport_request):
    TransportRequestMessage.objects.create(
        request=transport_request,
        author_kind=TransportRequestMessage.AUTHOR_STAFF,
        kind=TransportRequestMessage.KIND_DOC_REQUEST,
        body="Загрузите паспорт покупателя",
        requested_doc_types=["PASSPORT"],
    )
    response = logged_client.get(reverse("website:transport_requests"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Загрузите паспорт покупателя" in body
    assert "req-msg-thread" in body
    # Внутренняя работа со складом клиенту не показывается.
    assert "Переписка со складом" not in body


def test_client_portal_offers_bulk_upload_instead_of_other(logged_client, transport_request):
    body = logged_client.get(reverse("website:transport_requests")).content.decode()

    assert "Одним файлом" in body
    assert "docModal-BULK" in body
    # Ручной слот «Остальное» клиенту больше не предлагаем — только через AI.
    assert "docModal-OTHER" not in body


def test_client_portal_shows_country_and_procedure(logged_client, transport_request):
    body = logged_client.get(reverse("website:transport_requests")).content.decode()

    assert "id_destination_country" in body
    assert "data-default-procedures" in body
    assert "Таможенная процедура" in body


def test_warehouse_letter_mentions_destination_country(transport_request, warehouse, car):
    from core.services.warehouse_request_email import build_letter_draft

    car.warehouse = warehouse
    car.save(update_fields=["warehouse"])
    draft = build_letter_draft(transport_request, warehouse)

    assert "Baltarusija" in draft.body_text


def test_resolve_warehouse_recipients_includes_general_email(warehouse):
    from core.services.warehouse_request_email import resolve_warehouse_recipients

    assert "terminal@example.lt" in resolve_warehouse_recipients(warehouse)


def test_resolve_warehouse_recipients_dedupes_contacts(warehouse):
    from django.contrib.contenttypes.models import ContentType

    from core.models.contact import Contact, ContactEmail
    from core.models.warehouses import Warehouse
    from core.services.warehouse_request_email import resolve_warehouse_recipients

    contact = Contact.objects.create(
        content_type=ContentType.objects.get_for_model(Warehouse),
        object_id=warehouse.pk,
        name="Import Manager",
    )
    ContactEmail.objects.create(contact=contact, email="Terminal@Example.LT", is_primary=True)
    ContactEmail.objects.create(contact=contact, email="docs@example.lt")

    recipients = resolve_warehouse_recipients(warehouse)
    assert len(recipients) == 2
    assert "docs@example.lt" in recipients


def test_letter_draft_is_lithuanian(transport_request, warehouse, car):
    from core.services.warehouse_request_email import build_letter_draft

    draft = build_letter_draft(transport_request, warehouse)
    assert draft.subject.startswith("Autovežio pakrovimas")
    body = draft.body_text
    assert "tranzito deklaraciją (T1)" in body
    assert transport_request.number in draft.subject
    assert car.vin in body
    assert "Vilkikas: ABC123" in body
    assert "prašome" in body.lower()


def test_letter_draft_lists_types_per_car(transport_request, warehouse, car, second_car):
    from core.services import transport_declarations as decl
    from core.services.warehouse_request_email import build_letter_draft

    decl.create_group(transport_request, "EXPORT", [second_car.pk])

    body = build_letter_draft(transport_request, warehouse).body_text
    assert "tranzito deklaraciją (T1)" in body
    assert "eksporto deklaraciją" in body


def test_letter_draft_lists_declarations_with_cars(transport_request, warehouse, car, second_car):
    from core.services import transport_declarations as decl
    from core.services.warehouse_request_email import build_letter_draft

    decl.create_group(transport_request, "EXPORT", [second_car.pk])
    draft = build_letter_draft(transport_request, warehouse)

    assert [item.type_lt for item in draft.declarations] == [
        "eksporto deklaracija",
        "tranzito deklaracija (T1)",
    ]
    assert draft.declarations[0].vins == second_car.vin
    body = draft.body_text
    assert "Reikalingos deklaracijos (2)" in body
    assert "deklaracija Nr. 2" in body


def test_send_letter_marks_request_sent(transport_request, warehouse, monkeypatch):
    from core.services import warehouse_request_email as wh

    sent = {}

    def fake_compose(**kwargs):
        sent.update(kwargs)

        class FakeEmail:
            pk = 1

        return FakeEmail()

    monkeypatch.setattr("core.services.email_compose.compose_new_email_from_transport_request", fake_compose)

    draft = wh.build_letter_draft(transport_request, warehouse)
    wh.send_letter(
        transport_request=transport_request,
        warehouse=warehouse,
        user=None,
        to="terminal@example.lt",
        subject=draft.subject,
        body_text=draft.body_text,
        car_ids=[],
    )
    transport_request.refresh_from_db()
    assert transport_request.warehouse_state == TransportRequest.WAREHOUSE_SENT
    assert transport_request.sent_to_warehouse_at is not None
    assert transport_request.warehouse_id == warehouse.pk
    # Клиент видит только свой статус — новых значений в STATUS_CHOICES нет.
    assert transport_request.status in dict(TransportRequest.STATUS_CHOICES)
    assert sent["to"] == "terminal@example.lt"


# ---------------------------------------------------------------------------
# Матчинг писем на заявку
# ---------------------------------------------------------------------------


def _make_email(**kwargs):
    from django.utils import timezone

    from core.models.email import ContainerEmail

    defaults = {
        "gmail_id": "gm-1",
        "thread_id": "th-1",
        "message_id": "<m1@example.lt>",
        "direction": "INCOMING",
        "from_addr": "terminal@example.lt",
        "to_addrs": "us@caromoto.lt",
        "subject": "Re: klausimas",
        "body_text": "Gerai, patvirtiname.",
        "received_at": timezone.now(),
    }
    defaults.update(kwargs)
    return ContainerEmail.objects.create(**defaults)


def test_match_by_request_number_in_subject(transport_request):
    from core.services.email_matcher import match_email_to_transport_requests

    email = _make_email(subject=f"Re: Autovežio pakrovimas — {transport_request.number}")
    hits = match_email_to_transport_requests(email)
    assert [hit.request_id for hit in hits] == [transport_request.pk]


def test_match_inherits_thread(transport_request):
    from core.models.email import ContainerEmail, TransportRequestEmailLink
    from core.services.email_matcher import match_email_to_transport_requests

    outgoing = _make_email(gmail_id="gm-out", direction="OUTGOING", subject="Užklausa")
    TransportRequestEmailLink.objects.create(
        email=outgoing,
        request=transport_request,
        matched_by=ContainerEmail.MATCHED_BY_MANUAL,
        is_read=True,
    )
    reply = _make_email(gmail_id="gm-reply", message_id="<m2@example.lt>", subject="Re: Užklausa")
    hits = match_email_to_transport_requests(reply)
    assert [hit.request_id for hit in hits] == [transport_request.pk]


def test_foreign_email_not_matched(transport_request):
    from core.services.email_matcher import match_email_to_transport_requests

    email = _make_email(gmail_id="gm-foreign", thread_id="th-other", subject="Sveiki, sąskaita")
    assert match_email_to_transport_requests(email) == []


# ---------------------------------------------------------------------------
# Рейс из заявки
# ---------------------------------------------------------------------------


def test_email_panel_endpoints_match_template_urls(staff_client, transport_request):
    """URL панели писем в шаблоне и в ``core/urls.py`` должны совпадать."""
    from core.models.email import ContainerEmail, TransportRequestEmailLink

    email = _make_email(gmail_id="gm-panel")
    TransportRequestEmailLink.objects.create(
        email=email,
        request=transport_request,
        matched_by=ContainerEmail.MATCHED_BY_THREAD,
        is_read=False,
    )

    mark_read = f"/core/emails/transport-request/{transport_request.pk}/mark-all-read/"
    updates = f"/core/emails/transport-request/{transport_request.pk}/updates/"
    assert reverse("core:email_mark_transportrequest_read", args=[transport_request.pk]) == mark_read
    assert reverse("core:email_transportrequest_updates", args=[transport_request.pk]) == updates

    # В шаблоне URL собирается из префикса и id на стороне JS.
    panel = staff_client.get(reverse("admin_request_card", args=[transport_request.pk])).content.decode()
    assert "/core/emails/transport-request/" in panel

    assert staff_client.post(mark_read).status_code == 200
    assert TransportRequestEmailLink.objects.get(email=email, request=transport_request).is_read is True
    assert staff_client.get(updates, {"since_id": 0}).status_code == 200


def test_carrier_matched_by_eori(transport_request):
    from core.services.transport_request_autotransport import match_carrier

    carrier = Carrier.objects.create(name="Maxer Transport", eori_code="PL123456789")
    match = match_carrier(transport_request)
    assert match.carrier == carrier
    assert match.matched_by == "EORI"


def test_create_autotransport_transfers_data(transport_request, car):
    from core.services.transport_request_autotransport import create_autotransport

    carrier = Carrier.objects.create(name="Maxer Transport", eori_code="PL123456789")
    auto_transport = create_autotransport(transport_request, carrier=carrier)

    transport_request.refresh_from_db()
    assert transport_request.auto_transport_id == auto_transport.pk
    assert auto_transport.truck_number_manual == "ABC123"
    assert auto_transport.trailer_number_manual == "XYZ789"
    assert auto_transport.driver_name_manual == "Иванов Иван"
    assert auto_transport.border_crossing == "Medininkai"
    assert auto_transport.loading_date == datetime.date(2026, 9, 1)
    assert list(auto_transport.cars.all()) == [car]


def test_create_autotransport_requires_confirmation_for_new_carrier(transport_request):
    from core.services.transport_request_autotransport import (
        AutoTransportBuildError,
        create_autotransport,
    )

    with pytest.raises(AutoTransportBuildError, match="не найден"):
        create_autotransport(transport_request)

    auto_transport = create_autotransport(transport_request, create_carrier=True)
    assert auto_transport.carrier.eori_code == "PL123456789"
