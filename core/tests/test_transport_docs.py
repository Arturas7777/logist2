"""Тесты пакета документов автовоза (оформление на Беларусь).

Покрытие:

* рабочие дни: выходные и праздники (США/Беларусь) сдвигаются вперёд;
* платёжка и прочие документы не могут быть раньше даты инвойса;
* сумма прописью для платёжки (RU);
* генерация PDF: инвойс/платёжка/письмо/обязательство/договор дают валидный PDF;
* зависимости: инвойс без данных покупателя и платёжка без инвойса — ошибка;
* портал: сохранение данных паспорта (адрес обязателен), загрузка и удаление
  файлов, генерация через вьюху, номер заявки скрыт из кабинета;
* AI-паспорт: нормализация ответа модели, автозаполнение пустых полей пакета
  и транслитерация адреса (Claude мокается — сети в тестах нет).
"""

from __future__ import annotations

import datetime
import io
import re
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Car, Client
from core.models.website import ClientUser, TransportRequest, TransportRequestDocument
from core.services import transport_docs as docs
from core.services.transport_docs import PackageDataError
from core.services.transport_docs_pdf import amount_in_words_ru

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_ai_key(monkeypatch):
    """Тесты не должны ходить в Anthropic — ключ убирается из окружения."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Рабочие дни и даты
# ---------------------------------------------------------------------------


def test_weekend_shifts_forward():
    saturday = datetime.date(2026, 8, 15)
    assert docs.shift_to_business_day(saturday, ("US",)) == datetime.date(2026, 8, 17)


def test_us_holiday_shifts_forward():
    new_year = datetime.date(2027, 1, 1)  # пятница, праздник в США
    assert not docs.is_business_day(new_year, ("US",))
    assert docs.shift_to_business_day(new_year, ("US",)) == datetime.date(2027, 1, 4)


def test_by_holiday_not_business_day():
    independence_day = datetime.date(2026, 7, 3)  # День Независимости РБ, пятница
    assert not docs.is_business_day(independence_day, ("BY",))


def test_document_not_before_invoice():
    invoice_date = datetime.date(2026, 8, 12)
    with pytest.raises(PackageDataError):
        docs.resolve_document_date("PAYMENT_ORDER", datetime.date(2026, 8, 10), invoice_date)


def test_pick_payment_date_within_month_after_invoice():
    invoice_date = datetime.date(2026, 8, 12)
    for _ in range(20):
        day = docs.pick_payment_date(invoice_date)
        assert invoice_date <= day <= invoice_date + datetime.timedelta(days=30)
        assert docs.is_business_day(day, ("BY",))


def test_amount_in_words_ru():
    assert amount_in_words_ru(Decimal("2850")) == "Две тысячи восемьсот пятьдесят долларов США ноль центов"
    assert amount_in_words_ru(Decimal("21.05")) == "Двадцать один доллар США пять центов"


# ---------------------------------------------------------------------------
# Генерация документов
# ---------------------------------------------------------------------------


@pytest.fixture
def car(db):
    return Car.objects.create(year=2023, brand="Chevrolet Malibu", vin="1G1ZD5ST0PF171248", status="UNLOADED")


@pytest.fixture
def transport_request(car, portal_client):
    tr = TransportRequest.objects.create(
        client=portal_client,
        carrier_name="MAXER TRANSPORT Sp. z.o.o.",
        carrier_eori="PL123456789",
        truck_number="ABC123",
        driver_name="Иванов Иван",
        status="DRAFT",
    )
    tr.cars.add(car)
    return tr


@pytest.fixture
def portal_client(db):
    return Client.objects.create(name="Test Portal Client")


BUYER_DATA = {
    "buyer_name": "ZIZIKA ULADZIMIR",
    "buyer_name_ru": "Зизико Владимир Константинович",
    "buyer_passport_number": "MC3902087",
    "buyer_address": "ul. Gaya 5, d.Bolshaya lysitsa, Nesvizhskiy r-on, Belarus",
    "buyer_address_ru": "д. Большая Лысица, Несвижского р-на, ул. Гая 5",
    "buyer_birth_date": "1967-01-29",
    "buyer_passport_issue_date": "2025-10-22",
}


def test_generate_invoice_needs_buyer(transport_request, car):
    with pytest.raises(PackageDataError, match="Паспорт"):
        docs.generate_document(transport_request, car, {"invoice_amount": "2850"}, "INVOICE")


def test_generate_invoice(transport_request, car):
    data = {**BUYER_DATA, "invoice_amount": "2850", "invoice_date": "2026-08-12"}
    filename, pdf_bytes, _ = docs.generate_document(transport_request, car, data, "INVOICE")
    assert pdf_bytes.startswith(b"%PDF")
    assert filename == "INVOICE MALIBU 1248.pdf"
    assert data["invoice_number"]  # номер присвоен автоматически


def test_invoice_total_with_extra_lines():
    data = {
        "invoice_amount": "2850",
        "invoice_extra_lines": [
            {"description": "Delivery", "amount": "150"},
            {"description": "Fee", "amount": "50.5"},
        ],
    }
    assert docs.invoice_total_amount(data) == Decimal("3050.5")
    lines = docs.parse_invoice_extra_lines(data)
    assert len(lines) == 2
    assert lines[0]["description"] == "Delivery"
    assert lines[0]["amount"] == Decimal("150")


def test_generate_invoice_with_extra_lines(transport_request, car):
    import fitz

    data = {
        **BUYER_DATA,
        "invoice_amount": "2850",
        "invoice_date": "2026-08-12",
        "invoice_extra_lines": [
            {"description": "Auction fee", "amount": "150"},
            {"description": "Loading", "amount": "50"},
        ],
    }
    _, pdf_bytes, _ = docs.generate_document(transport_request, car, data, "INVOICE")
    page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    text = page.get_text()
    assert "Auction fee" in text
    assert "Loading" in text
    assert "$150" in text
    assert "$50" in text
    assert "$3,050" in text  # 2850 + 150 + 50


def test_payment_order_uses_invoice_total_with_extras(transport_request, car):
    import fitz

    data = {
        **BUYER_DATA,
        "invoice_number": "32545",
        "invoice_date": "2026-08-12",
        "invoice_amount": "2850",
        "invoice_extra_lines": [{"description": "Fee", "amount": "150"}],
    }
    _, pdf_bytes, _ = docs.generate_document(transport_request, car, data, "PAYMENT_ORDER")
    text = fitz.open(stream=pdf_bytes, filetype="pdf")[0].get_text()
    # платёжка берёт итоговую сумму (2850+150), не только цену авто
    assert "Три тысячи" in text
    assert amount_in_words_ru(Decimal("3000")).split()[0] in text
    assert "две тысячи восемьсот" not in text.lower()


def test_invoice_extra_lines_saved_via_view(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA},
    )
    response = logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "2026-08-12",
            "invoice_extra_desc": ["Auction fee", "Loading"],
            "invoice_extra_amount": ["150", "50"],
        },
    )
    assert response.status_code == 302
    package = transport_request.doc_packages.get(car=car)
    assert package.data["invoice_extra_lines"] == [
        {"description": "Auction fee", "amount": "150"},
        {"description": "Loading", "amount": "50"},
    ]
    doc = transport_request.documents.get(doc_type="INVOICE")
    import fitz

    text = fitz.open(stream=doc.file.read(), filetype="pdf")[0].get_text()
    assert "Auction fee" in text
    assert "$3,050" in text


def test_generate_payment_order_needs_invoice(transport_request, car):
    with pytest.raises(PackageDataError, match="инвойс"):
        docs.generate_document(transport_request, car, dict(BUYER_DATA), "PAYMENT_ORDER")


def test_generate_payment_order(transport_request, car):
    data = {
        **BUYER_DATA,
        "invoice_number": "32545",
        "invoice_date": "2026-08-12",
        "invoice_amount": "2850",
    }
    filename, pdf_bytes, notices = docs.generate_document(transport_request, car, data, "PAYMENT_ORDER")
    assert pdf_bytes[:4] == b"%PDF"
    import re

    import fitz

    page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    text = page.get_text()
    assert docs.DEFAULT_BELARUS_PAYER_IBAN in text.replace(" ", "")
    assert "BTA Bank" in text
    assert docs.DEFAULT_BELARUS_PAYER_BANK_CODE in text
    assert re.fullmatch(r"\d{4}-\d{1,4}", data["payment_number"])
    payment_date = datetime.date.fromisoformat(data["payment_date"])
    assert data["payment_number"].startswith(f"{payment_date:%d%m}-")
    invoice_date = datetime.date(2026, 8, 12)
    assert invoice_date <= payment_date <= invoice_date + datetime.timedelta(days=30)
    assert docs.is_business_day(payment_date, ("BY",))
    assert data["payer_bank_name"] == docs.DEFAULT_BELARUS_PAYER_BANK
    assert data["payer_bank_code"] == docs.DEFAULT_BELARUS_PAYER_BANK_CODE
    # Подпись клиента по умолчанию не ставится — предупреждения нет.
    assert not any("Подпись" in n for n in notices)
    # Печать исполнителя банка (PNG) встроена в PDF.
    assert page.get_images()

    # С галочкой, но без файла подписи — предупреждение.
    data_flag = {**data, "payment_include_signature": "1", "payment_number": "", "payment_date": ""}
    _, _, notices_flag = docs.generate_document(transport_request, car, data_flag, "PAYMENT_ORDER")
    assert any("Подпись" in n for n in notices_flag)


def test_bank_stamp_date_and_variety(tmp_path, monkeypatch):
    from core.services import bank_stamp

    monkeypatch.setattr(bank_stamp, "SIGNATURES_DIR", tmp_path / "sigs")
    day = datetime.date(2025, 11, 27)
    assert bank_stamp.format_stamp_date(day) == "27 НОЯ 2025"
    paths = bank_stamp.ensure_operator_signatures()
    # Без real-ассетов — fallback-набор процедурных подписей.
    assert len(paths) >= 8
    assert all(p.exists() and p.read_bytes()[:4] == b"\x89PNG" for p in paths)

    a = bank_stamp.compose_executor_stamp_field(day, field_width_pt=280, field_height_pt=150, payment_number="2711-24")
    b = bank_stamp.compose_executor_stamp_field(day, field_width_pt=280, field_height_pt=150, payment_number="2711-25")
    a2 = bank_stamp.compose_executor_stamp_field(day, field_width_pt=280, field_height_pt=150, payment_number="2711-24")
    assert a[:4] == b"\x89PNG"
    assert a == a2
    assert a != b
    # На самой печати (без поля) дата читается через OCR-подобный разбор пикселей —
    # проверяем, что PNG печати содержит непрозрачные пиксели синего чернила.
    stamp = bank_stamp.generate_bank_stamp_png(day, rng=bank_stamp.stamp_rng("1", day))
    from PIL import Image

    img = Image.open(io.BytesIO(stamp)).convert("RGBA")
    px = img.load()
    w, h = img.size
    ink = [px[x, y] for y in range(h) for x in range(w) if px[x, y][3] > 180]
    assert ink
    r, g, b, _a = ink[len(ink) // 2]
    assert b > r and b > g


def test_make_payment_number_unique_in_request():
    day = datetime.date(2026, 5, 20)
    used = set()
    for _ in range(30):
        number = docs.make_payment_number(day, used)
        assert number.startswith("2005-")
        assert number not in used
        used.add(number)


def test_pick_letter_usa_date_range_and_business_day():
    invoice = datetime.date(2026, 1, 5)
    today = datetime.date(2026, 4, 1)
    earliest = invoice + datetime.timedelta(weeks=3)
    latest = today - datetime.timedelta(weeks=1)
    for _ in range(40):
        day = docs.pick_letter_usa_date(invoice, today=today)
        assert earliest <= day <= latest
        assert docs.is_business_day(day, ("US",))


def test_pick_letter_usa_date_empty_window_raises():
    invoice = datetime.date(2026, 3, 20)
    today = datetime.date(2026, 4, 1)  # окно пустое: start > end
    with pytest.raises(PackageDataError, match="четырёх недель"):
        docs.pick_letter_usa_date(invoice, today=today)


def test_pick_obligation_date_last_three_weeks():
    today = datetime.date(2026, 8, 10)
    invoice = datetime.date(2026, 6, 1)
    earliest = today - datetime.timedelta(weeks=3)
    for _ in range(40):
        day = docs.pick_obligation_date(invoice, today=today)
        assert earliest <= day <= today
        assert day >= invoice
        assert docs.is_business_day(day, ("BY",))


def test_generate_letter_obligation_contract(transport_request, car):
    # Письмо USA требует «зрелый» инвойс (≥4 недель до сегодня).
    old_invoice = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    data = {**BUYER_DATA, "invoice_date": old_invoice}
    _, letter_pdf, _ = docs.generate_document(transport_request, car, dict(data), "LETTER_USA")
    assert letter_pdf.startswith(b"%PDF")
    # Остальные документы — с любой датой инвойса не раньше сегодняшней логики.
    data_recent = {**BUYER_DATA, "invoice_date": "2026-08-12"}
    for doc_type in ("OBLIGATION", "CONTRACT"):
        _, pdf_bytes, _ = docs.generate_document(transport_request, car, dict(data_recent), doc_type)
        assert pdf_bytes.startswith(b"%PDF"), doc_type


def test_generate_letter_usa_requires_invoice_date(transport_request, car):
    with pytest.raises(PackageDataError, match="инвойса"):
        docs.generate_document(transport_request, car, dict(BUYER_DATA), "LETTER_USA")


def test_upload_only_types_not_generatable(transport_request, car):
    with pytest.raises(PackageDataError):
        docs.generate_document(transport_request, car, {}, "PASSPORT")


# ---------------------------------------------------------------------------
# Вьюхи портала
# ---------------------------------------------------------------------------


@pytest.fixture
def portal_user(portal_client):
    user = User.objects.create_user(username="portal", password="secret123")
    ClientUser.objects.create(user=user, client=portal_client, is_verified=True)
    return user


@pytest.fixture
def logged_client(client, portal_user):
    client.force_login(portal_user)
    return client


def _doc_url(transport_request):
    return reverse("website:transport_request_doc_action", args=[transport_request.pk])


def test_number_hidden_on_portal(logged_client, transport_request):
    response = logged_client.get(reverse("website:transport_requests"))
    assert response.status_code == 200
    assert transport_request.number not in response.content.decode()


def _portal_request(portal_client, status, carrier_name):
    return TransportRequest.objects.create(
        client=portal_client,
        carrier_name=carrier_name,
        truck_number="ABC123",
        driver_name="Иванов Иван",
        status=status,
    )


def _req_card_class(html, pk):
    match = re.search(rf'<div class="([^"]+)"[^>]*id="req-{pk}"', html)
    assert match, html
    return match.group(1)


def test_portal_requests_default_tab_sorts_drafts_first(logged_client, portal_client):
    completed = _portal_request(portal_client, "COMPLETED", "Done Carrier")
    submitted = _portal_request(portal_client, "SUBMITTED", "Sent Carrier")
    accepted = _portal_request(portal_client, "ACCEPTED", "Ok Carrier")
    in_progress = _portal_request(portal_client, "IN_PROGRESS", "Work Carrier")
    draft = _portal_request(portal_client, "DRAFT", "Draft Carrier")
    cancelled = _portal_request(portal_client, "CANCELLED", "Gone Carrier")

    response = logged_client.get(reverse("website:transport_requests"))
    assert response.status_code == 200
    html = response.content.decode()

    assert response.context["active_tab"] == "current"
    assert 'data-tab="current"' in html
    assert "req-status-tabs" in html

    ids = re.findall(r'id="req-(\d+)"', html)
    assert ids == [str(draft.pk), str(submitted.pk), str(accepted.pk), str(in_progress.pk), str(completed.pk)]
    assert str(cancelled.pk) not in ids

    assert "d-none" not in _req_card_class(html, draft.pk)
    assert "d-none" not in _req_card_class(html, submitted.pk)
    assert "d-none" in _req_card_class(html, completed.pk)
    assert "request-card-DRAFT" in _req_card_class(html, draft.pk)
    assert "request-card-SUBMITTED" in _req_card_class(html, submitted.pk)
    assert "request-card-ACCEPTED" in _req_card_class(html, accepted.pk)
    assert "request-card-IN_PROGRESS" in _req_card_class(html, in_progress.pk)
    assert "request-card-COMPLETED" in _req_card_class(html, completed.pk)


def test_portal_requests_status_tab_hides_other_cards(logged_client, portal_client):
    draft = _portal_request(portal_client, "DRAFT", "Draft Carrier")
    submitted = _portal_request(portal_client, "SUBMITTED", "Sent Carrier")

    response = logged_client.get(reverse("website:transport_requests"), {"tab": "SUBMITTED"})
    assert response.status_code == 200
    html = response.content.decode()
    assert response.context["active_tab"] == "SUBMITTED"
    assert "d-none" in _req_card_class(html, draft.pk)
    assert "d-none" not in _req_card_class(html, submitted.pk)
    assert 'data-tab="SUBMITTED"' in html
    assert "is-active" in html


def test_portal_requests_completed_docs_open_completed_tab(logged_client, portal_client):
    completed = _portal_request(portal_client, "COMPLETED", "Done Carrier")
    response = logged_client.get(reverse("website:transport_requests"), {"docs_req": completed.pk})
    assert response.status_code == 200
    assert response.context["active_tab"] == "COMPLETED"
    assert "d-none" not in _req_card_class(response.content.decode(), completed.pk)


def test_request_form_omits_floating_cars(portal_client):
    from core.views_website.forms import TransportRequestForm

    floating = Car.objects.create(
        year=2024, brand="Kia", vin="FLOATVIN000000001", status="FLOATING", client=portal_client
    )
    in_port = Car.objects.create(
        year=2024, brand="BMW", vin="INPORTVIN00000001", status="IN_PORT", client=portal_client
    )
    unloaded = Car.objects.create(
        year=2023, brand="Audi", vin="UNLOADVIN00000001", status="UNLOADED", client=portal_client
    )
    form = TransportRequestForm(client=portal_client)
    pks = set(form.fields["cars"].queryset.values_list("pk", flat=True))
    assert floating.pk not in pks
    assert in_port.pk in pks
    assert unloaded.pk in pks


def test_transport_requests_page_hides_floating_cars(logged_client, portal_client):
    floating = Car.objects.create(
        year=2024, brand="Kia", vin="FLOATPAGEVIN00001", status="FLOATING", client=portal_client
    )
    unloaded = Car.objects.create(
        year=2023, brand="Audi", vin="UNLOADPAGEVIN0001", status="UNLOADED", client=portal_client
    )
    response = logged_client.get(reverse("website:transport_requests"), {"cars": [floating.pk, unloaded.pk]})
    assert response.status_code == 200
    html = response.content.decode()
    assert "UNLOADPAGEVIN0001" in html
    assert "FLOATPAGEVIN00001" not in html


def _draft_post(**extra):
    data = {
        "save_draft": "1",
        "destination_country": "BY",
        "declaration_type": "TRANSIT",
        "carrier_name": "Test Carrier",
        "carrier_eori": "LT123456789",
        "truck_number": "ABC123",
        "driver_name": "Иванов Иван",
    }
    data.update(extra)
    return data


def test_dashboard_preselect_checks_car_checkbox(logged_client, portal_client):
    car = Car.objects.create(year=2023, brand="Audi", vin="PRESELECTVIN00001", status="UNLOADED", client=portal_client)
    response = logged_client.get(reverse("website:transport_requests"), {"cars": [car.pk]})
    html = response.content.decode()
    tag = re.search(rf'<input[^>]*name="cars"[^>]*value="{car.pk}"[^>]*>', html)
    assert tag, html
    assert "checked" in tag.group(0)


def test_create_draft_saves_preselected_cars(logged_client, portal_client):
    car = Car.objects.create(year=2023, brand="BMW", vin="DRAFTCARSVIN00001", status="UNLOADED", client=portal_client)
    url = reverse("website:transport_requests")
    response = logged_client.post(f"{url}?cars={car.pk}", _draft_post(cars=[car.pk]))
    assert response.status_code == 302
    tr = TransportRequest.objects.get(client=portal_client)
    assert tr.status == "DRAFT"
    assert list(tr.cars.values_list("pk", flat=True)) == [car.pk]


def test_create_draft_keeps_cars_from_query_if_checkboxes_missing(logged_client, portal_client):
    """Дашборд передаёт авто в query string; чекбоксы могут не попасть в POST."""
    car = Car.objects.create(year=2023, brand="Kia", vin="QUERYONLYVIN00001", status="UNLOADED", client=portal_client)
    url = reverse("website:transport_requests")
    response = logged_client.post(f"{url}?cars={car.pk}", _draft_post())
    assert response.status_code == 302, response.content.decode()
    tr = TransportRequest.objects.get(client=portal_client)
    assert list(tr.cars.values_list("pk", flat=True)) == [car.pk]


def test_create_draft_requires_cars_without_query(logged_client, portal_client):
    Car.objects.create(year=2023, brand="Audi", vin="NEEDCARSVIN000001", status="UNLOADED", client=portal_client)
    response = logged_client.post(reverse("website:transport_requests"), _draft_post())
    assert response.status_code == 200
    assert not TransportRequest.objects.filter(client=portal_client).exists()
    assert "Выберите хотя бы один автомобиль." in response.content.decode()


def test_passport_requires_address(logged_client, transport_request, car):
    response = logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "PASSPORT", "car": car.pk, "action": "save", "buyer_name": "X", "buyer_address": ""},
        follow=True,
    )
    text = response.content.decode()
    assert "адрес" in text.lower()
    package = transport_request.doc_packages.get(car=car)
    assert package.data["buyer_name"] == "X"


def test_passport_upload_and_delete(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    upload = SimpleUploadedFile("passport.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
    post = {"doc_type": "PASSPORT", "car": car.pk, "action": "save", "files": upload, **BUYER_DATA}
    response = logged_client.post(_doc_url(transport_request), post)
    assert response.status_code == 302
    doc = transport_request.documents.get(doc_type="PASSPORT")
    assert not doc.is_generated

    delete_url = reverse("website:transport_request_doc_delete", args=[transport_request.pk, doc.pk])
    logged_client.post(delete_url)
    assert not transport_request.documents.filter(pk=doc.pk).exists()


def test_generate_invoice_via_view(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    logged_client.post(
        _doc_url(transport_request), {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA}
    )
    response = logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "",
            "invoice_number": "",
        },
    )
    assert response.status_code == 302
    doc = transport_request.documents.get(doc_type="INVOICE")
    assert doc.is_generated
    with doc.file.open("rb") as fh:
        assert fh.read(4) == b"%PDF"
    # повторная генерация заменяет документ, а не плодит копии
    logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "INVOICE", "car": car.pk, "action": "generate", "invoice_amount": "2850"},
    )
    assert transport_request.documents.filter(doc_type="INVOICE").count() == 1


def _pdf_text(doc) -> str:
    import fitz

    with doc.file.open("rb") as fh:
        return fitz.open(stream=fh.read(), filetype="pdf")[0].get_text()


def _signature_upload(name="sign.jpg"):
    from PIL import Image

    img = Image.new("RGB", (300, 150), (255, 255, 255))
    for x in range(30, 270):
        for y in range(60, 80):
            img.putpixel((x, y), (10, 10, 10))
    raw = io.BytesIO()
    img.save(raw, format="JPEG")
    return SimpleUploadedFile(name, raw.getvalue(), content_type="image/jpeg")


def _seed_generated_package(logged_client, transport_request, car):
    """Паспорт + инвойс + платёжка + обязательство — типичный пакет после «Сгенерировать всё»."""
    logged_client.post(
        _doc_url(transport_request), {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA}
    )
    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "2026-08-12",
        },
    )
    logged_client.post(_doc_url(transport_request), {"doc_type": "PAYMENT_ORDER", "car": car.pk, "action": "generate"})
    logged_client.post(_doc_url(transport_request), {"doc_type": "OBLIGATION", "car": car.pk, "action": "generate"})
    return {
        "invoice": transport_request.documents.get(doc_type="INVOICE"),
        "payment": transport_request.documents.get(doc_type="PAYMENT_ORDER"),
        "obligation": transport_request.documents.get(doc_type="OBLIGATION"),
    }


def test_invoice_amount_change_refreshes_payment(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    seeded = _seed_generated_package(logged_client, transport_request, car)
    old_payment_pk = seeded["payment"].pk
    old_obligation_pk = seeded["obligation"].pk

    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "9999",
            "invoice_date": "2026-08-12",
        },
    )
    invoice = transport_request.documents.get(doc_type="INVOICE")
    payment = transport_request.documents.get(doc_type="PAYMENT_ORDER")
    assert payment.pk != old_payment_pk
    assert payment.is_generated
    assert "9,999" in _pdf_text(invoice) or "9999" in _pdf_text(invoice)
    assert "Девять тысяч" in _pdf_text(payment)
    # Сумма инвойса в обязательстве не фигурирует — его не пересобираем.
    assert transport_request.documents.get(doc_type="OBLIGATION").pk == old_obligation_pk


def test_passport_change_refreshes_related_generated_docs(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    seeded = _seed_generated_package(logged_client, transport_request, car)
    old = {key: doc.pk for key, doc in seeded.items()}

    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "PASSPORT",
            "car": car.pk,
            "action": "save",
            "cascade": "1",
            **{**BUYER_DATA, "buyer_name": "IVAN IVANOV", "buyer_name_ru": "Иванов Иван Иванович"},
        },
    )
    invoice = transport_request.documents.get(doc_type="INVOICE")
    payment = transport_request.documents.get(doc_type="PAYMENT_ORDER")
    obligation = transport_request.documents.get(doc_type="OBLIGATION")
    assert invoice.pk != old["invoice"]
    assert payment.pk != old["payment"]
    assert obligation.pk != old["obligation"]
    assert "IVAN IVANOV" in _pdf_text(invoice)
    assert "IVAN IVANOV" in _pdf_text(payment)
    assert "Иванов Иван Иванович" in _pdf_text(obligation)
    assert not transport_request.documents.filter(doc_type="LETTER_USA").exists()


def test_uploaded_payment_not_replaced_on_invoice_change(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    logged_client.post(
        _doc_url(transport_request), {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA}
    )
    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "2026-08-12",
        },
    )
    uploaded = TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="PAYMENT_ORDER",
        file=SimpleUploadedFile("payment.pdf", _tiny_pdf("manual-payment"), content_type="application/pdf"),
        is_generated=False,
    )
    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "4000",
            "invoice_date": "2026-08-12",
        },
    )
    payment = transport_request.documents.get(doc_type="PAYMENT_ORDER")
    assert payment.pk == uploaded.pk
    assert not payment.is_generated


def test_new_signature_replaces_old_and_refreshes_obligation(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    logged_client.post(
        _doc_url(transport_request), {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA}
    )
    logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "SIGNATURE", "car": car.pk, "action": "save", "files": _signature_upload("first.jpg")},
    )
    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "2026-08-12",
        },
    )
    logged_client.post(_doc_url(transport_request), {"doc_type": "OBLIGATION", "car": car.pk, "action": "generate"})
    old_obligation_pk = transport_request.documents.get(doc_type="OBLIGATION").pk
    first_sign_pk = transport_request.documents.get(doc_type="SIGNATURE").pk

    logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "SIGNATURE", "car": car.pk, "action": "save", "files": _signature_upload("second.jpg")},
    )
    assert transport_request.documents.filter(doc_type="SIGNATURE").count() == 1
    assert transport_request.documents.get(doc_type="SIGNATURE").pk != first_sign_pk
    obligation = transport_request.documents.get(doc_type="OBLIGATION")
    assert obligation.pk != old_obligation_pk
    assert obligation.is_generated
    # Платёжка без галочки подписи не пересобирается.
    assert not transport_request.documents.filter(doc_type="PAYMENT_ORDER").exists()


def test_car_row_shows_edit_pencil(logged_client, transport_request, car):
    response = logged_client.get(reverse("website:transport_requests"))
    html = response.content.decode()
    assert 'class="doc-slot-edit doc-edit-btn"' in html
    assert 'data-doc-type="PASSPORT"' in html
    assert "bi-pencil" in html
    # Тайтл только загружается — карандаша у пустого слота нет.
    assert not re.search(r'doc-edit-btn"[^>]*data-doc-type="TITLE"', html)
    assert not re.search(r'data-doc-type="TITLE"[^>]*doc-edit-btn', html)


def test_preview_url_busts_cache_after_regenerate(logged_client, transport_request, car, settings, tmp_path):
    """После перегенерации путь файла тот же — предпросмотр идёт с новым ?v=pk."""
    settings.MEDIA_ROOT = str(tmp_path)
    logged_client.post(
        _doc_url(transport_request), {"doc_type": "PASSPORT", "car": car.pk, "action": "save", **BUYER_DATA}
    )
    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "2850",
            "invoice_date": "2026-08-12",
        },
    )
    first = transport_request.documents.get(doc_type="INVOICE")
    first_url = first.preview_url
    assert f"v={first.pk}" in first_url

    logged_client.post(
        _doc_url(transport_request),
        {
            "doc_type": "INVOICE",
            "car": car.pk,
            "action": "generate",
            "invoice_amount": "4000",
            "invoice_date": "2026-08-12",
        },
    )
    second = transport_request.documents.get(doc_type="INVOICE")
    assert second.pk != first.pk
    assert second.preview_url != first_url
    assert f"v={second.pk}" in second.preview_url
    html = logged_client.get(reverse("website:transport_requests")).content.decode()
    assert second.preview_url in html


def test_extract_passport_normalizes(monkeypatch):
    from core.services import passport_extractor as pe

    monkeypatch.setattr(pe, "render_document_images", lambda path: [("image/jpeg", "stub")])
    monkeypatch.setattr(
        pe,
        "_call_claude_vision",
        lambda images, system_prompt, user_text: {
            "passport_number": "mc 3902087",
            "surname_latin": "Zizika",
            "given_name_latin": "Uladzimir",
            "birth_date": "1967-01-29",
            "issue_date": "not-a-date",
        },
    )
    assert pe.extract_passport("x.jpg") == {
        "buyer_passport_number": "MC3902087",
        "buyer_name": "ZIZIKA ULADZIMIR",
        "buyer_birth_date": "1967-01-29",
    }


def test_passport_ai_autofill(logged_client, transport_request, car, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from core.services import passport_extractor

    monkeypatch.setattr(
        passport_extractor,
        "extract_passport",
        lambda path: {
            "buyer_name": "ZIZIKA ULADZIMIR",
            "buyer_passport_number": "MC3902087",
            "buyer_birth_date": "1967-01-29",
            "buyer_passport_issue_date": "2025-10-22",
        },
    )
    monkeypatch.setattr(
        passport_extractor,
        "transliterate_address",
        lambda ru: "ul. Gaya 5, d.Bolshaya lysitsa, Nesvizhskiy r-on, Belarus",
    )
    upload = SimpleUploadedFile("passport.jpg", b"fake-jpg", content_type="image/jpeg")
    post = {
        "doc_type": "PASSPORT",
        "car": car.pk,
        "action": "save",
        "files": upload,
        "buyer_name_ru": "Зизико Владимир Константинович",
        "buyer_address_ru": "д. Большая Лысица, Несвижского р-на, ул. Гая 5",
        "buyer_name": "",
        "buyer_address": "",
        # Ручной ввод не должен перетираться распознанным значением.
        "buyer_passport_number": "AB1234567",
    }
    response = logged_client.post(_doc_url(transport_request), post)
    assert response.status_code == 302
    package = transport_request.doc_packages.get(car=car)
    assert package.data["buyer_name"] == "ZIZIKA ULADZIMIR"
    assert package.data["buyer_birth_date"] == "1967-01-29"
    assert package.data["buyer_passport_issue_date"] == "2025-10-22"
    assert package.data["buyer_passport_number"] == "AB1234567"
    assert package.data["buyer_address"].startswith("ul. Gaya 5")
    assert not transport_request.documents.filter(doc_type="SIGNATURE").exists()


def test_normalize_signature_removes_bg_and_tints_blue():
    from PIL import Image

    from core.services.signature_normalizer import normalize_signature_image

    # Чуть сероватая бумага + чёрная «подпись»-полоска (как фото с телефона).
    img = Image.new("RGB", (400, 200), (235, 232, 225))
    for x in range(40, 360):
        for y in range(90, 110):
            img.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    png = normalize_signature_image(buf.getvalue())
    assert png and png[:4] == b"\x89PNG"
    out = Image.open(io.BytesIO(png)).convert("RGBA")
    assert max(out.size) <= 900
    px = out.load()
    w, h = out.size
    # Фон должен быть полностью прозрачным (не полупрозрачный «туман»).
    haze = sum(1 for y in range(h) for x in range(w) if 0 < px[x, y][3] < 70)
    assert haze == 0
    pixels = [px[x, y] for y in range(h) for x in range(w) if px[x, y][3] > 200]
    assert pixels
    r, g, b, _a = pixels[len(pixels) // 2]
    assert b > r and b > g


def test_normalize_signature_keeps_thin_stroke_continuous():
    """Тонкий штрих не должен разваливаться на «пробелы» (бывший opening Min→Max)."""
    from PIL import Image

    from core.services.signature_normalizer import normalize_signature_image

    img = Image.new("RGB", (500, 180), (255, 255, 255))
    # Тонкая линия ~2 px — типичный штрих шариковой ручки на фото.
    for x in range(40, 460):
        for y in range(88, 90):
            img.putpixel((x, y), (30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    png = normalize_signature_image(buf.getvalue())
    assert png
    out = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = out.split()[-1]
    # По горизонтали штрих должен покрывать большую часть ширины без больших дыр.
    ink_cols = 0
    w, h = out.size
    for x in range(w):
        if any(alpha.getpixel((x, y)) > 120 for y in range(h)):
            ink_cols += 1
    assert ink_cols / w > 0.55


def test_normalize_signature_rerun_on_rgba_keeps_transparency():
    from PIL import Image

    from core.services.signature_normalizer import normalize_signature_image

    img = Image.new("RGB", (300, 120), (255, 255, 255))
    for x in range(20, 280):
        for y in range(50, 70):
            img.putpixel((x, y), (15, 15, 15))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    first = normalize_signature_image(buf.getvalue())
    second = normalize_signature_image(first)
    assert second and second[:4] == b"\x89PNG"
    out = Image.open(io.BytesIO(second)).convert("RGBA")
    assert any(a == 0 for a in out.split()[-1].getdata())
    assert any(a > 200 for a in out.split()[-1].getdata())


def test_signature_upload_normalized(logged_client, transport_request, car, settings, tmp_path):
    from PIL import Image

    settings.MEDIA_ROOT = str(tmp_path)
    img = Image.new("RGB", (300, 150), (255, 255, 255))
    for x in range(30, 270):
        for y in range(60, 80):
            img.putpixel((x, y), (10, 10, 10))
    raw = io.BytesIO()
    img.save(raw, format="JPEG")
    upload = SimpleUploadedFile("sign.jpg", raw.getvalue(), content_type="image/jpeg")
    response = logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "SIGNATURE", "car": car.pk, "action": "save", "files": upload},
    )
    assert response.status_code == 302
    doc = transport_request.documents.get(doc_type="SIGNATURE")
    assert doc.filename.endswith(".png")
    with doc.file.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"


def test_docs_locked_when_in_progress(logged_client, transport_request, car):
    transport_request.status = "IN_PROGRESS"
    transport_request.save(update_fields=["status"])
    response = logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "OTHER", "car": car.pk, "action": "save"},
    )
    assert response.status_code == 302
    assert not TransportRequestDocument.objects.filter(request=transport_request).exists()


# ---------------------------------------------------------------------------
# Скачивание ZIP-пакетов (один PDF на VIN + тайтл)
# ---------------------------------------------------------------------------


def _tiny_pdf(text: str = "doc") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_build_car_package_includes_title_and_skips_signature(transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="PASSPORT",
        file=SimpleUploadedFile("passport.pdf", _tiny_pdf("passport"), content_type="application/pdf"),
    )
    TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="SIGNATURE",
        file=SimpleUploadedFile("sign.png", b"\x89PNG\r\n\x1a\n", content_type="image/png"),
    )
    TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="INVOICE",
        file=SimpleUploadedFile("invoice.pdf", _tiny_pdf("invoice"), content_type="application/pdf"),
        is_generated=True,
    )
    car.title_scan.save(
        "title.pdf", SimpleUploadedFile("title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    )
    car.save(update_fields=["title_scan"])

    pdf_bytes = docs.build_car_package_pdf(transport_request, car)
    assert pdf_bytes and pdf_bytes[:4] == b"%PDF"
    import fitz

    merged = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert merged.page_count == 3  # passport + invoice + title, без подписи
        texts = " ".join(page.get_text() for page in merged)
        assert "passport" in texts
        assert "invoice" in texts
        assert "title" in texts
    finally:
        merged.close()


def test_title_from_car_lands_in_package(transport_request, car, settings, tmp_path):
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    car.title_scan.save(
        "title.pdf", SimpleUploadedFile("title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    )
    car.save(update_fields=["title_scan"])

    assert actions.sync_title_documents(transport_request) == 1
    doc = transport_request.documents.get(doc_type="TITLE")
    assert doc.is_generated
    # Повторный вызов дубликат не создаёт.
    assert actions.sync_title_documents(transport_request) == 0
    assert transport_request.documents.filter(doc_type="TITLE").count() == 1


def test_title_not_attached_when_car_has_none(transport_request, car):
    from core.services import transport_package_actions as actions

    assert actions.sync_title_documents(transport_request) == 0
    assert not transport_request.documents.filter(doc_type="TITLE").exists()


def test_package_pdf_keeps_single_title_page(transport_request, car, settings, tmp_path):
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    car.title_scan.save(
        "title.pdf", SimpleUploadedFile("title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    )
    car.save(update_fields=["title_scan"])
    actions.sync_title_documents(transport_request)

    pdf_bytes = docs.build_car_package_pdf(transport_request, car)
    import fitz

    merged = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        # Тайтл лежит и в документах заявки, и в карточке авто — страница одна.
        assert merged.page_count == 1
    finally:
        merged.close()


def test_client_cannot_delete_title_added_by_us(logged_client, transport_request, car, settings, tmp_path):
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    car.title_scan.save(
        "title.pdf", SimpleUploadedFile("title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    )
    car.save(update_fields=["title_scan"])
    actions.sync_title_documents(transport_request)
    doc = transport_request.documents.get(doc_type="TITLE")

    url = reverse("website:transport_request_doc_delete", args=[transport_request.pk, doc.pk])
    response = logged_client.post(url)
    assert response.status_code == 302
    assert transport_request.documents.filter(pk=doc.pk).exists()


def test_client_can_upload_own_title(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    upload = SimpleUploadedFile("my-title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    response = logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "TITLE", "car": car.pk, "action": "save", "files": upload},
    )
    assert response.status_code == 302
    doc = transport_request.documents.get(doc_type="TITLE")
    assert not doc.is_generated


def test_title_cannot_be_generated(logged_client, transport_request, car):
    response = logged_client.post(
        _doc_url(transport_request),
        {"doc_type": "TITLE", "car": car.pk, "action": "generate"},
    )
    assert response.status_code == 302
    assert not transport_request.documents.filter(doc_type="TITLE").exists()


def test_download_packages_zip(logged_client, transport_request, car, settings, tmp_path):
    import zipfile

    settings.MEDIA_ROOT = str(tmp_path)
    TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="OTHER",
        file=SimpleUploadedFile("other.pdf", _tiny_pdf("other"), content_type="application/pdf"),
    )
    car.title_scan.save(
        "title.pdf", SimpleUploadedFile("title.pdf", _tiny_pdf("title"), content_type="application/pdf")
    )
    car.save(update_fields=["title_scan"])

    url = reverse("website:transport_request_download_packages", args=[transport_request.pk])
    response = logged_client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert transport_request.number in response["Content-Disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert names[0] == docs.package_pdf_filename(car)
        assert zf.read(names[0])[:4] == b"%PDF"


def test_download_packages_empty_redirects(logged_client, transport_request):
    url = reverse("website:transport_request_download_packages", args=[transport_request.pk])
    response = logged_client.get(url)
    assert response.status_code == 302
    assert response.url == reverse("website:transport_requests")


def test_generate_all_package(logged_client, transport_request, car, settings, tmp_path):
    from PIL import Image

    settings.MEDIA_ROOT = str(tmp_path)
    # Паспорт-картинка (AI отключён — заполняем поля вручную).
    passport = Image.new("RGB", (200, 120), (240, 240, 240))
    passport_buf = io.BytesIO()
    passport.save(passport_buf, format="JPEG")
    passport_upload = SimpleUploadedFile("passport.jpg", passport_buf.getvalue(), content_type="image/jpeg")

    sign = Image.new("RGB", (300, 120), (255, 255, 255))
    for x in range(30, 270):
        for y in range(55, 75):
            sign.putpixel((x, y), (10, 10, 10))
    sign_buf = io.BytesIO()
    sign.save(sign_buf, format="JPEG")
    sign_upload = SimpleUploadedFile("sign.jpg", sign_buf.getvalue(), content_type="image/jpeg")

    url = reverse("website:transport_request_generate_all", args=[transport_request.pk])
    response = logged_client.post(
        url,
        {
            "car": car.pk,
            "passport": passport_upload,
            "signature": sign_upload,
            "buyer_name_ru": BUYER_DATA["buyer_name_ru"],
            "buyer_address_ru": BUYER_DATA["buyer_address_ru"],
            "buyer_name": BUYER_DATA["buyer_name"],
            "buyer_passport_number": BUYER_DATA["buyer_passport_number"],
            "buyer_birth_date": BUYER_DATA["buyer_birth_date"],
            "buyer_passport_issue_date": BUYER_DATA["buyer_passport_issue_date"],
            "buyer_address": BUYER_DATA["buyer_address"],
            "invoice_amount": "2850",
            # Не позже чем за 4 недели до «сегодня» — иначе LETTER_USA не датируется.
            "invoice_date": "2026-06-10",
            "invoice_extra_desc": ["Fee"],
            "invoice_extra_amount": ["150"],
        },
    )
    assert response.status_code == 302
    types = set(transport_request.documents.filter(car=car, is_generated=True).values_list("doc_type", flat=True))
    assert types == {"INVOICE", "PAYMENT_ORDER", "LETTER_USA", "OBLIGATION"}
    assert not transport_request.documents.filter(car=car, doc_type="CONTRACT").exists()
    assert transport_request.documents.filter(car=car, doc_type="PASSPORT").exists()
    assert transport_request.documents.filter(car=car, doc_type="SIGNATURE").exists()
    package = transport_request.doc_packages.get(car=car)
    assert package.data["invoice_extra_lines"] == [{"description": "Fee", "amount": "150"}]


def test_signature_flowable_respects_max_box():
    """Широкая и высокая подписи вписываются в один и тот же max box."""
    from PIL import Image

    from core.services.transport_docs_pdf import _signature_flowable

    def _png(size):
        img = Image.new("RGBA", size, (25, 55, 160, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    wide = _signature_flowable(_png((900, 120)), max_height=1.8 * 28.35, max_width=5.2 * 28.35)
    tall = _signature_flowable(_png((120, 900)), max_height=1.8 * 28.35, max_width=5.2 * 28.35)
    assert wide is not None and tall is not None
    assert wide.drawWidth <= 5.2 * 28.35 + 0.01
    assert wide.drawHeight <= 1.8 * 28.35 + 0.01
    assert tall.drawWidth <= 5.2 * 28.35 + 0.01
    assert tall.drawHeight <= 1.8 * 28.35 + 0.01
