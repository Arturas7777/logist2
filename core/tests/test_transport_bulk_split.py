"""Тесты разбора пакета документов, присланного одним файлом.

Claude Vision мокается — сети в тестах нет. Проверяем то, что ломается в
реальной жизни: склейку подряд идущих страниц одного документа, уход
неуверенных страниц в «Остальное», нарезку PDF по страницам, ручную правку
типа и защиту от файлов, которые разбирать не станем.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Car, Client
from core.models.website import (
    ClientUser,
    TransportBulkUpload,
    TransportRequest,
    TransportRequestDocument,
)
from core.services import transport_bulk_split as bulk

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_client(db):
    return Client.objects.create(name="Bulk Client")


@pytest.fixture
def car(db):
    return Car.objects.create(year=2022, brand="Toyota Camry", vin="4T1BF1FK5GU123456", status="UNLOADED")


@pytest.fixture
def transport_request(car, portal_client):
    tr = TransportRequest.objects.create(
        client=portal_client,
        carrier_name="MAXER TRANSPORT Sp. z.o.o.",
        truck_number="ABC123",
        driver_name="Иванов Иван",
        destination_country="BY",
        declaration_type="TRANSIT",
        status="DRAFT",
    )
    tr.cars.add(car)
    return tr


@pytest.fixture
def logged_client(client, portal_client):
    user = User.objects.create_user("bulk-user", password="pass12345")
    ClientUser.objects.create(user=user, client=portal_client)
    client.force_login(user)
    return client


def _pdf_with_pages(count: int) -> bytes:
    import fitz

    doc = fitz.open()
    for index in range(count):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {index + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _make_upload(transport_request, car, pages: int) -> TransportBulkUpload:
    return TransportBulkUpload.objects.create(
        request=transport_request,
        car=car,
        file=SimpleUploadedFile("package.pdf", _pdf_with_pages(pages), content_type="application/pdf"),
    )


@pytest.fixture
def fake_ai(monkeypatch):
    """Подменяет рендер страниц и ответ модели: [(doc_type, confidence), ...]."""

    def _install(page_answers):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "core.services.scan_extractor.render_document_images",
            lambda path: [("image/jpeg", "fake") for _ in page_answers],
        )

        def fake_call(images, system_prompt, user_text):
            # Модель отвечает только про показанный чанк — нумерация с 1.
            offset = fake_call.sent
            fake_call.sent += len(images)
            return {
                "pages": [
                    {"page": index + 1, "doc_type": doc_type, "confidence": confidence}
                    for index, (doc_type, confidence) in enumerate(page_answers[offset : offset + len(images)])
                ]
            }

        fake_call.sent = 0
        monkeypatch.setattr("core.services.scan_extractor._call_claude_vision", fake_call)

    return _install


def test_consecutive_pages_of_one_type_become_one_document(transport_request, car, settings, tmp_path, fake_ai):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("PASSPORT", "high"), ("CONTRACT", "high"), ("CONTRACT", "high"), ("INVOICE", "high")])
    upload = _make_upload(transport_request, car, 4)

    result = bulk.split_upload(upload)

    upload.refresh_from_db()
    assert upload.status == TransportBulkUpload.STATUS_DONE
    assert [item["doc_type"] for item in result["documents"]] == ["PASSPORT", "CONTRACT", "INVOICE"]
    contract = next(item for item in result["documents"] if item["doc_type"] == "CONTRACT")
    assert contract["pages"] == [2, 3]
    assert transport_request.documents.count() == 3


def test_low_confidence_page_goes_to_other(transport_request, car, settings, tmp_path, fake_ai):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("INVOICE", "low"), ("PAYMENT_ORDER", "high")])
    upload = _make_upload(transport_request, car, 2)

    result = bulk.split_upload(upload)

    assert result["unrecognized"] == [1]
    assert transport_request.documents.filter(doc_type="OTHER").count() == 1
    assert transport_request.documents.filter(doc_type="PAYMENT_ORDER").count() == 1


def test_unknown_type_from_model_goes_to_other(transport_request, car, settings, tmp_path, fake_ai):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("BILL_OF_LADING", "high")])
    upload = _make_upload(transport_request, car, 1)

    bulk.split_upload(upload)

    assert transport_request.documents.filter(doc_type="OTHER").count() == 1


def test_split_document_keeps_only_its_pages(transport_request, car, settings, tmp_path, fake_ai):
    import fitz

    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("PASSPORT", "high"), ("INVOICE", "high"), ("INVOICE", "high")])
    upload = _make_upload(transport_request, car, 3)

    bulk.split_upload(upload)

    invoice = transport_request.documents.get(doc_type="INVOICE")
    with fitz.open(invoice.file.path) as pdf:
        assert pdf.page_count == 2
    passport = transport_request.documents.get(doc_type="PASSPORT")
    with fitz.open(passport.file.path) as pdf:
        assert pdf.page_count == 1


def test_pages_are_classified_in_chunks(transport_request, car, settings, tmp_path, fake_ai, monkeypatch):
    """Большой файл уходит в модель несколькими запросами, порядок сохраняется."""
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr(bulk, "PAGES_PER_CALL", 2)
    fake_ai([("PASSPORT", "high"), ("PASSPORT", "high"), ("INVOICE", "high"), ("CONTRACT", "high")])
    upload = _make_upload(transport_request, car, 4)

    result = bulk.split_upload(upload)

    assert [item["doc_type"] for item in result["documents"]] == ["PASSPORT", "INVOICE", "CONTRACT"]
    assert result["documents"][0]["pages"] == [1, 2]


def test_too_many_pages_is_rejected(transport_request, car, settings, tmp_path, fake_ai, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr(bulk, "MAX_PAGES", 2)
    fake_ai([("PASSPORT", "high")] * 3)
    upload = _make_upload(transport_request, car, 3)

    bulk.split_upload(upload)

    upload.refresh_from_db()
    assert upload.status == TransportBulkUpload.STATUS_ERROR
    assert "страниц" in upload.error_message
    assert not transport_request.documents.exists()


def test_without_ai_key_upload_ends_with_error(transport_request, car, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    upload = _make_upload(transport_request, car, 1)

    bulk.split_upload(upload)

    upload.refresh_from_db()
    assert upload.status == TransportBulkUpload.STATUS_ERROR
    assert "по одному" in upload.error_message


def test_passport_data_autofilled_after_split(transport_request, car, settings, tmp_path, fake_ai, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("PASSPORT", "high")])
    monkeypatch.setattr(
        "core.services.passport_extractor.ai_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "core.services.passport_extractor.extract_passport",
        lambda path: {"buyer_name": "IVANOU IVAN", "buyer_passport_number": "MC1234567"},
    )
    upload = _make_upload(transport_request, car, 1)

    bulk.split_upload(upload)

    package = transport_request.doc_packages.get(car=car)
    assert package.data["buyer_name"] == "IVANOU IVAN"
    assert package.data["buyer_passport_number"] == "MC1234567"


# ---------------------------------------------------------------------------
# Ручная правка раскладки и вьюхи кабинета
# ---------------------------------------------------------------------------


def test_retype_moves_document_to_another_slot(transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    doc = TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="OTHER",
        file=SimpleUploadedFile("scan.pdf", _pdf_with_pages(1), content_type="application/pdf"),
    )

    label = bulk.retype_document(doc, "INVOICE")

    doc.refresh_from_db()
    assert doc.doc_type == "INVOICE"
    assert label == "Инвойс"


def test_generated_document_cannot_be_retyped(transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    doc = TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="INVOICE",
        is_generated=True,
        file=SimpleUploadedFile("invoice.pdf", _pdf_with_pages(1), content_type="application/pdf"),
    )

    with pytest.raises(bulk.BulkSplitError):
        bulk.retype_document(doc, "OTHER")


def test_client_bulk_upload_view_queues_split(
    logged_client, transport_request, car, settings, tmp_path, fake_ai
):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("PASSPORT", "high"), ("INVOICE", "high")])

    response = logged_client.post(
        reverse("website:transport_request_bulk_upload", args=[transport_request.pk]),
        {
            "car": car.pk,
            "file": SimpleUploadedFile("package.pdf", _pdf_with_pages(2), content_type="application/pdf"),
        },
    )

    assert response.status_code == 302
    upload = TransportBulkUpload.objects.get(request=transport_request)
    # Celery в тестах eager — разбор уже прошёл.
    assert upload.status == TransportBulkUpload.STATUS_DONE
    assert set(transport_request.documents.values_list("doc_type", flat=True)) == {"PASSPORT", "INVOICE"}


def test_client_bulk_status_reports_result(logged_client, transport_request, car, settings, tmp_path, fake_ai):
    settings.MEDIA_ROOT = str(tmp_path)
    fake_ai([("INVOICE", "high")])
    upload = _make_upload(transport_request, car, 1)
    bulk.split_upload(upload)

    response = logged_client.get(reverse("website:transport_request_bulk_status", args=[transport_request.pk]))

    data = response.json()
    assert data["ok"] is True
    assert data["running"] is False
    assert data["uploads"][0]["sorted"] == ["Инвойс"]


def test_client_retype_view_moves_document(logged_client, transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    doc = TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="OTHER",
        file=SimpleUploadedFile("scan.pdf", _pdf_with_pages(1), content_type="application/pdf"),
    )

    response = logged_client.post(
        reverse("website:transport_request_doc_retype", args=[transport_request.pk, doc.pk]),
        {"doc_type": "PAYMENT_ORDER"},
    )

    assert response.status_code == 302
    doc.refresh_from_db()
    assert doc.doc_type == "PAYMENT_ORDER"


def test_rate_limit_is_retried_not_failed(transport_request, car, settings, tmp_path, monkeypatch):
    """Лимит Anthropic — временный сбой: загрузка ждёт повтора, а не падает."""
    import anthropic
    import httpx

    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "core.services.scan_extractor.render_document_images",
        lambda path: [("image/jpeg", "fake")],
    )

    def boom(images, system_prompt, user_text):
        raise anthropic.RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com")),
            body=None,
        )

    monkeypatch.setattr("core.services.scan_extractor._call_claude_vision", boom)
    upload = _make_upload(transport_request, car, 1)

    with pytest.raises(bulk.TransientSplitError):
        bulk.split_upload(upload)

    upload.refresh_from_db()
    assert upload.status == TransportBulkUpload.STATUS_PENDING
    assert upload.is_running


def test_bulk_upload_rejects_foreign_extension(transport_request, car, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    upload = SimpleUploadedFile("package.docx", b"fake", content_type="application/msword")

    with pytest.raises(bulk.BulkSplitError):
        bulk.queue_upload(transport_request, car, upload, None)
