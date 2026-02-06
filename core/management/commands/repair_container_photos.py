from __future__ import annotations

import os
import shutil
from typing import Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models_website import ContainerPhoto


class Command(BaseCommand):
    help = (
        "Repairs container photo records by moving files into MEDIA_ROOT when they "
        "exist in the project root, otherwise deletes broken records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without modifying files or DB.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of records to process (0 = no limit).",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]

        media_root = settings.MEDIA_ROOT
        base_dir = str(settings.BASE_DIR)

        if not media_root:
            self.stderr.write(self.style.ERROR("MEDIA_ROOT is not configured. Aborting."))
            return

        qs = ContainerPhoto.objects.select_related("container").only(
            "id", "photo", "thumbnail", "container_id"
        )
        if limit > 0:
            qs = qs[:limit]

        moved = 0
        deleted = 0
        skipped = 0

        for photo in qs:
            action = self._process_photo(photo, media_root, base_dir, dry_run=dry_run)
            if action == "moved":
                moved += 1
            elif action == "deleted":
                deleted += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. moved={moved}, deleted={deleted}, skipped={skipped}, dry_run={dry_run}"
            )
        )

    def _process_photo(
        self,
        photo: ContainerPhoto,
        media_root: str,
        base_dir: str,
        *,
        dry_run: bool,
    ) -> str:
        photo_rel = photo.photo.name if photo.photo else ""
        thumb_rel = photo.thumbnail.name if photo.thumbnail else ""

        photo_media_path = os.path.join(media_root, photo_rel) if photo_rel else ""
        thumb_media_path = os.path.join(media_root, thumb_rel) if thumb_rel else ""

        photo_exists = bool(photo_rel and os.path.exists(photo_media_path))
        thumb_exists = bool(thumb_rel and os.path.exists(thumb_media_path))

        if photo_exists or (thumb_rel and thumb_exists):
            return "skipped"

        # Try to find files in project root (BASE_DIR)
        photo_root_path = os.path.join(base_dir, photo_rel) if photo_rel else ""
        thumb_root_path = os.path.join(base_dir, thumb_rel) if thumb_rel else ""

        moved_any = False

        if photo_rel and os.path.exists(photo_root_path):
            moved_any |= self._move_file(photo_root_path, photo_media_path, dry_run)

        if thumb_rel and os.path.exists(thumb_root_path):
            moved_any |= self._move_file(thumb_root_path, thumb_media_path, dry_run)

        if moved_any:
            return "moved"

        # Nothing found -> delete record
        if not dry_run:
            photo.delete()
        return "deleted"

    def _move_file(self, src: str, dst: str, dry_run: bool) -> bool:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if dry_run:
            return True
        shutil.move(src, dst)
        return True
