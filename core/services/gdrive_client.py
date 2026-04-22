"""Google Drive API v3 client — тонкая обёртка для чтения публичных папок.

Используется в ``core/google_drive_sync.py`` как замена HTML-парсингу
``embeddedfolderview`` / ``/drive/folders/...``. HTML-подход режет списки
файлов (Drive не отдаёт в чистом HTML больше ~50–100 элементов — остальное
подгружается JavaScript-ом), из-за чего большие папки (100+ фото) синхятся
обрезанными. API v3 с ``pageToken`` отдаёт всё.

Режим работы: **API key** (``developerKey=``). Подходит для папок,
опубликованных как *«Anyone with the link»*. Сервисный аккаунт / OAuth
сейчас не нужны — если когда-то понадобится читать приватные папки, сюда
можно добавить второй режим без изменения вызывающего кода.

Настройка:
  1. В Google Cloud Console включить Google Drive API.
  2. Создать API key (Credentials → Create Credentials → API key), ограничить
     его по Drive API.
  3. Положить в ``.env``: ``GOOGLE_DRIVE_API_KEY=AIzaSy...``.

Если ключ не задан — ``is_configured`` вернёт ``False``, а
``google_drive_sync`` откатится на старую HTML-логику (чтобы не ломать
окружения, где Drive API ещё не подключён).
"""

from __future__ import annotations

import io
import logging
from typing import Any, Iterator

from django.conf import settings

logger = logging.getLogger(__name__)


# MIME тип подпапки в Google Drive (используется в запросе ``q=...``).
_MIME_FOLDER = 'application/vnd.google-apps.folder'

# Поля, которые запрашиваем у files.list — минимальный набор, чтобы не
# платить квотой за ненужное.
_LIST_FIELDS = 'nextPageToken, files(id, name, mimeType, size)'

# Расширения, которые считаем картинками (совпадает с HTML-веткой).
_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')


class DriveApiNotConfigured(RuntimeError):
    """GOOGLE_DRIVE_API_KEY не задан — клиент не может работать."""


class GoogleDriveApiClient:
    """Lazy-клиент к Drive API v3.

    Один экземпляр на процесс достаточно — HTTP-сессия внутри
    ``googleapiclient`` потокобезопасна, а discovery кэшируется.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or getattr(settings, 'GOOGLE_DRIVE_API_KEY', '') or '').strip()
        if not self._api_key:
            raise DriveApiNotConfigured(
                'GOOGLE_DRIVE_API_KEY не задан. Включите Drive API в Google '
                'Cloud Console, создайте API key и положите его в .env.'
            )
        self._service = None

    # ------------------------------------------------------------------
    # service (ленивая инициализация)
    # ------------------------------------------------------------------

    def _build_service(self):
        from googleapiclient.discovery import build

        return build(
            'drive', 'v3',
            developerKey=self._api_key,
            cache_discovery=False,
        )

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    # ------------------------------------------------------------------
    # list folders / files
    # ------------------------------------------------------------------

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        """Возвращает **все** элементы внутри папки с полной пагинацией.

        Каждый элемент — dict совместимый с форматом из
        ``GoogleDriveSync._parse_embedded_folder``:
        ``{id, name, mimeType, is_folder}``. Для файлов дополнительно
        кладём ``size`` (строка; Drive API отдаёт size как string).

        ``q`` = ``"<parent> in parents and trashed=false"``.
        """
        if not folder_id:
            return []

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in self._iter_files(
            query=f"'{folder_id}' in parents and trashed = false",
        ):
            fid = raw.get('id')
            if not fid or fid in seen:
                continue
            seen.add(fid)
            mime = raw.get('mimeType') or ''
            results.append({
                'id': fid,
                'name': raw.get('name') or '',
                'mimeType': mime,
                'is_folder': mime == _MIME_FOLDER,
                'size': raw.get('size'),
            })
        return results

    def list_images(self, folder_id: str) -> list[dict[str, Any]]:
        """Фильтрует ``list_children`` — оставляет только картинки.

        Смотрим сперва на ``mimeType=image/*`` (надёжнее), и как fallback —
        на расширение файла (бывает, что Drive отдаёт
        ``application/octet-stream`` для HEIC и т.п.).
        """
        items = self.list_children(folder_id)
        out: list[dict[str, Any]] = []
        for f in items:
            if f['is_folder']:
                continue
            mime = (f.get('mimeType') or '').lower()
            name = (f.get('name') or '').lower()
            if mime.startswith('image/') or name.endswith(_IMAGE_EXTS):
                out.append(f)
        return out

    def find_subfolder(
        self, parent_id: str, *, name_contains: str,
    ) -> dict[str, Any] | None:
        """Ищет первую подпапку, чьё имя содержит ``name_contains``.

        Сервер Drive умеет ``name contains 'xxx'`` в ``q=...``, поэтому
        делаем это одним запросом (а не тянем всю папку на клиент).
        Поиск нечувствителен к регистру на стороне Drive.
        """
        if not parent_id or not name_contains:
            return None

        safe = name_contains.replace("'", "\\'")
        query = (
            f"'{parent_id}' in parents "
            f"and trashed = false "
            f"and mimeType = '{_MIME_FOLDER}' "
            f"and name contains '{safe}'"
        )
        for raw in self._iter_files(query=query, page_size=50):
            fid = raw.get('id')
            if not fid:
                continue
            return {
                'id': fid,
                'name': raw.get('name') or '',
                'mimeType': _MIME_FOLDER,
                'is_folder': True,
            }
        return None

    def _iter_files(
        self, *, query: str, page_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Итератор по ``files.list`` с автоматической пагинацией."""
        from googleapiclient.errors import HttpError

        page_token: str | None = None
        while True:
            try:
                resp = self.service.files().list(
                    q=query,
                    pageSize=page_size,
                    pageToken=page_token,
                    fields=_LIST_FIELDS,
                    # supportsAllDrives / includeItemsFromAllDrives не ставим:
                    # наши папки живут в My Drive владельца, не в shared drive.
                    orderBy='name',
                ).execute()
            except HttpError as err:
                logger.error(
                    '[gdrive_api] files.list failed (q=%r): %s',
                    query, err,
                )
                return
            for item in resp.get('files', []) or []:
                yield item
            page_token = resp.get('nextPageToken')
            if not page_token:
                return

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def download_file(self, file_id: str) -> bytes | None:
        """Скачивает файл через ``files.get?alt=media``.

        В отличие от HTML-скачивания через ``uc?export=download`` — не надо
        разбираться с HTML-ом подтверждения для «больших» файлов и
        куками: Drive API отдаёт сразу raw bytes.

        Возвращает ``None`` при ошибке (чтобы вызвавший мог залогировать и
        продолжить со следующим файлом).
        """
        if not file_id:
            return None

        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaIoBaseDownload

        try:
            request = self.service.files().get_media(fileId=file_id)
        except HttpError as err:
            logger.warning(
                '[gdrive_api] get_media request build failed (id=%s): %s',
                file_id, err,
            )
            return None

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request, chunksize=5 * 1024 * 1024)
        try:
            done = False
            while not done:
                _status, done = downloader.next_chunk(num_retries=2)
        except HttpError as err:
            logger.warning(
                '[gdrive_api] download failed (id=%s): %s', file_id, err,
            )
            return None
        except Exception as exc:  # pragma: no cover — сеть/io
            logger.warning(
                '[gdrive_api] download unexpected error (id=%s): %s',
                file_id, exc,
            )
            return None

        data = buf.getvalue()
        if not data:
            logger.warning('[gdrive_api] empty payload for file_id=%s', file_id)
            return None
        return data


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def is_drive_api_configured() -> bool:
    """True, если в settings задан ``GOOGLE_DRIVE_API_KEY`` и установлены
    зависимости ``google-api-python-client``.

    Используется в ``google_drive_sync`` как быстрый feature-flag: если
    API не настроен — откатываемся на HTML-парсинг.
    """
    if not getattr(settings, 'GOOGLE_DRIVE_API_KEY', '').strip():
        return False
    try:
        import googleapiclient  # noqa: F401
    except ImportError:
        return False
    return True


_SINGLETON: GoogleDriveApiClient | None = None


def get_drive_api_client() -> GoogleDriveApiClient | None:
    """Возвращает shared-клиент или None, если API не настроен.

    Кэшируется в модульную переменную — один экземпляр на процесс.
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    if not is_drive_api_configured():
        return None
    try:
        _SINGLETON = GoogleDriveApiClient()
    except DriveApiNotConfigured:
        return None
    return _SINGLETON
