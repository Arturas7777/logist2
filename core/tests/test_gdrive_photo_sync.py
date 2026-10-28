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


def test_same_name_in_both_folders_imported_for_each_type(container):
    unloading = [{"id": "unl-a", "name": "a.jpg", "is_folder": False}]
    in_container = [{"id": "in-a", "name": "a.jpg", "is_folder": False}]
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", side_effect=[unloading, in_container]),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG),
    ):
        GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container, photo_type="UNLOADING")
        added = GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container, photo_type="IN_CONTAINER")

    assert added == 1
    assert set(ContainerPhoto.objects.filter(container=container).values_list("photo_type", flat=True)) == {
        "UNLOADING",
        "IN_CONTAINER",
    }


def test_drive_file_id_stored_and_renamed_file_not_reimported(container):
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=_listing("a.jpg")),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG),
    ):
        GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container)

    photo = ContainerPhoto.objects.get(container=container)
    assert photo.drive_file_id == "id-0-a.jpg"

    renamed = [{"id": "id-0-a.jpg", "name": "renamed.jpg", "is_folder": False}]
    with (
        patch.object(GoogleDriveSync, "get_folder_files_web", return_value=renamed),
        patch.object(GoogleDriveSync, "download_file", return_value=JPEG) as download,
    ):
        added = GoogleDriveSync.download_folder_photos("folderid1234567890abcdef", container)

    assert added == 0
    download.assert_not_called()


def test_drive_file_id_unique_per_container(container):
    from django.db import IntegrityError, transaction

    ContainerPhoto.objects.create(container=container, photo="a.jpg", drive_file_id="X1")
    ContainerPhoto.objects.create(container=container, photo="b.jpg")
    ContainerPhoto.objects.create(container=container, photo="c.jpg")
    with pytest.raises(IntegrityError), transaction.atomic():
        ContainerPhoto.objects.create(container=container, photo="d.jpg", drive_file_id="X1")


def test_store_photo_race_on_same_drive_file_is_skipped(container):
    ContainerPhoto.objects.create(container=container, photo="a.jpg", drive_file_id="X1", photo_type="IN_CONTAINER")
    with patch.object(GoogleDriveSync, "_photo_exists", return_value=False):
        stored = GoogleDriveSync._store_photo(container, "UNLOADING", "Google Drive: z.jpg", "z.jpg", JPEG, "X1")

    assert stored is False
    assert ContainerPhoto.objects.filter(container=container).count() == 1


def test_listing_network_error_propagates_for_retry(container):
    import requests

    from core.google_drive_sync import GoogleDriveUnavailable

    with (
        patch("core.google_drive_sync.get_drive_api_client", return_value=None),
        patch("core.google_drive_sync.requests.get", side_effect=requests.ConnectionError("down")),
        pytest.raises(GoogleDriveUnavailable),
    ):
        GoogleDriveSync.sync_container_by_number(container.number)


def test_listing_server_error_propagates_but_404_is_empty():
    from core.google_drive_sync import GoogleDriveUnavailable

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    with patch("core.google_drive_sync.get_drive_api_client", return_value=None):
        with (
            patch("core.google_drive_sync.requests.get", return_value=Resp(503)),
            pytest.raises(GoogleDriveUnavailable),
        ):
            GoogleDriveSync.get_folder_files_web("folderid1234567890abcdef")
        with patch("core.google_drive_sync.requests.get", return_value=Resp(404)):
            assert GoogleDriveSync.get_folder_files_web("folderid1234567890abcdef") == []


def test_gdrive_task_retries_on_sync_error(container):
    from celery.exceptions import Retry

    from core.tasks import sync_container_photos_gdrive_task

    with (
        patch.object(GoogleDriveSync, "sync_container_by_number", side_effect=RuntimeError("drive down")),
        patch.object(sync_container_photos_gdrive_task, "retry", side_effect=Retry()) as retry,
        pytest.raises(Retry),
    ):
        sync_container_photos_gdrive_task.run(container.pk)
    retry.assert_called_once()


def test_drive_api_service_is_per_thread():
    from concurrent.futures import ThreadPoolExecutor

    from core.services.gdrive_client import GoogleDriveApiClient

    client = GoogleDriveApiClient(api_key="test-key")
    with patch.object(GoogleDriveApiClient, "_build_service", side_effect=lambda: object()) as build:
        main_service = client.service
        assert client.service is main_service

        with ThreadPoolExecutor(max_workers=2) as pool:
            thread_services = list(pool.map(lambda _: client.service, range(2)))

    assert all(s is not main_service for s in thread_services)
    assert build.call_count == 1 + len({id(s) for s in thread_services})


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
