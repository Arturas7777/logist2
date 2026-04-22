"""
Пережатие существующих фотографий контейнеров (и опционально автомобилей)
до разумного web-качества для экономии места на диске.

Используется для одноразовой миграции старых фото, снятых на телефон
в исходном разрешении (3264×2448 … 4080×3072, 2-6 MB/шт).

По умолчанию:
  - target long-side 2560 px (LANCZOS, по EXIF ориентации)
  - JPEG quality 85, progressive, optimize
  - пропускаем фото, у которых max(w,h) уже <= 2560

Замена файла атомарна (tempfile + os.replace в той же директории), поэтому
прервать процесс безопасно — оригинал остаётся на диске до успешной записи
нового варианта.

Примеры:
  # Посмотреть что будет сделано, без изменений:
  python manage.py resize_photos --model container --dry-run --limit 50

  # Переделать всё по контейнерам:
  python manage.py resize_photos --model container --batch-log 100

  # Пережать фото конкретного контейнера:
  python manage.py resize_photos --model container --container TGBU5704688

  # Более агрессивно (1920 px, q=85):
  python manage.py resize_photos --model container --target-px 1920

  # Фото автомобилей:
  python manage.py resize_photos --model car
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


_KNOWN_JPEG_EXTS = {'.jpg', '.jpeg'}


class Command(BaseCommand):
    help = 'Пережимает существующие фотографии до web-качества для экономии диска'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            choices=['container', 'car', 'all'],
            default='container',
            help='Какие фото пережимать (default: container)',
        )
        parser.add_argument(
            '--container',
            dest='container_number',
            default=None,
            help='Только фото указанного контейнера (номер)',
        )
        parser.add_argument('--target-px', type=int, default=2560,
                            help='Максимальная длинная сторона (default: 2560)')
        parser.add_argument('--quality', type=int, default=85,
                            help='JPEG quality (default: 85)')
        parser.add_argument('--limit', type=int, default=0,
                            help='Обработать не более N фото (0 = все)')
        parser.add_argument('--batch-log', type=int, default=50,
                            help='Логировать прогресс каждые N фото')
        parser.add_argument('--dry-run', action='store_true',
                            help='Только считает, ничего не пишет')
        parser.add_argument('--force', action='store_true',
                            help='Пережимать даже если long-side уже <= target')

    # ------------------------------------------------------------------ helpers

    def _iter_querysets(self, *, model: str, container_number: str | None):
        """Возвращает список (label, queryset, field_name) для обработки."""
        from core.models_website import CarPhoto, ContainerPhoto

        jobs = []
        if model in ('container', 'all'):
            qs = ContainerPhoto.objects.all().order_by('id')
            if container_number:
                qs = qs.filter(container__number=container_number)
            jobs.append(('ContainerPhoto', qs, 'photo'))
        if model in ('car', 'all'):
            qs = CarPhoto.objects.all().order_by('id')
            if container_number:
                qs = qs.filter(car__container__number=container_number)
            jobs.append(('CarPhoto', qs, 'photo'))
        return jobs

    def _free_space_mb(self) -> float:
        try:
            usage = shutil.disk_usage(str(settings.MEDIA_ROOT))
            return usage.free / 1024 / 1024
        except OSError:
            return -1.0

    # ------------------------------------------------------------------ main

    def handle(self, *args, **opts):
        target_px: int = opts['target_px']
        quality: int = opts['quality']
        limit: int = opts['limit']
        dry_run: bool = opts['dry_run']
        force: bool = opts['force']
        batch_log: int = max(1, opts['batch_log'])
        model: str = opts['model']
        container_number: str | None = opts['container_number']

        if target_px < 400 or target_px > 8000:
            raise CommandError('--target-px должен быть в диапазоне 400..8000')
        if quality < 40 or quality > 100:
            raise CommandError('--quality должен быть 40..100')

        media_root = Path(settings.MEDIA_ROOT)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'=== resize_photos ===\n'
            f'  model:       {model}\n'
            f'  target-px:   {target_px}\n'
            f'  quality:     {quality}\n'
            f'  dry-run:     {dry_run}\n'
            f'  force:       {force}\n'
            f'  limit:       {limit or "∞"}\n'
            f'  container:   {container_number or "(все)"}\n'
            f'  MEDIA_ROOT:  {media_root}\n'
            f'  free space:  {self._free_space_mb():.0f} MB\n'
        ))

        jobs = self._iter_querysets(model=model, container_number=container_number)

        grand = {
            'seen': 0, 'skipped_small': 0, 'skipped_nonjpeg': 0,
            'skipped_missing': 0, 'skipped_unchanged_bigger': 0,
            'resized': 0, 'errors': 0,
            'bytes_before': 0, 'bytes_after': 0,
        }
        t_start = time.time()

        for label, qs, field_name in jobs:
            total = qs.count()
            self.stdout.write(self.style.HTTP_INFO(
                f'\n--- {label}: {total} записей ---'
            ))
            if total == 0:
                continue

            processed_here = 0
            for obj in qs.iterator(chunk_size=200):
                if limit and grand['seen'] >= limit:
                    break
                grand['seen'] += 1
                processed_here += 1

                result = self._process_one(
                    obj, field_name, media_root,
                    target_px=target_px, quality=quality,
                    dry_run=dry_run, force=force,
                )
                for k in ('skipped_small', 'skipped_nonjpeg', 'skipped_missing',
                          'skipped_unchanged_bigger', 'resized', 'errors',
                          'bytes_before', 'bytes_after'):
                    grand[k] += result.get(k, 0)

                if processed_here % batch_log == 0:
                    saved_mb = (grand['bytes_before'] - grand['bytes_after']) / 1024 / 1024
                    self.stdout.write(
                        f'  [{label}] {processed_here}/{total}  '
                        f'resized={grand["resized"]}  '
                        f'skipped={grand["skipped_small"] + grand["skipped_nonjpeg"] + grand["skipped_missing"] + grand["skipped_unchanged_bigger"]}  '
                        f'errors={grand["errors"]}  '
                        f'saved={saved_mb:.0f} MB  '
                        f'free={self._free_space_mb():.0f} MB'
                    )
                    sys.stdout.flush()

            if limit and grand['seen'] >= limit:
                break

        dt = time.time() - t_start
        before_mb = grand['bytes_before'] / 1024 / 1024
        after_mb = grand['bytes_after'] / 1024 / 1024
        saved_mb = before_mb - after_mb

        summary = (
            f'\n=== ИТОГО ({dt:.1f} сек) ===\n'
            f'  просмотрено:               {grand["seen"]}\n'
            f'  пережато:                  {grand["resized"]}\n'
            f'  пропущено (уже маленькое): {grand["skipped_small"]}\n'
            f'  пропущено (не JPEG):       {grand["skipped_nonjpeg"]}\n'
            f'  пропущено (файла нет):     {grand["skipped_missing"]}\n'
            f'  пропущено (стало больше):  {grand["skipped_unchanged_bigger"]}\n'
            f'  ошибок:                    {grand["errors"]}\n'
            f'  было:                      {before_mb:,.1f} MB\n'
            f'  стало:                     {after_mb:,.1f} MB\n'
            f'  освобождено:               {saved_mb:,.1f} MB '
            f'({(saved_mb/before_mb*100) if before_mb else 0:.1f}%)\n'
            f'  свободно сейчас:           {self._free_space_mb():.0f} MB\n'
        )
        style = self.style.SUCCESS if grand['errors'] == 0 else self.style.WARNING
        self.stdout.write(style(summary))

    # ---------------------------------------------------- per-file logic

    def _process_one(self, obj, field_name: str, media_root: Path,
                     *, target_px: int, quality: int,
                     dry_run: bool, force: bool) -> dict:
        out = {}
        field = getattr(obj, field_name, None)
        if not field or not field.name:
            out['skipped_missing'] = 1
            return out

        src_path = Path(field.path)
        if not src_path.exists():
            out['skipped_missing'] = 1
            return out

        ext = src_path.suffix.lower()
        if ext not in _KNOWN_JPEG_EXTS:
            # PNG/WebP/GIF не трогаем — их обычно единицы, и экономия мизерная,
            # а риск потерять прозрачность/анимацию есть.
            out['skipped_nonjpeg'] = 1
            return out

        try:
            size_before = src_path.stat().st_size
            with Image.open(src_path) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                w, h = im.size
                long_side = max(w, h)

                if not force and long_side <= target_px:
                    out['skipped_small'] = 1
                    return out

                if im.mode not in ('RGB',):
                    im = im.convert('RGB')

                if long_side > target_px:
                    im.thumbnail((target_px, target_px), Image.LANCZOS)

                if dry_run:
                    # Грубая оценка размера без записи (saver в BytesIO):
                    import io
                    buf = io.BytesIO()
                    im.save(buf, 'JPEG', quality=quality,
                            optimize=True, progressive=True)
                    size_after = buf.tell()
                else:
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        prefix='.resize_', suffix=ext, dir=str(src_path.parent),
                    )
                    os.close(tmp_fd)
                    try:
                        im.save(tmp_path, 'JPEG', quality=quality,
                                optimize=True, progressive=True)
                        size_after = os.path.getsize(tmp_path)

                        if size_after >= size_before and not force:
                            # Не стало легче — не трогаем файл.
                            os.remove(tmp_path)
                            out['skipped_unchanged_bigger'] = 1
                            out['bytes_before'] = size_before
                            out['bytes_after'] = size_before
                            return out

                        os.replace(tmp_path, src_path)
                    except Exception:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                        raise

            out['resized'] = 1
            out['bytes_before'] = size_before
            out['bytes_after'] = size_after
            return out

        except (UnidentifiedImageError, OSError, ValueError) as e:
            out['errors'] = 1
            logger.warning('resize_photos: %s (id=%s) — ошибка: %s',
                           src_path, getattr(obj, 'id', '?'), e)
            return out
