"""
Модуль для синхронизации фотографий контейнеров с Google Drive
"""
import requests
import re
import logging
import os
from django.core.files.base import ContentFile
from .models import Container
from .models_website import ContainerPhoto

logger = logging.getLogger(__name__)


# Конфигурация папок Google Drive
GOOGLE_DRIVE_FOLDERS = {
    'unloaded': '1711SSTZ3_YgUcZfNrgNzhscbmlHXlsKb',  # AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ)
    'in_container': '11poTWYYG3uKTuGTYDWS2m8uA52mlzP6f',  # KONTO VIDUS (В КОНТЕЙНЕРЕ)
}


class GoogleDriveSync:
    """Класс для работы с Google Drive"""
    
    @staticmethod
    def extract_folder_id(url):
        """Извлекает ID папки из URL Google Drive"""
        patterns = [
            r'/folders/([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    @staticmethod
    def get_folder_files_api(folder_id):
        """
        Получает список файлов из публичной папки через Google Drive API v3
        Без авторизации - только для публичных папок
        """
        try:
            # API endpoint для публичных файлов
            api_url = f"https://www.googleapis.com/drive/v3/files"
            
            params = {
                'q': f"'{folder_id}' in parents and trashed=false",
                'fields': 'files(id,name,mimeType)',
                'key': 'AIzaSyDummyForPublic'  # Для публичных папок можно без ключа
            }
            
            # Пробуем без API ключа - для публичных папок
            response = requests.get(api_url, params={'q': f"'{folder_id}' in parents"}, timeout=30)
            
            if response.status_code == 401:
                # Нужен API ключ, используем альтернативный метод
                return GoogleDriveSync.get_folder_files_web(folder_id)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('files', [])
            
            logger.warning(f"API вернул код {response.status_code}, используем веб-метод")
            return GoogleDriveSync.get_folder_files_web(folder_id)
            
        except Exception as e:
            logger.error(f"Ошибка Google Drive API: {e}")
            return GoogleDriveSync.get_folder_files_web(folder_id)
    
    @staticmethod
    def get_folder_files_web(folder_id):
        """
        Получает список файлов через веб-интерфейс Google Drive
        Работает для публичных папок
        """
        try:
            # Прямая ссылка на просмотр папки (embeddedfolderview)
            url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Не удалось получить доступ к папке {folder_id}: HTTP {response.status_code}")
                return []
            
            content = response.text
            
            # Парсим HTML для извлечения ID файлов и их имен
            # Ищем ссылки на файлы
            file_id_pattern = r'href="https://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)/view'
            file_ids = re.findall(file_id_pattern, content)
            
            # Ищем имена файлов
            filename_pattern = r'<div class="flip-entry-title">([^<]+)</div>'
            filenames = re.findall(filename_pattern, content)
            
            logger.info(f"Найдено ID: {len(file_ids)}, имен: {len(filenames)}")
            
            # Объединяем ID и имена (они должны идти в том же порядке)
            files = []
            for i, file_id in enumerate(file_ids):
                if i < len(filenames):
                    filename = filenames[i]
                    # Фильтруем только изображения
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                        files.append({
                            'id': file_id,
                            'name': filename,
                            'mimeType': 'image/jpeg'
                        })
            
            logger.info(f"Найдено {len(files)} изображений в папке {folder_id}")
            return files
            
        except Exception as e:
            logger.error(f"Ошибка веб-метода: {e}", exc_info=True)
            return []
    
    @staticmethod
    def download_file(file_id):
        """Скачивает файл с Google Drive"""
        try:
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            session = requests.Session()
            response = session.get(download_url, stream=True, timeout=60)
            
            # Обработка больших файлов с подтверждением
            if 'download_warning' in response.text or 'virus' in response.text:
                for key, value in response.cookies.items():
                    if key.startswith('download_warning'):
                        confirm_url = download_url + f"&confirm={value}"
                        response = session.get(confirm_url, stream=True, timeout=60)
                        break
            
            if response.status_code == 200:
                return response.content
            
            logger.warning(f"Не удалось скачать файл {file_id}: HTTP {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при скачивании файла {file_id}: {e}")
            return None
    
    @staticmethod
    def download_folder_photos(folder_url, container):
        """
        Скачивает все фотографии из папки Google Drive для контейнера
        
        Args:
            folder_url: прямая ссылка на папку с фотографиями
            container: объект Container
        
        Returns:
            количество загруженных фотографий
        """
        try:
            logger.info(f"Скачивание фотографий из Google Drive для контейнера {container.number}")
            logger.info(f"URL: {folder_url}")
            
            # Извлекаем ID папки из URL
            folder_id = GoogleDriveSync.extract_folder_id(folder_url)
            
            if not folder_id:
                logger.error(f"Не удалось извлечь ID папки из URL: {folder_url}")
                return 0
            
            logger.info(f"ID папки: {folder_id}")
            
            # Получаем список файлов
            files = GoogleDriveSync.get_folder_files_web(folder_id)
            
            if not files:
                logger.warning(f"Нет файлов в папке {folder_id}")
                return 0
            
            logger.info(f"Найдено {len(files)} изображений")
            
            photos_added = 0
            
            for file_info in files:
                filename = file_info['name']
                file_id = file_info['id']
                
                # Проверяем, не добавляли ли уже это фото
                exists = ContainerPhoto.objects.filter(
                    container=container,
                    description__contains=filename
                ).exists()
                
                if exists:
                    logger.debug(f"Фото {filename} уже существует, пропускаем")
                    continue
                
                try:
                    logger.info(f"Загружаем {filename}...")
                    
                    # Скачиваем файл
                    file_content = GoogleDriveSync.download_file(file_id)
                    
                    if not file_content:
                        logger.warning(f"Не удалось скачать {filename}")
                        continue
                    
                    # Создаем запись фотографии
                    photo = ContainerPhoto(
                        container=container,
                        photo_type='GENERAL',
                        description=f"Google Drive: {filename}",
                        is_public=True
                    )
                    
                    # Сохраняем файл
                    photo.photo.save(filename, ContentFile(file_content), save=False)
                    photo.save()  # Автоматически создаст миниатюру
                    
                    photos_added += 1
                    logger.info(f"✓ Добавлено фото: {filename}")
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке фото {filename}: {e}")
                    continue
            
            logger.info(f"Всего добавлено {photos_added} фотографий для контейнера {container.number}")
            return photos_added
                
        except Exception as e:
            logger.error(f"Ошибка при скачивании папки: {e}", exc_info=True)
            return 0
    
    @staticmethod
    def download_file(file_id):
        """Скачивает файл с Google Drive"""
        try:
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            session = requests.Session()
            response = session.get(download_url, stream=True, timeout=60)
            
            # Обработка больших файлов с подтверждением
            if 'download_warning' in response.text or 'virus' in response.text:
                # Ищем токен подтверждения
                for key, value in response.cookies.items():
                    if key.startswith('download_warning'):
                        confirm_url = download_url + f"&confirm={value}"
                        response = session.get(confirm_url, stream=True, timeout=60)
                        break
            
            if response.status_code == 200:
                return response.content
            
            logger.warning(f"Не удалось скачать файл {file_id}: HTTP {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при скачивании файла {file_id}: {e}")
            return None
    
    @staticmethod
    def sync_container_folder(container_number, folder_id, photo_type='GENERAL'):
        """
        Синхронизирует фотографии для конкретного контейнера
        
        Args:
            container_number: номер контейнера
            folder_id: ID папки в Google Drive
            photo_type: тип фотографии (UNLOADING, GENERAL, и т.д.)
        
        Returns:
            количество добавленных фотографий
        """
        try:
            # Находим контейнер
            container = Container.objects.filter(number=container_number).first()
            
            if not container:
                logger.warning(f"Контейнер {container_number} не найден в базе данных")
                return 0
            
            # Получаем файлы из папки
            files = GoogleDriveSync.get_public_folder_files(folder_id)
            
            # Фильтруем только изображения
            images = [f for f in files if not f['is_folder']]
            
            logger.info(f"Найдено {len(images)} изображений для контейнера {container_number}")
            
            added_count = 0
            
            for img in images:
                try:
                    # Проверяем, не добавляли ли уже это фото
                    exists = ContainerPhoto.objects.filter(
                        container=container,
                        description__contains=img['name']
                    ).exists()
                    
                    if exists:
                        logger.debug(f"Фото {img['name']} уже существует, пропускаем")
                        continue
                    
                    # Скачиваем файл
                    logger.info(f"Загрузка {img['name']}...")
                    content = GoogleDriveSync.download_file(img['id'])
                    
                    if not content:
                        continue
                    
                    # Создаем запись фотографии
                    photo = ContainerPhoto(
                        container=container,
                        photo_type=photo_type,
                        description=f"Google Drive: {img['name']}",
                        is_public=True
                    )
                    
                    # Сохраняем файл
                    photo.photo.save(img['name'], ContentFile(content), save=False)
                    photo.save()  # Автоматически создаст миниатюру
                    
                    added_count += 1
                    logger.info(f"✓ Добавлено фото: {img['name']}")
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке фото {img['name']}: {e}")
                    continue
            
            return added_count
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации контейнера {container_number}: {e}", exc_info=True)
            return 0
    
    @staticmethod
    def sync_all_containers():
        """
        Сканирует все папки и синхронизирует фотографии для всех контейнеров
        
        Returns:
            dict с статистикой синхронизации
        """
        stats = {
            'unloaded_photos': 0,
            'in_container_photos': 0,
            'errors': []
        }
        
        try:
            # 1. Синхронизируем выгруженные контейнеры
            logger.info("=" * 70)
            logger.info("Синхронизация: AUTO IŠ KONTO (ВЫГРУЖЕННЫЕ)")
            logger.info("=" * 70)
            
            unloaded_folder_id = GOOGLE_DRIVE_FOLDERS['unloaded']
            month_folders = GoogleDriveSync.get_public_folder_files(unloaded_folder_id)
            
            for month_folder in month_folders:
                if not month_folder['is_folder']:
                    continue
                
                logger.info(f"\n--- Месяц: {month_folder['name']} ---")
                
                # Получаем папки контейнеров в этом месяце
                container_folders = GoogleDriveSync.get_public_folder_files(month_folder['id'])
                
                for container_folder in container_folders:
                    if not container_folder['is_folder']:
                        continue
                    
                    container_number = container_folder['name']
                    logger.info(f"Контейнер: {container_number}")
                    
                    count = GoogleDriveSync.sync_container_folder(
                        container_number,
                        container_folder['id'],
                        photo_type='UNLOADING'
                    )
                    
                    stats['unloaded_photos'] += count
            
            # 2. Синхронизируем контейнеры в пути
            logger.info("\n" + "=" * 70)
            logger.info("Синхронизация: KONTO VIDUS (В КОНТЕЙНЕРЕ)")
            logger.info("=" * 70)
            
            in_container_folder_id = GOOGLE_DRIVE_FOLDERS['in_container']
            month_folders = GoogleDriveSync.get_public_folder_files(in_container_folder_id)
            
            for month_folder in month_folders:
                if not month_folder['is_folder']:
                    continue
                
                logger.info(f"\n--- Месяц: {month_folder['name']} ---")
                
                container_folders = GoogleDriveSync.get_public_folder_files(month_folder['id'])
                
                for container_folder in container_folders:
                    if not container_folder['is_folder']:
                        continue
                    
                    container_number = container_folder['name']
                    logger.info(f"Контейнер: {container_number}")
                    
                    count = GoogleDriveSync.sync_container_folder(
                        container_number,
                        container_folder['id'],
                        photo_type='GENERAL'
                    )
                    
                    stats['in_container_photos'] += count
            
            logger.info("\n" + "=" * 70)
            logger.info(f"Синхронизация завершена!")
            logger.info(f"Выгруженные: {stats['unloaded_photos']} фото")
            logger.info(f"В контейнере: {stats['in_container_photos']} фото")
            logger.info(f"Всего: {stats['unloaded_photos'] + stats['in_container_photos']} фото")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"Критическая ошибка синхронизации: {e}", exc_info=True)
            stats['errors'].append(str(e))
        
        return stats

