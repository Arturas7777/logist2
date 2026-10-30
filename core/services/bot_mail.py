"""Разбор писем для ботов: все номера из PDF, ответы Neto, план Evaldas, VIN."""

from __future__ import annotations

import datetime
import logging
import re

from django.db import transaction
from django.utils import timezone

from core.services.email_matcher import _CONTAINER_NUMBER_RE
from core.services.vin import find_vins

logger = logging.getLogger(__name__)

_NETO_RULES = (
    ("SUFORMINTA", "COMPLETED"),
    ("SURUOŠTA", "IN_PROGRESS"),
    ("SURUOSTA", "IN_PROGRESS"),
    ("LEIDIMAS UŽSAKYTAS", "IN_PROGRESS"),
    ("LEIDIMAS UZSAKYTAS", "IN_PROGRESS"),
)
_DATE_D = re.compile(r"(\d{1,2})[./](\d{1,2})d\b", re.IGNORECASE)
# «MSC - MSBU5269952 - ETA 2026-10-31» — строка копирования, не план разгрузки.
_COPY_ETA = re.compile(r"(?i)\b([A-Z]{4}\d{7})\b\s*-\s*ETA\s+(20\d{2})-(\d{2})-(\d{2})\b")
_UNLOAD_PHRASE = re.compile(r"krovos\s+datos|krausime", re.IGNORECASE)
_HANDOVER = re.compile(r"perduodam", re.IGNORECASE)


def container_numbers_in_text(text: str) -> list[str]:
    found = []
    seen = set()
    for match in _CONTAINER_NUMBER_RE.finditer((text or "").upper()):
        number = match.group(1)
        if number not in seen:
            seen.add(number)
            found.append(number)
    return found


def neto_status_from_text(text: str) -> str | None:
    folded = (text or "").upper()
    for phrase, status in _NETO_RULES:
        if phrase in folded:
            return status
    return None


def _copy_eta_dates(text: str) -> list[datetime.date]:
    """Даты из «НОМЕР - ETA ГГГГ-ММ-ДД». Это ETA контейнера, не план разгрузки."""
    found: list[datetime.date] = []
    for match in _COPY_ETA.finditer(text or ""):
        try:
            day = datetime.date(int(match.group(2)), int(match.group(3)), int(match.group(4)))
        except ValueError:
            continue
        if day not in found:
            found.append(day)
    return found


def _d_dates(text: str, received) -> list[datetime.date]:
    """Уникальные даты формата «10.22d», без строк копирования контейнеров."""
    raw = _COPY_ETA.sub(" ", text or "")
    found: list[datetime.date] = []
    year = received.year if received else timezone.localdate().year
    for match in _DATE_D.finditer(raw):
        month, day = int(match.group(1)), int(match.group(2))
        if month > 12 and day <= 12:
            month, day = day, month
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        try:
            parsed = datetime.date(year, month, day)
        except ValueError:
            continue
        if parsed not in found:
            found.append(parsed)
    return found


def _explicit_unload_date(text: str, received) -> datetime.date | None:
    """Одна команда «krausime» / «krovos datos» и ровно одна дата «10.22d»."""
    cleaned = _COPY_ETA.sub(" ", text or "")
    if not _UNLOAD_PHRASE.search(cleaned):
        return None
    dates = _d_dates(text, received)
    if len(dates) == 1:
        return dates[0]
    return None


def parse_evaldas_date(text: str, received) -> datetime.date | None:
    """План разгрузки из письма Evaldas.

    Строка копирования «MSC - НОМЕР - ETA ГГГГ-ММ-ДД» и первая попавшаяся
    дата ГГГГ-ММ-ДД в цитате планом не являются. Несколько разных ETA без
    одной команды «Krausime 10.22d» тоже не дают дату на все контейнеры.
    """
    explicit = _explicit_unload_date(text, received)
    if explicit:
        return explicit
    if _copy_eta_dates(text or ""):
        return None
    dates = _d_dates(text or "", received)
    if len(dates) == 1:
        return dates[0]
    return None


def is_atlantic_unload(subject: str) -> bool:
    folded = (subject or "").upper()
    return "KROVA" in folded and "SANDELIAI" in folded


def is_handover_mail(text: str) -> bool:
    return bool(_HANDOVER.search(text or ""))


def _pdf_text(email) -> str:
    from core.services.email_attachments import cached_path
    from core.services.invoice_audit_service import extract_text_from_pdf

    chunks = []
    for att in email.attachments_json or []:
        name = (att.get("filename") or "").lower()
        ctype = (att.get("content_type") or "").lower()
        if not (name.endswith(".pdf") or "pdf" in ctype):
            continue
        path = cached_path(att)
        if path is None:
            continue
        try:
            chunks.append(extract_text_from_pdf(str(path)) or "")
        except Exception:
            logger.warning("PDF письма %s не прочитан: %s", email.pk, att.get("filename"), exc_info=True)
    return "\n".join(chunks)


def _ensure_job(email, job_type: str, role: str, payload: dict):
    from core.models.bots import BotJob

    BotJob.objects.get_or_create(
        source_email=email,
        job_type=job_type,
        defaults={"role": role, "payload": payload, "status": BotJob.STATUS_NEW},
    )


def _link_container_numbers(email, numbers: list[str]) -> list[str]:
    from core.models import Container
    from core.models.email import ContainerEmailLink

    linked = []
    existing = set(email.container_links.values_list("container_id", flat=True))
    for number in numbers:
        container = Container.objects.filter(number__iexact=number).only("id").first()
        if container is None or container.id in existing:
            continue
        ContainerEmailLink.objects.get_or_create(
            email=email,
            container_id=container.id,
            defaults={"matched_by": "CONTAINER_NUMBER"},
        )
        existing.add(container.id)
        linked.append(number)
    return linked


def _remember_unknown_vins(email) -> list[str]:
    from core.models import Car

    text = f"{email.subject or ''}\n{email.body_text or ''}"
    vins = find_vins(text)
    if not vins:
        email.unknown_vins = []
        return []
    known = set(Car.objects.filter(vin__in=vins).values_list("vin", flat=True))
    missing = [vin for vin in vins if vin not in known]
    email.unknown_vins = missing
    if missing:
        _ensure_job(
            email,
            "UNKNOWN_VIN",
            "neto",
            {"vins": missing, "subject": (email.subject or "")[:200]},
        )
    return missing


def _apply_neto_status(email) -> list[dict]:
    from core.services.bot_ops import advance_tr_to

    status = neto_status_from_text(f"{email.subject or ''}\n{email.body_text or ''}")
    if not status:
        return []
    changed = []
    for request in email.transport_requests.all():
        path = advance_tr_to(request, status)
        if path:
            changed.append({"request": request.number, "path": path})
    if changed:
        _ensure_job(email, "NETO_STATUS", "neto", {"changes": changed})
    return changed


def _apply_evaldas(email) -> dict | None:
    from core.models import Container

    sender = (email.from_addr or "").lower()
    text = f"{email.subject or ''}\n{email.body_text or ''}"
    if "evaldas@" not in sender and "krovos" not in text.lower() and "krausime" not in text.lower():
        return None
    received = email.received_at
    explicit = _explicit_unload_date(text, received)
    # Разные ETA в цитате списка не размазываем одной датой на все контейнеры.
    # Явная команда «krausime / krovos datos» + одна «10.22d» — по-прежнему на всех.
    if explicit:
        planned = explicit
    elif len(_copy_eta_dates(text)) > 1:
        planned = None
    else:
        planned = parse_evaldas_date(text, received)
    confirmed = bool(re.search(r"iškraut|iskraut", text, re.IGNORECASE))
    container_ids = list(email.containers.values_list("id", flat=True))
    if planned:
        for container in Container.objects.filter(pk__in=container_ids, planned_unload_date__isnull=True):
            container.planned_unload_date = planned
            container.save(update_fields=["planned_unload_date"])
    if confirmed:
        Container.objects.filter(pk__in=container_ids, unload_confirmed_at__isnull=True).update(
            unload_confirmed_at=email.received_at or timezone.now()
        )
    if planned or confirmed:
        payload = {"planned_unload_date": planned.isoformat() if planned else None, "confirmed": confirmed}
        _ensure_job(email, "UNLOAD_PLAN", "handover", payload)
        return payload
    return None


def _apply_handover(email):
    from core.models import Container

    if email.direction != "OUTGOING":
        return []
    text = f"{email.subject or ''}\n{email.body_text or ''}"
    if not is_handover_mail(text):
        return []
    now = email.received_at or timezone.now()
    ids = list(email.containers.values_list("id", flat=True))
    Container.objects.filter(pk__in=ids, transferred_to_neto_at__isnull=True).update(
        transferred_to_neto_at=now,
        transferred_to_neto_email=email,
    )
    return list(Container.objects.filter(pk__in=ids).values_list("number", flat=True))


def _apply_atlantic(email):
    if not is_atlantic_unload(email.subject or ""):
        return None
    numbers = list(email.containers.values_list("number", flat=True))
    _ensure_job(
        email,
        "ATLANTIC_UNLOAD",
        "handover",
        {"containers": numbers, "subject": (email.subject or "")[:200]},
    )
    return numbers


@transaction.atomic
def enrich_email_for_bots(email) -> dict:
    """После сохранения письма. Ошибки PDF не откатывают само письмо: их глушит вызывающий."""
    pdf_numbers = container_numbers_in_text(_pdf_text(email))
    body_numbers = container_numbers_in_text(f"{email.subject or ''}\n{email.body_text or ''}")
    numbers = []
    seen = set()
    for number in pdf_numbers + body_numbers:
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    email.attachment_container_numbers = pdf_numbers
    linked = _link_container_numbers(email, numbers)
    missing_vins = _remember_unknown_vins(email)
    email.save(update_fields=["attachment_container_numbers", "unknown_vins"])
    return {
        "pdf_containers": pdf_numbers,
        "linked": linked,
        "unknown_vins": missing_vins,
        "neto": _apply_neto_status(email),
        "evaldas": _apply_evaldas(email),
        "handed": _apply_handover(email),
        "atlantic": _apply_atlantic(email),
    }
