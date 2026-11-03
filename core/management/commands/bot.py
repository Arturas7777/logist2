"""Единая команда ботов. Всегда печатает JSON со schema_version.

Примеры:
    python manage.py bot capabilities --token %BOT_TOKEN%
    python manage.py bot set-container-status --token %BOT_TOKEN% --container MSMU1 --status IN_PORT
    python manage.py bot set-container-status --token %BOT_TOKEN% --container MSMU1 --status UNLOADED --apply --idempotency-key once-1 --no-notify
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.services.bot_runtime import (
    BotError,
    acquire_lock,
    authenticate,
    begin_apply,
    bot_temp_dir,
    capabilities_for,
    dictionary,
    envelope,
    finish_apply,
    release_lock,
    require_command,
    write_audit,
)


class Command(BaseCommand):
    help = "Команды ботов CRM: токен, роль, dry-run, журнал."

    def add_arguments(self, parser):
        common = argparse.ArgumentParser(add_help=False)
        self._add_common(common)
        sub = parser.add_subparsers(dest="command")
        for name in (
            "capabilities",
            "dictionary",
            "mail-health",
            "morning",
            "discrepancies",
            "audit",
            "jobs",
            "claim-job",
            "finish-job",
            "emails",
            "mark-email",
            "download-attachment",
            "car",
            "car-docs",
            "download-car-doc",
            "tempdir",
            "lock",
            "unlock",
            "set-container-status",
            "caromoto-client-map",
            "car-upsert",
            "transfer-cars",
            "complete-tr",
            "arrived-not-handed",
            "mark-handed",
            "backfill-transfer-dates",
            "plans-unconfirmed",
            "import-caromoto",
            "apply-caromoto",
            "neto-bundle",
            "tr-docs",
            "download-tr-doc",
            "tr-update",
            "tr-upload-doc",
            "set-ths",
            "proforma-upsert",
            "proformas",
            "match-proforma",
            "propose-invoice-payment",
            "profit",
            "ledger-entry",
            "reconcile-neto",
            "fix-flexible-tariff",
            "revolut-health",
            "t1-deadlines",
        ):
            sub.add_parser(name, parents=[common])

    def _add_common(self, parser):
        parser.add_argument("--token", default="")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--no-notify", action="store_true")
        parser.add_argument("--idempotency-key", default="")
        parser.add_argument("--container", default="")
        parser.add_argument("--status", default="")
        parser.add_argument("--unload-date", default="")
        parser.add_argument("--warehouse", default="")
        parser.add_argument("--source-email-id", type=int, default=None)
        parser.add_argument("--vins", default="")
        parser.add_argument("--transfer-date", default="")
        parser.add_argument("--request", default="")
        parser.add_argument("--date", default="")
        parser.add_argument("--email-id", type=int, default=None)
        parser.add_argument("--job-id", type=int, default=None)
        parser.add_argument("--file", default="")
        parser.add_argument("--batch", type=int, default=None)
        parser.add_argument("--amount", default="")
        parser.add_argument("--payer", default="")
        parser.add_argument("--number", default="")
        parser.add_argument("--kind", default="")
        parser.add_argument("--client-id", type=int, default=None)
        parser.add_argument("--due", default="")
        parser.add_argument("--note", default="")
        parser.add_argument("--bank-transaction-id", type=int, default=None)
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--invoice-id", type=int, default=None)
        parser.add_argument("--date-from", default="")
        parser.add_argument("--date-to", default="")
        parser.add_argument("--description", default="")
        parser.add_argument("--category-id", type=int, default=None)
        parser.add_argument("--car-id", type=int, default=None)
        parser.add_argument("--lines-json", default="")
        parser.add_argument("--client-name", default="CAROMOTO MOLDOVA")
        parser.add_argument("--target-type", default="container")
        parser.add_argument("--target-id", default="")
        parser.add_argument("--attachment-index", type=int, default=0)
        parser.add_argument("--only-unprocessed", action="store_true")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--vin", default="")
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument("--type", default="")
        parser.add_argument("--out", default="")
        parser.add_argument("--map-action", default="list")
        parser.add_argument("--office", default="")
        parser.add_argument("--caromoto-client-id", default="")
        parser.add_argument("--priority", type=int, default=0)
        parser.add_argument("--brand", default="")
        parser.add_argument("--model", default="")
        parser.add_argument("--year", type=int, default=None)
        parser.add_argument("--unload-site", type=int, default=None)
        parser.add_argument("--foreign-container", default="")
        parser.add_argument("--eta", default="")
        parser.add_argument("--notify", action="store_true")
        parser.add_argument("--client-docs", action="store_true")
        parser.add_argument("--set-warehouse", action="store_true")
        parser.add_argument("--tr", default="")
        parser.add_argument("--driver-name", default=None)
        parser.add_argument("--driver-passport", default=None)
        parser.add_argument("--driver-phone", default=None)
        parser.add_argument("--carrier-name", default=None)
        parser.add_argument("--carrier-eori", default=None)
        parser.add_argument("--tractor-plate", default=None)
        parser.add_argument("--trailer-plate", default=None)
        parser.add_argument("--border-crossing", default=None)

    def handle(self, *args, **options):
        command = options.get("command")
        if not command:
            self.stdout.write(
                json.dumps({"schema_version": 1, "ok": False, "error": "Укажите команду."}, ensure_ascii=False)
            )
            raise CommandError("Укажите команду бота.")
        try:
            principal = authenticate(options["token"] or os.environ.get("BOT_TOKEN", ""))
            spec = require_command(principal, command)
            payload = self._run(principal, command, spec, options)
        except BotError as exc:
            self.stdout.write(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, ensure_ascii=False))
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))

    def _run(self, principal, command, spec, options):
        apply = bool(options["apply"]) and spec["mutates"]
        if spec["mutates"] and apply:
            replay = begin_apply(principal, command, options["idempotency_key"])
            if replay is not None:
                return replay
        body = self._dispatch(principal, command, options, apply=apply if spec["mutates"] else False)
        if isinstance(body, dict) and "schema_version" in body and "result" not in body:
            wrapped = body
        elif isinstance(body, dict) and {"before", "after", "result"} <= body.keys():
            wrapped = envelope(
                command,
                principal,
                body["result"],
                dry_run=not apply if spec["mutates"] else False,
                notifications=body.get("notifications"),
            )
            if spec["mutates"]:
                write_audit(
                    principal,
                    command,
                    before=body.get("before"),
                    after=body.get("after"),
                    source_email_id=options.get("source_email_id"),
                    dry_run=not apply,
                    idempotency_key=options.get("idempotency_key") or "",
                )
            if apply:
                wrapped = finish_apply(principal, command, options["idempotency_key"], wrapped)
        else:
            wrapped = envelope(command, principal, body, dry_run=False)
        return wrapped

    def _dispatch(self, principal, command, options, *, apply: bool):
        from core.services.bot_docs import neto_bundle
        from core.services.bot_finance import (
            add_ledger_entry,
            list_proformas,
            match_proforma,
            parse_day,
            profit_report,
            propose_invoice_payment,
            reconcile_neto,
            upsert_proforma,
        )
        from core.services.bot_import import (
            apply_batch,
            flexible_tariff_preview,
            list_client_maps,
            store_batch,
            upsert_client_map,
        )
        from core.services.bot_ops import (
            arrived_not_handed,
            backfill_transfer_dates,
            car_upsert,
            complete_tr,
            discrepancies,
            mail_health,
            mark_handed,
            morning_snapshot,
            plans_unconfirmed,
            revolut_health,
            set_container_status,
            t1_deadlines,
            transfer_cars,
        )

        if command == "capabilities":
            return capabilities_for(principal)
        if command == "dictionary":
            return dictionary()
        if command == "mail-health":
            return mail_health()
        if command == "morning":
            return morning_snapshot()
        if command == "discrepancies":
            return discrepancies()
        if command == "revolut-health":
            return revolut_health()
        if command == "t1-deadlines":
            return t1_deadlines()
        if command == "arrived-not-handed":
            return arrived_not_handed()
        if command == "tempdir":
            return {"path": bot_temp_dir(principal)}
        if command == "audit":
            return _audit(principal)
        if command == "jobs":
            return _jobs(principal, options["limit"])
        if command == "claim-job":
            return _claim(principal, options["job_id"], apply)
        if command == "finish-job":
            return _finish(principal, options["job_id"], apply)
        if command == "emails":
            return _emails(principal, options)
        if command == "mark-email":
            return _mark_email(principal, options["email_id"], apply)
        if command == "download-attachment":
            return _download(options["email_id"], options["attachment_index"])
        if command == "car":
            return _car(options["vin"], options["car_id"])
        if command == "car-docs":
            return _car_docs(options["vin"], options["car_id"])
        if command == "download-car-doc":
            return _download_car_doc(principal, options["doc_id"], options["vin"], options["type"], options["out"])
        if command == "lock":
            if not apply:
                return {"before": {}, "after": {}, "result": {"outcome": "would_lock"}, "notifications": []}
            lock = acquire_lock(principal, options["target_type"], options["target_id"] or options["container"])
            return {
                "before": {},
                "after": {"expires_at": lock.expires_at.isoformat()},
                "result": {"outcome": "locked"},
                "notifications": [],
            }
        if command == "unlock":
            if not apply:
                return {"before": {}, "after": {}, "result": {"outcome": "would_unlock"}, "notifications": []}
            released = release_lock(principal, options["target_type"], options["target_id"] or options["container"])
            return {
                "before": {},
                "after": {},
                "result": {"outcome": "unlocked" if released else "absent"},
                "notifications": [],
            }
        if command == "set-container-status":
            day = parse_day(options["unload_date"]) if options["unload_date"] else None
            if apply:
                acquire_lock(principal, "container", options["container"])
            return set_container_status(
                number=options["container"],
                status=options["status"],
                unload_date=day,
                warehouse_code=options["warehouse"] or None,
                set_warehouse=options["set_warehouse"],
                notify=not options["no_notify"],
                apply=apply,
            )
        if command == "transfer-cars":
            day = parse_day(options["transfer_date"]) if options["transfer_date"] else None
            return transfer_cars(
                vins=[part.strip() for part in options["vins"].split(",") if part.strip()],
                transfer_date=day,
                notify=not options["no_notify"],
                apply=apply,
            )
        if command == "complete-tr":
            return complete_tr(options["request"] or options["number"], apply=apply)
        if command == "mark-handed":
            return mark_handed(options["container"], options["email_id"], apply=apply)
        if command == "backfill-transfer-dates":
            return backfill_transfer_dates(apply=apply)
        if command == "plans-unconfirmed":
            return plans_unconfirmed(parse_day(options["date"]))
        if command == "import-caromoto":
            payload = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
            if not apply:
                from core.services.bot_import import build_diff

                diff = build_diff(payload)
                return {"before": {}, "after": diff["summary"], "result": diff, "notifications": []}
            batch, diff = store_batch(principal.name, payload)
            return {
                "before": {},
                "after": {"batch": batch.pk},
                "result": {"batch": batch.pk, **diff},
                "notifications": [],
            }
        if command == "apply-caromoto":
            if not apply:
                return {
                    "before": {},
                    "after": {},
                    "result": {"outcome": "would_apply", "batch": options["batch"]},
                    "notifications": [],
                }
            report = apply_batch(options["batch"], overwrite_warehouse=options["set_warehouse"])
            return {"before": {}, "after": report, "result": report, "notifications": []}
        if command == "neto-bundle":
            return neto_bundle(
                options["request"] or options["number"],
                apply=apply,
                force=options["force"],
                client_docs=options["client_docs"],
            )
        if command == "tr-docs":
            from core.services.bot_tr import list_tr_docs

            return list_tr_docs(options["tr"])
        if command == "download-tr-doc":
            from core.services.bot_tr import download_tr_doc

            return download_tr_doc(options["tr"], options["type"], options["doc_id"])
        if command == "tr-update":
            from core.services.bot_tr import update_tr

            return update_tr(
                options["tr"],
                driver_name=options["driver_name"],
                driver_passport=options["driver_passport"],
                driver_phone=options["driver_phone"],
                carrier_name=options["carrier_name"],
                carrier_eori=options["carrier_eori"],
                tractor_plate=options["tractor_plate"],
                trailer_plate=options["trailer_plate"],
                border_crossing=options["border_crossing"],
                apply=apply,
            )
        if command == "tr-upload-doc":
            from core.services.bot_tr import upload_tr_doc

            return upload_tr_doc(
                options["tr"],
                options["type"],
                options["file"],
                vin=options["vin"],
                car_id=options["car_id"],
                apply=apply,
            )
        if command == "caromoto-client-map":
            if options["map_action"] == "list":
                rows = list_client_maps()
                return {"before": {}, "after": {}, "result": rows, "notifications": []}
            return upsert_client_map(
                office=options["office"],
                caromoto_client_id=options["caromoto_client_id"],
                client_id=options["client_id"],
                priority=options["priority"],
                apply=apply,
            )
        if command == "car-upsert":
            return car_upsert(
                vin=options["vin"],
                brand=options["brand"],
                model=options["model"],
                year=options["year"],
                client_id=options["client_id"],
                warehouse_code=options["warehouse"] or None,
                unload_site=options["unload_site"],
                foreign_container_number=options["foreign_container"],
                status=options["status"],
                unload_date=parse_day(options["unload_date"]) if options["unload_date"] else None,
                eta=parse_day(options["eta"]) if options["eta"] else None,
                notify=options["notify"],
                apply=apply,
            )
        if command == "set-ths":
            return _set_ths(options["container"], options["amount"], options["payer"], apply)
        if command == "proforma-upsert":
            return upsert_proforma(
                number=options["number"],
                kind=options["kind"] or "UNOFFICIAL",
                amount=options["amount"],
                client_id=options["client_id"],
                due_date=parse_day(options["due"]) if options["due"] else None,
                note=options["note"],
                apply=apply,
            )
        if command == "proformas":
            return list_proformas()
        if command == "match-proforma":
            confirmed = apply and options["confirm"]
            if apply and not options["confirm"]:
                apply = False
            return match_proforma(
                number=options["number"],
                bank_transaction_id=options["bank_transaction_id"],
                confirm=confirmed,
            )
        if command == "propose-invoice-payment":
            return propose_invoice_payment(
                bank_transaction_id=options["bank_transaction_id"],
                invoice_id=options["invoice_id"],
                amount=options["amount"],
                confirm=bool(options["confirm"]),
                apply=apply,
            )
        if command == "profit":
            return profit_report(parse_day(options["date_from"]), parse_day(options["date_to"]))
        if command == "ledger-entry":
            return add_ledger_entry(
                kind=options["kind"],
                amount=options["amount"],
                occurred_on=parse_day(options["date"]),
                description=options["description"],
                category_id=options["category_id"],
                car_id=options["car_id"],
                apply=apply,
            )
        if command == "reconcile-neto":
            lines = json.loads(options["lines_json"] or "[]")
            return reconcile_neto(lines)
        if command == "fix-flexible-tariff":
            return _fix_tariff(options["client_name"], apply, flexible_tariff_preview)
        raise BotError(f"Команда не собрана: {command}")


def _audit(principal):
    from core.models.bots import BotAuditEntry

    qs = BotAuditEntry.objects.all()
    if principal.role != "chief":
        qs = qs.filter(principal_name=principal.name)
    rows = [
        {
            "id": row.pk,
            "at": row.created_at.isoformat(),
            "who": row.principal_name,
            "command": row.command,
            "dry_run": row.dry_run,
            "source_email_id": row.source_email_id,
        }
        for row in qs.order_by("-id")[:50]
    ]
    return {"rows": rows}


def _jobs(principal, limit: int):
    from core.models.bots import BotJob

    qs = BotJob.objects.all()
    if principal.role != "chief":
        qs = qs.filter(role__in=["", principal.role])
    rows = [
        {"id": job.pk, "type": job.job_type, "status": job.status, "role": job.role, "payload": job.payload}
        for job in qs.order_by("-id")[:limit]
    ]
    return {"jobs": rows}


def _claim(principal, job_id, apply: bool):
    from core.models.bots import BotJob

    job = BotJob.objects.filter(pk=job_id).first()
    if job is None:
        raise BotError("Задание не найдено.")
    if job.role and job.role != principal.role and principal.role != "chief":
        raise BotError("Чужая роль.")
    if not apply:
        return {
            "before": {"status": job.status},
            "after": {"status": "CLAIMED"},
            "result": {"outcome": "would_claim"},
            "notifications": [],
        }
    job.status = BotJob.STATUS_CLAIMED
    job.claimed_by = principal
    job.save(update_fields=["status", "claimed_by", "updated_at"])
    return {
        "before": {},
        "after": {"status": job.status},
        "result": {"outcome": "claimed", "id": job.pk},
        "notifications": [],
    }


def _finish(principal, job_id, apply: bool):
    from core.models.bots import BotJob

    job = BotJob.objects.filter(pk=job_id).first()
    if job is None:
        raise BotError("Задание не найдено.")
    if not apply:
        return {
            "before": {"status": job.status},
            "after": {"status": "DONE"},
            "result": {"outcome": "would_finish"},
            "notifications": [],
        }
    job.status = BotJob.STATUS_DONE
    job.save(update_fields=["status", "updated_at"])
    return {
        "before": {},
        "after": {"status": job.status},
        "result": {"outcome": "done", "id": job.pk},
        "notifications": [],
    }


def _emails(principal, options):
    from core.models.email import ContainerEmail

    qs = ContainerEmail.objects.filter(hidden_reason="").order_by("-id")
    if options["only_unprocessed"]:
        qs = qs.exclude(bot_marks__principal=principal)
    if options["container"]:
        qs = qs.filter(containers__number__iexact=options["container"])
    rows = []
    for email in qs.distinct()[: options["limit"]]:
        rows.append(
            {
                "id": email.pk,
                "subject": email.subject,
                "from": email.from_addr,
                "received_at": email.received_at.isoformat() if email.received_at else None,
                "pdf_containers": email.attachment_container_numbers,
                "unknown_vins": email.unknown_vins,
                "containers": list(email.containers.values_list("number", flat=True)),
            }
        )
    return {"emails": rows}


def _mark_email(principal, email_id, apply: bool):
    from core.models.bots import BotEmailMark
    from core.models.email import ContainerEmail

    if not ContainerEmail.objects.filter(pk=email_id).exists():
        raise BotError("Письмо не найдено.")
    if not apply:
        return {"before": {}, "after": {"email_id": email_id}, "result": {"outcome": "would_mark"}, "notifications": []}
    BotEmailMark.objects.get_or_create(email_id=email_id, principal=principal)
    return {"before": {}, "after": {"email_id": email_id}, "result": {"outcome": "marked"}, "notifications": []}


def _download(email_id, index: int):
    from core.models.email import ContainerEmail
    from core.services.email_attachments import cached_path

    email = ContainerEmail.objects.filter(pk=email_id).first()
    if email is None:
        raise BotError("Письмо не найдено.")
    attachments = email.attachments_json or []
    if index < 0 or index >= len(attachments):
        raise BotError("Нет вложения с таким индексом.")
    path = cached_path(attachments[index])
    return {"filename": attachments[index].get("filename") or "", "path": str(path) if path else None}


def _set_ths(number: str, amount: str, payer: str, apply: bool):
    from decimal import Decimal

    from core.models import Container

    container = Container.objects.filter(number__iexact=number.strip()).first()
    if container is None:
        raise BotError(f"Контейнер {number} не найден.")
    before = {"ths": str(container.ths) if container.ths is not None else None, "ths_payer": container.ths_payer}
    after = dict(before)
    if amount:
        after["ths"] = str(Decimal(amount))
    if payer:
        after["ths_payer"] = payer
    if not apply:
        return {"before": before, "after": after, "result": {"outcome": "would_update"}, "notifications": []}
    if amount:
        container.ths = Decimal(amount)
    if payer:
        container.ths_payer = payer
    container.save(update_fields=["ths", "ths_payer"])
    return {"before": before, "after": after, "result": {"outcome": "updated"}, "notifications": []}


def _fix_tariff(client_name: str, apply: bool, preview):
    report = preview(client_name)
    if not apply:
        return {"before": {}, "after": report, "result": report, "notifications": []}
    from core.models import Car, Client
    from core.services.car_service_manager import apply_client_tariff_for_car

    client = Client.objects.filter(name__icontains=client_name, tariff_type="FLEXIBLE").first()
    updated = []
    if client:
        for car in Car.objects.filter(client=client, status__in=["FLOATING", "IN_PORT", "UNLOADED"]):
            apply_client_tariff_for_car(car)
            updated.append(car.vin)
    return {"before": report, "after": {"updated": updated}, "result": {"updated": updated}, "notifications": []}


def _car(vin: str, car_id: int | None) -> dict:
    from core.models import Car
    from core.services.bot_runtime import BotError

    car = None
    if car_id:
        car = Car.objects.filter(pk=car_id).first()
    elif vin:
        car = Car.objects.filter(vin__iexact=vin.strip()).first()
    else:
        raise BotError("Нужен --vin или --car-id.")
    if car is None:
        raise BotError(f"Машина не найдена (VIN={vin}, id={car_id}).")

    return {
        "vin": car.vin,
        "id": car.pk,
        "brand": car.brand,
        "year": car.year,
        "client": car.client.name if car.client_id else None,
        "client_id": car.client_id,
        "container": car.container.number if car.container_id else None,
        "container_id": car.container_id,
        "status": car.status,
        "warehouse": car.warehouse.name if car.warehouse_id else None,
        "warehouse_id": car.warehouse_id,
        "weight_kg": str(car.weight_kg) if car.weight_kg else None,
        "eta": car.container.eta.isoformat() if car.container_id and car.container.eta else None,
        "unload_date": car.unload_date.isoformat() if car.unload_date else None,
        "transfer_date": car.transfer_date.isoformat() if car.transfer_date else None,
        "has_title": car.has_title,
    }


def _car_docs(vin: str, car_id: int | None) -> dict:
    from core.models import Car
    from core.services.bot_runtime import BotError

    car = None
    if car_id:
        car = Car.objects.filter(pk=car_id).first()
    elif vin:
        car = Car.objects.filter(vin__iexact=vin.strip()).first()
    else:
        raise BotError("Нужен --vin или --car-id.")
    if car is None:
        raise BotError(f"Машина не найдена (VIN={vin}, id={car_id}).")

    docs = []
    if car.title_scan:
        import os

        from django.conf import settings

        file_path = (
            car.title_scan.path
            if hasattr(car.title_scan, "path")
            else os.path.join(settings.MEDIA_ROOT, car.title_scan.name)
        )
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        docs.append(
            {
                "id": f"car_{car.pk}_title",
                "type": "TITLE",
                "filename": os.path.basename(car.title_scan.name),
                "size": file_size,
                "uploaded_date": None,
            }
        )

    return {"vin": car.vin, "car_id": car.pk, "documents": docs}


def _download_car_doc(principal, doc_id: int | None, vin: str, doc_type: str, out: str) -> dict:
    import os
    import shutil
    from pathlib import Path

    from django.conf import settings

    from core.models import Car
    from core.services.bot_runtime import BotError, bot_temp_dir

    car = None
    if doc_id:
        if doc_id and not str(doc_id).startswith("car_"):
            raise BotError("--doc-id должен быть в формате car_<id>_title или использовать --vin --type.")
    elif vin and doc_type:
        car = Car.objects.filter(vin__iexact=vin.strip()).first()
        if car is None:
            raise BotError(f"Машина VIN={vin} не найдена.")
    else:
        raise BotError("Нужен --doc-id или --vin --type TITLE.")

    if doc_id:
        parts = str(doc_id).split("_")
        if len(parts) == 3 and parts[0] == "car" and parts[2] == "title":
            car_id = int(parts[1])
            car = Car.objects.filter(pk=car_id).first()
            if car is None:
                raise BotError(f"Машина id={car_id} не найдена.")
            doc_type = "TITLE"

    if not car:
        raise BotError("Машина не найдена.")

    if doc_type == "TITLE":
        if not car.title_scan:
            raise BotError(f"У машины {car.vin} нет TITLE.")
        source_path = (
            car.title_scan.path
            if hasattr(car.title_scan, "path")
            else os.path.join(settings.MEDIA_ROOT, car.title_scan.name)
        )
        if not os.path.exists(source_path):
            raise BotError(f"Файл TITLE не найден на диске: {source_path}")
        filename = os.path.basename(car.title_scan.name)
    else:
        raise BotError(f"Неизвестный тип документа: {doc_type}")

    temp_dir = Path(bot_temp_dir(principal))
    if out:
        out_path = Path(out)
        if not out_path.is_absolute():
            out_path = temp_dir / out
        if not str(out_path).startswith(str(temp_dir)):
            raise BotError(f"--out должен быть внутри {temp_dir}")
        target = out_path
    else:
        target = temp_dir / filename

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)

    return {"filename": filename, "path": str(target), "size": os.path.getsize(target)}
