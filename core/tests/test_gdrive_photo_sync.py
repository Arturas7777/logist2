"""Синхронизация фото контейнеров с Google Drive: защита от дублей.

* параллельный процесс по тому же контейнеру пропускается (замок);
* фото, появившееся в базе во время прохода, повторно не скачивается;
* команда ``dedupe_container_photos`` удаляет только побайтовые дубли.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.management import call_command

from core.google_drive_sync import GoogleDriveSync
from core.models import Container
from core.models.website import ContainerPhoto

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8" + b"x" * 200


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    cache.clear()
    with (
        patch("core.services.photo_optimize.maybe_compress_image_field", return_value=False),
        patch("core.services.photo_optimize.compress_image_bytes", return_value=None),
    ):
        yield


@pytest.fixture
def container():
    return Container.objects.create(number="DUPE1234567", status="IN_PORT")


def _listing(*names):
    return [{"id": f"id-{i}-{name}", "name": name, "is_folder": False} for i, name in enumerate(names)]


def _add_photo(container, name, content=JPEG):
    photo = ContainerPhoto(container=container, description=f"Google Drive: {name}", photo_type="UNLOADING")
    photo.photo.save(name, ContentFile(content), save=False)
    photo.save()
    return photo


def test_sync_skipped_while_other_process_holds_container(container):
    cache.add(f"gdrive_photo_sync:{container.pk}", "1", 60)
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=_listing("a.jpg")),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG) as download,
    ):
        added = GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container)

    assert added == 0
    download.assert_not_called()
    assert not ContainerPhoto.objects.filter(container=container).exists()


def test_lock_released_after_sync(container):
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=_listing("a.jpg")),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG),
    ):
        assert GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container) == 1
    assert cache.add(f"gdrive_photo_sync:{container.pk}", "1", 60)


def test_photo_added_meanwhile_is_not_downloaded_again(container):
    def fake_download(file_id):
        # Пока мы качаем первый файл, «другой процесс» успел сохранить второй.
        if file_id.endswith("a.jpg"):
            _add_photo(container, "b.jpg")
        return JPEG

    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=_listing("a.jpg", "b.jpg")),
        patch.object(GoogleDriveSync, "download_file", side_effect=fake_download) as download,
    ):
        added = GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container)

    assert added == 1
    assert download.call_count == 1
    assert ContainerPhoto.objects.filter(container=container, description="Google Drive: b.jpg").count() == 1


def test_same_name_listed_twice_imported_once(container):
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=_listing("a.jpg", "a.jpg")),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG),
    ):
        added = GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container)

    assert added == 1
    assert ContainerPhoto.objects.filter(container=container).count() == 1


def test_dedupe_command_removes_only_identical_copies(container):
    original = _add_photo(container, "a.jpg")
    copy = _add_photo(container, "a.jpg")
    other = _add_photo(container, "a.jpg", content=b"\xff\xd8" + b"y" * 200)
    copy_path = copy.photo.path

    call_command("dedupe_container_photos")
    assert ContainerPhoto.objects.filter(container=container).count() == 3

    call_command("dedupe_container_photos", "--delete")

    remaining = set(ContainerPhoto.objects.filter(container=container).values_list("pk", flat=True))
    assert remaining == {original.pk, other.pk}
    assert not os.path.exists(copy_path)
    assert os.path.exists(original.photo.path)
