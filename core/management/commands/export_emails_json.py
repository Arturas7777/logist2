"""Письма из БД CRM в JSON — источник почты для ботов вместо Gmail API.

Команда только читает БД. Письма отдаются по возрастанию ``id``; ``id``
монотонно растёт в порядке попадания письма в CRM, поэтому он и служит
курсором: бот запоминает ``next_after_id`` из ответа и передаёт его в
следующий вызов как ``--after-id``. ``--since`` фильтрует по
``received_at`` (дата из заголовка письма, её задаёт отправитель) и для
опроса «что нового» не годится — письмо с «задней» датой будет пропущено.

    python manage.py export_emails_json --after-id 12345
    python manage.py export_emails_json --since 2d --sender maersk.com --pretty
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.services.email_attachments import cached_path

MAX_LIMIT = 500
_RELATIVE_RE = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_since(value: str):
    """'30m' / '2h' / '7d' / ISO-дата или дата-время → aware datetime."""
    raw = (value or "").strip()
    match = _RELATIVE_RE.match(raw.lower())
    if match:
        return timezone.now() - timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    dt = parse_datetime(raw)
    if dt is None:
        d = parse_date(raw)
        if d is not None:
            dt = datetime(d.year, d.month, d.day)
    if dt is None:
        raise CommandError(f"--since={value!r}: ожидается 30m, 2h, 7d или ISO-дата/время")
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


class Command(BaseCommand):
    help = "Письма из БД CRM в JSON (только чтение; для ботов вместо Gmail API)."

    def add_arguments(self, parser):
        parser.add_argument("--after-id", type=int, default=0, help="Курсор: письма с id больше указанного.")
        parser.add_argument("--since", help="received_at не раньше: 30m, 2h, 7d или ISO-дата/время.")
        parser.add_argument("--sender", help="Подстрока в From (без учёта регистра).")
        parser.add_argument("--subject", help="Подстрока в теме (без учёта регистра).")
        parser.add_argument("--direction", choices=["INCOMING", "OUTGOING"])
        parser.add_argument("--container", help="Номер контейнера, к которому привязано письмо.")
        parser.add_argument("--car-vin", help="VIN машины, к которой привязано письмо.")
        parser.add_argument("--transport-request", help="Номер заявки на автовоз (TR-…).")
        parser.add_argument("--include-body", action="store_true", help="Добавить body_text.")
        parser.add_argument(
            "--include-attachment-text",
            action="store_true",
            help="Добавить номера контейнеров из PDF и VIN, которых нет в CRM.",
        )
        parser.add_argument("--include-hidden", action="store_true", help="Не скрывать дубли и отфильтрованные.")
        parser.add_argument("--limit", type=int, default=100, help=f"Сколько писем (1..{MAX_LIMIT}, по умолчанию 100).")
        parser.add_argument("--pretty", action="store_true", help="JSON с отступами.")

    def handle(self, *args, **options):
        from core.models import Car, Container
        from core.models.email import ContainerEmail
        from core.models.website import TransportRequest

        limit = options["limit"]
        if not 1 <= limit <= MAX_LIMIT:
            raise CommandError(f"--limit должен быть от 1 до {MAX_LIMIT}")
        if options["after_id"] < 0:
            raise CommandError("--after-id не может быть отрицательным")

        qs = ContainerEmail.objects.filter(pk__gt=options["after_id"])
        if not options["include_hidden"]:
            qs = qs.filter(hidden_reason="")
        if options["since"] is not None:
            qs = qs.filter(received_at__gte=parse_since(options["since"]))
        if options["sender"]:
            qs = qs.filter(from_addr__icontains=options["sender"])
        if options["subject"]:
            qs = qs.filter(subject__icontains=options["subject"])
        if options["direction"]:
            qs = qs.filter(direction=options["direction"])
        if options["container"]:
            qs = qs.filter(containers__number__iexact=options["container"].strip())
        if options["car_vin"]:
            qs = qs.filter(cars__vin__iexact=options["car_vin"].strip())
        if options["transport_request"]:
            qs = qs.filter(transport_requests__number__iexact=options["transport_request"].strip())

        fields = [
            "id",
            "gmail_id",
            "thread_id",
            "message_id",
            "direction",
            "from_addr",
            "to_addrs",
            "cc_addrs",
            "subject",
            "received_at",
            "snippet",
            "attachments_json",
        ]
        if options["include_body"]:
            fields.append("body_text")
        if options["include_attachment_text"]:
            fields.extend(["attachment_container_numbers", "unknown_vins"])

        qs = (
            qs.distinct()
            .order_by("pk")
            .only(*fields)
            .prefetch_related(
                Prefetch("containers", queryset=Container.objects.only("id", "number")),
                Prefetch("cars", queryset=Car.objects.only("id", "vin")),
                Prefetch("transport_requests", queryset=TransportRequest.objects.only("id", "number")),
            )
        )
        rows = list(qs[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]

        payload = {
            "count": len(rows),
            "has_more": has_more,
            "next_after_id": rows[-1].pk if rows else options["after_id"],
            "emails": [
                self._serialize(e, options["include_body"], options["include_attachment_text"]) for e in rows
            ],
        }
        if options["pretty"]:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.stdout.write(text)

    @staticmethod
    def _serialize(email, include_body: bool, include_attachment_text: bool = False) -> dict:
        item = {
            "id": email.pk,
            "gmail_id": email.gmail_id,
            "thread_id": email.thread_id,
            "message_id": email.message_id,
            "direction": email.direction,
            "from": email.from_addr,
            "to": email.to_addrs,
            "cc": email.cc_addrs,
            "subject": email.subject,
            "received_at": email.received_at.isoformat(),
            "snippet": email.snippet,
            "attachments": [
                {
                    "index": idx,
                    "filename": att.get("filename") or "",
                    "size": att.get("size") or 0,
                    "content_type": att.get("content_type") or "",
                    "is_inline": bool(att.get("is_inline")),
                    "cached": cached_path(att) is not None,
                }
                for idx, att in enumerate(email.attachments_json or [])
            ],
            "containers": [c.number for c in email.containers.all()],
            "cars": [c.vin for c in email.cars.all()],
            "transport_requests": [t.number for t in email.transport_requests.all()],
        }
        if include_body:
            item["body_text"] = email.body_text
        if include_attachment_text:
            item["attachment_container_numbers"] = email.attachment_container_numbers or []
            item["unknown_vins"] = email.unknown_vins or []
        return item
