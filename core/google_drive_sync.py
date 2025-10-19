"""
Модуль для синхронизации фотографий контейнеров с Google Drive
"""
import requests
import re
import logging
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
    def get_public_folder_files(folder_id):
        """
        Получает список файлов из публичной папки Google Drive
        Использует прямую ссылку для скачивания
        """
        try:
            url = f"https://drive.google.com/drive/folders/{folder_id}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Ошибка доступа к папке {folder_id}: HTTP {response.status_code}")
                return []
            
            # Извлекаем ID файлов и имена из HTML
            # Google Drive использует определенную структуру данных
            content = response.text
            
            # Паттерн для поиска файлов
            file_pattern = r'"([\w-]{28,33})","([^"]{2,})"'
            matches = re.findall(file_pattern, content)
            
            files = []
            seen = set()
            
            for file_id, name in matches:
                # Фильтруем служебные имена
                if (name and 
                    not name.startswith('_') and 
                    not name in ['null', 'undefined'] and
                    file_id not in seen and
                    len(name) > 2):
                    
                    files.append({
                        'id': file_id,
                        'name': name,
                        'is_folder': not any(ext in name.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif'])
                    })
                    seen.add(file_id)
            
            logger.info(f"Найдено {len(files)} элементов в папке {folder_id}")
            return files
            
        except Exception as e:
            logger.error(f"Ошибка при получении содержимого папки {folder_id}: {e}", exc_info=True)
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

