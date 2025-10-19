"""
Модели для клиентского сайта Caromoto Lithuania
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Car, Client, Container
import os
import zipfile
from django.core.files.base import ContentFile
from PIL import Image
import io


class ClientUser(models.Model):
    """
    Пользователь клиентского портала, связанный с клиентом из CRM
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='portal_users', verbose_name="Клиент")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    is_verified = models.BooleanField(default=False, verbose_name="Верифицирован")
    language = models.CharField(max_length=10, default='ru', choices=[
        ('ru', 'Русский'),
        ('en', 'English'),
        ('lt', 'Lietuvių'),
    ], verbose_name="Язык")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата регистрации")
    last_login = models.DateTimeField(null=True, blank=True, verbose_name="Последний вход")
    
    def __str__(self):
        return f"{self.user.username} ({self.client.name})"
    
    class Meta:
        verbose_name = "Клиентский пользователь"
        verbose_name_plural = "Клиентские пользователи"


class CarPhoto(models.Model):
    """
    Фотографии автомобилей
    """
    PHOTO_TYPES = [
        ('LOADING', 'Погрузка'),
        ('UNLOADING', 'Разгрузка'),
        ('DAMAGE', 'Повреждения'),
        ('GENERAL', 'Общее'),
        ('DOCUMENTS', 'Документы'),
    ]
    
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name='photos', verbose_name="Автомобиль")
    photo = models.ImageField(upload_to='car_photos/%Y/%m/%d/', verbose_name="Фотография")
    photo_type = models.CharField(max_length=20, choices=PHOTO_TYPES, default='GENERAL', verbose_name="Тип фото")
    description = models.TextField(blank=True, verbose_name="Описание")
    
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    
    is_public = models.BooleanField(default=True, verbose_name="Доступно клиенту")
    
    def __str__(self):
        return f"{self.car.vin} - {self.get_photo_type_display()}"
    
    @property
    def filename(self):
        return os.path.basename(self.photo.name)
    
    class Meta:
        verbose_name = "Фотография автомобиля"
        verbose_name_plural = "Фотографии автомобилей"
        ordering = ['-uploaded_at']


class ContainerPhoto(models.Model):
    """
    Фотографии контейнеров
    """
    PHOTO_TYPES = [
        ('LOADING', 'Погрузка'),
        ('UNLOADING', 'Разгрузка'),
        ('SEAL', 'Пломба'),
        ('GENERAL', 'Общее'),
    ]
    
    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name='photos', verbose_name="Контейнер")
    photo = models.ImageField(upload_to='container_photos/%Y/%m/%d/', verbose_name="Фотография")
    thumbnail = models.ImageField(upload_to='container_photos/thumbnails/%Y/%m/%d/', blank=True, null=True, verbose_name="Миниатюра")
    photo_type = models.CharField(max_length=20, choices=PHOTO_TYPES, default='GENERAL', verbose_name="Тип фото")
    description = models.TextField(blank=True, verbose_name="Описание")
    
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    
    is_public = models.BooleanField(default=True, verbose_name="Доступно клиенту")
    
    def __str__(self):
        return f"{self.container.number} - {self.get_photo_type_display()}"
    
    @property
    def filename(self):
        return os.path.basename(self.photo.name)
    
    def create_thumbnail(self):
        """Создает миниатюру изображения для быстрой загрузки"""
        import logging
        logger = logging.getLogger(__name__)
        
        if not self.photo:
            logger.warning(f"ContainerPhoto {self.id}: нет оригинального фото для создания миниатюры")
            return False
        
        try:
            # Проверяем существование файла
            if not os.path.exists(self.photo.path):
                logger.error(f"ContainerPhoto {self.id}: файл не найден: {self.photo.path}")
                return False
            
            # Открываем оригинальное изображение
            img = Image.open(self.photo)
            
            # Конвертируем в RGB если нужно
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            
            # Создаем миниатюру (максимум 400x400px)
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)
            
            # Сохраняем в буфер
            thumb_io = io.BytesIO()
            img.save(thumb_io, format='JPEG', quality=85, optimize=True)
            thumb_io.seek(0)
            
            # Создаем имя файла для миниатюры
            thumb_name = f"thumb_{os.path.basename(self.photo.name)}"
            
            # Сохраняем миниатюру
            self.thumbnail.save(thumb_name, ContentFile(thumb_io.read()), save=False)
            logger.info(f"ContainerPhoto {self.id}: миниатюра успешно создана: {thumb_name}")
            return True
        except Exception as e:
            logger.error(f"ContainerPhoto {self.id}: ошибка создания миниатюры: {e}", exc_info=True)
            return False
    
    def save(self, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        # Определяем, новый ли это объект
        is_new = self.pk is None
        
        # Сохраняем объект первый раз
        super().save(*args, **kwargs)
        
        # Создаем миниатюру только если её нет и есть оригинальное фото
        if self.photo and not self.thumbnail:
            logger.info(f"ContainerPhoto {self.id}: попытка создания миниатюры (is_new={is_new})")
            if self.create_thumbnail():
                # Сохраняем только поле thumbnail, чтобы избежать рекурсии
                super().save(update_fields=['thumbnail'])
            else:
                logger.warning(f"ContainerPhoto {self.id}: не удалось создать миниатюру")
    
    class Meta:
        verbose_name = "Фотография контейнера"
        verbose_name_plural = "Фотографии контейнеров"
        ordering = ['photo']  # Сортировка по имени файла для сохранения последовательности


class ContainerPhotoArchive(models.Model):
    """
    Архивы фотографий контейнеров для массовой загрузки
    """
    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name='photo_archives', verbose_name="Контейнер")
    archive_file = models.FileField(upload_to='container_archives/%Y/%m/%d/', verbose_name="Архивный файл")
    description = models.TextField(blank=True, verbose_name="Описание")
    
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    
    is_processed = models.BooleanField(default=False, verbose_name="Обработан")
    photos_count = models.PositiveIntegerField(default=0, verbose_name="Количество фотографий")
    
    def __str__(self):
        return f"Архив {self.container.number} - {self.uploaded_at.strftime('%Y-%m-%d')}"
    
    def extract_photos(self):
        """Извлекает фотографии из архива и создает ContainerPhoto объекты"""
        import logging
        logger = logging.getLogger(__name__)
        
        if not self.archive_file:
            logger.warning(f"ContainerPhotoArchive {self.id}: нет архивного файла")
            return []
        
        photos = []
        errors = []
        
        try:
            logger.info(f"ContainerPhotoArchive {self.id}: начало извлечения из {self.archive_file.path}")
            
            with zipfile.ZipFile(self.archive_file.path, 'r') as zip_file:
                # Фильтруем файлы изображений
                image_files = [f for f in zip_file.filelist 
                              if f.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp'))
                              and not f.filename.startswith('__MACOSX')  # Игнорируем служебные файлы Mac
                              and not os.path.basename(f.filename).startswith('.')]  # Игнорируем скрытые файлы
                
                logger.info(f"ContainerPhotoArchive {self.id}: найдено {len(image_files)} изображений")
                
                for file_info in image_files:
                    try:
                        # Извлекаем файл
                        file_data = zip_file.read(file_info.filename)
                        
                        # Получаем только имя файла без пути
                        filename = os.path.basename(file_info.filename)
                        
                        # Создаем ContainerPhoto объект БЕЗ автоматического сохранения фото
                        photo = ContainerPhoto(
                            container=self.container,
                            description=f"Из архива: {filename}",
                            uploaded_by=self.uploaded_by
                        )
                        
                        # Сохраняем изображение (save=False чтобы не вызывать model.save() дважды)
                        photo.photo.save(
                            filename,
                            ContentFile(file_data),
                            save=False
                        )
                        
                        # Теперь сохраняем модель - это вызовет создание миниатюры
                        photo.save()
                        
                        photos.append(photo)
                        logger.debug(f"ContainerPhoto: успешно обработано {filename}")
                        
                    except Exception as e:
                        error_msg = f"Ошибка при обработке {file_info.filename}: {e}"
                        logger.error(error_msg, exc_info=True)
                        errors.append(error_msg)
                        continue
                        
        except Exception as e:
            error_msg = f"Ошибка при открытии архива: {e}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            
        self.is_processed = True
        self.photos_count = len(photos)
        self.save()
        
        logger.info(f"ContainerPhotoArchive {self.id}: обработка завершена. Успешно: {len(photos)}, ошибок: {len(errors)}")
        
        if errors:
            logger.warning(f"ContainerPhotoArchive {self.id}: ошибки при обработке:\n" + "\n".join(errors))
        
        return photos
    
    class Meta:
        verbose_name = "Архив фотографий контейнера"
        verbose_name_plural = "Архивы фотографий контейнеров"
        ordering = ['-uploaded_at']


class AIChat(models.Model):
    """
    История чата с ИИ-помощником
    """
    session_id = models.CharField(max_length=100, verbose_name="ID сессии")
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Пользователь")
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Клиент")
    
    message = models.TextField(verbose_name="Сообщение")
    response = models.TextField(verbose_name="Ответ")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    
    # Метаданные для аналитики
    processing_time = models.FloatField(null=True, blank=True, verbose_name="Время обработки (сек)")
    was_helpful = models.BooleanField(null=True, blank=True, verbose_name="Был ли полезен ответ")
    
    class Meta:
        verbose_name = "Чат с ИИ"
        verbose_name_plural = "Чаты с ИИ"
        ordering = ['-created_at']
    
    def __str__(self):
        user_str = self.user.username if self.user else "Анонимный"
        return f"{user_str} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class NewsPost(models.Model):
    """
    Новости компании
    """
    title = models.CharField(max_length=200, verbose_name="Заголовок")
    slug = models.SlugField(unique=True, verbose_name="URL")
    content = models.TextField(verbose_name="Содержание")
    excerpt = models.TextField(blank=True, verbose_name="Краткое описание")
    
    image = models.ImageField(upload_to='news/%Y/%m/%d/', blank=True, null=True, verbose_name="Изображение")
    
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Автор")
    
    published = models.BooleanField(default=False, verbose_name="Опубликовано")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата публикации")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    views = models.PositiveIntegerField(default=0, verbose_name="Просмотры")
    
    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ['-published_at', '-created_at']
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if self.published and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class ContactMessage(models.Model):
    """
    Сообщения из формы обратной связи
    """
    name = models.CharField(max_length=100, verbose_name="Имя")
    email = models.EmailField(verbose_name="Email")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    subject = models.CharField(max_length=200, verbose_name="Тема")
    message = models.TextField(verbose_name="Сообщение")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    replied = models.BooleanField(default=False, verbose_name="Ответили")
    
    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} - {self.subject}"


class TrackingRequest(models.Model):
    """
    Запросы на отслеживание груза
    """
    tracking_number = models.CharField(max_length=100, verbose_name="Номер для отслеживания (VIN/Контейнер)")
    email = models.EmailField(blank=True, verbose_name="Email")
    
    car = models.ForeignKey(Car, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Автомобиль")
    container = models.ForeignKey(Container, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Контейнер")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата запроса")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP адрес")
    
    class Meta:
        verbose_name = "Запрос отслеживания"
        verbose_name_plural = "Запросы отслеживания"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.tracking_number} - {self.created_at.strftime('%Y-%m-%d')}"

