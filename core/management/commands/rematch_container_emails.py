"""Повторный матчинг сохранённых писем (ContainerEmail) к контейнерам.

Используется, когда письмо было ingest-нуто с ``matched_by=UNMATCHED``
(например, контейнер ещё не был заведён в БД), а потом контейнер появился.

По умолчанию обрабатываются только UNMATCHED-письма и только «активные»
контейнеры (FLOATING / IN_PORT / UNLOADED). Уже привязанные письма не
трогаются — ручная/прежняя связка сохраняется.

Примеры:
    python manage.py rematch_container_emails
    python manage.py rematch_container_emails --dry-run
    python manage.py rematch_container_emails --statuses FLOATING IN_PORT
"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand

from core.models import Container
from core.models_email import ContainerEmail


_CONTAINER_NUMBER_RE = re.compile(r'\b([A-Z]{4}\d{7})\b')
_MIN_BOOKING_LEN = 4


class Command(BaseCommand):
    help = 'Повторно сматчить письма ContainerEmail к активным контейнерам.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--statuses',
            nargs='+',
            default=['FLOATING', 'IN_PORT', 'UNLOADED'],
            help='Коды статусов контейнеров, которые считаем активными '
                 '(по умолчанию: FLOATING IN_PORT UNLOADED).',
        )
        parser.add_argument(
            '--include-matched',
            action='store_true',
            help='Переопроверить также уже сматченные письма '
                 '(по умолчанию трогаем только UNMATCHED).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ничего не менять, только показать план изменений.',
        )

    def handle(self, *args, **opts):
        statuses: list[str] = opts['statuses']
        include_matched: bool = opts['include_matched']
        dry_run: bool = opts['dry_run']

        containers = Container.objects.filter(status__in=statuses)
        number_index: dict[str, int] = {}
        booking_index: dict[str, int] = {}
        for cid, number, booking in containers.values_list(
            'id', 'number', 'booking_number',
        ):
            if number:
                number_index[number.upper().strip()] = cid
            if booking:
                key = booking.strip().lower()
                if len(key) >= _MIN_BOOKING_LEN:
                    booking_index.setdefault(key, cid)

        self.stdout.write(
            f'Активных контейнеров: {len(number_index)}  '
            f'(с booking-номерами: {len(booking_index)}). '
            f'Статусы: {", ".join(statuses)}.'
        )

        emails_qs = ContainerEmail.objects.all()
        if not include_matched:
            emails_qs = emails_qs.filter(container__isnull=True)
        total = emails_qs.count()
        self.stdout.write(f'Писем-кандидатов к проверке: {total}')

        changed_by_number = 0
        changed_by_booking = 0
        moved_between = 0
        skipped_same = 0

        for email in emails_qs.iterator(chunk_size=200):
            text_upper = f'{email.subject or ""}\n{email.body_text or ""}'.upper()

            hit_container_id: int | None = None
            hit_matched_by: str | None = None

            for num in _CONTAINER_NUMBER_RE.findall(text_upper):
                if num in number_index:
                    hit_container_id = number_index[num]
                    hit_matched_by = ContainerEmail.MATCHED_BY_CONTAINER_NUMBER
                    break

            if hit_container_id is None and booking_index:
                text_lower = text_upper.lower()
                for booking_lower, cid in booking_index.items():
                    if booking_lower not in text_lower:
                        continue
                    if re.search(
                        rf'(?<![a-z0-9]){re.escape(booking_lower)}(?![a-z0-9])',
                        text_lower,
                    ):
                        hit_container_id = cid
                        hit_matched_by = ContainerEmail.MATCHED_BY_BOOKING_NUMBER
                        break

            if hit_container_id is None:
                continue

            if email.container_id == hit_container_id:
                skipped_same += 1
                continue

            if email.container_id is not None:
                # --include-matched → можно перебросить; без опции не трогаем.
                if not include_matched:
                    continue
                moved_between += 1

            if not dry_run:
                email.container_id = hit_container_id
                email.matched_by = hit_matched_by
                email.save(update_fields=['container_id', 'matched_by'])

            if hit_matched_by == ContainerEmail.MATCHED_BY_CONTAINER_NUMBER:
                changed_by_number += 1
            else:
                changed_by_booking += 1

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Сматчено по номеру контейнера: {changed_by_number}'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Сматчено по букингу:          {changed_by_booking}'
        ))
        if include_matched:
            self.stdout.write(
                f'{prefix}Перепривязано к другому:      {moved_between}'
            )
        self.stdout.write(
            f'Уже были привязаны правильно: {skipped_same}'
        )
