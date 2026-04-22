"""Удаление дубликатов писем по «видимому» содержимому.

Автоматические рассылки Caromoto, Maersk (Salesforce), Fleet-Viewer и пр.
периодически отправляют одно и то же уведомление несколькими Gmail-
сообщениями с разными ``Message-ID``. До фикса в
``core/services/email_ingest.py`` все копии сохранялись как отдельные
``ContainerEmail`` и прилетали в карточку контейнера дважды (или трижды)
с разными ярлыками сопоставления.

Команда группирует записи по **нормализованному** содержимому (FROM +
SUBJECT + очищенное тело без <script>/<style>, без Salesforce-инициализа-
торов, без длинных JSON-блобов) и в каждой группе оставляет только
самое раннее письмо — у остальных удаляет все ``ContainerEmailLink`` /
``CarEmailLink`` (карточки очищаются).

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

from core.models_email import (
    CarEmailLink,
    ContainerEmail,
    ContainerEmailLink,
)
from core.services.email_ingest import _content_digest


class Command(BaseCommand):
    help = (
        'Найти и скрыть (удалив линки) или удалить физически дубли '
        'ContainerEmail по видимому содержимому (FROM + SUBJECT + body).'
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
        parser.add_argument(
            '--limit-preview',
            type=int,
            default=10,
            help='Сколько групп показать в превью (default: 10).',
        )

    def handle(self, *args, **opts):
        dry_run: bool = opts['dry_run']
        hard: bool = opts['hard_delete']
        preview_limit: int = opts['limit_preview']

        self.stdout.write('Считаем digest для всех ContainerEmail…')

        # digest → (earliest_id, [(id, received_at, from_addr, subject), ...])
        groups: dict[str, list[tuple[int, object, str, str]]] = defaultdict(list)

        # iterator + chunk_size чтобы не держать все письма в памяти.
        qs = (
            ContainerEmail.objects
            .only(
                'id', 'from_addr', 'subject', 'body_text', 'body_html',
                'received_at',
            )
            .order_by('id')
            .iterator(chunk_size=200)
        )

        total = 0
        for e in qs:
            total += 1
            digest = _content_digest(
                from_addr=e.from_addr or '',
                subject=e.subject or '',
                body_text=e.body_text or '',
                body_html=e.body_html or '',
            )
            groups[digest].append(
                (e.id, e.received_at, e.from_addr or '', e.subject or '')
            )

        # Оставляем только группы с ≥2 элементами.
        dup_groups = {d: rows for d, rows in groups.items() if len(rows) > 1}

        self.stdout.write(
            f'Писем обработано: {total}. '
            f'Групп-дубликатов: {len(dup_groups)}.'
        )

        if not dup_groups:
            self.stdout.write(self.style.SUCCESS(
                'Дубликатов не найдено — чистить нечего.'
            ))
            return

        total_dupes = sum(len(rows) - 1 for rows in dup_groups.values())
        self.stdout.write(f'Всего лишних копий: {total_dupes}.')

        # Превью: группы, отсортированные по числу копий убыв.
        sorted_groups = sorted(
            dup_groups.items(),
            key=lambda kv: len(kv[1]),
            reverse=True,
        )
        for digest, rows in sorted_groups[:preview_limit]:
            earliest = min(rows, key=lambda r: (r[1] or r[0], r[0]))
            self.stdout.write(
                f'  · {len(rows)} писем от "{earliest[2][:50]}" '
                f'/ «{(earliest[3] or "(без темы)")[:60]}» '
                f'— оставим #{earliest[0]}'
            )
        if len(sorted_groups) > preview_limit:
            self.stdout.write(
                f'  … и ещё {len(sorted_groups) - preview_limit} групп'
            )

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: изменений нет.'))
            return

        # Распределяем id на (keep) и (to_strip / to_delete).
        to_strip: list[int] = []
        to_delete: list[int] = []

        for rows in dup_groups.values():
            # Самое раннее письмо (по received_at, tie-break id) — оставляем.
            earliest = min(rows, key=lambda r: (r[1] or r[0], r[0]))
            keep_id = earliest[0]
            dupes = [r[0] for r in rows if r[0] != keep_id]
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
