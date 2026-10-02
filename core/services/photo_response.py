"""Единая точка формирования HTTP-ответа с файлом фотографии (P1).

Два режима, переключаются ``settings.PHOTO_SERVE_VIA_NGINX``:

* **выключено** (локально, тесты) — ``FileResponse``: Django сам стримит
  байты;
* **включено** (прод) — пустой ``HttpResponse`` с заголовком
  ``X-Accel-Redirect: <PHOTO_ACCEL_PREFIX>/<путь относительно MEDIA_ROOT>``.
  nginx перехватывает его и отдаёт файл из ``location /_protected_media/
  { internal; alias <MEDIA_ROOT>/; }`` — gunicorn-воркер освобождается сразу
  после проверки подписи/прав, а не держится на время передачи файла.

В обоих режимах выставляются одинаковые ``Content-Type``,
``Cache-Control: private, max-age=<TTL>`` и (для скачивания)
``Content-Disposition``. Путь к файлу берётся через ``storage.path`` (он
уже использует ``safe_join``) и дополнительно проверяется ``commonpath``
против ``MEDIA_ROOT`` — выйти за пределы media нельзя.
"""

from __future__ import annotations

import mimetypes
import os
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import content_disposition_header


def _media_root() -> str:
    return os.path.realpath(str(settings.MEDIA_ROOT))


def resolve_media_file(file_field) -> tuple[str, str]:
    """Абсолютный путь файла и путь относительно MEDIA_ROOT (с ``/``).

    Raises:
        Http404: файл отсутствует, пустое поле или путь вне ``MEDIA_ROOT``.
    """
    if not file_field or not getattr(file_field, "name", ""):
        raise Http404("Файл не задан")

    media_root = _media_root()
    try:
        abs_path = os.path.realpath(file_field.storage.path(file_field.name))
    except Exception as exc:  # SuspiciousFileOperation, NotImplementedError и т.п.
        raise Http404("Недопустимый путь файла") from exc

    try:
        inside = os.path.commonpath([media_root, abs_path]) == media_root
    except ValueError:  # разные диски на Windows
        inside = False
    if not inside:
        raise Http404("Файл вне MEDIA_ROOT")

    if not os.path.isfile(abs_path):
        raise Http404("Файл не найден")

    rel_path = os.path.relpath(abs_path, media_root).replace(os.sep, "/")
    return abs_path, rel_path


def build_photo_response(file_field, *, max_age: int, download_name: str | None = None):
    """Собирает ответ с файлом фото.

    Args:
        file_field: ``FieldFile`` (``photo.photo`` / ``photo.thumbnail``).
        max_age: TTL для ``Cache-Control: private, max-age=…`` — обычно TTL
            подписи, чтобы браузер не ходил за тем же превью повторно.
        download_name: имя файла для ``Content-Disposition: attachment``;
            ``None`` — отдаём inline (галерея).
    """
    abs_path, rel_path = resolve_media_file(file_field)
    content_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"

    if getattr(settings, "PHOTO_SERVE_VIA_NGINX", False):
        prefix = (getattr(settings, "PHOTO_ACCEL_PREFIX", "/_protected_media") or "").rstrip("/")
        response = HttpResponse(content_type=content_type)
        # nginx разэкранирует URI из заголовка, поэтому кириллица/пробелы в
        # именах файлов должны быть percent-encoded.
        response["X-Accel-Redirect"] = f"{prefix}/{quote(rel_path)}"
    else:
        response = FileResponse(open(abs_path, "rb"), content_type=content_type)

    response["Cache-Control"] = f"private, max-age={int(max_age)}"
    if download_name:
        response["Content-Disposition"] = content_disposition_header(as_attachment=True, filename=download_name)
    return response


__all__ = ["build_photo_response", "resolve_media_file"]
