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
        ("TRANSIT", "Транзитная (T1)"),
        ("EXPORT", "Экспортная"),
        ("IMPORT", "Импортная"),
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
TRANSPORT_UPLOAD_ONLY_TYPES = {"PASSPORT", "SIGNATURE", "OTHER"}


class TransportRequest(models.Model):
    """Заявка клиента с данными автовоза, который заберёт его автомобили.

    Жизненный цикл: Черновик → Подана → Принята → В процессе → Оформлена.
    Клиент может редактировать и удалять заявку только в статусах
    «Черновик»/«Подана» — см. ``portal_transport``. «Удаление» клиентом —
    мягкое: заявка получает статус «Отменена» и скрывается из кабинета,
    но остаётся видимой администратору как неактуальная.
    """

    STATUS_CHOICES = [
        ("DRAFT", "Черновик"),
        ("SUBMITTED", "Подана"),
        ("ACCEPTED", "Принята"),
        ("IN_PROGRESS", "В процессе"),
        ("COMPLETED", "Оформлена"),
        ("CANCELLED", "Отменена"),
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
        ]

    def __str__(self):
        return f"{self.number} — {self.client.name}"

    @property
    def is_client_editable(self):
        """Может ли клиент редактировать заявку (до статуса «В процессе»)."""
        return self.status in self.CLIENT_EDITABLE_STATUSES

    def save(self, *args, **kwargs):
        if not self.number:
            from django.db import transaction as db_transaction

            from .series import next_document_number

            date_str = timezone.now().strftime("%Y%m%d")
            with db_transaction.atomic():
                self.number = next_document_number(TransportRequest, f"TR-{date_str}", pad=3)
        super().save(*args, **kwargs)


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
