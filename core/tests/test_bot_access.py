"""Боты: токен и роль, статусы без писем, почта, импорт, тариф по своим машинам."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import Car, Client, Container, Warehouse
from core.models.bots import BotJob, Proforma
from core.models.email import ContainerEmail
from core.services.bot_import import build_diff, iso6346_ok
from core.services.bot_mail import enrich_email_for_bots, neto_status_from_text, parse_evaldas_date
from core.services.bot_ops import set_container_status
from core.services.bot_runtime import issue_token
from core.services.car_service_manager import apply_client_tariffs_for_container

pytestmark = pytest.mark.django_db


def _iso(prefix10: str) -> str:
    letters = {}
    value = 10
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        letters[char] = value
        value += 1
        if value % 11 == 0:
            value += 1
    total = 0
    for index, char in enumerate(prefix10):
        total += (int(char) if char.isdigit() else letters[char]) * (2**index)
    return prefix10 + str(total % 11 % 10)


def test_iso6346_check_digit():
    number = _iso("CSQU305438")
    assert iso6346_ok(number)
    assert iso6346_ok("CSQU3054383")
    assert not iso6346_ok("EBKG1856942")


def test_evaldas_date_and_neto_phrase():
    received = datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc)
    assert parse_evaldas_date("Krausime 10.22d", received) == datetime.date(2026, 10, 22)
    assert neto_status_from_text("Krovinys SUFORMINTA") == "COMPLETED"
    assert neto_status_from_text("Leidimas užsakytas") == "IN_PROGRESS"


def test_unknown_vin_becomes_job():
    email = ContainerEmail.objects.create(
        message_id="<vin-test@example>",
        subject="Tesla 5YJ3E1EA1PF499330",
        body_text="",
        received_at=timezone.now(),
        direction="INCOMING",
    )
    result = enrich_email_for_bots(email)
    assert result["unknown_vins"] == ["5YJ3E1EA1PF499330"]
    assert BotJob.objects.filter(job_type="UNKNOWN_VIN", source_email=email).exists()


def test_chief_cannot_change_status():
    _principal, token = issue_token("chief-test", "chief")
    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "bot", "set-container-status", "--token", token, "--container", "X", "--status", "IN_PORT", stdout=out
        )
    payload = json.loads(out.getvalue())
    assert payload["ok"] is False


def test_status_dry_run_and_mute(warehouse=None):
    warehouse = Warehouse.objects.create(name="NETO Klaipeda")
    container = Container.objects.create(number="BOTSTATUS000001", status="FLOATING", warehouse=warehouse)
    preview = set_container_status(number=container.number, status="IN_PORT", apply=False)
    container.refresh_from_db()
    assert container.status == "FLOATING"
    assert preview["result"]["outcome"] == "would_update"

    with patch("core.tasks.send_unload_notifications_task.delay") as delayed:
        applied = set_container_status(
            number=container.number,
            status="UNLOADED",
            unload_date=timezone.localdate(),
            notify=False,
            apply=True,
        )
    container.refresh_from_db()
    assert container.status == "UNLOADED"
    assert applied["result"]["outcome"] == "updated"
    delayed.assert_not_called()


def test_idempotent_apply_replays():
    _principal, token = issue_token("handover-test", "handover")
    Warehouse.objects.create(name="NETO")
    Container.objects.create(number="BOTIDEM0000001", status="FLOATING")
    args = [
        "bot",
        "set-container-status",
        "--token",
        token,
        "--container",
        "BOTIDEM0000001",
        "--status",
        "IN_PORT",
        "--apply",
        "--idempotency-key",
        "once",
        "--no-notify",
    ]
    first = StringIO()
    call_command(*args, stdout=first)
    second = StringIO()
    call_command(*args, stdout=second)
    assert json.loads(second.getvalue())["idempotent_replay"] is True


def test_caromoto_diff_does_not_create_client_and_holds_maersk_eta():
    container = Container.objects.create(
        number=_iso("MSCU123456"),
        status="FLOATING",
        eta=datetime.date(2026, 12, 19),
        eta_source="MAERSK_API",
    )
    payload = {
        "containers": [
            {
                "number": container.number,
                "eta": "2026-12-12",
                "cars": [
                    {
                        "vin": "1HGCM82633A004352",
                        "brand": "Honda",
                        "year": 2020,
                        "caromoto_client_id": "99",
                        "office": "KLP",
                    }
                ],
            }
        ]
    }
    diff = build_diff(payload)
    assert diff["containers"][0]["eta_decision"] == "held"
    assert diff["containers"][0]["cars"][0]["action"] == "skip"
    assert Client.objects.count() == 0


def test_flexible_tariff_counts_only_this_client():
    warehouse = Warehouse.objects.create(name="WH-BOT")
    container = Container.objects.create(number="BOTFLEX0000001", status="FLOATING", warehouse=warehouse)
    moldova = Client.objects.create(name="CAROMOTO MOLDOVA", tariff_type="FLEXIBLE")
    other = Client.objects.create(name="Other", tariff_type="FLEXIBLE")
    from core.models import ClientTariffRate

    for client in (moldova, other):
        ClientTariffRate.objects.create(
            client=client, vehicle_type="SEDAN", min_cars=1, max_cars=2, agreed_total_price=Decimal("360")
        )
        ClientTariffRate.objects.create(
            client=client, vehicle_type="SEDAN", min_cars=3, max_cars=4, agreed_total_price=Decimal("290")
        )
    for index, client in enumerate((moldova, moldova, other, other)):
        Car.objects.create(
            year=2020,
            brand="VW",
            vin=f"BOTFLEXVIN0000{index:03d}",
            status="FLOATING",
            container=container,
            warehouse=warehouse,
            client=client,
            vehicle_type="SEDAN",
        )
    with patch("core.services.car_service_manager._get_agreed_total", return_value=None) as agreed:
        apply_client_tariffs_for_container(container)
    counts = [call.args[2] for call in agreed.call_args_list]
    assert counts
    assert set(counts) == {2}


def test_proforma_match_does_not_need_invoice_status():
    proforma = Proforma.objects.create(number="AV 301987300", kind="UNOFFICIAL", amount=Decimal("100"))
    from core.services.bot_finance import match_proforma

    preview = match_proforma(number=proforma.number, bank_transaction_id=1, confirm=False)
    assert preview["result"]["outcome"] == "proposed"
    proforma.refresh_from_db()
    assert proforma.paid_amount == 0


def test_dictionary_lists_notification_fields():
    _principal, token = issue_token("chief-dict", "chief")
    out = StringIO()
    call_command("bot", "dictionary", "--token", token, stdout=out)
    payload = json.loads(out.getvalue())
    fields = [row["field"] for row in payload["result"]["notifications"]]
    assert "Container.unload_date" in fields
    assert "Car.status=TRANSFERRED" in fields
