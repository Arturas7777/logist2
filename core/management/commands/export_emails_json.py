"""Management-команда для экспорта писем в JSON (read-only доступ для ботов).

Боты подключаются к серверу по SSH и запускают эту команду с фильтрами.
Возвращает компактный JSON без тяжёлого body_html — только метаданные
и опционально body_text.

Примеры:
    # Новые письма за последние 2 часа
    python manage.py export_emails_json --since=2h

    # От конкретного отправителя
    python manage.py export_emails_json --since=24h --sender=maersk.com

    # По конкретному контейнеру
    python manage.py export_emails_json --container=MSKU9754460

    # С полным телом (осторожно: большой вывод)
    python manage.py export_emails_json --since=1h --include-body
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Prefetch, Q
from django.utils import timezone


class Command(BaseCommand):
    help = "Экспорт писем из БД в JSON (для ботов через SSH)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=str,
            help="Временной фильтр: '2h' (часы), '7d' (дни), или ISO timestamp",
        )
        parser.add_argument(
            "--sender",
            type=str,
            help="Фильтр по from_addr (substring match, case-insensitive)",
        )
        parser.add_argument(
            "--subject",
            type=str,
            help="Фильтр по subject (substring match, case-insensitive)",
        )
        parser.add_argument(
            "--direction",
            choices=["INCOMING", "OUTGOING"],
            help="Фильтр по направлению",
        )
        parser.add_argument(
            "--container",
            type=str,
            help="Фильтр по номеру контейнера",
        )
        parser.add_argument(
            "--car-vin",
            type=str,
            help="Фильтр по VIN машины",
        )
        parser.add_argument(
            "--transport-request",
            type=str,
            help="Фильтр по номеру заявки на автовоз (TR-YYYYMMDD-NNN)",
        )
        parser.add_argument(
            "--include-body",
            action="store_true",
            help="Включить body_text в вывод (может быть большим)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Максимальное количество писем в выводе (default: 100)",
        )
        parser.add_argument(
            "--compact",
            action="store_true",
            help="Компактный JSON (без отступов)",
        )

    def handle(self, *args, **options):
        from core.models.email import ContainerEmail

        qs = ContainerEmail.objects.all()

        # ── Фильтры ─────────────────────────────────────────────────────────
        since_str = options.get("since")
        if since_str:
            cutoff = self._parse_since(since_str)
            if cutoff:
                qs = qs.filter(received_at__gte=cutoff)

        sender = options.get("sender")
        if sender:
            qs = qs.filter(from_addr__icontains=sender)

        subject = options.get("subject")
        if subject:
            qs = qs.filter(subject__icontains=subject)

        direction = options.get("direction")
        if direction:
            qs = qs.filter(direction=direction)

        container_num = options.get("container")
        if container_num:
            qs = qs.filter(containers__number__iexact=container_num).distinct()

        car_vin = options.get("car_vin")
        if car_vin:
            qs = qs.filter(cars__vin__iexact=car_vin).distinct()

        transport_req = options.get("transport_request")
        if transport_req:
            qs = qs.filter(transport_requests__number__iexact=transport_req).distinct()

        # ── Исключаем скрытые письма (дубли и отфильтрованные) ───────────────
        qs = qs.filter(hidden_reason="")

        # ── Prefetch связей для вывода linked entities ───────────────────────
        qs = qs.prefetch_related(
            Prefetch("containers", queryset=lambda: self._container_queryset()),
            Prefetch("cars", queryset=lambda: self._car_queryset()),
            Prefetch("transport_requests", queryset=lambda: self._tr_queryset()),
        )

        # ── Лимит + сортировка ───────────────────────────────────────────────
        limit = options.get("limit", 100)
        qs = qs.order_by("-received_at")[:limit]

        # ── Сериализация ─────────────────────────────────────────────────────
        include_body = options.get("include_body", False)
        items = [self._serialize_email(email, include_body) for email in qs]

        output = {
            "count": len(items),
            "timestamp": timezone.now().isoformat(),
            "emails": items,
        }

        indent = None if options.get("compact") else 2
        json.dump(output, sys.stdout, ensure_ascii=False, indent=indent)
        sys.stdout.write("\n")

    def _parse_since(self, since_str: str):
        """Парсит '2h', '7d', или ISO timestamp."""
        if not since_str:
            return None

        # ISO timestamp
        if "T" in since_str or "-" in since_str[:10]:
            try:
                from django.utils.dateparse import parse_datetime

                dt = parse_datetime(since_str)
                if dt:
                    return dt
            except Exception:
                pass

        # Относительное время: '2h', '30m', '7d'
        import re

        m = re.match(r"^(\d+)([mhd])$", since_str.lower())
        if m:
            value = int(m.group(1))
            unit = m.group(2)
            if unit == "m":
                return timezone.now() - timedelta(minutes=value)
            elif unit == "h":
                return timezone.now() - timedelta(hours=value)
            elif unit == "d":
                return timezone.now() - timedelta(days=value)

        self.stderr.write(
            self.style.WARNING(
                f"Не удалось распарсить --since={since_str}; "
                f"ожидается '2h', '7d', или ISO timestamp"
            )
        )
        return None

    def _container_queryset(self):
        from core.models import Container

        return Container.objects.only("id", "number")

    def _car_queryset(self):
        from core.models import Car

        return Car.objects.only("id", "vin")

    def _tr_queryset(self):
        from core.models import TransportRequest

        return TransportRequest.objects.only("id", "number")

    def _serialize_email(self, email, include_body: bool) -> dict[str, Any]:
        """Компактная сериализация одного письма."""
        result = {
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
        }

        if include_body:
            result["body_text"] = email.body_text

        # Вложения: filename, size, content_type, is_inline
        if email.attachments_json:
            result["attachments"] = [
                {
                    "filename": att.get("filename"),
                    "size": att.get("size"),
                    "content_type": att.get("content_type"),
                    "attachment_id": att.get("attachment_id"),
                    "is_inline": att.get("is_inline", False),
                    "available": bool(att.get("storage_path")),
                }
                for att in email.attachments_json
            ]
        else:
            result["attachments"] = []

        # Связанные сущности
        result["linked_containers"] = [c.number for c in email.containers.all()]
        result["linked_cars"] = [car.vin for car in email.cars.all()]
        result["linked_transport_requests"] = [tr.number for tr in email.transport_requests.all()]

        return result
