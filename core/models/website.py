"""
Модели для клиентского сайта Caromoto Lithuania
"""

import io
import os
import zipfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from PIL import Image

from .cars import Car
from .clients import Client
from .containers import Container


class ClientUser(models.Model):
    """
    Пользователь клиентского портала, связанный с клиентом из CRM
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="portal_users", verbose_name="Клиент")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    is_verified = models.BooleanField(default=False, verbose_name="Верифицирован")
    language = models.CharField(
        max_length=10,
        default="ru",
        choices=[
            ("ru", "Русский"),
            ("en", "English"),
            ("lt", "Lietuvių"),
        ],
        verbose_name="Язык",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата регистрации")
    last_login = models.DateTimeField(null=True, blank=True, verbose_name="Последний вход")

    def __str__(self):
        return f"{self.user.username} ({self.client.name})"

    class Meta:
        verbose_name = "Доступ клиента в портал"
        verbose_name_plural = "Доступы клиентов в портал"


class CarPhoto(models.Model):
    """
    Фотографии автомобилей
    """

    PHOTO_TYPES = [
        ("LOADING", "Погрузка"),
        ("UNLOADING", "Разгрузка"),
        ("DAMAGE", "Повреждения"),
        ("GENERAL", "Общее"),
        ("DOCUMENTS", "Документы"),
    ]

    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="photos", verbose_name="Автомобиль")
    photo = models.ImageField(upload_to="car_photos/%Y/%m/%d/", verbose_name="Фотография")
    photo_type = models.CharField(max_length=20, choices=PHOTO_TYPES, default="GENERAL", verbose_name="Тип фото")
    description = models.TextField(blank=True, verbose_name="Описание")

    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    is_public = models.BooleanField(default=True, verbose_name="Доступно клиенту")

    def __str__(self):
        return f"{self.car.vin} - {self.get_photo_type_display()}"

    @property
    def filename(self):
        return os.path.basename(self.photo.name)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self.photo and (update_fields is None or "photo" in update_fields):
            from ..services.photo_optimize import maybe_compress_image_field

            maybe_compress_image_field(self, "photo")
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Фотография автомобиля"
        verbose_name_plural = "Фотографии автомобилей"
        ordering = ["-uploaded_at"]
        indexes = [
            # Галерея/портал: фото авто, доступные клиенту.
            models.Index(fields=["car", "is_public"], name="carphoto_car_public_idx"),
            # ordering = ['-uploaded_at'].
            models.Index(fields=["car", "-uploaded_at"], name="carphoto_car_uploaded_idx"),
        ]


class ContainerPhoto(models.Model):
    """
    Фотографии контейнеров
    """

    PHOTO_TYPES = [
        ("LOADING", "Погрузка"),
        ("UNLOADING", "Разгрузка"),
        ("IN_CONTAINER", "В контейнере"),
        ("SEAL", "Пломба"),
        ("GENERAL", "Общее"),
    ]

    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name="photos", verbose_name="Контейнер")
    photo = models.ImageField(upload_to="container_photos/%Y/%m/%d/", verbose_name="Фотография")
    thumbnail = models.ImageField(
        upload_to="container_photos/thumbnails/%Y/%m/%d/", blank=True, null=True, verbose_name="Миниатюра"
    )
    photo_type = models.CharField(max_length=20, choices=PHOTO_TYPES, default="GENERAL", verbose_name="Тип фото")
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
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            # Создаем миниатюру (максимум 400x400px)
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)

            # Сохраняем в буфер
            thumb_io = io.BytesIO()
            img.save(thumb_io, format="JPEG", quality=85, optimize=True)
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
        update_fields = kwargs.get("update_fields")
        if self.photo and (update_fields is None or "photo" in update_fields):
            from ..services.photo_optimize import maybe_compress_image_field

            maybe_compress_image_field(self, "photo")

        super().save(*args, **kwargs)

        if self.photo and not self.thumbnail:
            from django.db import transaction

            transaction.on_commit(lambda: _create_thumbnail_async(self.pk))

    class Meta:
        verbose_name = "Фотография контейнера"
        verbose_name_plural = "Фотографии контейнеров"
        ordering = ["photo"]  # Сортировка по имени файла для сохранения последовательности
        indexes = [
            # Самая частая выборка: публичные фото конкретного контейнера
            # (галерея, tracking, портал). Раньше indexes не было вовсе —
            # только FK-индекс на container_id.
            models.Index(fields=["container", "is_public"], name="ctrphoto_container_public_idx"),
        ]


def _create_thumbnail_async(photo_pk):
    """Dispatch thumbnail creation to Celery or run inline as fallback."""
    try:
        from ..tasks import create_container_photo_thumbnail_task

        create_container_photo_thumbnail_task.delay(photo_pk)
    except Exception:
        import logging

        logger = logging.getLogger(__name__)
        try:
            photo = ContainerPhoto.objects.get(pk=photo_pk)
            if photo.create_thumbnail():
                ContainerPhoto.objects.filter(pk=photo_pk).update(thumbnail=photo.thumbnail)
        except Exception as e:
            logger.error(f"Fallback thumbnail creation failed for {photo_pk}: {e}")


class ContainerPhotoArchive(models.Model):
    """
    Архивы фотографий контейнеров для массовой загрузки
    """

    container = models.ForeignKey(
        Container, on_delete=models.CASCADE, related_name="photo_archives", verbose_name="Контейнер"
    )
    archive_file = models.FileField(upload_to="container_archives/%Y/%m/%d/", verbose_name="Архивный файл")
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

        # Лимиты против zip-bomb: архив в 25 МБ может разжиматься в
        # гигабайты. file_size — заявленный НЕсжатый размер записи.
        max_photos = 500
        max_file_bytes = 30 * 1024 * 1024  # 30 МБ на одно фото
        max_total_bytes = 2 * 1024**3  # 2 ГБ суммарно на архив

        try:
            logger.info(f"ContainerPhotoArchive {self.id}: начало извлечения из {self.archive_file.path}")

            with zipfile.ZipFile(self.archive_file.path, "r") as zip_file:
                # Фильтруем файлы изображений
                image_files = [
                    f
                    for f in zip_file.filelist
                    if f.filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp"))
                    and not f.filename.startswith("__MACOSX")  # Игнорируем служебные файлы Mac
                    and not os.path.basename(f.filename).startswith(".")
                ]  # Игнорируем скрытые файлы

                logger.info(f"ContainerPhotoArchive {self.id}: найдено {len(image_files)} изображений")

                if len(image_files) > max_photos:
                    errors.append(
                        f"В архиве {len(image_files)} изображений — обрабатываются только первые {max_photos}"
                    )
                    image_files = image_files[:max_photos]

                total_declared = sum(f.file_size for f in image_files)
                if total_declared > max_total_bytes:
                    error_msg = (
                        f"Суммарный несжатый размер изображений {total_declared // 1024**2} МБ "
                        f"превышает лимит {max_total_bytes // 1024**2} МБ — архив отклонён"
                    )
                    logger.error(f"ContainerPhotoArchive {self.id}: {error_msg}")
                    errors.append(error_msg)
                    image_files = []

                for file_info in image_files:
                    try:
                        if file_info.file_size > max_file_bytes:
                            errors.append(
                                f"{file_info.filename}: {file_info.file_size // 1024**2} МБ "
                                f"превышает лимит {max_file_bytes // 1024**2} МБ на файл — пропущен"
                            )
                            continue

                        # Извлекаем файл
                        file_data = zip_file.read(file_info.filename)

                        # Получаем только имя файла без пути
                        filename = os.path.basename(file_info.filename)

                        # Создаем ContainerPhoto объект БЕЗ автоматического сохранения фото
                        photo = ContainerPhoto(
                            container=self.container, description=f"Из архива: {filename}", uploaded_by=self.uploaded_by
                        )

                        # Сохраняем изображение (save=False чтобы не вызывать model.save() дважды)
                        photo.photo.save(filename, ContentFile(file_data), save=False)

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

        logger.info(
            f"ContainerPhotoArchive {self.id}: обработка завершена. Успешно: {len(photos)}, ошибок: {len(errors)}"
        )

        if errors:
            logger.warning(f"ContainerPhotoArchive {self.id}: ошибки при обработке:\n" + "\n".join(errors))

        return photos

    class Meta:
        verbose_name = "Архив фотографий контейнера"
        verbose_name_plural = "Архивы фотографий контейнеров"
        ordering = ["-uploaded_at"]


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
    context_snapshot = models.JSONField(null=True, blank=True, verbose_name="Контекст")

    class Meta:
        verbose_name = "Чат с ИИ"
        verbose_name_plural = "Чаты с ИИ"
        ordering = ["-created_at"]

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

    image = models.ImageField(upload_to="news/%Y/%m/%d/", blank=True, null=True, verbose_name="Изображение")

    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="Автор")

    published = models.BooleanField(default=False, verbose_name="Опубликовано")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата публикации")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    views = models.PositiveIntegerField(default=0, verbose_name="Просмотры")

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            # Публичный список новостей фильтрует published=True
            # и сортирует по published_at — composite индекс покрывает оба.
            models.Index(fields=["-published_at"], name="news_published_at_idx", condition=models.Q(published=True)),
        ]

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
        verbose_name = "Сообщение с сайта"
        verbose_name_plural = "Сообщения с сайта"
        ordering = ["-created_at"]

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
        ordering = ["-created_at"]
        indexes = [
            # Списки в админке + аналитика «откуда трек»: по дате создания
            # и IP (rate-limit-style выборки). Без индекса полный seqscan.
            models.Index(fields=["-created_at"], name="tracking_req_created_idx"),
        ]

    def __str__(self):
        return f"{self.tracking_number} - {self.created_at.strftime('%Y-%m-%d')}"


class NotificationLog(models.Model):
    """
    Лог отправленных email-уведомлений клиентам
    """

    NOTIFICATION_TYPES = [
        ("PLANNED", "Планируемая разгрузка"),
        ("UNLOADED", "Разгрузка выполнена"),
        ("CAR_UNLOADED", "Разгрузка ТС (без контейнера)"),
        ("REQUEST_MESSAGE", "Сообщение по заявке на автовоз"),
        ("REQUEST_DOCS", "Запрос документов по заявке"),
    ]

    CHANNEL_CHOICES = [
        ("EMAIL", "Email"),
        ("TELEGRAM", "Telegram"),
    ]

    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="Контейнер",
        null=True,
        blank=True,
    )
    car = models.ForeignKey(
        "Car",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="ТС",
        null=True,
        blank=True,
        help_text="Для уведомлений о разгрузке ТС без контейнера",
    )
    transport_request = models.ForeignKey(
        "TransportRequest",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="Заявка на автовоз",
        null=True,
        blank=True,
        help_text="Для уведомлений по переписке в заявке на автовоз",
    )
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="notifications", verbose_name="Клиент")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, verbose_name="Тип уведомления")
    channel = models.CharField(
        max_length=10, choices=CHANNEL_CHOICES, default="EMAIL", db_index=True, verbose_name="Канал"
    )

    email_to = models.CharField(
        max_length=255, blank=True, verbose_name="Получатель", help_text="Email-адрес или Telegram chat_id получателя"
    )
    subject = models.CharField(max_length=255, verbose_name="Тема письма")

    cars_info = models.TextField(
        blank=True, verbose_name="Информация об авто", help_text="JSON со списком VIN и марок авто в уведомлении"
    )

    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата отправки")
    success = models.BooleanField(default=True, verbose_name="Успешно отправлено")
    error_message = models.TextField(blank=True, verbose_name="Сообщение об ошибке")

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Отправил",
        help_text="Кто инициировал отправку",
    )

    class Meta:
        verbose_name = "Лог уведомлений"
        verbose_name_plural = "Логи уведомлений"
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["container", "notification_type", "channel"]),
            models.Index(fields=["car", "notification_type", "channel"]),
            models.Index(fields=["client", "sent_at"]),
        ]

    def __str__(self):
        status = "✓" if self.success else "✗"
        if self.container:
            return f"{status} [{self.channel}] {self.get_notification_type_display()} - {self.container.number} → {self.email_to}"
        elif self.car:
            return (
                f"{status} [{self.channel}] {self.get_notification_type_display()} - {self.car.vin} → {self.email_to}"
            )
        return f"{status} [{self.channel}] {self.get_notification_type_display()} → {self.email_to}"


# ---------------------------------------------------------------------------
# Кабинет клиента: документы, декларации, заявки на автовоз
# ---------------------------------------------------------------------------

CLIENT_DOCUMENT_ALLOWED_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]


class ClientDocument(models.Model):
    """Документ, загруженный клиентом через кабинет (для оформления декларации и пр.)."""

    DOCUMENT_TYPES = [
        ("PURCHASE_INVOICE", "Инвойс покупки (Bill of Sale)"),
        ("TITLE", "Тайтл"),
        ("BILL_OF_LADING", "Коносамент (Bill of Lading)"),
        ("EXPORT_DECLARATION", "Экспортная декларация"),
        ("POWER_OF_ATTORNEY", "Доверенность"),
        ("OTHER", "Другой документ"),
    ]

    STATUS_CHOICES = [
        ("NEW", "Новый"),
        ("ACCEPTED", "Принят"),
        ("REJECTED", "Отклонён"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="portal_documents", verbose_name="Клиент")
    car = models.ForeignKey(
        Car,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="client_documents",
        verbose_name="Автомобиль",
    )
    document_type = models.CharField(
        max_length=30, choices=DOCUMENT_TYPES, default="OTHER", verbose_name="Тип документа"
    )
    file = models.FileField(
        upload_to="client_documents/%Y/%m/",
        validators=[FileExtensionValidator(CLIENT_DOCUMENT_ALLOWED_EXTENSIONS)],
        verbose_name="Файл",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий клиента")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="NEW", verbose_name="Статус")
    staff_comment = models.TextField(blank=True, verbose_name="Комментарий сотрудника")

    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    class Meta:
        verbose_name = "Документ клиента"
        verbose_name_plural = "Документы клиентов"
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["client", "-uploaded_at"], name="clientdoc_client_idx"),
        ]

    def __str__(self):
        target = self.car.vin if self.car else self.client.name
        return f"{self.get_document_type_display()} — {target}"

    @property
    def filename(self):
        return os.path.basename(self.file.name)


class DeclarationRequest(models.Model):
    """Заявка клиента на оформление декларации: данные + печатная форма."""

    DECLARATION_TYPES = [
        ("TRANSIT", "Транзит (T1)"),
        ("EXPORT", "Экспорт"),
        ("IMPORT", "Импорт"),
        ("REEXPORT", "Реэкспорт"),
    ]

    STATUS_CHOICES = [
        ("NEW", "Новая"),
        ("IN_PROGRESS", "В работе"),
        ("READY", "Готова"),
        ("REJECTED", "Отклонена"),
    ]

    CURRENCY_CHOICES = [
        ("USD", "USD"),
        ("EUR", "EUR"),
    ]

    number = models.CharField(max_length=50, unique=True, blank=True, verbose_name="Номер заявки")
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="declaration_requests", verbose_name="Клиент"
    )
    car = models.ForeignKey(
        Car, on_delete=models.CASCADE, related_name="declaration_requests", verbose_name="Автомобиль"
    )
    declaration_type = models.CharField(
        max_length=10, choices=DECLARATION_TYPES, default="TRANSIT", verbose_name="Тип декларации"
    )
    transport_request = models.ForeignKey(
        "TransportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="declaration_requests",
        verbose_name="Заявка на автовоз",
        help_text="Заявка, из которой создана декларация (когда оформляем сами).",
    )

    # Получатель / покупатель
    buyer_name = models.CharField(max_length=255, verbose_name="Получатель (имя / компания)")
    buyer_code = models.CharField(
        max_length=50, blank=True, verbose_name="Код получателя", help_text="Регистрационный или личный код"
    )
    buyer_country = models.CharField(max_length=100, verbose_name="Страна получателя")
    buyer_address = models.CharField(max_length=255, blank=True, verbose_name="Адрес получателя")

    # Направление
    destination_country = models.CharField(max_length=100, verbose_name="Страна назначения")
    destination_city = models.CharField(max_length=100, blank=True, verbose_name="Город назначения")

    # Стоимость по инвойсу
    invoice_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Стоимость по инвойсу"
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD", verbose_name="Валюта")

    notes = models.TextField(blank=True, verbose_name="Примечания клиента")

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="NEW", verbose_name="Статус")
    staff_comment = models.TextField(blank=True, verbose_name="Комментарий сотрудника")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Создал")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Заявка на декларацию"
        verbose_name_plural = "Заявки на декларации"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["client", "-created_at"], name="declreq_client_idx"),
            models.Index(fields=["status"], name="declreq_status_idx"),
        ]

    def __str__(self):
        return f"{self.number} — {self.car.vin} ({self.get_declaration_type_display()})"

    def save(self, *args, **kwargs):
        if not self.number:
            from django.db import transaction as db_transaction

            from .series import next_document_number

            date_str = timezone.now().strftime("%Y%m%d")
            with db_transaction.atomic():
                self.number = next_document_number(DeclarationRequest, f"DECL-{date_str}", pad=3)
        super().save(*args, **kwargs)


# Типы документов пакета для оформления автовоза (Беларусь).
# Порядок = порядок кнопок в кабинете клиента.
TRANSPORT_DOCUMENT_TYPES = [
    ("TITLE", "Тайтл"),
    ("PASSPORT", "Паспорт"),
    ("INVOICE", "Инвойс"),
    ("SIGNATURE", "Подпись"),
    ("PAYMENT_ORDER", "Платёжка"),
    ("LETTER_USA", "Письмо USA"),
    ("OBLIGATION", "Обязательство клиента"),
    ("CONTRACT", "Договор на перевозку"),
    ("OTHER", "Остальное"),
]

# Типы, которые нельзя сгенерировать — только реальные файлы.
TRANSPORT_UPLOAD_ONLY_TYPES = {"TITLE", "PASSPORT", "SIGNATURE", "OTHER"}


# Типы деклараций (таможенных процедур), которые может потребовать заявка на
# автовоз. Совпадают с ``DeclarationRequest.DECLARATION_TYPES`` — заявка на
# автовоз всегда подразумевает декларацию, и когда мы начнём оформлять их
# сами, тип перенесётся в ``DeclarationRequest`` без конвертации.
TRANSPORT_DECLARATION_TYPES = [
    ("TRANSIT", "Транзит (T1)"),
    ("EXPORT", "Экспорт"),
    ("IMPORT", "Импорт"),
    ("REEXPORT", "Реэкспорт"),
]

# Страны назначения, куда клиенты забирают авто автовозом. Список короткий
# намеренно: требования таможни по документам заводятся на каждую страну
# отдельно (``TransportDocumentRule``), поэтому новую страну добавляем
# вместе с её набором документов.
TRANSPORT_DESTINATION_COUNTRIES = [
    ("BY", "Беларусь"),
    ("MD", "Молдова"),
    ("UA", "Украина"),
]

# Процедура, которая подставляется клиенту по умолчанию при выборе страны.
# Клиент может её изменить — это только подсказка под типовой сценарий.
DEFAULT_PROCEDURE_BY_COUNTRY = {
    "BY": "TRANSIT",
    "MD": "TRANSIT",
    "UA": "REEXPORT",
}


class TransportDocumentRule(models.Model):
    """Требования таможни к пакету документов: страна + процедура → типы.

    В разные страны таможня требует разный набор документов, поэтому список
    обязательных типов заводит сотрудник в админке, а не разработчик в коде:
    одна строка = одна пара «страна назначения + таможенная процедура».
    Если строки для пары нет или она выключена, используется встроенный
    набор по умолчанию (см. ``core.services.transport_request_check``).
    """

    country = models.CharField(max_length=2, choices=TRANSPORT_DESTINATION_COUNTRIES, verbose_name="Страна назначения")
    procedure = models.CharField(
        max_length=10, choices=TRANSPORT_DECLARATION_TYPES, verbose_name="Таможенная процедура"
    )
    required_doc_types = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Обязательные документы",
        help_text="Типы документов, без которых пакет по авто считается неполным.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Действует",
        help_text="Выключено — для этой пары берётся набор по умолчанию.",
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Примечание")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Изменил")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата изменения")

    class Meta:
        verbose_name = "Требования к документам"
        verbose_name_plural = "Требования к документам (страна + процедура)"
        ordering = ["country", "procedure"]
        constraints = [
            models.UniqueConstraint(fields=["country", "procedure"], name="uniq_docrule_country_procedure"),
        ]

    def __str__(self):
        return f"{self.get_country_display()} · {self.get_procedure_display()}"

    @property
    def required_doc_labels(self):
        labels = dict(TRANSPORT_DOCUMENT_TYPES)
        return [labels.get(code, code) for code in self.required_doc_types or []]


class TransportRequest(models.Model):
    """Заявка клиента с данными автовоза, который заберёт его автомобили.

    Жизненный цикл: Черновик → Подана → Принята → В процессе → Оформлена.
    Клиент может редактировать и удалять заявку только в статусах
    «Черновик»/«Подана» — см. ``portal_transport``. «Удаление» клиентом —
    мягкое: заявка получает статус «Отменена» и скрывается из кабинета,
    но остаётся видимой администратору как неактуальная.

    Заявка на автовоз всегда подразумевает декларацию. Клиент выбирает
    страну назначения (``destination_country``) и одну процедуру на всю
    заявку (``declaration_type``); от пары «страна + процедура» зависит
    обязательный пакет документов (``TransportDocumentRule``). Сотрудник при
    необходимости разбивает машины на отдельные декларации вручную — см.
    ``TransportDeclarationGroup``. Сейчас декларации оформляет склад по
    нашему письму-заявке (``core.services.warehouse_request_email``);
    в будущем будем оформлять сами — точка расширения
    ``DeclarationRequest.transport_request``.

    Работа со складом — ВНУТРЕННЕЕ состояние (``warehouse_state`` и
    отметки времени), клиент его не видит: в кабинете отображается только
    ``status``. Поэтому новые значения в ``STATUS_CHOICES`` не вводятся.
    """

    STATUS_CHOICES = [
        ("DRAFT", "Черновик"),
        ("SUBMITTED", "Подана"),
        ("ACCEPTED", "Принята"),
        ("IN_PROGRESS", "В процессе"),
        ("COMPLETED", "Оформлена"),
        ("CANCELLED", "Отменена"),
    ]

    WAREHOUSE_NOT_SENT = "NOT_SENT"
    WAREHOUSE_SENT = "SENT"
    WAREHOUSE_CONFIRMED = "CONFIRMED"
    WAREHOUSE_REJECTED = "REJECTED"
    WAREHOUSE_STATE_CHOICES = [
        (WAREHOUSE_NOT_SENT, "Не отправлена складу"),
        (WAREHOUSE_SENT, "Отправлена складу"),
        (WAREHOUSE_CONFIRMED, "Подтверждена складом"),
        (WAREHOUSE_REJECTED, "Отклонена складом"),
    ]

    # Статусы, в которых клиент ещё может менять и удалять заявку.
    CLIENT_EDITABLE_STATUSES = {"DRAFT", "SUBMITTED"}

    # Статусы, в которых заявка «активна» (авто занято заявкой).
    INACTIVE_STATUSES = {"COMPLETED", "CANCELLED"}

    number = models.CharField(max_length=50, unique=True, blank=True, verbose_name="Номер заявки")
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="transport_requests", verbose_name="Клиент"
    )
    cars = models.ManyToManyField(Car, related_name="transport_requests", verbose_name="Автомобили")

    # Данные автовоза
    carrier_name = models.CharField(max_length=255, blank=True, verbose_name="Перевозчик (название)")
    carrier_eori = models.CharField(max_length=50, blank=True, verbose_name="EORI код перевозчика")
    truck_number = models.CharField(max_length=20, verbose_name="Номер тягача")
    trailer_number = models.CharField(max_length=20, blank=True, verbose_name="Номер прицепа")
    driver_name = models.CharField(max_length=100, verbose_name="ФИО водителя")
    driver_phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон водителя")
    border_crossing = models.CharField(max_length=100, blank=True, verbose_name="Граница пересечения")
    planned_loading_date = models.DateField(null=True, blank=True, verbose_name="Планируемая дата загрузки")

    comment = models.TextField(blank=True, verbose_name="Комментарий клиента")

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="DRAFT", verbose_name="Статус")
    staff_comment = models.TextField(blank=True, verbose_name="Комментарий сотрудника")
    auto_transport = models.ForeignKey(
        "AutoTransport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_requests",
        verbose_name="Автовоз",
        help_text="Автовоз, созданный по этой заявке",
    )

    # ── Оформление декларации ────────────────────────────────────────────
    destination_country = models.CharField(
        max_length=2,
        choices=TRANSPORT_DESTINATION_COUNTRIES,
        blank=True,
        verbose_name="Страна назначения",
        help_text="Куда идут автомобили — от страны зависят требования таможни к пакету документов.",
    )
    declaration_type = models.CharField(
        max_length=10,
        choices=TRANSPORT_DECLARATION_TYPES,
        blank=True,
        verbose_name="Требуемая декларация",
        help_text=(
            "Тип по умолчанию: одна декларация на все авто заявки. "
            "Отдельные декларации на часть машин задаются в карточке заявки."
        ),
    )

    # ── Работа со складом (внутреннее, клиенту не показывается) ──────────
    warehouse = models.ForeignKey(
        "Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transport_requests",
        verbose_name="Склад-получатель",
        help_text="Склад, которому отправлена заявка на загрузку автовоза.",
    )
    warehouse_state = models.CharField(
        max_length=10,
        choices=WAREHOUSE_STATE_CHOICES,
        default=WAREHOUSE_NOT_SENT,
        verbose_name="Состояние по складу",
    )
    sent_to_warehouse_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлена складу")
    warehouse_confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="Подтверждена складом")

    # Ждём от клиента документы: разрешает клиенту править заявку и
    # догружать файлы даже когда заявка уже в работе.
    awaiting_client_docs = models.BooleanField(default=False, verbose_name="Ожидаем документы от клиента")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Создал")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Заявка на автовоз"
        verbose_name_plural = "Заявки на автовоз"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["client", "-created_at"], name="transreq_client_idx"),
            models.Index(fields=["status"], name="transreq_status_idx"),
            # Табы доски «У склада» / «Подтверждены складом» фильтруют по нему.
            models.Index(fields=["warehouse_state", "-created_at"], name="transreq_whstate_idx"),
        ]

    def __str__(self):
        return f"{self.number} — {self.client.name}"

    @property
    def is_client_editable(self):
        """Может ли клиент редактировать заявку.

        Обычно — до статуса «В процессе». Плюс исключение: если мы запросили
        у клиента документы (``awaiting_client_docs``), правка и догрузка
        снова разрешены, даже когда заявка уже в работе.
        """
        if self.status == "CANCELLED":
            return False
        return self.status in self.CLIENT_EDITABLE_STATUSES or self.awaiting_client_docs

    def emails_for_panel(self):
        """Переписка со складом для панели в карточке заявки.

        ``is_read_here`` — флаг из ``TransportRequestEmailLink`` именно этой
        заявки (аналог ``Container.emails_for_panel`` / ``AutoTransport``).
        """
        from django.db.models import OuterRef, Subquery

        from .email import ContainerEmail, TransportRequestEmailLink

        return (
            ContainerEmail.objects.filter(transport_requests__id=self.pk)
            .annotate(
                is_read_here=Subquery(
                    TransportRequestEmailLink.objects.filter(
                        email=OuterRef("pk"),
                        request_id=self.pk,
                    ).values("is_read")[:1]
                )
            )
            .distinct()
            .order_by("-received_at")
        )

    def default_warehouse(self):
        """Склад по машинам заявки: единственный — вернём его, иначе ``None``.

        Если в заявке машины с разных складов, выбрать получателя письма
        автоматически нельзя — сотрудник указывает склад вручную.
        """
        ids = {car.warehouse_id for car in self.cars.all() if car.warehouse_id}
        if len(ids) != 1:
            return None
        from .warehouses import Warehouse

        return Warehouse.objects.filter(pk=ids.pop()).first()

    def declaration_types_by_car(self):
        """``{car_id: declaration_type}`` — под какой тип собирается пакет.

        Приоритет: отдельная декларация (``declaration_groups``), затем тип
        заявки. Пустая строка означает «тип не выбран» — письмо складу такие
        авто не даёт отправить, чтобы не просить декларацию наугад.
        """
        by_group = {}
        for group in self.declaration_groups.prefetch_related("cars"):
            for car in group.cars.all():
                by_group.setdefault(car.pk, group.declaration_type)
        return {car.pk: by_group.get(car.pk) or self.declaration_type for car in self.cars.all()}

    def unread_messages_for_staff(self):
        """Сколько ответов клиента мы ещё не прочитали."""
        return self.messages.filter(author_kind="CLIENT", read_by_staff_at__isnull=True).count()

    def unread_messages_for_client(self):
        """Сколько наших сообщений клиент ещё не прочитал."""
        return self.messages.filter(author_kind="STAFF", read_by_client_at__isnull=True).count()

    def pending_requested_doc_types(self):
        """Типы документов, которые мы запросили и которых до сих пор нет.

        Считаем по всем нашим сообщениям заявки: объединение запрошенных
        типов минус типы, файлы которых уже загружены (по любому авто).
        Порядок — как в ``TRANSPORT_DOCUMENT_TYPES``.
        """
        requested: set[str] = set()
        for codes in self.messages.filter(author_kind="STAFF").values_list("requested_doc_types", flat=True):
            requested.update(codes or [])
        if not requested:
            return []
        present = set(self.documents.values_list("doc_type", flat=True))
        return [code for code, _label in TRANSPORT_DOCUMENT_TYPES if code in requested - present]

    def save(self, *args, **kwargs):
        if not self.number:
            from django.db import transaction as db_transaction

            from .series import next_document_number

            date_str = timezone.now().strftime("%Y%m%d")
            with db_transaction.atomic():
                self.number = next_document_number(TransportRequest, f"TR-{date_str}", pad=3)
        super().save(*args, **kwargs)


class TransportBulkUpload(models.Model):
    """Пакет документов, присланный одним файлом, и его разбор искусственным интеллектом.

    Клиенты часто сканируют всё подряд в один PDF: паспорт, инвойс, платёжку,
    договор. Такой файл сохраняется здесь целиком, а
    ``core.tasks.process_transport_bulk_upload`` отправляет страницы в Claude
    Vision, определяет тип каждой страницы, режет исходник на отдельные
    документы пакета (``TransportRequestDocument``) и раскладывает их по
    слотам. Страницы, тип которых уверенно определить не удалось, попадают в
    «Остальное» — клиент их видит и может указать тип вручную.

    Исходный файл не удаляем: если раскладка вышла неудачной, сотрудник
    всегда может посмотреть, что именно прислал клиент.
    """

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_DONE = "DONE"
    STATUS_ERROR = "ERROR"
    STATUS_CHOICES = [
        (STATUS_PENDING, "В очереди"),
        (STATUS_PROCESSING, "Обрабатывается"),
        (STATUS_DONE, "Разобран"),
        (STATUS_ERROR, "Ошибка"),
    ]

    request = models.ForeignKey(
        TransportRequest, on_delete=models.CASCADE, related_name="bulk_uploads", verbose_name="Заявка"
    )
    car = models.ForeignKey(
        Car, on_delete=models.CASCADE, related_name="transport_bulk_uploads", verbose_name="Автомобиль"
    )
    file = models.FileField(
        upload_to="transport_docs/bulk/%Y/%m/",
        validators=[FileExtensionValidator(CLIENT_DOCUMENT_ALLOWED_EXTENSIONS)],
        verbose_name="Исходный файл",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name="Статус")
    pages_total = models.PositiveSmallIntegerField(default=0, verbose_name="Страниц в файле")
    # {"documents": [{"doc_type", "pages": [1,2], "filename"}], "unrecognized": [5, 6]}
    result = models.JSONField(default=dict, blank=True, verbose_name="Результат разбора")
    error_message = models.TextField(blank=True, verbose_name="Ошибка")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата разбора")

    class Meta:
        verbose_name = "Пакет одним файлом"
        verbose_name_plural = "Пакеты одним файлом"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} — {self.request.number}"

    @property
    def filename(self):
        return os.path.basename(self.file.name)

    @property
    def is_running(self):
        return self.status in (self.STATUS_PENDING, self.STATUS_PROCESSING)

    @property
    def sorted_labels(self):
        """Что получилось после разбора: «Паспорт, Инвойс (2 стр.)»."""
        labels = dict(TRANSPORT_DOCUMENT_TYPES)
        out = []
        for item in (self.result or {}).get("documents", []):
            label = labels.get(item.get("doc_type"), item.get("doc_type", ""))
            pages = item.get("pages") or []
            out.append(f"{label} ({len(pages)} стр.)" if len(pages) > 1 else label)
        return out


class TransportDeclarationGroup(models.Model):
    """Отдельная декларация внутри заявки: тип + набор авто под неё.

    По умолчанию на заявку оформляется одна декларация типа
    ``TransportRequest.declaration_type`` — в неё попадают все авто заявки.
    Реальность бывает сложнее: часть машин уходит одной транзитной T1,
    другая часть — второй отдельной T1, а несколько машин — каждая по своей
    экспортной. Такие расклады сотрудник собирает вручную в карточке заявки:
    каждая группа = одна декларация, авто вне групп идут одной декларацией
    типа заявки. Клиент этой разбивки не видит — он выбирает только тип.

    Когда декларации начнём оформлять сами, группа станет источником для
    ``DeclarationRequest`` (одна группа → одна декларация).
    """

    request = models.ForeignKey(
        TransportRequest, on_delete=models.CASCADE, related_name="declaration_groups", verbose_name="Заявка"
    )
    declaration_type = models.CharField(
        max_length=10, choices=TRANSPORT_DECLARATION_TYPES, verbose_name="Тип декларации"
    )
    cars = models.ManyToManyField(
        Car, blank=True, related_name="transport_declaration_groups", verbose_name="Автомобили"
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Примечание")
    position = models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Декларация заявки"
        verbose_name_plural = "Декларации заявки"
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.get_declaration_type_display()} — {self.request.number}"


class TransportDocumentPackage(models.Model):
    """Данные пакета документов на авто в заявке (оформление на Беларусь).

    Хранит введённые клиентом данные покупателя (из паспорта — адрес вводится
    вручную, т.к. рукописный адрес плохо читается машинно) и параметры
    генерации документов (номера/даты/суммы/реквизиты) в ``data``.
    """

    request = models.ForeignKey(
        TransportRequest, on_delete=models.CASCADE, related_name="doc_packages", verbose_name="Заявка"
    )
    car = models.ForeignKey(
        Car, on_delete=models.CASCADE, related_name="transport_doc_packages", verbose_name="Автомобиль"
    )
    data = models.JSONField(default=dict, blank=True, verbose_name="Данные пакета")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Пакет документов автовоза"
        verbose_name_plural = "Пакеты документов автовоза"
        constraints = [
            models.UniqueConstraint(fields=["request", "car"], name="uniq_docpackage_request_car"),
        ]

    def __str__(self):
        return f"Пакет документов — {self.request.number} / {self.car.vin}"


class TransportRequestDocument(models.Model):
    """Файл документа пакета (загруженный клиентом или сгенерированный)."""

    request = models.ForeignKey(
        TransportRequest, on_delete=models.CASCADE, related_name="documents", verbose_name="Заявка"
    )
    car = models.ForeignKey(
        Car, on_delete=models.CASCADE, related_name="transport_documents", verbose_name="Автомобиль"
    )
    doc_type = models.CharField(
        max_length=20, choices=TRANSPORT_DOCUMENT_TYPES, default="OTHER", verbose_name="Тип документа"
    )
    file = models.FileField(
        upload_to="transport_docs/%Y/%m/",
        validators=[FileExtensionValidator(CLIENT_DOCUMENT_ALLOWED_EXTENSIONS)],
        verbose_name="Файл",
    )
    is_generated = models.BooleanField(default=False, verbose_name="Сгенерирован системой")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Загрузил")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата добавления")

    class Meta:
        verbose_name = "Документ заявки на автовоз"
        verbose_name_plural = "Документы заявок на автовоз"
        ordering = ["doc_type", "-created_at"]
        indexes = [
            models.Index(fields=["request", "car"], name="transdoc_request_car_idx"),
        ]

    def __str__(self):
        return f"{self.get_doc_type_display()} — {self.request.number} / {self.car.vin}"

    @property
    def filename(self):
        return os.path.basename(self.file.name)


class TransportRequestMessage(models.Model):
    """Сообщение в переписке по заявке на автовоз (мы ↔ клиент).

    Отдельная от email-переписки со складом ветка: сотрудник пишет клиенту
    замечания («не хватает паспорта», «поправьте номер прицепа»), клиент
    отвечает и правит заявку в кабинете. Сообщения видны в карточке заявки
    и на доске заявок в админке.

    Непрочитанное считаем по двум полям-временам, а не счётчиками: временная
    метка отвечает сразу на два вопроса — «прочитано ли» и «когда», не
    требует денормализации на заявке и не рассинхронизируется при удалении
    сообщений. Своё сообщение автор помечает прочитанным сразу при создании
    (см. ``TransportRequestMessage.save``), поэтому непрочитанные для стороны
    X — это ``read_by_X_at__isnull=True``.

    ``requested_doc_types`` — коды из ``TRANSPORT_DOCUMENT_TYPES``, которые
    мы просим клиента догрузить; кабинет показывает их не текстом, а
    кнопками, открывающими существующие модалки загрузки нужного типа.
    """

    AUTHOR_STAFF = "STAFF"
    AUTHOR_CLIENT = "CLIENT"
    AUTHOR_KIND_CHOICES = [
        (AUTHOR_STAFF, "От нас клиенту"),
        (AUTHOR_CLIENT, "От клиента"),
    ]

    KIND_MESSAGE = "MESSAGE"
    KIND_DOC_REQUEST = "DOC_REQUEST"
    KIND_CHOICES = [
        (KIND_MESSAGE, "Сообщение"),
        (KIND_DOC_REQUEST, "Запрос документов"),
    ]

    request = models.ForeignKey(
        TransportRequest, on_delete=models.CASCADE, related_name="messages", verbose_name="Заявка"
    )
    author_kind = models.CharField(
        max_length=10, choices=AUTHOR_KIND_CHOICES, default=AUTHOR_STAFF, verbose_name="Автор (сторона)"
    )
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default=KIND_MESSAGE, verbose_name="Тип")
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Автор", related_name="+"
    )
    car = models.ForeignKey(
        Car,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transport_request_messages",
        verbose_name="Автомобиль",
        help_text="Если замечание относится к конкретному авто заявки.",
    )
    body = models.TextField(blank=True, verbose_name="Текст")
    requested_doc_types = models.JSONField(
        default=list, blank=True, verbose_name="Запрошенные документы", help_text="Коды типов документов пакета."
    )
    attachment = models.FileField(
        upload_to="transport_messages/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(CLIENT_DOCUMENT_ALLOWED_EXTENSIONS)],
        verbose_name="Вложение",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    read_by_staff_at = models.DateTimeField(null=True, blank=True, verbose_name="Прочитано сотрудником")
    read_by_client_at = models.DateTimeField(null=True, blank=True, verbose_name="Прочитано клиентом")

    class Meta:
        verbose_name = "Сообщение по заявке на автовоз"
        verbose_name_plural = "Сообщения по заявкам на автовоз"
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["request", "created_at"], name="trmsg_request_idx"),
            models.Index(fields=["request", "read_by_staff_at"], name="trmsg_unread_staff_idx"),
            models.Index(fields=["request", "read_by_client_at"], name="trmsg_unread_client_idx"),
        ]

    def __str__(self):
        return f"{self.get_author_kind_display()} — {self.request.number} ({self.created_at:%d.%m.%Y %H:%M})"

    @property
    def is_from_staff(self):
        return self.author_kind == self.AUTHOR_STAFF

    @property
    def requested_doc_labels(self):
        """Человеческие названия запрошенных типов документов."""
        labels = dict(TRANSPORT_DOCUMENT_TYPES)
        return [labels.get(code, code) for code in (self.requested_doc_types or [])]

    def save(self, *args, **kwargs):
        if not self.pk:
            # Автор своё сообщение уже «прочитал» — иначе бейдж непрочитанного
            # загорится у самого отправителя.
            now = timezone.now()
            if self.author_kind == self.AUTHOR_STAFF and self.read_by_staff_at is None:
                self.read_by_staff_at = now
            elif self.author_kind == self.AUTHOR_CLIENT and self.read_by_client_at is None:
                self.read_by_client_at = now
        super().save(*args, **kwargs)
