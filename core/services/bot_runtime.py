"""Контракт команд ботов: токен, роль, идемпотентность, блокировка, журнал.

Каждый ответ — один JSON со ``schema_version``. Повтор ``--idempotency-key``
после успешного ``--apply`` возвращает сохранённый результат.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

SCHEMA_VERSION = 1

ROLE_CHIEF = "chief"
ROLE_ACCOUNTANT = "accountant"
ROLE_NETO = "neto"
ROLE_HANDOVER = "handover"
ROLE_PARSER = "parser"
ALL_ROLES = (ROLE_CHIEF, ROLE_ACCOUNTANT, ROLE_NETO, ROLE_HANDOVER, ROLE_PARSER)
READ_ROLES = ALL_ROLES

# Команда → кто может её звать и пишет ли она в базу.
COMMANDS: dict[str, dict] = {
    "capabilities": {"roles": READ_ROLES, "mutates": False},
    "dictionary": {"roles": READ_ROLES, "mutates": False},
    "mail-health": {"roles": READ_ROLES, "mutates": False},
    "morning": {"roles": READ_ROLES, "mutates": False},
    "discrepancies": {"roles": READ_ROLES, "mutates": False},
    "audit": {"roles": READ_ROLES, "mutates": False},
    "jobs": {"roles": READ_ROLES, "mutates": False},
    "claim-job": {"roles": READ_ROLES, "mutates": True},
    "finish-job": {"roles": READ_ROLES, "mutates": True},
    "emails": {"roles": (ROLE_CHIEF, ROLE_NETO, ROLE_HANDOVER, ROLE_PARSER), "mutates": False},
    "mark-email": {"roles": (ROLE_NETO, ROLE_HANDOVER, ROLE_PARSER), "mutates": True},
    "download-attachment": {"roles": (ROLE_NETO, ROLE_HANDOVER), "mutates": False},
    "tempdir": {"roles": READ_ROLES, "mutates": False},
    "lock": {"roles": (ROLE_NETO, ROLE_HANDOVER, ROLE_PARSER, ROLE_ACCOUNTANT), "mutates": True},
    "unlock": {"roles": (ROLE_NETO, ROLE_HANDOVER, ROLE_PARSER, ROLE_ACCOUNTANT), "mutates": True},
    "set-container-status": {"roles": (ROLE_HANDOVER, ROLE_NETO), "mutates": True},
    "transfer-cars": {"roles": (ROLE_NETO,), "mutates": True},
    "complete-tr": {"roles": (ROLE_NETO,), "mutates": True},
    "arrived-not-handed": {"roles": READ_ROLES, "mutates": False},
    "mark-handed": {"roles": (ROLE_HANDOVER, ROLE_NETO), "mutates": True},
    "backfill-transfer-dates": {"roles": (ROLE_NETO,), "mutates": True},
    "plans-unconfirmed": {"roles": READ_ROLES, "mutates": False},
    "import-caromoto": {"roles": (ROLE_PARSER,), "mutates": True},
    "apply-caromoto": {"roles": (ROLE_PARSER,), "mutates": True},
    "neto-bundle": {"roles": (ROLE_NETO,), "mutates": True},
    "set-ths": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "proforma-upsert": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "proformas": {"roles": (ROLE_CHIEF, ROLE_ACCOUNTANT), "mutates": False},
    "match-proforma": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "propose-invoice-payment": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "profit": {"roles": (ROLE_CHIEF, ROLE_ACCOUNTANT), "mutates": False},
    "ledger-entry": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "reconcile-neto": {"roles": (ROLE_ACCOUNTANT,), "mutates": False},
    "fix-flexible-tariff": {"roles": (ROLE_ACCOUNTANT,), "mutates": True},
    "revolut-health": {"roles": (ROLE_CHIEF, ROLE_ACCOUNTANT), "mutates": False},
    "t1-deadlines": {"roles": READ_ROLES, "mutates": False},
}

_MUTED: ContextVar[bool] = ContextVar("client_notifications_muted", default=False)


class BotError(Exception):
    def __init__(self, message: str, *, status: str = "error"):
        super().__init__(message)
        self.status = status


def notifications_muted() -> bool:
    return bool(_MUTED.get())


@contextmanager
def mute_client_notifications():
    token = _MUTED.set(True)
    try:
        yield
    finally:
        _MUTED.reset(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(name: str, role: str):
    from core.models.bots import BotPrincipal

    if role not in dict(BotPrincipal.ROLE_CHOICES):
        raise BotError(f"Неизвестная роль: {role}")
    token = secrets.token_urlsafe(32)
    principal, _created = BotPrincipal.objects.update_or_create(
        name=name,
        defaults={
            "role": role,
            "token_hash": hash_token(token),
            "token_prefix": token[:6],
            "is_active": True,
        },
    )
    return principal, token


def authenticate(token: str):
    from core.models.bots import BotPrincipal

    raw = (token or "").strip()
    if not raw:
        raise BotError("Нужен токен бота (--token или BOT_TOKEN).")
    principal = BotPrincipal.objects.filter(token_hash=hash_token(raw), is_active=True).first()
    if principal is None:
        raise BotError("Токен не принят.")
    return principal


def require_command(principal, command: str) -> dict:
    spec = COMMANDS.get(command)
    if spec is None:
        raise BotError(f"Неизвестная команда: {command}")
    if principal.role not in spec["roles"]:
        raise BotError(f"Роли {principal.role} команда {command} недоступна.")
    return spec


def envelope(command: str, principal, result: dict, *, dry_run: bool, notifications=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "principal": principal.name,
        "role": principal.role,
        "dry_run": dry_run,
        "notifications": notifications or [],
        "result": result,
    }


def begin_apply(principal, command: str, idempotency_key: str):
    """Вернуть прошлый результат, если ключ уже использован. Иначе None."""
    from core.models.bots import BotCommandReceipt

    key = (idempotency_key or "").strip()
    if not key:
        raise BotError("Для --apply нужен --idempotency-key.")
    receipt = BotCommandReceipt.objects.filter(principal=principal, idempotency_key=key).first()
    if receipt is not None:
        stored = dict(receipt.result)
        stored["idempotent_replay"] = True
        return stored
    return None


def finish_apply(principal, command: str, idempotency_key: str, payload: dict) -> dict:
    from core.models.bots import BotCommandReceipt

    BotCommandReceipt.objects.create(
        principal=principal,
        idempotency_key=idempotency_key.strip(),
        command=command,
        result=payload,
    )
    return payload


def write_audit(principal, command: str, *, before, after, source_email_id=None, dry_run=False, idempotency_key=""):
    from core.models.bots import BotAuditEntry

    BotAuditEntry.objects.create(
        principal_name=principal.name,
        role=principal.role,
        command=command,
        before=before or {},
        after=after or {},
        source_email_id=source_email_id,
        dry_run=dry_run,
        idempotency_key=idempotency_key or "",
    )


def acquire_lock(principal, target_type: str, target_id: str, *, minutes: int = 10):
    from core.models.bots import BotLock

    now = timezone.now()
    with transaction.atomic():
        BotLock.objects.filter(expires_at__lte=now).delete()
        current = BotLock.objects.select_for_update().filter(target_type=target_type, target_id=str(target_id)).first()
        if current and current.principal_id != principal.id and current.expires_at > now:
            raise BotError(
                f"{target_type} {target_id} занят ботом {current.principal.name} до {current.expires_at.isoformat()}."
            )
        if current:
            current.principal = principal
            current.expires_at = now + timedelta(minutes=minutes)
            current.save(update_fields=["principal", "expires_at"])
            return current
        return BotLock.objects.create(
            target_type=target_type,
            target_id=str(target_id),
            principal=principal,
            expires_at=now + timedelta(minutes=minutes),
        )


def release_lock(principal, target_type: str, target_id: str) -> bool:
    from core.models.bots import BotLock

    deleted, _ = BotLock.objects.filter(target_type=target_type, target_id=str(target_id), principal=principal).delete()
    return bool(deleted)


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def bot_temp_dir(principal) -> str:
    from pathlib import Path

    from django.conf import settings

    path = Path(settings.MEDIA_ROOT) / "bot_tmp" / principal.name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def dictionary() -> dict:
    return {
        "container_statuses": ["FLOATING", "IN_PORT", "UNLOADED", "TRANSFERRED"],
        "forward_only": ["FLOATING", "IN_PORT", "UNLOADED"],
        "transport_request_statuses": ["DRAFT", "SUBMITTED", "ACCEPTED", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        "warehouses": {"NETO": "имя содержит NETO", "ATLANTIC": "имя содержит Atlantic"},
        "notifications": [
            {
                "field": "Container.planned_unload_date",
                "when": "дата ставится впервые",
                "channels": ["email", "telegram"],
                "mute": "notify=false не ставит задачу рассылки",
            },
            {
                "field": "Container.unload_date",
                "when": "дата ставится впервые (статус сам становится UNLOADED)",
                "channels": ["email", "telegram"],
                "mute": "notify=false",
            },
            {
                "field": "Car.status=TRANSFERRED",
                "when": "машина перешла в передана и у неё есть клиент",
                "channels": ["email", "telegram"],
                "mute": "notify=false",
            },
            {
                "field": "Container.status=IN_PORT",
                "when": "сам по себе письмо клиенту не шлёт",
                "channels": [],
            },
        ],
        "eta_sources": ["MAERSK_API", "LINE_API", "CAROMOTO", "MANUAL"],
        "neto_phrases": {
            "SURUOŠTA": "IN_PROGRESS",
            "SUFORMINTA": "COMPLETED",
            "Leidimas užsakytas": "IN_PROGRESS",
        },
        "evaldas": "тема или текст «Krovos datos» / «Krausime 10.22d» → planned_unload_date",
        "atlantic_unload": "тема содержит KROVA и SANDELIAI",
        "handover_mail": "исходящее содержит Perduodam konteineri",
        "money": "статус счёта бот не пишет. Оплата — propose-invoice-payment и подтверждение, дальше BillingService.",
    }


def capabilities_for(principal) -> dict:
    allowed = []
    for name, spec in COMMANDS.items():
        if principal.role in spec["roles"]:
            allowed.append({"command": name, "mutates": spec["mutates"]})
    return {"role": principal.role, "commands": allowed}
