"""Импортирует статические иконки моделей авто в БД (CarModelImage).

Разово переносит файлы из ``core/static/icons/car_models/*.png`` в модель
``CarModelImage``, чтобы дальше всё управлялось через админку. При сохранении
каждая картинка автоматически нормализуется под единый канвас (WebP).

Имя файла парсится как ``ГОД МАРКА МОДЕЛЬ.png`` (напр. ``2018 BMW 430I.png``):
первый токен — год (если это 4 цифры), остальное — марка/модель. Если года
нет — year=None (подходит для любого года).

Идемпотентно: существующие записи (по brand+year) пропускаются, если не
указан ``--overwrite``.

Использование:
    python manage.py import_car_model_icons
    python manage.py import_car_model_icons --overwrite
"""

import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Импортирует static/icons/car_models/*.png в CarModelImage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--overwrite", action="store_true",
            help="Перезаписывать изображение у существующих записей (brand+year).",
        )

    def handle(self, *args, **options):
        from core.models import CarModelImage

        icons_dir = os.path.join(settings.BASE_DIR, "core", "static", "icons", "car_models")
        if not os.path.isdir(icons_dir):
            self.stderr.write(self.style.ERROR(f"Папка не найдена: {icons_dir}"))
            return

        files = sorted(f for f in os.listdir(icons_dir) if f.lower().endswith(".png"))
        if not files:
            self.stdout.write(self.style.WARNING("PNG-иконки не найдены — нечего импортировать."))
            return

        overwrite = options["overwrite"]
        created = updated = skipped = failed = 0

        for fname in files:
            stem = os.path.splitext(fname)[0].strip()
            parts = stem.split(" ", 1)
            year = None
            brand = stem
            if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 4:
                year = int(parts[0])
                brand = parts[1].strip()

            existing = CarModelImage.objects.filter(brand__iexact=brand, year=year).first()
            if existing and not overwrite:
                skipped += 1
                continue

            try:
                with open(os.path.join(icons_dir, fname), "rb") as fh:
                    raw = fh.read()

                obj = existing or CarModelImage(brand=brand, year=year)
                obj.is_active = True
                # save=False — нормализация и запись произойдут в obj.save()
                obj.image.save(fname, ContentFile(raw), save=False)
                obj.save()

                if existing:
                    updated += 1
                    self.stdout.write(f"  ~ обновлено: {brand} ({year or 'любой год'})")
                else:
                    created += 1
                    self.stdout.write(f"  + добавлено: {brand} ({year or 'любой год'})")
            except Exception as e:
                failed += 1
                self.stderr.write(self.style.ERROR(f"  ! ошибка {fname}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Добавлено: {created}, обновлено: {updated}, "
            f"пропущено: {skipped}, ошибок: {failed}."
        ))
