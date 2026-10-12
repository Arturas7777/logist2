"""Выдача вложений писем с докачкой из Gmail по требованию.

``storage_path`` в ``attachments_json`` — место, где файл лежит, когда он
в кэше. Очистка (``email_storage``) удаляет только файлы и путь не трогает,
поэтому «есть в кэше» = «путь задан и файл существует». Если файла нет,
вложение один раз скачивается из Gmail и снова кладётся по этому пути.

mtime файла служит отметкой последнего обращения: при каждой выдаче он
обновляется, и по нему очистка решает, что вытеснять.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.services import gmail_quota
from core.services.gmail_quota import GmailRateLimited

logger = logging.getLogger(__name__)

STORAGE_DIR = "container_emails"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")


class AttachmentUnavailable(Exception):
    """Вложение нельзя выдать: нет в кэше и не удаётся скачать из Gmail."""


def storage_root() -> Path:
    return Path(settings.MEDIA_ROOT) / STORAGE_DIR


def resolve_storage_path(storage_path: str) -> Path | None:
    """Абсолютный путь внутри каталога вложений или None для чужих путей."""
    if not storage_path:
        return None
    root = storage_root().resolve()
    candidate = (Path(settings.MEDIA_ROOT) / storage_path).resolve()
    if root != candidate and root not in candidate.parents:
        return None
    return candidate


def cached_path(meta: dict) -> Path | None:
    path = resolve_storage_path(meta.get("storage_path") or "")
    if path is not None and path.is_file():
        return path
    return None


def build_storage_path(email, index: int, filename: str) -> str:
    """Тот же формат, что у ingest: container_emails/YYYY/MM/<gmail_id>/NN_name."""
    when = email.received_at or timezone.now()
    safe_name = _SAFE_FILENAME_RE.sub("_", filename or f"attachment_{index}")
    if not safe_name or safe_name == "_":
        safe_name = f"attachment_{index}"
    rel = Path(STORAGE_DIR) / when.strftime("%Y") / when.strftime("%m") / email.gmail_id / f"{index:02d}_{safe_name}"
    return rel.as_posix()


def get_attachment_meta(email, index: int) -> dict:
    attachments = email.attachments_json or []
    if index < 0 or index >= len(attachments):
        raise IndexError(f"У письма {email.pk} нет вложения с индексом {index} (всего {len(attachments)})")
    return attachments[index]


def get_attachment_file(email, index: int, *, client_factory=None) -> tuple[Path, dict]:
    """Путь к файлу вложения; при отсутствии в кэше — скачивает из Gmail.

    ``IndexError`` — нет такого индекса; ``AttachmentUnavailable`` — файла
    нет и скачать нельзя (нет gmail_id, письмо удалено из Gmail, пауза
    после отказа по квоте и т.п.).
    """
    meta = get_attachment_meta(email, index)
    path = cached_path(meta)
    if path is not None:
        _touch(path)
        return path, meta

    storage_path = meta.get("storage_path") or build_storage_path(email, index, meta.get("filename") or "")
    path = resolve_storage_path(storage_path)
    if path is None:
        raise AttachmentUnavailable(f"Недопустимый storage_path: {storage_path!r}")
    data = _download(email, index, meta, client_factory=client_factory)
    _write_atomic(path, data)
    if not meta.get("storage_path"):
        meta = _remember_storage_path(email, index, storage_path, len(data))
    return path, meta


def _download(email, index: int, meta: dict, *, client_factory=None) -> bytes:
    from core.services.email_ingest import _is_gmail_not_found
    from core.services.gmail_client import GmailApiClient, GmailNotConfigured

    if not email.gmail_id:
        raise AttachmentUnavailable("Письмо не связано с Gmail (нет gmail_id) — скачать вложение неоткуда")
    remaining = gmail_quota.cooldown_remaining()
    if remaining:
        raise AttachmentUnavailable(f"Gmail на паузе после отказа по квоте, повторите через {remaining} с")
    try:
        client = (client_factory or GmailApiClient)()
    except GmailNotConfigured as err:
        raise AttachmentUnavailable(f"Gmail не настроен: {err}") from err

    try:
        attachment_id = meta.get("attachment_id") or ""
        if attachment_id:
            try:
                return client.get_attachment(email.gmail_id, attachment_id)
            except GmailRateLimited:
                raise
            except Exception as exc:
                # attachmentId от Gmail со временем может стать недействительным,
                # а у исходящих писем его нет вовсе — тогда берём свежий id
                # из самого письма.
                logger.info("[email_attachments] attachments.get(%s) failed, refetching message: %s", email.pk, exc)
        fresh_id = _fresh_attachment_id(client, email, index, meta)
        return client.get_attachment(email.gmail_id, fresh_id)
    except GmailRateLimited as err:
        seconds = gmail_quota.start_cooldown(err.retry_after)
        raise AttachmentUnavailable(f"Gmail отказал по квоте, пауза {seconds} с") from err
    except AttachmentUnavailable:
        raise
    except Exception as exc:
        if _is_gmail_not_found(exc):
            raise AttachmentUnavailable("Письмо или вложение удалено из Gmail") from exc
        raise AttachmentUnavailable(f"Не удалось скачать вложение из Gmail: {exc}") from exc


def _fresh_attachment_id(client, email, index: int, meta: dict) -> str:
    msg = client.get_message(email.gmail_id)
    filename = meta.get("filename") or ""
    candidates = msg.attachments or []
    if index < len(candidates) and candidates[index].filename == filename:
        return candidates[index].attachment_id
    for att in candidates:
        if att.filename == filename:
            return att.attachment_id
    raise AttachmentUnavailable(f"В письме Gmail не найдено вложение {filename!r}")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise AttachmentUnavailable(f"Не удалось сохранить вложение на диск: {exc}") from exc


def _remember_storage_path(email, index: int, storage_path: str, size: int) -> dict:
    from core.models.email import ContainerEmail

    with transaction.atomic():
        locked = ContainerEmail.objects.select_for_update().only("attachments_json").get(pk=email.pk)
        attachments = list(locked.attachments_json or [])
        meta = dict(attachments[index])
        meta["storage_path"] = storage_path
        meta["size"] = size
        if meta.get("skipped_reason") != "inline":
            meta["skipped_reason"] = ""
        attachments[index] = meta
        locked.attachments_json = attachments
        locked.save(update_fields=["attachments_json"])
    email.attachments_json = attachments
    return meta


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        logger.debug("[email_attachments] utime failed for %s", path, exc_info=True)
