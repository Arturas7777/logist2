"""Удаление дубликатов писем по содержимому (одинаковые from/subject/body).

Автоматические рассылки Caromoto, Maersk (Salesforce), Fleet-Viewer и пр.
периодически отправляют одно и то же уведомление несколькими Gmail-
сообщениями с разными ``Message-ID``. До фикса в
``core/services/email_ingest.py`` все копии сохранялись как отдельные
``ContainerEmail`` и прилетали в карточку контейнера дважды (или трижды)
с разными ярлыками сопоставления.

Команда группирует записи по ``(from_addr, subject, body_text, body_html)``
и в каждой группе оставляет только самое раннее письмо — у остальных
удаляет все ``ContainerEmailLink`` / ``CarEmailLink`` (карточки очищаются).

Сами ``ContainerEmail`` оставляем — нужны для идемпотентности
``sync_mailbox`` (чтобы следующая синхронизация не загружала тот же
``gmail_id`` заново).

Если нужно всё же физически удалить — передай ``--hard-delete``.

Примеры::

    python manage.py cleanup_email_duplicates --dry-run
    python manage.py cleanup_email_duplicates
    python manage.py cleanup_email_duplicates --hard-delete
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Min

from core.models_email import (
    CarEmailLink,
    ContainerEmail,
    ContainerEmailLink,
)


class Command(BaseCommand):
    help = (
        'Найти и скрыть (удалив линки) или удалить физически дубли '
        'ContainerEmail по from/subject/body.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать найденные группы, без изменений в БД.',
        )
        parser.add_argument(
            '--hard-delete',
            action='store_true',
            help='Физически удалить дубли (а не только разлинковать).',
        )

    def handle(self, *args, **opts):
        dry_run: bool = opts['dry_run']
        hard: bool = opts['hard_delete']

        # 1) Находим группы (from_addr, subject, body_text, body_html) с >1 записью.
        dup_keys = (
            ContainerEmail.objects
            .values('from_addr', 'subject', 'body_text', 'body_html')
            .annotate(cnt=Count('id'), earliest_id=Min('id'))
            .filter(cnt__gt=1)
            .order_by('-cnt')
        )
        dup_keys = list(dup_keys)

        if not dup_keys:
            self.stdout.write(self.style.SUCCESS(
                'Дубликатов не найдено — чистить нечего.'
            ))
            return

        total_groups = len(dup_keys)
        total_dupes = sum(g['cnt'] - 1 for g in dup_keys)

        self.stdout.write(
            f'Найдено групп: {total_groups}, лишних копий: {total_dupes}.'
        )
        # Превью первых 5 групп: что собираемся чистить.
        for g in dup_keys[:5]:
            self.stdout.write(
                f'  · {g["cnt"]} писем от "{(g["from_addr"] or "")[:50]}"'
                f' / «{(g["subject"] or "(без темы)")[:60]}»'
                f' — оставим #{g["earliest_id"]}'
            )
        if total_groups > 5:
            self.stdout.write(f'  … и ещё {total_groups - 5} групп')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: изменений нет.'))
            return

        # 2) Для каждой группы — находим все id, оставляем earliest_id,
        #    остальным прибираем линки (или удаляем, если --hard-delete).
        to_strip: list[int] = []         # id дублей, у которых только убираем линки
        to_delete: list[int] = []        # id дублей, которых удалим физически

        for g in dup_keys:
            ids = list(
                ContainerEmail.objects
                .filter(
                    from_addr=g['from_addr'],
                    subject=g['subject'],
                    body_text=g['body_text'],
                    body_html=g['body_html'],
                )
                .values_list('id', flat=True)
                .order_by('id')
            )
            if len(ids) < 2:
                continue
            keep = ids[0]
            dupes = [i for i in ids if i != keep]
            if hard:
                to_delete.extend(dupes)
            else:
                to_strip.extend(dupes)

        stripped_container_links = 0
        stripped_car_links = 0
        deleted = 0

        with transaction.atomic():
            if to_strip:
                res1 = ContainerEmailLink.objects.filter(email_id__in=to_strip).delete()
                res2 = CarEmailLink.objects.filter(email_id__in=to_strip).delete()
                stripped_container_links = res1[0]
                stripped_car_links = res2[0]
            if to_delete:
                res = ContainerEmail.objects.filter(id__in=to_delete).delete()
                deleted = res[0]

        if hard:
            self.stdout.write(self.style.SUCCESS(
                f'Удалено ContainerEmail-дублей: {deleted} '
                f'(вкл. каскадом линки).'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Разлинковано дублей: {len(to_strip)} писем. '
                f'Удалено ContainerEmailLink: {stripped_container_links}, '
                f'CarEmailLink: {stripped_car_links}. '
                f'Сами ContainerEmail сохранены для идемпотентности sync.'
            ))
