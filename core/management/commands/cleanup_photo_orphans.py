"""
Удаляет orphan-оригиналы фото контейнеров, оставшиеся на диске после старого
бага в `photo_optimize.maybe_compress_image_field`:

  • При сохранении ContainerPhoto через Google Drive sync Django сначала писал
    оригинал `IMG_xxx.jpg` (2-3 МБ).
  • Потом `ContainerPhoto.save()` вызывал `maybe_compress_image_field`, тот
    делал `field.save('IMG_xxx.jpg', compressed_bytes)`. Django storage видел,
    что файл уже есть, и через `get_available_name()` переименовывал в
    `IMG_xxx_RANDOM.jpg`.
  • Запись в БД смотрела на сжатую копию (~600 КБ), а оригинал оставался
    сиротой на диске. Накопилось 3+ ГБ.

Команда находит такие пары: для каждой ContainerPhoto, у которой имя файла
заканчивается на `_<7символов>.jpg`, проверяет, есть ли рядом файл с тем же
именем без суффикса и НЕ привязанный ни к одной записи в БД. Если да — удаляет.

По умолчанию работает в --dry-run режиме (ничего не трогает).

Примеры:
    python manage.py cleanup_photo_orphans --dry-run
    python manage.py cleanup_photo_orphans                    # реально удалить
    python manage.py cleanup_photo_orphans --limit 100        # ограничить
    python manage.py cleanup_photo_orphans --since 2026-04-01 # только новее
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models_website import CarPhoto, ContainerPhoto

logger = logging.getLogger(__name__)

PHOTO_MODELS = {
    'container': ContainerPhoto,
    'car': CarPhoto,
}

# Суффикс, который добавляет django.core.files.storage.get_available_name():
# 7 символов из [A-Za-z0-9] перед расширением.
SUFFIX_RE = re.compile(r'^(?P<stem>.+)_(?P<suffix>[A-Za-z0-9]{7})(?P<ext>\.[Jj][Pp][Ee]?[Gg])$')


class Command(BaseCommand):
    help = 'Удаляет orphan-оригиналы фото, не привязанные к ContainerPhoto в БД'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Только показать что будет удалено, ничего не трогать (default)',
        )
        parser.add_argument(
            '--no-dry-run', action='store_true',
            help='Реально удалить файлы (без этого флага — только просчёт)',
        )
        parser.add_argument(
            '--limit', type=int, default=0,
            help='Не обрабатывать больше N записей на каждый тип фото (0 = все)',
        )
        parser.add_argument(
            '--since', type=str, default=None,
            help='ISO-дата (YYYY-MM-DD), обрабатывать только фото загруженные не раньше',
        )
        parser.add_argument(
            '--batch-log', type=int, default=200,
            help='Логировать прогресс каждые N записей (default: 200)',
        )
        parser.add_argument(
            '--only', type=str, choices=list(PHOTO_MODELS.keys()), default=None,
            help='Обрабатывать только container или car (по умолчанию оба)',
        )

    def handle(self, *args, **opts):
        dry_run = not opts['no_dry_run']  # по умолчанию dry-run
        if opts['dry_run'] and opts['no_dry_run']:
            self.stdout.write(self.style.WARNING(
                'Указаны и --dry-run, и --no-dry-run; работаю в dry-run.'
            ))
            dry_run = True

        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            self.stderr.write(f'MEDIA_ROOT не найден: {media_root}')
            return

        cutoff = None
        if opts['since']:
            try:
                cutoff_naive = datetime.fromisoformat(opts['since'])
                if timezone.is_naive(cutoff_naive):
                    cutoff = timezone.make_aware(cutoff_naive)
                else:
                    cutoff = cutoff_naive
                self.stdout.write(f'Фильтр: uploaded_at >= {cutoff.isoformat()}')
            except ValueError:
                self.stderr.write(f'Некорректная дата --since: {opts["since"]}')
                return

        models_to_process = (
            {opts['only']: PHOTO_MODELS[opts['only']]}
            if opts['only'] else PHOTO_MODELS
        )

        totals = {'scanned': 0, 'candidates': 0, 'deleted': 0,
                  'bytes_freed': 0, 'errors': 0}

        for label, model in models_to_process.items():
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE(f'=== {label.upper()}PHOTO ==='))

            qs = model.objects.all().only('id', 'photo')
            if cutoff is not None:
                qs = qs.filter(uploaded_at__gte=cutoff)

            # Множество путей в БД (на которые ссылается какая-либо запись),
            # чтобы случайно не удалить файл, на который смотрит другая запись.
            all_db_paths = set(
                model.objects.exclude(photo='').values_list('photo', flat=True)
            )
            self.stdout.write(f'Записей в БД: {len(all_db_paths)}')

            limit = opts['limit'] or 0
            batch_log = opts['batch_log']

            scanned = candidates = deleted = errors = 0
            bytes_freed = 0

            for cp in qs.iterator():
                scanned += 1
                if limit and scanned > limit:
                    break

                if scanned % batch_log == 0:
                    self.stdout.write(
                        f'  ... просмотрено {scanned}, кандидатов {candidates}, '
                        f'удалено {deleted}, '
                        f'освобождено {bytes_freed/1024/1024:.1f} MB'
                    )

                fname = cp.photo.name if cp.photo else ''
                if not fname:
                    continue

                base = os.path.basename(fname)
                m = SUFFIX_RE.match(base)
                if not m:
                    continue  # имя без 7-символьного суффикса — не наш случай

                stem = m.group('stem')
                ext = m.group('ext')
                orig_name = f'{stem}{ext}'

                db_dir = os.path.dirname(fname)
                orig_rel_path = (
                    os.path.join(db_dir, orig_name) if db_dir else orig_name
                )
                orig_abs_path = media_root / orig_rel_path

                if not orig_abs_path.exists():
                    continue

                # Защита: если этот «оригинал» сам зарегистрирован в БД —
                # не трогаем (на него смотрит другая запись).
                if orig_rel_path.replace(os.sep, '/') in all_db_paths:
                    continue

                candidates += 1

                try:
                    size = orig_abs_path.stat().st_size
                except OSError:
                    size = 0

                if dry_run:
                    if candidates <= 5:
                        self.stdout.write(
                            f'  ORPHAN: {orig_rel_path} ({size//1024} KB) '
                            f'[{label}#{cp.id} → {fname}]'
                        )
                    bytes_freed += size
                    continue

                try:
                    orig_abs_path.unlink()
                    deleted += 1
                    bytes_freed += size
                except OSError as e:
                    errors += 1
                    logger.warning('Не удалось удалить %s: %s',
                                   orig_abs_path, e)

            self.stdout.write(
                f'  Итог по {label}: просмотрено {scanned}, '
                f'orphans {candidates}, удалено {deleted}, '
                f'освобождено {bytes_freed/1024/1024:.1f} MB, ошибок {errors}'
            )
            totals['scanned'] += scanned
            totals['candidates'] += candidates
            totals['deleted'] += deleted
            totals['bytes_freed'] += bytes_freed
            totals['errors'] += errors

        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write(f'ВСЕГО просмотрено:        {totals["scanned"]}')
        self.stdout.write(f'ВСЕГО orphan-оригиналов:  {totals["candidates"]}')
        bytes_freed = totals['bytes_freed']
        gb = bytes_freed / 1024 / 1024 / 1024
        mb = bytes_freed / 1024 / 1024
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: ничего не удалено. Можно освободить: '
                f'{mb:.1f} MB ({gb:.2f} GB)'
            ))
            self.stdout.write(self.style.NOTICE(
                'Запустить реально:  '
                'python manage.py cleanup_photo_orphans --no-dry-run'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Удалено: {totals["deleted"]}, освобождено: '
                f'{mb:.1f} MB ({gb:.2f} GB)'
            ))
            if totals['errors']:
                self.stdout.write(self.style.ERROR(
                    f'Ошибок при удалении: {totals["errors"]}'
                ))
