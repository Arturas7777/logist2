"""Удаление Gmail-черновиков, случайно попавших в ContainerEmail.

До фикса в `core/services/email_ingest.py` мы ingest-или любые сообщения
из `users.history.list` / `users.messages.list`, включая черновики
(лейбл ``DRAFT``). При автосохранении черновика в Gmail web-интерфейсе
каждые несколько секунд создаётся новый message_id — так в карточках
контейнеров, машин и автовозов накапливались десятки «писем» из одного
и того же недописанного черновика.

Фикс отфильтровывает черновики на входе, но существующие записи надо
прибрать вручную. Команда удаляет записи ``ContainerEmail`` у которых
в ``labels_json`` есть ``DRAFT`` (M2M-линки к Container / Car удалятся
каскадом).

Примеры::

    python manage.py cleanup_gmail_drafts --dry-run
    python manage.py cleanup_gmail_drafts
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models_email import ContainerEmail


class Command(BaseCommand):
    help = 'Удалить ContainerEmail-записи, которые являются Gmail-черновиками (лейбл DRAFT).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать, сколько будет удалено, без реальных изменений.',
        )

    def handle(self, *args, **opts):
        dry_run: bool = opts['dry_run']

        # labels_json — JSONField со списком строк. Ищем записи, где
        # строка 'DRAFT' присутствует в массиве. Работает как для
        # PostgreSQL (jsonb contains), так и для SQLite (fallback).
        qs = ContainerEmail.objects.filter(
            Q(labels_json__contains=['DRAFT']) | Q(labels_json__icontains='DRAFT')
        ).distinct()

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Черновики не найдены — чистить нечего.'))
            return

        self.stdout.write(f'Найдено черновиков: {total}')
        preview = list(qs.values_list('id', 'subject', 'from_addr')[:10])
        for pk, subj, frm in preview:
            self.stdout.write(f'  #{pk} «{(subj or "")[:60]}» от {frm[:60]}')
        if total > len(preview):
            self.stdout.write(f'  … и ещё {total - len(preview)}')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: ничего не удалено.'))
            return

        deleted, details = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Удалено {deleted} записей (вкл. каскад): {details}'
        ))
