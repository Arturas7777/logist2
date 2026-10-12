"""Management-команда для получения вложения email (для ботов через SSH).

Если файл уже закэширован (storage_path) — отдаёт его. Если нет — скачивает
из Gmail один раз и кэширует. Вывод: бинарный файл на stdout (для pipe в файл
или обработку).

Примеры:
    # Получить вложение и сохранить в файл
    python manage.py get_email_attachment <email_id> <attachment_index> > file.pdf

    # Проверить метаданные без скачивания
    python manage.py get_email_attachment <email_id> <attachment_index> --info-only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Получить вложение email (кэшируется после первой загрузки из Gmail)"

    def add_arguments(self, parser):
        parser.add_argument("email_id", type=int, help="ID записи ContainerEmail")
        parser.add_argument(
            "attachment_index",
            type=int,
            help="Индекс вложения в attachments_json (0-based)",
        )
        parser.add_argument(
            "--info-only",
            action="store_true",
            help="Вывести только метаданные (JSON), не скачивать файл",
        )

    def handle(self, *args, **options):
        from core.models.email import ContainerEmail
        from core.services.gmail_client import GmailApiClient, GmailNotConfigured

        email_id = options["email_id"]
        att_index = options["attachment_index"]
        info_only = options.get("info_only", False)

        try:
            email = ContainerEmail.objects.get(pk=email_id)
        except ContainerEmail.DoesNotExist:
            raise CommandError(f"Email {email_id} не найден")

        if not email.attachments_json:
            raise CommandError(f"Email {email_id} не содержит вложений")

        if att_index < 0 or att_index >= len(email.attachments_json):
            raise CommandError(
                f"Индекс {att_index} вне диапазона "
                f"(доступно {len(email.attachments_json)} вложений)"
            )

        att_meta = email.attachments_json[att_index]

        # ── Режим --info-only ────────────────────────────────────────────────
        if info_only:
            info = {
                "email_id": email.pk,
                "attachment_index": att_index,
                "filename": att_meta.get("filename"),
                "size": att_meta.get("size"),
                "content_type": att_meta.get("content_type"),
                "is_inline": att_meta.get("is_inline", False),
                "cached": bool(att_meta.get("storage_path")),
                "storage_path": att_meta.get("storage_path", ""),
            }
            json.dump(info, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return

        # ── Проверка: файл уже закэширован? ──────────────────────────────────
        storage_path = att_meta.get("storage_path", "")
        if storage_path:
            abs_path = Path(settings.MEDIA_ROOT) / storage_path
            if abs_path.exists():
                # Отдаём закэшированный файл
                with open(abs_path, "rb") as fh:
                    sys.stdout.buffer.write(fh.read())
                return
            else:
                # Файл был удалён или не доступен — скачаем заново
                self.stderr.write(
                    self.style.WARNING(
                        f"Закэшированный файл {storage_path} не найден — скачиваем из Gmail"
                    )
                )

        # ── Скачивание из Gmail ──────────────────────────────────────────────
        if not email.gmail_id:
            raise CommandError(f"Email {email_id} не имеет gmail_id — невозможно скачать вложение")

        attachment_id = att_meta.get("attachment_id")
        if not attachment_id:
            raise CommandError(f"Вложение {att_index} не имеет attachment_id")

        try:
            client = GmailApiClient()
        except GmailNotConfigured as err:
            raise CommandError(f"Gmail не сконфигурирован: {err}")

        try:
            data = client.get_attachment(email.gmail_id, attachment_id)
        except Exception as exc:
            raise CommandError(f"Не удалось скачать вложение из Gmail: {exc}")

        # ── Кэширование ──────────────────────────────────────────────────────
        if not storage_path:
            # Генерируем путь для сохранения (аналогично email_ingest._save_attachment_bytes)
            storage_path = self._generate_storage_path(email, att_index, att_meta)
            abs_dir = Path(settings.MEDIA_ROOT) / Path(storage_path).parent
            abs_dir.mkdir(parents=True, exist_ok=True)

            abs_path = Path(settings.MEDIA_ROOT) / storage_path

            try:
                with open(abs_path, "wb") as fh:
                    fh.write(data)
                # Обновляем storage_path в БД
                att_meta["storage_path"] = storage_path
                email.save(update_fields=["attachments_json"])
                self.stderr.write(
                    self.style.SUCCESS(f"Вложение закэшировано: {storage_path}")
                )
            except OSError as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Не удалось сохранить файл в кэш: {exc}; отдаём из памяти"
                    )
                )

        # ── Вывод бинарных данных на stdout ──────────────────────────────────
        sys.stdout.buffer.write(data)

    def _generate_storage_path(self, email, att_index: int, att_meta: dict) -> str:
        """Генерирует относительный путь для сохранения вложения."""
        import re

        from django.utils import timezone

        when = email.received_at or timezone.now()
        gmail_id = email.gmail_id or f"id{email.pk}"

        # Безопасное имя файла
        filename = att_meta.get("filename") or f"attachment_{att_index}"
        safe_name = re.sub(r"[^A-Za-z0-9._\-]+", "_", filename)
        if not safe_name or safe_name == "_":
            safe_name = f"attachment_{att_index}"

        # Префикс индекса — чтобы файлы с одинаковым именем не затирались
        safe_name = f"{att_index:02d}_{safe_name}"

        rel_dir = Path("container_emails") / when.strftime("%Y") / when.strftime("%m") / gmail_id
        return str((rel_dir / safe_name).as_posix())
