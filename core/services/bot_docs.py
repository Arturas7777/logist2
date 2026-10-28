"""Пакет документов для Neto: чего не хватает, ZIP, тема письма с номером BLR."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.services.bot_runtime import BotError

_REQUIRED = ("TITLE", "PASSPORT", "INVOICE", "PAYMENT_ORDER", "LETTER_USA", "CONTRACT", "SIGNATURE")


def _missing_for_car(request, car) -> list[str]:
    from core.models.website import TransportRequestDocument

    present = set(TransportRequestDocument.objects.filter(request=request, car=car).values_list("doc_type", flat=True))
    if car.title_scan:
        present.add("TITLE")
    missing = [code for code in _REQUIRED if code not in present]
    if not car.weight_kg:
        missing.append("WEIGHT")
    moto = car.vehicle_type in {"MOTO", "BIG_MOTO"}
    transit = (request.declaration_type or "") == "TRANSIT"
    if transit and not moto and "OBLIGATION" not in present:
        missing.append("OBLIGATION")
    if not transit or moto:
        missing = [code for code in missing if code != "OBLIGATION"]
    return missing


def completeness(request) -> dict:
    rows = []
    for car in request.cars.all().order_by("id"):
        rows.append(
            {
                "vin": car.vin,
                "weight_kg": str(car.weight_kg) if car.weight_kg else None,
                "missing": _missing_for_car(request, car),
            }
        )
    request_missing = []
    if not (request.driver_name or "").strip():
        request_missing.append("DRIVER")
    eori = (request.carrier_eori or "").strip()
    if request.auto_transport_id and not eori:
        eori = request.auto_transport.eori_code or ""
    if not eori:
        request_missing.append("EORI")
    return {"number": request.number, "request_missing": request_missing, "cars": rows}


def _warehouse_code(request) -> str:
    names = []
    for car in request.cars.select_related("warehouse"):
        if car.warehouse_id:
            names.append(car.warehouse.name.lower())
    if any("atlantic" in name for name in names):
        return "ATLANTIC"
    return "NETO"


def recipients_for(code: str) -> list[str]:
    from django.conf import settings

    if code == "ATLANTIC":
        return list(getattr(settings, "ATLANTIC_MAIL_RECIPIENTS", ["info@atlanticexpress.lt"]))
    return list(getattr(settings, "NETO_MAIL_RECIPIENTS", ["evaldas@netoterminal.lt"]))


def next_blr_number(code: str) -> int:
    from core.models.bots import NetoDispatchCounter

    today = timezone.localdate()
    with transaction.atomic():
        row, _created = NetoDispatchCounter.objects.get_or_create(day=today, warehouse_code=code)
        locked = NetoDispatchCounter.objects.select_for_update().get(pk=row.pk)
        locked.seq += 1
        locked.save(update_fields=["seq"])
        return locked.seq


def neto_bundle(number: str, *, apply: bool, force: bool = False) -> dict:
    from pathlib import Path

    from core.models.website import TransportRequest
    from core.services.transport_docs import PackageDataError, build_request_packages_zip

    request = TransportRequest.objects.filter(number__iexact=number.strip()).first()
    if request is None:
        raise BotError(f"Заявка {number} не найдена.")
    report = completeness(request)
    code = _warehouse_code(request)
    blocked = report["request_missing"] or any(row["missing"] for row in report["cars"])
    subject = None
    path = None
    if apply:
        if blocked and not force:
            raise BotError("Пакет неполный. Сначала закройте missing или передайте --force.")
        seq = next_blr_number(code)
        subject = f"Konteinerio dokumentai {timezone.localdate():%d.%m.%Y} BLR {seq:02d}"
        try:
            filename, payload = build_request_packages_zip(request)
        except PackageDataError as exc:
            raise BotError(str(exc)) from exc
        folder = Path("media") / "bot_tmp" / "neto"
        from django.conf import settings

        folder = Path(settings.MEDIA_ROOT) / "bot_tmp" / "neto"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        target.write_bytes(payload)
        path = str(target)
    return {
        "before": {},
        "after": {"subject": subject, "path": path, "warehouse": code},
        "result": {
            "completeness": report,
            "recipients": recipients_for(code),
            "subject": subject,
            "path": path,
            "obligation": "только транзит и не мото",
        },
        "notifications": [],
    }
