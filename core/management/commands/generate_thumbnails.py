"""
Команда для создания миниатюр для существующих фотографий контейнеров
и картинок моделей авто (CarModelImage).
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import CarModelImage
from core.models_website import ContainerPhoto


class Command(BaseCommand):
    help = "Создает миниатюры для фотографий контейнеров и картинок моделей авто"

    def handle(self, *args, **options):
        self._process(
            ContainerPhoto.objects.filter(thumbnail__isnull=True),
            label="фотографий контейнеров",
        )
        self._process(
            CarModelImage.objects.filter(Q(thumbnail__isnull=True) | Q(thumbnail="")).exclude(image=""),
            label="картинок моделей авто",
        )

    def _process(self, queryset, label):
        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(f"Все {label} уже имеют миниатюры"))
            return

        self.stdout.write(f"Найдено {label} без миниатюр: {total}")

        success_count = 0
        error_count = 0

        for i, photo in enumerate(queryset, 1):
            try:
                if photo.create_thumbnail():
                    photo.save(update_fields=["thumbnail"])
                    success_count += 1
                else:
                    error_count += 1

                if i % 10 == 0:
                    self.stdout.write(f"Обработано: {i}/{total}")

            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(f"Ошибка для ID {photo.id}: {e!s}"))

        self.stdout.write(self.style.SUCCESS(f"Готово ({label})! Успешно: {success_count}, Ошибок: {error_count}"))
