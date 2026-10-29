"""Пакет документов для Neto: чего не хватает, ZIP, тема письма с номером BLR."""

from __future__ import annotations

import os
import re

from django.db import transaction
from django.utils import timezone

from core.services.bot_runtime import BotError

_REQUIRED = ("TITLE", "PASSPORT", "INVOICE", "PAYMENT_ORDER", "LETTER_USA", "CONTRACT", "SIGNATURE")
_CLIENT_DOC_TYPES = {"TITLE", "OTHER", "OBLIGATION"}
# Код страны EORI (ЕС, Греция как EL, плюс знакомые префиксы логистики).
# IK — код компании, не страна: Исландия это IS. XI — Северная Ирландия.
_EORI_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "EL",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        "BY",
        "NO",
        "IS",
        "LI",
        "CH",
        "GB",
        "XI",
        "UA",
        "MD",
        "RS",
    }
)
_EORI_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{1,15}$")
# BLR — тема письма Neto. Домен общий, в задании он указан у последнего адреса.
BLR_RECIPIENTS = [
    "dovile@netoterminal.lt",
    "rita@netoterminal.lt",
    "customs@netoterminal.lt",
    "evaldas@netoterminal.lt",
    "evaldas.k@netoterminal.lt",
    "ramune@netoterminal.lt",
]


def eori_problem(value: str) -> str | None:
    """Пусто — нет кода. Код компании вроде IK185300064 — не EORI.

    Годный код: две буквы страны из известного списка и ещё до 15 букв
    или цифр. Польские перевозчики (PL…) проходят, IK… — нет.
    """
    raw = (value or "").strip().upper()
    if not raw:
        return "EORI"
    if not _EORI_RE.match(raw) or raw[:2] not in _EORI_COUNTRIES:
        return "EORI_FORMAT"
    return None


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


def _missing_client_brought(request, car) -> list[str]:
    from core.models.website import TransportRequestDocument

    docs = list(TransportRequestDocument.objects.filter(request=request, car=car))
    present = {doc.doc_type for doc in docs}
    if car.title_scan:
        present.add("TITLE")
    missing = []
    if "TITLE" not in present:
        missing.append("TITLE")
    client_pdf = any(not doc.is_generated and doc.doc_type == "OTHER" for doc in docs)
    if not client_pdf:
        missing.append("CLIENT_PDF")
    if sum(1 for doc in docs if doc.doc_type == "OBLIGATION") < 2:
        missing.append("OBLIGATION")
    return missing


def completeness(request, *, client_docs: bool = False) -> dict:
    rows = []
    for car in request.cars.all().order_by("id"):
        missing = _missing_client_brought(request, car) if client_docs else _missing_for_car(request, car)
        rows.append(
            {
                "vin": car.vin,
                "weight_kg": str(car.weight_kg) if car.weight_kg else None,
                "missing": missing,
            }
        )
    request_missing = []
    if not (request.driver_name or "").strip():
        request_missing.append("DRIVER")
    eori = (request.carrier_eori or "").strip()
    if request.auto_transport_id and not eori:
        eori = request.auto_transport.eori_code or ""
    problem = eori_problem(eori)
    if problem:
        request_missing.append(problem)
    return {
        "number": request.number,
        "request_missing": request_missing,
        "cars": rows,
        "client_docs": client_docs,
    }


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
    return list(getattr(settings, "NETO_MAIL_RECIPIENTS", BLR_RECIPIENTS))


def preview_subject(code: str) -> str:
    """Следующий номер BLR без записи в счётчик."""
    from core.models.bots import NetoDispatchCounter

    today = timezone.localdate()
    row = NetoDispatchCounter.objects.filter(day=today, warehouse_code=code).first()
    seq = (row.seq if row else 0) + 1
    return f"Konteinerio dokumentai {today:%d.%m.%Y} BLR {seq:02d}"


def bundle_files(request, *, client_docs: bool = False) -> list[dict]:
    from core.models.website import TransportRequestDocument

    allowed = _CLIENT_DOC_TYPES if client_docs else None
    rows = []
    for car in request.cars.all().order_by("id"):
        docs = list(TransportRequestDocument.objects.filter(request=request, car=car))
        has_title = False
        for doc in docs:
            if doc.doc_type == "SIGNATURE":
                continue
            if allowed is not None and doc.doc_type not in allowed:
                continue
            if doc.doc_type == "TITLE":
                has_title = True
            rows.append({"id": doc.pk, "vin": car.vin, "type": doc.doc_type, "filename": doc.filename})
        if not has_title and getattr(car, "title_scan", None) and (allowed is None or "TITLE" in allowed):
            name = os.path.basename(car.title_scan.name) if car.title_scan.name else "title.pdf"
            rows.append({"id": f"car_{car.pk}_title", "vin": car.vin, "type": "TITLE", "filename": name})
    return rows


def next_blr_number(code: str) -> int:
    from core.models.bots import NetoDispatchCounter

    today = timezone.localdate()
    with transaction.atomic():
        row, _created = NetoDispatchCounter.objects.get_or_create(day=today, warehouse_code=code)
        locked = NetoDispatchCounter.objects.select_for_update().get(pk=row.pk)
        locked.seq += 1
        locked.save(update_fields=["seq"])
        return locked.seq


def neto_bundle(number: str, *, apply: bool, force: bool = False, client_docs: bool = False) -> dict:
    from pathlib import Path

    from core.models.website import TransportRequest
    from core.services.transport_docs import PackageDataError, build_request_packages_zip

    request = TransportRequest.objects.filter(number__iexact=number.strip()).first()
    if request is None:
        raise BotError(f"Заявка {number} не найдена.")
    report = completeness(request, client_docs=client_docs)
    code = _warehouse_code(request)
    files = bundle_files(request, client_docs=client_docs)
    subject = preview_subject(code)
    blocked = report["request_missing"] or any(row["missing"] for row in report["cars"])
    path = None
    only_types = _CLIENT_DOC_TYPES if client_docs else None
    if apply:
        if blocked and not force:
            raise BotError("Пакет неполный. Сначала закройте missing или передайте --force.")
        seq = next_blr_number(code)
        subject = f"Konteinerio dokumentai {timezone.localdate():%d.%m.%Y} BLR {seq:02d}"
        try:
            filename, payload = build_request_packages_zip(request, only_types=only_types)
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
            "files": files,
            "path": path,
            "client_docs": client_docs,
            "obligation": "клиент приносит тайтл, свой PDF и два ĮSIPAREIGOJIMAS"
            if client_docs
            else "только транзит и не мото",
        },
        "notifications": [],
    }
