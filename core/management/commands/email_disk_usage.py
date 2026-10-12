"""Сколько места занимает почта: строки ContainerEmail и файлы вложений.

    python manage.py email_disk_usage --pretty

Только чтение; ничего не удаляет (очистка — задача cleanup_email_storage).
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Отчёт о месте, занятом почтой (БД и файлы вложений), и о том, что освободит ретеншен."

    def add_arguments(self, parser):
        parser.add_argument("--pretty", action="store_true")

    def handle(self, *args, **options):
        from core.services.email_storage import disk_usage

        report = disk_usage()
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2 if options["pretty"] else None))
