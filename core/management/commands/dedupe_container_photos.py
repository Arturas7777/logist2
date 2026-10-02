"""
Management command: удаление повторно импортированных фото с Google Drive.

Дубль — вторая и следующие записи ``ContainerPhoto`` одного контейнера с тем
же описанием «Google Drive: <имя файла>» и побайтово тем же файлом. Остаётся
самая ранняя запись; файлы (фото и миниатюра) удалённых дублей стираются.
Записи с тем же именем, но другим содержимым не трогаются — только в отчёт.
"""

import hashlib

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models_website import ContainerPhoto


def _file_md5(field):
    try:
        with field.storage.open(field.name, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return None


class Command(BaseCommand):
    help = "Удаляет дубли фото контейнеров, повторно скачанные с Google Drive"

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Удалить дубли (без этого флага только показывает, что будет удалено)",
        )

    def handle(self, *args, **options):
        delete_mode = options["delete"]

        groups = (
            ContainerPhoto.objects.filter(description__startswith="Google Drive: ")
            .values("container_id", "description")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )

        to_delete = []
        skipped = []
        per_container = {}
        for group in groups:
            photos = list(
                ContainerPhoto.objects.filter(container_id=group["container_id"], description=group["description"])
                .select_related("container")
                .order_by("id")
            )
            keep, rest = photos[0], photos[1:]
            keep_hash = _file_md5(keep.photo)
            for photo in rest:
                if (
                    keep_hash is not None
                    and photo.photo.name != keep.photo.name
                    and _file_md5(photo.photo) == keep_hash
                ):
                    to_delete.append((keep, photo))
                    number = photo.container.number
                    per_container[number] = per_container.get(number, 0) + 1
                else:
                    skipped.append(photo)

        self.stdout.write(f"Дублей к удалению: {len(to_delete)} (групп: {groups.count()})")
        for number, count in sorted(per_container.items(), key=lambda item: -item[1]):
            self.stdout.write(f"  {number}: {count}")
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"Пропущено (другое содержимое или нет файла): {len(skipped)} — ids {[p.id for p in skipped][:20]}"
                )
            )

        if not delete_mode:
            self.stdout.write(self.style.HTTP_INFO("Для удаления запустите с флагом --delete"))
            return

        referenced = set(
            ContainerPhoto.objects.exclude(pk__in=[p.pk for _k, p in to_delete]).values_list("photo", flat=True)
        )
        referenced |= set(
            ContainerPhoto.objects.exclude(pk__in=[p.pk for _k, p in to_delete]).values_list("thumbnail", flat=True)
        )
        removed = 0
        for _keep, photo in to_delete:
            files = [f for f in (photo.photo, photo.thumbnail) if f and f.name and f.name not in referenced]
            photo.delete()
            for field in files:
                try:
                    field.storage.delete(field.name)
                except OSError as exc:
                    self.stdout.write(self.style.WARNING(f"  файл {field.name} не удалён: {exc}"))
            removed += 1

        self.stdout.write(self.style.SUCCESS(f"Удалено дублей: {removed}"))
