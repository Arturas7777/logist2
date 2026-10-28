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
            "tempdir",
            "lock",
            "unlock",
            "set-container-status",
            "transfer-cars",
            "complete-tr",
            "arrived-not-handed",
            "mark-handed",
            "backfill-transfer-dates",
            "plans-unconfirmed",
            "import-caromoto",
            "apply-caromoto",
            "neto-bundle",
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
        from core.services.bot_import import apply_batch, flexible_tariff_preview, store_batch
        from core.services.bot_ops import (
            arrived_not_handed,
            backfill_transfer_dates,
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
            report = apply_batch(options["batch"])
            return {"before": {}, "after": report, "result": report, "notifications": []}
        if command == "neto-bundle":
            return neto_bundle(options["request"] or options["number"], apply=apply, force=options["force"])
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
