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
        if not self.photo:
            return
        
        try:
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
        except Exception as e:
            print(f"Ошибка создания миниатюры: {e}")
    
    def save(self, *args, **kwargs):
        # Если это новое фото или фото изменилось
        if not self.pk or self.photo:
            # Сначала сохраняем, чтобы файл был записан
            super().save(*args, **kwargs)
            # Затем создаем миниатюру если её нет
            if not self.thumbnail:
                self.create_thumbnail()
                super().save(update_fields=['thumbnail'])
        else:
            super().save(*args, **kwargs)
    
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
        if not self.archive_file:
            return []
        
        photos = []
        try:
            with zipfile.ZipFile(self.archive_file.path, 'r') as zip_file:
                for file_info in zip_file.filelist:
                    if file_info.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                        # Извлекаем файл
                        file_data = zip_file.read(file_info.filename)
                        
                        # Создаем ContainerPhoto объект
                        photo = ContainerPhoto.objects.create(
                            container=self.container,
                            description=f"Из архива: {file_info.filename}",
                            uploaded_by=self.uploaded_by
                        )
                        
                        # Сохраняем изображение
                        photo.photo.save(
                            file_info.filename,
                            ContentFile(file_data),
                            save=True
                        )
                        photos.append(photo)
                        
        except Exception as e:
            print(f"Ошибка при извлечении архива: {e}")
            
        self.is_processed = True
        self.photos_count = len(photos)
        self.save()
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

