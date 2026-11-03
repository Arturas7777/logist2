"""Документы и поля заявки для роли neto."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client as DjangoClient
from django.urls import reverse

from core.models import Car, Client
from core.models.bots import BotAuditEntry
from core.models.website import TransportRequest, TransportRequestDocument
from core.services.bot_docs import bundle_files, completeness
from core.services.bot_runtime import issue_token
from core.services.bot_tr import neto_tmp_dir

pytestmark = pytest.mark.django_db


def _request_with_car(*, eori="LT3066363532", vin="1HGCM82633A004352"):
    client = Client.objects.create(name="Buyer")
    car = Car.objects.create(vin=vin, brand="Honda", year=2020, status="UNLOADED", client=client)
    request = TransportRequest.objects.create(
        client=client,
        driver_name="Jonas",
        carrier_name="OLD",
        carrier_eori=eori,
        truck_number="OLD1",
        declaration_type="TRANSIT",
        status="IN_PROGRESS",
    )
    request.cars.add(car)
    return request, car


def _pdf(name: str, body: bytes = b"%PDF-1.4 neto") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _call(token: str, *args: str) -> dict:
    out = StringIO()
    call_command("bot", *args, "--token", token, stdout=out)
    return json.loads(out.getvalue())


def test_tr_docs_lists_allowed_types_and_skips_title():
    _principal, token = issue_token("neto-tr-list", "neto")
    request, car = _request_with_car()
    kept = TransportRequestDocument.objects.create(request=request, car=car, doc_type="CONTRACT", file=_pdf("deal.pdf"))
    TransportRequestDocument.objects.create(request=request, car=car, doc_type="TITLE", file=_pdf("title.pdf"))
    payload = _call(token, "tr-docs", "--tr", request.number)
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    docs = payload["result"]["documents"]
    assert len(docs) == 1
    assert docs[0]["id"] == kept.pk
    assert docs[0]["type"] == "CONTRACT"
    assert docs[0]["filename"].endswith(".pdf")
    assert docs[0]["size"] > 0
    assert docs[0]["vin"] == car.vin
    by_id = _call(token, "tr-docs", "--tr", str(request.pk))
    assert by_id["result"]["documents"][0]["id"] == kept.pk


def test_download_tr_doc_copies_into_neto_folder_and_needs_doc_id_when_ambiguous():
    _principal, token = issue_token("neto-tr-dl", "neto")
    request, car = _request_with_car()
    first = TransportRequestDocument.objects.create(
        request=request, car=car, doc_type="OTHER", file=_pdf("a.pdf", b"one")
    )
    second = TransportRequestDocument.objects.create(
        request=request, car=car, doc_type="OTHER", file=_pdf("b.pdf", b"two")
    )
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("bot", "tr-docs", "--token", token, stdout=out)
    ambiguous = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot", "download-tr-doc", "--token", token, "--tr", request.number, "--type", "OTHER", stdout=ambiguous
        )
    assert "doc-id" in json.loads(ambiguous.getvalue())["error"]
    payload = _call(token, "download-tr-doc", "--tr", request.number, "--type", "OTHER", "--doc-id", str(second.pk))
    target = Path(payload["result"]["path"])
    assert target.is_file()
    assert target.read_bytes() == b"two"
    assert str(target).startswith(str(neto_tmp_dir()))
    assert payload["result"]["id"] == second.pk
    assert payload["result"]["size"] == 3
    other, _other_car = _request_with_car(vin="2HGCM82633A004353")
    foreign = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "download-tr-doc",
            "--token",
            token,
            "--tr",
            other.number,
            "--type",
            "OTHER",
            "--doc-id",
            str(first.pk),
            stdout=foreign,
        )
    assert "не относится" in json.loads(foreign.getvalue())["error"]


def test_chief_cannot_read_or_write_tr_docs():
    _principal, token = issue_token("chief-no-tr", "chief")
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("bot", "tr-update", "--token", token, "--tr", "1", "--driver-name", "A", stdout=out)
    assert json.loads(out.getvalue())["ok"] is False


def test_tr_update_dry_run_then_apply_audits_and_does_not_mail():
    _principal, token = issue_token("neto-tr-upd", "neto")
    request, _car = _request_with_car(eori="IK185300064")
    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "tr-update",
            "--token",
            token,
            "--tr",
            request.number,
            "--carrier-eori",
            "IK185300064",
            stdout=out,
        )
    assert "EORI_FORMAT" in json.loads(out.getvalue())["error"]
    request.refresh_from_db()
    assert request.carrier_eori == "IK185300064"

    preview = _call(
        token,
        "tr-update",
        "--tr",
        request.number,
        "--driver-name",
        "Petras",
        "--driver-passport",
        "MC3902087",
        "--driver-phone",
        "+370600",
        "--carrier-name",
        "ANGER TRANSPORT",
        "--carrier-eori",
        "pl525308706300000",
        "--tractor-plate",
        "LTL123",
        "--trailer-plate",
        "TR9",
        "--border-crossing",
        "Medininkai",
    )
    assert preview["dry_run"] is True
    assert preview["result"]["outcome"] == "would_update"
    assert preview["notifications"] == []
    request.refresh_from_db()
    assert request.driver_name == "Jonas"
    assert request.driver_passport == ""

    with patch("core.tasks.send_request_status_notification_task.delay") as delayed:
        applied = _call(
            token,
            "tr-update",
            "--tr",
            request.number,
            "--driver-name",
            "Petras",
            "--driver-passport",
            "MC3902087",
            "--driver-phone",
            "+370600",
            "--carrier-name",
            "ANGER TRANSPORT",
            "--carrier-eori",
            "pl525308706300000",
            "--tractor-plate",
            "LTL123",
            "--trailer-plate",
            "TR9",
            "--border-crossing",
            "Medininkai",
            "--apply",
            "--idempotency-key",
            "tr-upd-1",
        )
    delayed.assert_not_called()
    assert applied["dry_run"] is False
    assert applied["notifications"] == []
    assert mail.outbox == []
    request.refresh_from_db()
    assert request.status == "IN_PROGRESS"
    assert request.driver_name == "Petras"
    assert request.driver_passport == "MC3902087"
    assert request.driver_phone == "+370600"
    assert request.carrier_name == "ANGER TRANSPORT"
    assert request.carrier_eori == "PL525308706300000"
    assert request.truck_number == "LTL123"
    assert request.trailer_number == "TR9"
    assert request.border_crossing == "Medininkai"
    audit = BotAuditEntry.objects.filter(command="tr-update", dry_run=False).get()
    assert audit.after["driver_passport"] == "MC3902087"
    assert BotAuditEntry.objects.filter(command="tr-update", dry_run=True).exists()

    again = _call(
        token,
        "tr-update",
        "--tr",
        request.number,
        "--driver-name",
        "Other",
        "--apply",
        "--idempotency-key",
        "tr-upd-1",
    )
    assert again["idempotent_replay"] is True
    request.refresh_from_db()
    assert request.driver_name == "Petras"

    report = completeness(request)
    assert "EORI_FORMAT" not in report["request_missing"]
    request.carrier_eori = "PL527318145700000"
    request.save(update_fields=["carrier_eori"])
    assert "EORI_FORMAT" not in completeness(request)["request_missing"]


def test_tr_upload_doc_dry_run_apply_and_rejects_paths_outside_neto_tmp():
    _principal, token = issue_token("neto-tr-up", "neto")
    request, car = _request_with_car()
    extra = Car.objects.create(vin="JH4KA4650MC000001", brand="Acura", year=2019, status="UNLOADED", client=car.client)
    request.cars.add(extra)
    folder = neto_tmp_dir()
    source = folder / "contract.pdf"
    source.write_bytes(b"%PDF-1.4 contract")
    outside = Path(folder).parent / "not-here.pdf"
    outside.write_bytes(b"%PDF-1.4")

    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "tr-upload-doc",
            "--token",
            token,
            "--tr",
            request.number,
            "--type",
            "INVOICE",
            "--file",
            str(source),
            "--vin",
            car.vin,
            stdout=out,
        )
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "tr-upload-doc",
            "--token",
            token,
            "--tr",
            request.number,
            "--type",
            "CONTRACT",
            "--file",
            str(outside),
            "--vin",
            car.vin,
            stdout=out,
        )
    with pytest.raises(CommandError):
        call_command(
            "bot",
            "tr-upload-doc",
            "--token",
            token,
            "--tr",
            request.number,
            "--type",
            "CONTRACT",
            "--file",
            str(source),
            stdout=out,
        )
    preview = _call(
        token,
        "tr-upload-doc",
        "--tr",
        request.number,
        "--type",
        "CONTRACT",
        "--file",
        source.name,
        "--vin",
        car.vin,
    )
    assert preview["dry_run"] is True
    assert preview["notifications"] == []
    assert TransportRequestDocument.objects.filter(request=request).count() == 0

    with patch("core.tasks.send_request_status_notification_task.delay") as delayed:
        applied = _call(
            token,
            "tr-upload-doc",
            "--tr",
            request.number,
            "--type",
            "contract",
            "--file",
            str(source),
            "--car-id",
            str(car.pk),
            "--apply",
            "--idempotency-key",
            "up-1",
        )
    delayed.assert_not_called()
    assert mail.outbox == []
    doc = TransportRequestDocument.objects.get(request=request)
    assert applied["result"]["id"] == doc.pk
    assert doc.doc_type == "CONTRACT"
    assert doc.car_id == car.pk
    assert doc.file.read() == b"%PDF-1.4 contract"
    assert BotAuditEntry.objects.filter(command="tr-upload-doc", dry_run=False).count() == 1
    replay = _call(
        token,
        "tr-upload-doc",
        "--tr",
        request.number,
        "--type",
        "CONTRACT",
        "--file",
        str(source),
        "--vin",
        car.vin,
        "--apply",
        "--idempotency-key",
        "up-1",
    )
    assert replay["idempotent_replay"] is True
    assert TransportRequestDocument.objects.filter(request=request).count() == 1
    outside.unlink(missing_ok=True)


def test_bundle_files_include_title_scan_id():
    client = Client.objects.create(name="Scan")
    car = Car.objects.create(vin="5YJ3E1EA1PF499330", brand="Tesla", year=2021, status="UNLOADED", client=client)
    car.title_scan.save("scan_title.pdf", ContentFile(b"title-bytes"), save=True)
    request = TransportRequest.objects.create(client=client, driver_name="A", carrier_eori="LT1", status="SUBMITTED")
    request.cars.add(car)
    rows = bundle_files(request)
    assert rows[0]["id"] == f"car_{car.pk}_title"
    assert rows[0]["type"] == "TITLE"


def test_staff_card_shows_and_saves_driver_passport():
    request, _car = _request_with_car()
    user = get_user_model().objects.create_user(username="staff-pass", password="secret123", is_staff=True)
    client = DjangoClient()
    client.force_login(user)
    page = client.get(reverse("admin_request_card", args=[request.pk]))
    assert page.status_code == 200
    assert b'name="driver_passport"' in page.content
    saved = client.post(reverse("admin_request_update", args=[request.pk]), {"driver_passport": "KH1234567"})
    assert saved.status_code == 200
    request.refresh_from_db()
    assert request.driver_passport == "KH1234567"
