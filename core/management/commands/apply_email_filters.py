"""Применить текущие фильтры Gmail-ингеста к уже сохранённым письмам.

Полезно после добавления нового ``EmailIngestFilter`` — команда находит
ранее загруженные ``ContainerEmail``, чьё содержимое матчится активными
фильтрами, и удаляет их ``ContainerEmailLink`` / ``CarEmailLink``, чтобы
письма исчезли из карточек контейнеров / машин / автовозов.

Сами ``ContainerEmail`` не удаляются: они нужны для идемпотентности
``sync_mailbox`` (по ``gmail_id``). Если снять галочку «Активен» у
фильтра и запустить команду повторно — ничего не произойдёт, а заново
появиться в карточках скрытые письма не смогут, т.к. ``match_email_to_
containers`` для этого не прогоняется. Для ручного восстановления
запустите с флагом ``--restore``.

Примеры::

    python manage.py apply_email_filters --dry-run
    python manage.py apply_email_filters
    python manage.py apply_email_filters --days 90
    python manage.py apply_email_filters --restore --dry-run
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models_email import (
    CarEmailLink,
    ContainerEmail,
    ContainerEmailLink,
)
from core.services.email_ingest import (
    load_active_ingest_filters,
    matches_ingest_filter,
)


class Command(BaseCommand):
    help = (
        'Применить активные EmailIngestFilter к уже загруженным письмам: '
        'скрыть матчащиеся (удалить ContainerEmailLink/CarEmailLink).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать, какие письма будут затронуты.',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=60,
            help=(
                'Сканировать только письма за последние N дней '
                '(default: 60). Для полного прогона укажите 0.'
            ),
        )
        parser.add_argument(
            '--limit-preview',
            type=int,
            default=15,
            help='Сколько писем показать в превью (default: 15).',
        )
        parser.add_argument(
            '--restore',
            action='store_true',
            help=(
                'Режим восстановления: вместо скрытия матчащих писем '
                'повторно прогнать match_email_to_containers для писем '
                'без связей, не попадающих под активные фильтры.'
            ),
        )

    def handle(self, *args, **opts):
        dry_run: bool = opts['dry_run']
        days: int = opts['days']
        preview_limit: int = opts['limit_preview']
        restore: bool = opts['restore']

        filters = load_active_ingest_filters()
        self.stdout.write(
            f'Активных фильтров: {len(filters)}.'
        )
        for _, scope, match_type, phrase in filters:
            self.stdout.write(f'  · [{scope}/{match_type}] {phrase!r}')

        if not filters and not restore:
            self.stdout.write(self.style.WARNING(
                'Нет активных фильтров — нечего применять. '
                'Добавьте фильтры в /admin/core/emailingestfilter/.'
            ))
            return

        qs = ContainerEmail.objects.only(
            'id', 'from_addr', 'subject', 'body_text', 'body_html',
            'received_at',
        ).order_by('-id')
        if days and days > 0:
            since = timezone.now() - timedelta(days=days)
            qs = qs.filter(received_at__gte=since)

        if restore:
            return self._handle_restore(qs, filters, dry_run, preview_limit)

        return self._handle_hide(qs, filters, dry_run, preview_limit)

    def _handle_hide(self, qs, filters, dry_run: bool, preview_limit: int):
        matched: list[tuple[int, str, str, str]] = []  # (id, phrase, from, subject)
        scanned = 0
        for e in qs.iterator(chunk_size=200):
            scanned += 1
            hit = matches_ingest_filter(
                subject=e.subject or '',
                body_text=e.body_text or '',
                body_html=e.body_html or '',
                filters=filters,
            )
            if hit:
                matched.append((e.id, hit, e.from_addr or '', e.subject or ''))

        self.stdout.write(
            f'Просмотрено писем: {scanned}. Матчей фильтра: {len(matched)}.'
        )
        if not matched:
            self.stdout.write(self.style.SUCCESS('Нечего скрывать.'))
            return

        for mid, phrase, frm, subj in matched[:preview_limit]:
            self.stdout.write(
                f'  · #{mid} «{(subj or "(без темы)")[:55]}» '
                f'от «{frm[:40]}» → фраза «{phrase[:40]}»'
            )
        if len(matched) > preview_limit:
            self.stdout.write(f'  … и ещё {len(matched) - preview_limit}')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: изменений нет.'))
            return

        ids = [m[0] for m in matched]
        with transaction.atomic():
            r1 = ContainerEmailLink.objects.filter(email_id__in=ids).delete()
            r2 = CarEmailLink.objects.filter(email_id__in=ids).delete()

        self.stdout.write(self.style.SUCCESS(
            f'Скрыто {len(ids)} писем. '
            f'Удалено ContainerEmailLink: {r1[0]}, CarEmailLink: {r2[0]}. '
            f'Сами ContainerEmail сохранены для идемпотентности sync.'
        ))

    def _handle_restore(self, qs, filters, dry_run: bool, preview_limit: int):
        """Повторно прогнать matcher для писем без связей, не попадающих
        под активные фильтры. Позволяет «вернуть» письма в карточки после
        удаления/отключения фильтра."""
        from core.services.email_matcher import (
            build_booking_index, match_email_to_containers,
        )

        booking_index = build_booking_index()

        candidate_ids = list(
            qs.filter(
                containers__isnull=True, cars__isnull=True,
            ).values_list('id', flat=True)
        )
        self.stdout.write(
            f'Писем без связей в окне: {len(candidate_ids)}. '
            f'Проверяем, не скрыты ли они активными фильтрами…'
        )

        restored: list[tuple[int, int, int]] = []  # (email_id, container_links, car_links)

        for chunk_start in range(0, len(candidate_ids), 200):
            chunk_ids = candidate_ids[chunk_start:chunk_start + 200]
            for e in ContainerEmail.objects.filter(id__in=chunk_ids):
                if filters and matches_ingest_filter(
                    subject=e.subject or '',
                    body_text=e.body_text or '',
                    body_html=e.body_html or '',
                    filters=filters,
                ):
                    continue  # письмо всё ещё скрыто активным фильтром

                class _Tmp:
                    pass
                parsed = _Tmp()
                parsed.subject = e.subject or ''
                parsed.body_text = e.body_text or ''
                parsed.body_html = e.body_html or ''
                parsed.thread_id = e.thread_id or ''
                parsed.in_reply_to = e.in_reply_to or ''
                parsed.from_addr = e.from_addr or ''

                match = match_email_to_containers(parsed, booking_index=booking_index)
                if not match.hits and not match.car_hits:
                    continue

                if dry_run:
                    restored.append((e.id, len(match.hits), len(match.car_hits)))
                    continue

                with transaction.atomic():
                    if match.hits:
                        ContainerEmailLink.objects.bulk_create(
                            [
                                ContainerEmailLink(
                                    email=e, container_id=h.container_id,
                                    matched_by=h.matched_by, is_read=False,
                                )
                                for h in match.hits
                            ],
                            ignore_conflicts=True,
                        )
                    if match.car_hits:
                        CarEmailLink.objects.bulk_create(
                            [
                                CarEmailLink(
                                    email=e, car_id=h.car_id,
                                    matched_by=h.matched_by, is_read=False,
                                )
                                for h in match.car_hits
                            ],
                            ignore_conflicts=True,
                        )
                restored.append((e.id, len(match.hits), len(match.car_hits)))

        self.stdout.write(
            f'Кандидатов на восстановление: {len(restored)}.'
        )
        for eid, nc, ncar in restored[:preview_limit]:
            self.stdout.write(
                f'  · #{eid} → контейнеров: {nc}, машин: {ncar}'
            )
        if len(restored) > preview_limit:
            self.stdout.write(f'  … и ещё {len(restored) - preview_limit}')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: изменений нет.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Восстановлено писем: {len(restored)}.'
            ))
