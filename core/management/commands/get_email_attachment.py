"""Файл вложения письма: из кэша CRM, а при отсутствии — один раз из Gmail.

    python manage.py get_email_attachment 12345 0 --info
    python manage.py get_email_attachment 12345 0 --output /tmp/release.pdf
    python manage.py get_email_attachment 12345 0 > release.pdf

Индекс — поле ``index`` из ``export_emails_json``. Код выхода 1 и текст
ошибки в stderr, если вложение недоступно.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.services.email_attachments import (
    AttachmentUnavailable,
    cached_path,
    get_attachment_file,
    get_attachment_meta,
)


class Command(BaseCommand):
    help = "Выдать файл вложения письма (кэш CRM, при отсутствии — однократная докачка из Gmail)."

    def add_arguments(self, parser):
        parser.add_argument("email_id", type=int)
        parser.add_argument("index", type=int, help="Индекс вложения (поле index из export_emails_json).")
        parser.add_argument("--info", action="store_true", help="Только метаданные в JSON, без файла и без Gmail.")
        parser.add_argument("--output", help="Записать файл по этому пути вместо stdout.")

    def handle(self, *args, **options):
        from core.models.email import ContainerEmail

        email = ContainerEmail.objects.filter(pk=options["email_id"]).first()
        if email is None:
            raise CommandError(f"Письмо {options['email_id']} не найдено")
        try:
            meta = get_attachment_meta(email, options["index"])
        except IndexError as exc:
            raise CommandError(str(exc)) from None

        if options["info"]:
            info = {
                "email_id": email.pk,
                "index": options["index"],
                "filename": meta.get("filename") or "",
                "size": meta.get("size") or 0,
                "content_type": meta.get("content_type") or "",
                "is_inline": bool(meta.get("is_inline")),
                "cached": cached_path(meta) is not None,
            }
            self.stdout.write(json.dumps(info, ensure_ascii=False))
            return

        try:
            path, _meta = get_attachment_file(email, options["index"])
        except AttachmentUnavailable as exc:
            raise CommandError(str(exc)) from None

        if options["output"]:
            shutil.copyfile(path, Path(options["output"]))
            return
        out = self.stdout._out
        sink = getattr(out, "buffer", out)
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, sink)
        sink.flush()
