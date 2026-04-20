"""Повторный матчинг сохранённых писем (ContainerEmail) к контейнерам.

Используется, когда письмо было ingest-нуто с ``matched_by=UNMATCHED``
(например, контейнер ещё не был заведён в БД), а потом контейнер появился.

По умолчанию обрабатываются только UNMATCHED-письма и только «активные»
контейнеры (FLOATING / IN_PORT / UNLOADED). Уже привязанные письма не
трогаются — ручная/прежняя связка сохраняется.

После перехода на M2M команда:

- собирает ВСЕ контейнеры, которые упоминаются в письме (номер / букинг);
- создаёт недостающие ``ContainerEmailLink`` с причиной (CONTAINER_NUMBER /
  BOOKING_NUMBER) — уже существующие линки не трогает (ignore_conflicts);
- обновляет первичный ``ContainerEmail.matched_by`` только если оно было
  UNMATCHED — явную ручную/тредовую первичную причину не перетираем.

Примеры:
    python manage.py rematch_container_emails
    python manage.py rematch_container_emails --dry-run
    python manage.py rematch_container_emails --statuses FLOATING IN_PORT
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import Container
from core.models_email import ContainerEmail, ContainerEmailLink


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
        from core.services.email_matcher import (
            build_booking_index, match_email_to_containers,
        )

        statuses: list[str] = opts['statuses']
        include_matched: bool = opts['include_matched']
        dry_run: bool = opts['dry_run']

        containers = Container.objects.filter(status__in=statuses)
        booking_index = build_booking_index(containers)

        self.stdout.write(
            f'Активных контейнеров: {containers.count()} '
            f'(с booking-номерами: {len(booking_index)}). '
            f'Статусы: {", ".join(statuses)}.'
        )

        emails_qs = ContainerEmail.objects.all()
        if not include_matched:
            # UNMATCHED по первичной причине ИЛИ вообще без линков.
            emails_qs = emails_qs.filter(
                matched_by=ContainerEmail.MATCHED_BY_UNMATCHED,
            )
        total = emails_qs.count()
        self.stdout.write(f'Писем-кандидатов к проверке: {total}')

        new_links_created = 0
        emails_touched = 0
        emails_primary_updated = 0

        # Минимальный совместимый с email_matcher объект: достаточно полей
        # subject / body_text / from_addr / to_addrs / cc_addrs / thread_id.
        # У нас в БД всё это уже есть на ContainerEmail — собираем лёгкий
        # shim, чтобы не тянуть ParsedMessage.
        class _Shim:
            __slots__ = (
                'subject', 'body_text', 'body_html',
                'from_addr', 'to_addrs', 'cc_addrs',
                'thread_id',
            )

            def __init__(self, e: ContainerEmail) -> None:
                self.subject = e.subject or ''
                self.body_text = e.body_text or ''
                self.body_html = e.body_html or ''
                self.from_addr = e.from_addr or ''
                self.to_addrs = e.to_addrs or ''
                self.cc_addrs = e.cc_addrs or ''
                self.thread_id = e.thread_id or ''

        for email in emails_qs.iterator(chunk_size=200):
            shim = _Shim(email)
            result = match_email_to_containers(
                shim, booking_index=booking_index,
            )
            if not result.is_matched:
                continue

            existing_ids = set(
                email.container_links.values_list('container_id', flat=True)
            )
            to_create = [
                ContainerEmailLink(
                    email=email,
                    container_id=hit.container_id,
                    matched_by=hit.matched_by,
                )
                for hit in result.hits
                if hit.container_id not in existing_ids
            ]

            if not to_create and email.matched_by != ContainerEmail.MATCHED_BY_UNMATCHED:
                continue

            emails_touched += 1
            new_links_created += len(to_create)

            if not dry_run:
                if to_create:
                    ContainerEmailLink.objects.bulk_create(
                        to_create, ignore_conflicts=True,
                    )
                if email.matched_by == ContainerEmail.MATCHED_BY_UNMATCHED:
                    email.matched_by = result.primary_matched_by
                    email.save(update_fields=['matched_by'])
                    emails_primary_updated += 1
            elif email.matched_by == ContainerEmail.MATCHED_BY_UNMATCHED:
                emails_primary_updated += 1

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Писем затронуто:              {emails_touched}'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Новых ContainerEmailLink:     {new_links_created}'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}У писем обновлено matched_by: {emails_primary_updated}'
        ))
