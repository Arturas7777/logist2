"""Импорт JSON с Caromoto: проверка, diff, применение без создания клиентов."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_date

from core.services.bot_runtime import BotError
from core.services.vin import normalize_vin

_LETTERS = {}
_value = 10
for _char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _LETTERS[_char] = _value
    _value += 1
    if _value % 11 == 0:
        _value += 1

_ISO_RE = re.compile(r"^[A-Z]{4}\d{7}$")


def iso6346_ok(number: str) -> bool:
    raw = (number or "").strip().upper()
    if not _ISO_RE.match(raw):
        return False
    total = 0
    for index, char in enumerate(raw[:10]):
        total += (int(char) if char.isdigit() else _LETTERS[char]) * (2**index)
    return total % 11 % 10 == int(raw[10])


def _line(name: str | None):
    from core.models import Line

    if not name:
        return None
    return (
        Line.objects.filter(name__iexact=name.strip()).first()
        or Line.objects.filter(name__icontains=name.strip()).first()
    )


def _warehouse(name: str | None):
    from core.models import Warehouse

    if not name:
        return None
    return (
        Warehouse.objects.filter(name__iexact=name.strip()).first()
        or Warehouse.objects.filter(name__icontains=name.strip()).first()
    )


def _map_client(office: str, caromoto_client_id: str):
    from core.models.bots import CaromotoClientMap

    if not caromoto_client_id:
        return None
    row = (
        CaromotoClientMap.objects.filter(caromoto_client_id=caromoto_client_id, office=office or "")
        .order_by("-priority")
        .first()
    )
    if row is None:
        row = CaromotoClientMap.objects.filter(caromoto_client_id=caromoto_client_id).order_by("-priority").first()
    return row.client if row else None


def _hold_eta(container, new_eta, source: str) -> str:
    if container is None or new_eta is None:
        return "set"
    if container.eta == new_eta:
        return "same"
    if container.eta_source == "MAERSK_API" and container.eta and container.eta != new_eta:
        return "held"
    return "set"


def build_diff(payload: dict) -> dict:
    from core.models import Car, Container

    containers_out = []
    for raw in payload.get("containers") or []:
        number = (raw.get("number") or "").strip().upper()
        booking = (raw.get("booking_number") or "").strip().upper()
        item = {"number": number, "booking_number": booking, "errors": [], "cars": [], "action": "skip"}
        if number and not iso6346_ok(number):
            item["errors"].append("номер не проходит ISO 6346, похоже на букинг — в номер контейнера не пишем")
            if not booking:
                booking = number
                item["booking_number"] = booking
            number = ""
            item["number"] = ""
        container = None
        if number:
            container = Container.objects.filter(number__iexact=number).first()
        elif booking:
            container = Container.objects.filter(booking_number__iexact=booking).first()
            if container is None:
                container = Container.objects.filter(number__iexact=booking).first()
        item["action"] = "update" if container else "create"
        eta = parse_date(raw.get("eta") or "") if raw.get("eta") else None
        item["eta"] = eta.isoformat() if isinstance(eta, datetime.date) else None
        item["eta_decision"] = _hold_eta(container, eta, "CAROMOTO")
        if raw.get("line") and _line(raw.get("line")) is None:
            item["errors"].append(f"линия не найдена: {raw.get('line')}")
        if raw.get("warehouse") and _warehouse(raw.get("warehouse")) is None:
            item["errors"].append(f"склад не найден: {raw.get('warehouse')}")
        for car in raw.get("cars") or []:
            vin = normalize_vin(car.get("vin") or "")
            brand = (car.get("brand") or "").strip()
            car_row = {"vin": vin, "action": "skip", "errors": []}
            if len(vin) != 17:
                car_row["errors"].append("VIN должен быть 17 символов")
            if len(brand) > 50:
                car_row["errors"].append("brand длиннее 50")
            if not car.get("year"):
                car_row["errors"].append("нет года")
            existing = Car.objects.filter(vin=vin).first() if len(vin) == 17 else None
            client = _map_client(car.get("office") or "", str(car.get("caromoto_client_id") or ""))
            if existing is None and client is None and car.get("caromoto_client_id"):
                car_row["errors"].append("клиент Caromoto не сопоставлен, машина не создаётся")
            if existing is None and not car.get("caromoto_client_id") and not car_row["errors"]:
                car_row["errors"].append("нет caromoto_client_id")
            car_row["action"] = "update" if existing else ("create" if not car_row["errors"] else "skip")
            item["cars"].append(car_row)
        containers_out.append(item)
    created = sum(1 for row in containers_out if row["action"] == "create")
    updated = sum(1 for row in containers_out if row["action"] == "update")
    return {
        "containers": containers_out,
        "summary": {"create": created, "update": updated, "rows": len(containers_out)},
    }


def store_batch(principal_name: str, payload: dict):
    from core.models.bots import CaromotoImportBatch

    diff = build_diff(payload)
    batch = CaromotoImportBatch.objects.create(principal_name=principal_name, diff={"payload": payload, "diff": diff})
    return batch, diff


def apply_batch(batch_id: int) -> dict:
    from core.models import Car, Container
    from core.models.bots import CaromotoImportBatch, EtaHistory

    batch = CaromotoImportBatch.objects.filter(pk=batch_id).first()
    if batch is None:
        raise BotError(f"Партия {batch_id} не найдена.")
    if batch.applied_at:
        raise BotError("Партия уже применена.")
    payload = (batch.diff or {}).get("payload") or {}
    report = {"containers": [], "cars": []}
    for raw in payload.get("containers") or []:
        number = (raw.get("number") or "").strip().upper()
        booking = (raw.get("booking_number") or "").strip().upper()
        if number and not iso6346_ok(number):
            if not booking:
                booking = number
            number = ""
        container = None
        renamed = False
        if number:
            container = Container.objects.filter(number__iexact=number).first()
            if container is None and booking:
                placeholder = Container.objects.filter(number__iexact=booking).first()
                if placeholder is not None:
                    placeholder.number = number
                    placeholder.booking_number = booking
                    placeholder.save(update_fields=["number", "booking_number"])
                    container = placeholder
                    renamed = True
        elif booking:
            container = Container.objects.filter(booking_number__iexact=booking).first()
            if container is None:
                container = Container.objects.filter(number__iexact=booking).first()
        if container is None:
            if not number and not booking:
                report["containers"].append({"error": "нет номера и букинга"})
                continue
            container = Container(number=number or booking, booking_number=booking, status="FLOATING")
        elif booking and not container.booking_number:
            container.booking_number = booking
        line = _line(raw.get("line"))
        warehouse = _warehouse(raw.get("warehouse"))
        if line and container.line_id is None:
            container.line = line
        if warehouse and container.warehouse_id is None:
            container.warehouse = warehouse
        eta = parse_date(raw.get("eta") or "") if raw.get("eta") else None
        previous_eta = container.eta
        eta_decision = _hold_eta(container if container.pk else None, eta, "CAROMOTO")
        if eta and eta_decision != "held":
            container.eta = eta
            container.eta_source = Container.ETA_SOURCE_CAROMOTO
            container.eta_updated_at = timezone.now()
        container.save()
        if eta and eta_decision == "set":
            EtaHistory.objects.create(
                container=container,
                eta=container.eta,
                previous_eta=previous_eta,
                source=Container.ETA_SOURCE_CAROMOTO,
            )
        report["containers"].append(
            {
                "number": container.number,
                "booking_number": container.booking_number,
                "renamed": renamed,
                "eta": eta_decision,
            }
        )
        for car in raw.get("cars") or []:
            vin = normalize_vin(car.get("vin") or "")
            brand = (car.get("brand") or "").strip()
            if len(vin) != 17 or len(brand) > 50 or not car.get("year"):
                report["cars"].append({"vin": vin, "action": "skip"})
                continue
            client = _map_client(car.get("office") or "", str(car.get("caromoto_client_id") or ""))
            existing = Car.objects.filter(vin=vin).first()
            if existing is None and client is None:
                report["cars"].append({"vin": vin, "action": "skip", "reason": "no-client"})
                continue
            if existing is None:
                existing = Car(
                    vin=vin,
                    brand=brand[:50],
                    year=int(car["year"]),
                    status=container.status,
                    container=container,
                    warehouse=container.warehouse,
                    line=container.line,
                    client=client,
                )
                action = "create"
            else:
                action = "update"
                if client and existing.client_id is None:
                    existing.client = client
                if existing.container_id is None:
                    existing.container = container
            existing.caromoto_client_id = str(car.get("caromoto_client_id") or existing.caromoto_client_id or "")
            existing.caromoto_office = car.get("office") or existing.caromoto_office or ""
            existing.foreign_container_number = (
                car.get("foreign_container_number") or existing.foreign_container_number or ""
            )[:20]
            existing.foreign_line = (car.get("foreign_line") or existing.foreign_line or "")[:64]
            if brand and not existing.source_title:
                existing.source_title = brand[:255]
            existing.save()
            report["cars"].append({"vin": vin, "action": action})
    batch.applied_at = timezone.now()
    batch.save(update_fields=["applied_at"])
    return report


def _tariff_package_price(car) -> Decimal:
    """Склад без хранения плюс THS линии — то, что приводит к тарифу пересчёт."""
    from core.models.services import CarService
    from core.service_codes import is_storage_service, is_ths_service

    total = Decimal("0")
    for service in CarService.objects.filter(car=car):
        if service.service_type == "WAREHOUSE" and not is_storage_service(service):
            total += Decimal(str(service.invoice_price))
        elif service.service_type == "LINE" and is_ths_service(service):
            total += Decimal(str(service.invoice_price))
    return total


def flexible_tariff_preview(client_name: str = "CAROMOTO MOLDOVA") -> dict:
    """Машины, у которых пакет склада+THS не равен ставке по своим авто в контейнере."""
    from core.models import Car, Client
    from core.services.car_service_manager import _count_vehicles_for_tariff, _get_agreed_total, _tariff_line_id

    client = Client.objects.filter(name__icontains=client_name, tariff_type="FLEXIBLE").first()
    if client is None:
        return {"client": None, "rows": []}
    rows = []
    cars = Car.objects.filter(client=client, container_id__isnull=False).select_related("container")
    for car in cars:
        mates = list(Car.objects.filter(container_id=car.container_id, client_id=client.id).only("id", "vehicle_type"))
        count = _count_vehicles_for_tariff(mates, car.vehicle_type)
        agreed = _get_agreed_total(client, car.vehicle_type, count, line_id=_tariff_line_id(car))
        if agreed is None:
            continue
        current = _tariff_package_price(car)
        if current != Decimal(str(agreed)):
            rows.append(
                {
                    "id": car.pk,
                    "vin": car.vin,
                    "container": car.container.number if car.container_id else "",
                    "current": str(current),
                    "agreed": str(agreed),
                    "own_cars": count,
                }
            )
    return {"client": client.name, "rows": rows}
