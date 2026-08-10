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
    import fitz
    import re

    page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    text = page.get_text()
    assert docs.DEFAULT_BELARUS_PAYER_IBAN in text.replace(" ", "")
    assert 'BTA Bank' in text
    assert docs.DEFAULT_BELARUS_PAYER_BANK_CODE in text
    assert re.fullmatch(r"\d{4}-\d{1,4}", data["payment_number"])
    payment_date = datetime.date.fromisoformat(data["payment_date"])
    assert data["payment_number"].startswith(f"{payment_date:%d%m}-")
    invoice_date = datetime.date(2026, 8, 12)
    assert invoice_date <= payment_date <= invoice_date + datetime.timedelta(days=30)
    assert docs.is_business_day(payment_date, ("BY",))
    assert data["payer_bank_name"] == docs.DEFAULT_BELARUS_PAYER_BANK
    assert data["payer_bank_code"] == docs.DEFAULT_BELARUS_PAYER_BANK_CODE
    # подписи нет — предупреждение
    assert any("Подпись" in n for n in notices)
    # Печать исполнителя банка (PNG) встроена в PDF.
    assert page.get_images()


def test_bank_stamp_date_and_variety(tmp_path, monkeypatch):
    from core.services import bank_stamp

    monkeypatch.setattr(bank_stamp, "SIGNATURES_DIR", tmp_path / "sigs")
    day = datetime.date(2025, 11, 27)
    assert bank_stamp.format_stamp_date(day) == "27 НОЯ 2025"
    paths = bank_stamp.ensure_operator_signatures()
    # Без real-ассетов — fallback-набор процедурных подписей.
    assert len(paths) >= 8
    assert all(p.exists() and p.read_bytes()[:4] == b"\x89PNG" for p in paths)

    a = bank_stamp.compose_executor_stamp_field(
        day, field_width_pt=280, field_height_pt=150, payment_number="2711-24"
    )
    b = bank_stamp.compose_executor_stamp_field(
        day, field_width_pt=280, field_height_pt=150, payment_number="2711-25"
    )
    a2 = bank_stamp.compose_executor_stamp_field(
        day, field_width_pt=280, field_height_pt=150, payment_number="2711-24"
    )
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
