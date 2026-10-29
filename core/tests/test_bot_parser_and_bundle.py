"""Сопоставление Caromoto, машина без контейнера, пакет Neto."""

from __future__ import annotations

import datetime
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Car, Client, Container, Warehouse
from core.models.bots import CaromotoClientMap
from core.models.website import TransportRequest, TransportRequestDocument
from core.services.bot_docs import completeness, eori_problem, neto_bundle, recipients_for
from core.services.bot_import import _map_client, apply_batch, store_batch
from core.services.bot_ops import car_upsert, set_container_status
from core.services.bot_runtime import issue_token
from core.tests.test_bot_access import _iso

pytestmark = pytest.mark.django_db


def test_eori_requires_country_prefix():
    assert eori_problem("") == "EORI"
    assert eori_problem("IK185300064") == "EORI_FORMAT"
    assert eori_problem("PL123456789") == "EORI_FORMAT"
    assert eori_problem("LT3066363532") is None
    assert eori_problem("lv123") is None
    assert eori_problem("BY9988") is None


def test_blr_recipients_include_the_desk():
    emails = recipients_for("NETO")
    assert emails == [
        "dovile@netoterminal.lt",
        "rita@netoterminal.lt",
        "customs@netoterminal.lt",
        "evaldas@netoterminal.lt",
        "evaldas.k@netoterminal.lt",
        "ramune@netoterminal.lt",
    ]


def test_client_map_preview_then_upsert_and_office_fallback():
    belarus = Client.objects.create(name="CAROMOTO BELARUS")
    ivan = Client.objects.create(name="Ivan Sadlovskyi")
    from core.services.bot_import import upsert_client_map

    preview = upsert_client_map(office="PLB Office", caromoto_client_id="", client_id=belarus.pk, apply=False)
    assert preview["result"]["outcome"] == "would_upsert"
    assert CaromotoClientMap.objects.count() == 0

    _principal, token = issue_token("parser-map", "parser")
    out = StringIO()
    call_command(
        "bot",
        "caromoto-client-map",
        "--token",
        token,
        "--map-action",
        "upsert",
        "--office",
        "PLB Office",
        "--client-id",
        str(belarus.pk),
        "--apply",
        "--idempotency-key",
        "map-plb",
        stdout=out,
    )
    assert json.loads(out.getvalue())["result"]["outcome"] == "created"
    call_command(
        "bot",
        "caromoto-client-map",
        "--token",
        token,
        "--map-action",
        "upsert",
        "--caromoto-client-id",
        "63570",
        "--client-id",
        str(ivan.pk),
        "--apply",
        "--idempotency-key",
        "map-ivan",
        stdout=StringIO(),
    )
    assert _map_client("PLB Office", "999") == belarus
    assert _map_client("", "63570") == ivan
    assert _map_client("PLB Office", "63570") == ivan


def test_import_uses_map_and_keeps_warehouse_unless_flag():
    client = Client.objects.create(name="CAROMOTO BELARUS")
    CaromotoClientMap.objects.create(office="PLB Office", caromoto_client_id="", client=client)
    neto = Warehouse.objects.create(name="NETO")
    atlantic = Warehouse.objects.create(name="Atlantic Express")
    number = _iso("MSCU123456")
    container = Container.objects.create(number=number, status="FLOATING", warehouse=neto)
    payload = {
        "containers": [
            {
                "number": number,
                "warehouse": "Atlantic",
                "cars": [
                    {
                        "vin": "1HGCM82633A004352",
                        "brand": "Honda",
                        "year": 2020,
                        "office": "PLB Office",
                        "caromoto_client_id": "1",
                    }
                ],
            }
        ]
    }
    batch, _diff = store_batch("parser", payload)
    report = apply_batch(batch.pk)
    container.refresh_from_db()
    assert container.warehouse_id == neto.pk
    assert report["cars"][0]["action"] == "create"
    assert Car.objects.get(vin="1HGCM82633A004352").client_id == client.pk

    payload["containers"][0]["cars"][0]["vin"] = "1HGCM82633A004353"
    batch2, _diff = store_batch("parser", payload)
    apply_batch(batch2.pk, overwrite_warehouse=True)
    container.refresh_from_db()
    assert container.warehouse_id == atlantic.pk


def test_car_upsert_does_not_notify_or_create_container(django_capture_on_commit_callbacks):
    client = Client.objects.create(name="Buyer")
    Warehouse.objects.create(name="Atlantic Express")
    with django_capture_on_commit_callbacks(execute=True):
        with patch("core.tasks.send_car_unload_notification_task.delay") as delayed:
            result = car_upsert(
                vin="1HGCM82633A004352",
                brand="Honda",
                model="Accord",
                year=2020,
                client_id=client.pk,
                warehouse_code="ATLANTIC",
                unload_site=2,
                foreign_container_number="TCLU8824843",
                status="UNLOADED",
                unload_date=datetime.date(2026, 10, 20),
                eta=datetime.date(2026, 10, 18),
                notify=False,
                apply=True,
            )
    delayed.assert_not_called()
    car = Car.objects.get(pk=result["result"]["id"])
    assert car.container_id is None
    assert car.foreign_container_number == "TCLU8824843"
    assert car.foreign_eta == datetime.date(2026, 10, 18)
    assert car.unload_site == 2
    assert car.source_title == "Honda Accord"
    assert result["notifications"] == []


def test_parser_can_replace_warehouse_with_flag():
    _principal, token = issue_token("parser-wh", "parser")
    neto = Warehouse.objects.create(name="NETO")
    atlantic = Warehouse.objects.create(name="Atlantic Express")
    Container.objects.create(
        number="BOTWH000000001", status="UNLOADED", warehouse=neto, unload_date=datetime.date(2026, 10, 1)
    )
    kept = set_container_status(
        number="BOTWH000000001",
        status="UNLOADED",
        warehouse_code="ATLANTIC",
        apply=False,
    )
    assert kept["result"]["warehouse_kept"] is True
    out = StringIO()
    call_command(
        "bot",
        "set-container-status",
        "--token",
        token,
        "--container",
        "BOTWH000000001",
        "--status",
        "UNLOADED",
        "--warehouse",
        "ATLANTIC",
        "--set-warehouse",
        "--apply",
        "--idempotency-key",
        "wh-1",
        "--no-notify",
        stdout=out,
    )
    assert json.loads(out.getvalue())["ok"] is True
    assert Container.objects.get(number="BOTWH000000001").warehouse_id == atlantic.pk


def test_chief_cannot_upsert_car():
    _principal, token = issue_token("chief-no-car", "chief")
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("bot", "car-upsert", "--token", token, "--vin", "1HGCM82633A004352", stdout=out)
    assert json.loads(out.getvalue())["ok"] is False


def test_neto_preview_subject_files_and_client_docs():
    client = Client.objects.create(name="Buyer")
    car = Car.objects.create(vin="1HGCM82633A004352", brand="Honda", year=2020, status="UNLOADED", client=client)
    request = TransportRequest.objects.create(
        client=client, driver_name="Jonas", carrier_eori="IK185300064", declaration_type="TRANSIT"
    )
    request.cars.add(car)
    bad = neto_bundle(request.number, apply=False)
    assert bad["result"]["subject"].startswith("Konteinerio dokumentai ")
    assert "BLR" in bad["result"]["subject"]
    assert "evaldas.k@netoterminal.lt" in bad["result"]["recipients"]
    assert "EORI_FORMAT" in bad["result"]["completeness"]["request_missing"]

    request.carrier_eori = "LT3066363532"
    request.save(update_fields=["carrier_eori"])
    report = completeness(request, client_docs=True)
    assert report["cars"][0]["missing"] == ["TITLE", "CLIENT_PDF", "OBLIGATION"]

    blob = SimpleUploadedFile("doc.pdf", b"%PDF-1.4", content_type="application/pdf")

    def _doc(doc_type, generated):
        return TransportRequestDocument.objects.create(
            request=request,
            car=car,
            doc_type=doc_type,
            is_generated=generated,
            file=SimpleUploadedFile(f"{doc_type}.pdf", blob.read() or b"%PDF-1.4", content_type="application/pdf"),
        )

    blob.seek(0)
    _doc("TITLE", False)
    _doc("OTHER", False)
    _doc("OBLIGATION", True)
    _doc("OBLIGATION", True)
    ready = neto_bundle(request.number, apply=False, client_docs=True)
    assert ready["result"]["completeness"]["cars"][0]["missing"] == []
    types = [row["type"] for row in ready["result"]["files"]]
    assert types.count("OBLIGATION") == 2
    assert "OTHER" in types
    assert "TITLE" in types
