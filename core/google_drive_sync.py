"""
Модуль для автоматической синхронизации фотографий контейнеров с Google Drive

Структура папок на Google Drive:
  - AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ)/
    - Месяц (например "Январь 2026")/
      - НОМЕР_КОНТЕЙНЕРА/
        - фото1.jpg
        - фото2.jpg

  - KONTO VIDUS (В КОНТЕЙНЕРЕ)/
    - Месяц/
      - НОМЕР_КОНТЕЙНЕРА/
        - фото1.jpg

Логика синхронизации:
1. Сканируем главные папки Google Drive
2. Находим папки с названиями контейнеров
3. Сравниваем номер папки с номерами контейнеров в БД
4. Загружаем новые фото (которых ещё нет)
5. Создаём миниатюры автоматически
"""
import logging
import re

import requests
from django.core.files.base import ContentFile

from core.services.gdrive_client import get_drive_api_client

logger = logging.getLogger(__name__)


# Конфигурация папок Google Drive
# Эти ID берутся из URL папки: https://drive.google.com/drive/folders/ID_ПАПКИ
GOOGLE_DRIVE_FOLDERS = {
    'unloaded': '1711SSTZ3_YgUcZfNrgNzhscbmlHXlsKb',  # AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ)
    'in_container': '11poTWYYG3uKTuGTYDWS2m8uA52mlzP6f',  # KONTO VIDUS (В КОНТЕЙНЕРЕ)
}


class GoogleDriveSync:
    """Класс для работы с Google Drive"""

    @staticmethod
    def extract_folder_id(url):
        """Извлекает ID папки из URL Google Drive"""
        if not url:
            return None

        patterns = [
            r'/folders/([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
            r'^([a-zA-Z0-9_-]{20,})$',  # Просто ID без URL
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    @staticmethod
    def _parse_embedded_folder(content):
        """Парсит HTML страницу embeddedfolderview и возвращает список файлов/папок."""
        files = []
        seen_ids = set()

        # Паттерн 1: ссылки на файлы с названиями (flip-entry-title)
        file_pattern = r'href="https://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)/view[^"]*"[^>]*>.*?<div class="flip-entry-title">([^<]+)</div>'
        for match in re.finditer(file_pattern, content, re.DOTALL):
            file_id = match.group(1)
            if file_id in seen_ids:
                continue
            seen_ids.add(file_id)
            filename = match.group(2).strip()
            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
                files.append({
                    'id': file_id,
                    'name': filename,
                    'mimeType': 'image/jpeg',
                    'is_folder': False
                })

        # Паттерн 2: подпапки
        folder_pattern = r'href="https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)[^"]*"[^>]*>.*?<div class="flip-entry-title">([^<]+)</div>'
        for match in re.finditer(folder_pattern, content, re.DOTALL):
            fid = match.group(1)
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            files.append({
                'id': fid,
                'name': match.group(2).strip(),
                'mimeType': 'application/vnd.google-apps.folder',
                'is_folder': True
            })

        # Паттерн 3 (fallback): простой поиск file ID + название
        if not files:
            file_ids = re.findall(r'href="https://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', content)
            filenames = re.findall(r'<div class="flip-entry-title">([^<]+)</div>', content)
            for i, file_id in enumerate(file_ids):
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)
                if i < len(filenames):
                    filename = filenames[i].strip()
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
                        files.append({
                            'id': file_id,
                            'name': filename,
                            'mimeType': 'image/jpeg',
                            'is_folder': False
                        })

        return files

    @staticmethod
    def _parse_drive_folder_page(content):
        """
        Парсит обычную страницу Google Drive папки.
        Данные о файлах встроены в JavaScript на странице.
        """
        files = []
        seen_ids = set()

        # Google Drive встраивает данные о файлах в JS.
        # Ищем ID файлов и имена из различных паттернов в JS-данных.

        # Паттерн: массивы вида ["FILE_ID","FILENAME",...] в JS
        js_file_pattern = r'\["([a-zA-Z0-9_-]{25,})","([^"]+\.(?:jpe?g|png|gif|bmp|webp))"'
        for match in re.finditer(js_file_pattern, content, re.IGNORECASE):
            file_id = match.group(1)
            filename = match.group(2)
            if file_id not in seen_ids:
                seen_ids.add(file_id)
                files.append({
                    'id': file_id,
                    'name': filename,
                    'mimeType': 'image/jpeg',
                    'is_folder': False
                })

        # Паттерн для папок в JS-данных: ID папки + имя + application/vnd.google-apps.folder
        js_folder_pattern = r'\["([a-zA-Z0-9_-]{25,})","([^"]+)","application/vnd\.google-apps\.folder"'
        for match in re.finditer(js_folder_pattern, content):
            fid = match.group(1)
            if fid not in seen_ids:
                seen_ids.add(fid)
                files.append({
                    'id': fid,
                    'name': match.group(2),
                    'mimeType': 'application/vnd.google-apps.folder',
                    'is_folder': True
                })

        return files

    @staticmethod
    def get_folder_files_web(folder_id):
        """
        Получает список файлов и подпапок в папке Google Drive.

        Порядок попыток:
        0. **Google Drive API v3** (если задан ``GOOGLE_DRIVE_API_KEY``) —
           единственный способ получить *полный* список в больших папках.
           HTML-методы Drive обрезают выдачу на ~50–120 элементов, из-за
           чего крупные папки (100+ фото) синхронизировались частично.
        1. ``embeddedfolderview`` (list mode) — HTML fallback, если API
           недоступен или вернул ошибку.
        2. Обычная страница ``/drive/folders/<id>`` — второй HTML fallback
           для кейсов, когда embeddedfolderview пуст.

        Returns:
            list: Список словарей ``{id, name, mimeType, is_folder}``.
        """
        if not folder_id:
            return []

        # --- Drive API (предпочтительный путь, без обрезки) --------------
        api_client = get_drive_api_client()
        if api_client is not None:
            try:
                api_items = api_client.list_children(folder_id)
                # list_children уже кладёт is_folder; но download_folder_photos
                # дальше фильтрует по расширению имени — поэтому оставляем
                # как есть.
                if api_items:
                    images_count = sum(1 for f in api_items if not f.get('is_folder'))
                    folders_count = sum(1 for f in api_items if f.get('is_folder'))
                    logger.debug(
                        '[gdrive] API: folder %s -> %d files (%d images, %d subfolders)',
                        folder_id, len(api_items), images_count, folders_count,
                    )
                    return api_items
                # Пустая выдача от API — возможно, папка реально пустая.
                # Чтобы не зацикливаться на HTML (где мы можем распарсить
                # мусор), возвращаем пусто.
                logger.debug('[gdrive] API: folder %s is empty', folder_id)
                return []
            except Exception as exc:  # pragma: no cover — сеть/креды
                logger.warning(
                    '[gdrive] API fallback to HTML for folder %s: %s',
                    folder_id, exc,
                )

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        }

        try:
            # Метод 1: embeddedfolderview в режиме списка (показывает больше файлов)
            url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"Не удалось получить доступ к папке {folder_id}: HTTP {response.status_code}")
                return []

            files = GoogleDriveSync._parse_embedded_folder(response.text)

            # Если нашли файлы — проверяем, не обрезан ли результат
            if files:
                images_count = sum(1 for f in files if not f.get('is_folder'))
                if images_count >= 50:
                    logger.warning(
                        f"Папка {folder_id}: найдено {images_count} изображений — "
                        f"возможно, список обрезан Google Drive. Пробуем альтернативный метод."
                    )
                    # Метод 2: обычная страница Drive (может содержать все файлы в JS)
                    alt_files = GoogleDriveSync._get_files_via_drive_page(folder_id, headers)
                    if len(alt_files) > len(files):
                        logger.info(f"Альтернативный метод нашёл больше файлов: {len(alt_files)} vs {len(files)}")
                        files = alt_files

                logger.debug(f"Найдено {len(files)} элементов в папке {folder_id}")
                return files

            # Метод 2: если embeddedfolderview ничего не дал
            logger.debug(f"embeddedfolderview пуст для {folder_id}, пробуем обычную страницу Drive")
            alt_files = GoogleDriveSync._get_files_via_drive_page(folder_id, headers)
            if alt_files:
                logger.info(f"Альтернативный метод нашёл {len(alt_files)} элементов для {folder_id}")
                return alt_files

            logger.debug(f"Найдено 0 элементов в папке {folder_id}")
            return []

        except Exception as e:
            logger.error(f"Ошибка веб-метода для папки {folder_id}: {e}", exc_info=True)
            return []

    @staticmethod
    def _get_files_via_drive_page(folder_id, headers):
        """Получает файлы через обычную страницу Google Drive папки."""
        try:
            url = f"https://drive.google.com/drive/folders/{folder_id}"
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return GoogleDriveSync._parse_drive_folder_page(response.text)
        except Exception as e:
            logger.debug(f"Альтернативный метод не сработал для {folder_id}: {e}")
        return []

    @staticmethod
    def _is_image_content(content):
        """Проверяет, является ли содержимое файла изображением по magic bytes."""
        if not content or len(content) < 100:
            return False
        is_jpeg = content[:2] == b'\xff\xd8'
        is_png = content[:4] == b'\x89PNG'
        is_webp = len(content) > 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP'
        is_gif = content[:3] == b'GIF'
        is_bmp = content[:2] == b'BM'
        return is_jpeg or is_png or is_webp or is_gif or is_bmp

    @staticmethod
    def download_file(file_id, max_retries=2):
        """
        Скачивает файл с Google Drive с retry-логикой.

        Порядок попыток:
        0. **Drive API** (``files.get?alt=media``) — надёжно, без HTML-
           страниц подтверждения; работает, если задан
           ``GOOGLE_DRIVE_API_KEY``.
        1. Старый публичный URL ``uc?export=download`` — HTML fallback
           (оставлен на случай, если API отвалится или не настроен).

        Returns:
            bytes or None: Содержимое файла
        """
        if not file_id:
            return None

        # --- Drive API (если настроен) -----------------------------------
        api_client = get_drive_api_client()
        if api_client is not None:
            try:
                content = api_client.download_file(file_id)
                if content and GoogleDriveSync._is_image_content(content):
                    return content
                if content:
                    logger.warning(
                        '[gdrive] API returned non-image payload for %s '
                        '(size=%d, header=%s) — falling back to HTML',
                        file_id, len(content),
                        content[:4].hex() if content else 'empty',
                    )
                # если API вернул None — пробуем HTML
            except Exception as exc:  # pragma: no cover — сеть/креды
                logger.warning(
                    '[gdrive] API download failed for %s, falling back to '
                    'HTML: %s', file_id, exc,
                )

        import time

        for attempt in range(max_retries + 1):
            try:
                download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

                session = requests.Session()
                response = session.get(download_url, timeout=60)

                if response.status_code != 200:
                    logger.warning(f"Не удалось скачать файл {file_id}: HTTP {response.status_code}")
                    if attempt < max_retries:
                        time.sleep(2 * (attempt + 1))
                        continue
                    return None

                content = response.content

                # Если Google вернул HTML-страницу подтверждения скачивания
                if content[:5] == b'<!DOC' or content[:5] == b'<html' or b'download_warning' in content[:5000]:
                    # Ищем токен подтверждения в cookies или в HTML
                    confirm_token = None
                    for key, value in response.cookies.items():
                        if 'download_warning' in key:
                            confirm_token = value
                            break

                    if not confirm_token:
                        # Ищем токен в HTML (новый формат Google Drive)
                        import re as _re
                        token_match = _re.search(r'confirm=([a-zA-Z0-9_-]+)', response.text)
                        if token_match:
                            confirm_token = token_match.group(1)

                    if confirm_token:
                        confirm_url = f"{download_url}&confirm={confirm_token}"
                        response = session.get(confirm_url, timeout=60)
                        content = response.content
                    else:
                        # Пробуем прямой URL без подтверждения
                        alt_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
                        response = session.get(alt_url, timeout=60)
                        content = response.content

                if GoogleDriveSync._is_image_content(content):
                    return content

                logger.warning(
                    f"Файл {file_id} не является изображением "
                    f"(size={len(content)}, header={content[:4].hex() if content else 'empty'})"
                )
                return None

            except requests.exceptions.Timeout:
                logger.warning(f"Таймаут при скачивании {file_id} (попытка {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    time.sleep(3 * (attempt + 1))
                    continue
                return None
            except Exception as e:
                logger.error(f"Ошибка при скачивании файла {file_id}: {e}")
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

        return None

    @staticmethod
    def download_folder_photos(folder_url, container, photo_type='UNLOADING'):
        """
        Скачивает все фотографии из папки Google Drive для конкретного контейнера.

        Args:
            folder_url: URL или ID папки с фотографиями
            container: объект Container
            photo_type: тип фотографий ('IN_CONTAINER' или 'UNLOADING')
                - IN_CONTAINER: фото внутри контейнера ДО разгрузки
                - UNLOADING: фото ПОСЛЕ разгрузки

        Returns:
            int: количество загруженных фотографий
        """
        from .models_website import ContainerPhoto

        try:
            type_label = "В контейнере" if photo_type == 'IN_CONTAINER' else "Разгрузка"
            logger.info(f"[SYNC] {container.number} - {type_label}")

            # Извлекаем ID папки из URL
            folder_id = GoogleDriveSync.extract_folder_id(folder_url)

            if not folder_id:
                logger.error(f"Cannot extract folder ID from: {folder_url}")
                return 0

            # Получаем список файлов
            files = GoogleDriveSync.get_folder_files_web(folder_id)

            # Фильтруем только изображения
            images = [f for f in files if not f.get('is_folder', False)]

            if not images:
                logger.info(f"   No images found")
                return 0

            logger.info(f"   Found {len(images)} images")

            # Получаем список уже загруженных фото (по описанию)
            existing_descriptions = set(
                ContainerPhoto.objects.filter(container=container)
                .values_list('description', flat=True)
            )

            photos_added = 0

            for file_info in images:
                filename = file_info['name']
                file_id = file_info['id']
                description = f"Google Drive: {filename}"

                # Проверяем, не добавляли ли уже это фото
                if description in existing_descriptions:
                    continue

                try:
                    # Скачиваем файл
                    file_content = GoogleDriveSync.download_file(file_id)

                    if not file_content:
                        logger.warning(f"   Failed to download {filename}")
                        continue

                    # Сжимаем ДО записи на диск, чтобы оригинал вообще не
                    # попадал в storage. Иначе мы бы сначала писали 2 МБ
                    # оригинала, потом ContainerPhoto.save() через
                    # maybe_compress_image_field создавал бы рядом сжатую
                    # копию с суффиксом — а оригинал оставался orphan.
                    from .services.photo_optimize import compress_image_bytes
                    compressed = compress_image_bytes(file_content)
                    if compressed is not None:
                        file_content = compressed

                    # Создаем запись фотографии с правильным типом
                    photo = ContainerPhoto(
                        container=container,
                        photo_type=photo_type,  # IN_CONTAINER или UNLOADING
                        description=description,
                        is_public=True
                    )

                    # Сохраняем файл
                    photo.photo.save(filename, ContentFile(file_content), save=False)
                    photo.save()  # Автоматически создаст миниатюру

                    photos_added += 1

                except Exception as e:
                    logger.error(f"   Error processing {filename}: {e}")
                    continue

            if photos_added > 0:
                logger.info(f"   [OK] Added {photos_added} photos ({type_label})")
            return photos_added

        except Exception as e:
            logger.error(f"Error downloading folder: {e}", exc_info=True)
            return 0

    @staticmethod
    def find_container_folder(container_number, root_folder_id, verbose=False):
        """
        Ищет папку с номером контейнера в структуре Google Drive.
        Структура: ROOT / Месяц / НОМЕР_КОНТЕЙНЕРА (или папка содержащая номер в названии)

        Папки могут называться по-разному:
        - "ECMU5566195"
        - "ECMU5566195 Toyota Camry"
        - "15.01 ECMU5566195"
        - "ECMU5566195 - 2 авто"

        Args:
            container_number: Номер контейнера для поиска
            root_folder_id: ID корневой папки (unloaded или in_container)
            verbose: Выводить подробную информацию о поиске

        Returns:
            str or None: ID найденной папки или None
        """
        try:
            # Нормализуем номер контейнера (убираем пробелы, верхний регистр)
            search_number = container_number.strip().upper().replace(' ', '')

            # Получаем список месячных папок
            month_folders = GoogleDriveSync.get_folder_files_web(root_folder_id)
            month_folders = [f for f in month_folders if f.get('is_folder', False)]

            if verbose:
                logger.info(f"   Searching in {len(month_folders)} month folders...")

            for month_folder in month_folders:
                # Получаем папки контейнеров в этом месяце
                container_folders = GoogleDriveSync.get_folder_files_web(month_folder['id'])
                container_folders = [f for f in container_folders if f.get('is_folder', False)]

                if verbose and container_folders:
                    logger.info(f"   Month '{month_folder['name']}': {len(container_folders)} container folders")

                for container_folder in container_folders:
                    folder_name = container_folder['name'].strip().upper()
                    folder_name_no_spaces = folder_name.replace(' ', '')

                    # Ищем по ВХОЖДЕНИЮ номера контейнера в название папки
                    # Это найдёт папки типа "ECMU5566195 Toyota" или "15.01 ECMU5566195"
                    if search_number in folder_name_no_spaces:
                        logger.info(f"   [FOUND] '{container_folder['name']}' -> {container_number}")
                        return container_folder['id']

            logger.debug(f"[NOT FOUND] No folder for container {container_number}")
            return None

        except Exception as e:
            logger.error(f"Error searching folder for {container_number}: {e}")
            return None

    @staticmethod
    def sync_container_by_number(container_number, verbose=False):
        """
        Синхронизирует фотографии для контейнера по его номеру.
        Автоматически ищет папку на Google Drive в обеих корневых папках:
        - AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ) -> тип UNLOADING
        - KONTO VIDUS (В КОНТЕЙНЕРЕ) -> тип IN_CONTAINER

        После успешной синхронизации сохраняет ссылку на найденную папку
        в поле google_drive_folder_url контейнера.

        Args:
            container_number: Номер контейнера
            verbose: Выводить подробную информацию

        Returns:
            int: Количество добавленных фотографий
        """
        from .models import Container

        # Соответствие папок и типов фото
        FOLDER_PHOTO_TYPES = {
            'unloaded': 'UNLOADING',       # AUTO IŠ KONTO -> Фото после разгрузки
            'in_container': 'IN_CONTAINER'  # KONTO VIDUS -> Фото внутри контейнера
        }

        try:
            # Находим контейнер в БД
            container = Container.objects.filter(number__iexact=container_number).first()

            if not container:
                logger.warning(f"[{container_number}] Not found in database")
                return 0

            total_added = 0

            logger.info(f"[{container_number}] Searching for photos...")

            # ВСЕГДА ищем в обеих корневых папках (ВЫГРУЖЕННЫЕ + В КОНТЕЙНЕРЕ)
            for folder_type, folder_id in GOOGLE_DRIVE_FOLDERS.items():
                photo_type = FOLDER_PHOTO_TYPES.get(folder_type, 'GENERAL')
                type_label = "Выгруженные" if folder_type == 'unloaded' else "В контейнере"

                # Для папки "unloaded" используем прямую ссылку если есть
                if folder_type == 'unloaded' and container.google_drive_folder_url:
                    logger.info(f"   [{type_label}] Используем прямую ссылку из карточки")
                    added = GoogleDriveSync.download_folder_photos(
                        container.google_drive_folder_url,
                        container,
                        photo_type=photo_type
                    )
                    total_added += added
                    continue

                if verbose:
                    logger.info(f"   [{type_label}] Поиск папки на Google Drive...")

                found_folder_id = GoogleDriveSync.find_container_folder(
                    container_number,
                    folder_id,
                    verbose=verbose
                )

                if found_folder_id:
                    added = GoogleDriveSync.download_folder_photos(
                        found_folder_id,
                        container,
                        photo_type=photo_type
                    )
                    total_added += added

                    # Сохраняем ссылку на папку ВЫГРУЖЕННЫХ если ещё не сохранена
                    if folder_type == 'unloaded' and not container.google_drive_folder_url:
                        container.google_drive_folder_url = f"https://drive.google.com/drive/folders/{found_folder_id}"
                        container.save(update_fields=['google_drive_folder_url'])
                        logger.info(f"[{container_number}] Saved Google Drive folder URL")

                elif verbose:
                    logger.info(f"   [{type_label}] Папка не найдена")

            if total_added == 0:
                logger.info(f"[{container_number}] No new photos found on Google Drive")

            return total_added

        except Exception as e:
            logger.error(f"[{container_number}] Sync error: {e}", exc_info=True)
            return 0

    @staticmethod
    def sync_all_containers(limit=None):
        """
        Автоматически синхронизирует фотографии для всех контейнеров.
        Сканирует структуру Google Drive и сопоставляет с контейнерами в БД.

        Args:
            limit: Максимальное количество контейнеров для обработки (для тестов)

        Returns:
            dict: Статистика синхронизации
        """
        from .models import Container

        stats = {
            'containers_processed': 0,
            'photos_added': 0,
            'containers_not_found': [],
            'errors': []
        }

        try:
            logger.info("=" * 70)
            logger.info("🔄 НАЧАЛО АВТОМАТИЧЕСКОЙ СИНХРОНИЗАЦИИ ФОТОГРАФИЙ")
            logger.info("=" * 70)

            processed_containers = set()

            # Обрабатываем обе корневые папки
            for folder_type, folder_id in GOOGLE_DRIVE_FOLDERS.items():
                logger.info(f"\n--- Папка: {folder_type} ---")

                # Получаем месячные папки
                month_folders = GoogleDriveSync.get_folder_files_web(folder_id)
                month_folders = [f for f in month_folders if f.get('is_folder', False)]

                logger.info(f"Найдено {len(month_folders)} месячных папок")

                for month_folder in month_folders:
                    logger.info(f"\n📁 Месяц: {month_folder['name']}")

                    # Получаем папки контейнеров
                    container_folders = GoogleDriveSync.get_folder_files_web(month_folder['id'])
                    container_folders = [f for f in container_folders if f.get('is_folder', False)]

                    for container_folder in container_folders:
                        container_number = container_folder['name'].strip()

                        # Проверяем лимит
                        if limit and stats['containers_processed'] >= limit:
                            logger.info(f"Достигнут лимит в {limit} контейнеров")
                            break

                        # Пропускаем если уже обработали этот контейнер
                        if container_number.upper() in processed_containers:
                            continue

                        processed_containers.add(container_number.upper())

                        # Ищем контейнер в БД
                        container = Container.objects.filter(number__iexact=container_number).first()

                        if not container:
                            logger.warning(f"⚠ Контейнер {container_number} не найден в БД")
                            stats['containers_not_found'].append(container_number)
                            continue

                        try:
                            # Синхронизируем фотографии
                            added = GoogleDriveSync.download_folder_photos(
                                container_folder['id'],
                                container
                            )

                            stats['containers_processed'] += 1
                            stats['photos_added'] += added

                            if added > 0:
                                logger.info(f"✅ {container_number}: добавлено {added} фото")

                        except Exception as e:
                            error_msg = f"Ошибка при обработке {container_number}: {e}"
                            logger.error(error_msg)
                            stats['errors'].append(error_msg)

                    if limit and stats['containers_processed'] >= limit:
                        break

                if limit and stats['containers_processed'] >= limit:
                    break

            logger.info("\n" + "=" * 70)
            logger.info("✅ СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА")
            logger.info(f"   Контейнеров обработано: {stats['containers_processed']}")
            logger.info(f"   Фотографий добавлено: {stats['photos_added']}")
            logger.info(f"   Контейнеров не найдено в БД: {len(stats['containers_not_found'])}")
            logger.info(f"   Ошибок: {len(stats['errors'])}")
            logger.info("=" * 70)

        except Exception as e:
            logger.error(f"Критическая ошибка синхронизации: {e}", exc_info=True)
            stats['errors'].append(str(e))

        return stats

    @staticmethod
    def sync_unloaded_containers_after_delay(hours=12):
        """
        Синхронизирует фотографии для разгруженных контейнеров
        только спустя заданную задержку после статуса UNLOADED.

        Логика: если контейнер в статусе UNLOADED и
        unloaded_status_at <= now - hours, и фото еще нет — проверяем Google Drive.
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        from .models import Container

        stats = {
            'containers_checked': 0,
            'containers_with_new_photos': 0,
            'photos_added': 0,
            'errors': []
        }

        try:
            threshold = timezone.now() - timedelta(hours=hours)

            containers_no_photos = (
                Container.objects.filter(
                    status='UNLOADED',
                    unloaded_status_at__isnull=False,
                    unloaded_status_at__lte=threshold
                )
                .annotate(photos_count=Count('photos'))
                .filter(photos_count=0)
                .order_by('unloaded_status_at')
            )

            count = containers_no_photos.count()
            if count == 0:
                logger.info("✅ Нет разгруженных контейнеров без фото для проверки")
                return stats

            logger.info(f"🔍 Найдено {count} разгруженных контейнеров без фото (задержка {hours}ч)")

            for container in containers_no_photos:
                try:
                    if container.google_drive_folder_url:
                        added = GoogleDriveSync.download_folder_photos(
                            container.google_drive_folder_url,
                            container
                        )
                    else:
                        added = GoogleDriveSync.sync_container_by_number(container.number)

                    stats['containers_checked'] += 1
                    stats['photos_added'] += added

                    if added > 0:
                        stats['containers_with_new_photos'] += 1
                        logger.info(f"   🎉 {container.number}: найдено {added} фото!")
                    else:
                        logger.debug(f"   ⏳ {container.number}: фото пока нет на Google Drive")

                except Exception as e:
                    stats['errors'].append(f"{container.number}: {e}")

            logger.info(f"✅ Проверено: {stats['containers_checked']}, "
                       f"с новыми фото: {stats['containers_with_new_photos']}, "
                       f"всего фото: {stats['photos_added']}")

        except Exception as e:
            logger.error(f"Ошибка sync_unloaded_containers_after_delay: {e}", exc_info=True)
            stats['errors'].append(str(e))

        return stats

    @staticmethod
    def sync_recent_containers(days=30, prioritize_no_photos=True):
        """
        Синхронизирует фотографии только для недавних контейнеров.
        Более эффективный вариант для регулярного запуска по крону.

        ВАЖНО: Склад загружает фотки с задержкой 1-2 суток после разгрузки!
        Поэтому эту команду нужно запускать регулярно (каждые 2-4 часа).

        Args:
            days: Количество дней назад для поиска (по дате разгрузки)
            prioritize_no_photos: Приоритет контейнерам без фото

        Returns:
            dict: Статистика синхронизации
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        from .models import Container

        stats = {
            'containers_processed': 0,
            'containers_with_new_photos': 0,
            'photos_added': 0,
            'errors': []
        }

        try:
            # Получаем контейнеры за последние N дней
            start_date = timezone.now().date() - timedelta(days=days)

            # Базовый queryset - разгруженные или в порту
            recent_containers = Container.objects.filter(
                unload_date__gte=start_date,
                status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']
            ).annotate(
                photos_count=Count('photos')
            )

            if prioritize_no_photos:
                # Сначала контейнеры БЕЗ фото, потом остальные
                recent_containers = recent_containers.order_by('photos_count', '-unload_date')
            else:
                recent_containers = recent_containers.order_by('-unload_date')

            total_count = recent_containers.count()
            logger.info(f"🔄 Синхронизация фотографий для {total_count} контейнеров (за последние {days} дней)")

            # Считаем контейнеры без фото
            no_photos_count = recent_containers.filter(photos_count=0).count()
            if no_photos_count > 0:
                logger.info(f"   📷 Из них БЕЗ фото: {no_photos_count} - будут проверены в первую очередь")

            for container in recent_containers:
                try:
                    # Если есть ссылка на Google Drive - используем её
                    if container.google_drive_folder_url:
                        added = GoogleDriveSync.download_folder_photos(
                            container.google_drive_folder_url,
                            container
                        )
                    else:
                        # Ищем папку автоматически по номеру контейнера
                        added = GoogleDriveSync.sync_container_by_number(container.number)

                    stats['containers_processed'] += 1
                    stats['photos_added'] += added

                    if added > 0:
                        stats['containers_with_new_photos'] += 1
                        logger.info(f"   ✅ {container.number}: +{added} фото")

                except Exception as e:
                    stats['errors'].append(f"{container.number}: {e}")
                    logger.error(f"Ошибка синхронизации {container.number}: {e}")

            logger.info(f"✅ Итого: обработано {stats['containers_processed']}, "
                       f"с новыми фото: {stats['containers_with_new_photos']}, "
                       f"добавлено фото: {stats['photos_added']}")

        except Exception as e:
            logger.error(f"Ошибка sync_recent_containers: {e}", exc_info=True)
            stats['errors'].append(str(e))

        return stats

    @staticmethod
    def sync_containers_without_photos(days=14):
        """
        Специальная функция: синхронизирует ТОЛЬКО контейнеры без фотографий.

        Идеально для частого запуска (каждые 2-4 часа) - быстрая проверка
        только тех контейнеров, у которых ещё нет фото.

        Args:
            days: Количество дней назад для поиска

        Returns:
            dict: Статистика синхронизации
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        from .models import Container

        stats = {
            'containers_checked': 0,
            'containers_with_new_photos': 0,
            'photos_added': 0,
            'errors': []
        }

        try:
            start_date = timezone.now().date() - timedelta(days=days)

            # Только контейнеры БЕЗ фото
            containers_no_photos = Container.objects.filter(
                unload_date__gte=start_date,
                status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']
            ).annotate(
                photos_count=Count('photos')
            ).filter(
                photos_count=0
            ).order_by('-unload_date')

            count = containers_no_photos.count()

            if count == 0:
                logger.info("✅ Все недавние контейнеры уже имеют фотографии")
                return stats

            logger.info(f"🔍 Найдено {count} контейнеров БЕЗ фото за последние {days} дней")

            for container in containers_no_photos:
                try:
                    if container.google_drive_folder_url:
                        added = GoogleDriveSync.download_folder_photos(
                            container.google_drive_folder_url,
                            container
                        )
                    else:
                        added = GoogleDriveSync.sync_container_by_number(container.number)

                    stats['containers_checked'] += 1
                    stats['photos_added'] += added

                    if added > 0:
                        stats['containers_with_new_photos'] += 1
                        logger.info(f"   🎉 {container.number}: найдено {added} фото!")
                    else:
                        logger.debug(f"   ⏳ {container.number}: фото пока нет на Google Drive")

                except Exception as e:
                    stats['errors'].append(f"{container.number}: {e}")

            logger.info(f"✅ Проверено: {stats['containers_checked']}, "
                       f"с новыми фото: {stats['containers_with_new_photos']}, "
                       f"всего фото: {stats['photos_added']}")

        except Exception as e:
            logger.error(f"Ошибка sync_containers_without_photos: {e}", exc_info=True)
            stats['errors'].append(str(e))

        return stats
