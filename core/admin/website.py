"""
Админ-панель для клиентского сайта

УПРОЩЁННАЯ ВЕРСИЯ:
- Фотографии контейнеров теперь только в inline карточки контейнера
- CarPhoto, ContainerPhoto, ContainerPhotoArchive убраны из отдельного меню
- Загрузка фото происходит автоматически с Google Drive
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
from django.utils.html import format_html

from core.models.website import (
    TRANSPORT_DOCUMENT_TYPES,
    AIChat,
    ClientDocument,
    ClientUser,
    ContactMessage,
    DeclarationRequest,
    NewsPost,
    NotificationLog,
    TrackingRequest,
    TransportDeclarationGroup,
    TransportDocumentRule,
    TransportRequest,
    TransportRequestDocument,
    TransportRequestMessage,
)

# Без похожих символов (l/1/I/O/0), чтобы пароль легко диктовался клиенту.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_client_password():
    return get_random_string(10, allowed_chars=PASSWORD_ALPHABET)


class ClientUserCreateForm(forms.ModelForm):
    """Создание доступа клиента одной формой: логин + пароль + клиент.

    Django User создаётся автоматически; пустой пароль = сгенерировать.
    """

    username = forms.CharField(max_length=150, label="Логин")
    email = forms.EmailField(required=False, label="Email")
    password = forms.CharField(
        required=False,
        label="Пароль",
        help_text="Оставьте пустым — пароль будет сгенерирован автоматически и показан после сохранения.",
    )

    class Meta:
        model = ClientUser
        fields = ["client", "phone", "language", "is_verified"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Пользователь с таким логином уже существует.")
        return username


@admin.register(ClientUser)
class ClientUserAdmin(admin.ModelAdmin):
    """Управление клиентскими пользователями"""

    add_form = ClientUserCreateForm

    list_display = ["user", "client", "phone", "language", "is_verified", "created_at"]
    list_filter = ["is_verified", "language", "created_at"]
    list_select_related = ("user", "client")
    search_fields = ["user__username", "user__email", "client__name", "phone"]
    autocomplete_fields = ["user", "client"]  # M5
    readonly_fields = ["created_at", "last_login"]

    fieldsets = (
        ("Основная информация", {"fields": ("user", "client", "phone")}),
        ("Настройки", {"fields": ("language", "is_verified")}),
        ("Даты", {"fields": ("created_at", "last_login"), "classes": ("collapse",)}),
    )

    add_fieldsets = (
        (
            "Новый доступ клиента",
            {
                "fields": ("username", "email", "password", "client", "phone", "language", "is_verified"),
                "description": "Django-пользователь будет создан автоматически. "
                "Если пароль не указан — он будет сгенерирован и показан в сообщении после сохранения.",
            },
        ),
    )

    actions = ["reset_password"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            password = form.cleaned_data.get("password") or generate_client_password()
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data.get("email") or "",
                password=password,
            )
            obj.user = user
            messages.success(
                request,
                f"Создан доступ: логин «{user.username}», пароль «{password}». "
                "Передайте эти данные клиенту — пароль больше нигде не отображается.",
            )
        super().save_model(request, obj, form, change)

    @admin.action(description="Сгенерировать новый пароль")
    def reset_password(self, request, queryset):
        for client_user in queryset.select_related("user"):
            password = generate_client_password()
            client_user.user.set_password(password)
            client_user.user.save(update_fields=["password"])
            messages.success(
                request,
                f"{client_user.user.username} ({client_user.client.name}): новый пароль «{password}»",
            )


# CarPhotoAdmin и ContainerPhotoAdmin УДАЛЕНЫ
# Фотографии теперь отображаются только в inline карточки контейнера
# Загрузка фото происходит автоматически с Google Drive


@admin.register(ClientDocument)
class ClientDocumentAdmin(admin.ModelAdmin):
    """Документы, загруженные клиентами через кабинет"""

    list_display = ["document_type", "client", "car", "status_display", "uploaded_at", "file_link"]
    list_filter = ["status", "document_type", "uploaded_at"]
    list_select_related = ("client", "car", "uploaded_by")
    search_fields = ["client__name", "car__vin", "comment"]
    autocomplete_fields = ["client", "car"]
    readonly_fields = ["uploaded_by", "uploaded_at"]
    date_hierarchy = "uploaded_at"

    fieldsets = (
        ("Документ", {"fields": ("client", "car", "document_type", "file", "comment")}),
        ("Обработка", {"fields": ("status", "staff_comment")}),
        ("Метаданные", {"fields": ("uploaded_by", "uploaded_at"), "classes": ("collapse",)}),
    )

    actions = ["mark_accepted", "mark_rejected"]

    def status_display(self, obj):
        colors = {"NEW": "#f0ad4e", "ACCEPTED": "#4CAF50", "REJECTED": "#f44336"}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, "#666"),
            obj.get_status_display(),
        )

    status_display.short_description = "Статус"
    status_display.admin_order_field = "status"

    def file_link(self, obj):
        if obj.file:
            return format_html('<a href="{}" target="_blank">📎 {}</a>', obj.file.url, obj.filename)
        return "—"

    file_link.short_description = "Файл"

    def mark_accepted(self, request, queryset):
        queryset.update(status="ACCEPTED")

    mark_accepted.short_description = "Отметить как принятые"

    def mark_rejected(self, request, queryset):
        queryset.update(status="REJECTED")

    mark_rejected.short_description = "Отметить как отклонённые"


@admin.register(DeclarationRequest)
class DeclarationRequestAdmin(admin.ModelAdmin):
    """Заявки клиентов на оформление деклараций"""

    list_display = ["number", "client", "car", "declaration_type", "status_display", "created_at", "print_link"]
    list_filter = ["status", "declaration_type", "created_at"]
    list_select_related = ("client", "car")
    search_fields = ["number", "client__name", "car__vin", "buyer_name"]
    autocomplete_fields = ["client", "car", "transport_request"]
    readonly_fields = ["number", "created_by", "created_at", "updated_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Заявка", {"fields": ("number", "client", "car", "declaration_type", "transport_request")}),
        ("Получатель", {"fields": ("buyer_name", "buyer_code", "buyer_country", "buyer_address")}),
        (
            "Направление и стоимость",
            {"fields": ("destination_country", "destination_city", "invoice_value", "currency")},
        ),
        ("Примечания", {"fields": ("notes",)}),
        ("Обработка", {"fields": ("status", "staff_comment")}),
        ("Метаданные", {"fields": ("created_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def status_display(self, obj):
        colors = {"NEW": "#f0ad4e", "IN_PROGRESS": "#2196F3", "READY": "#4CAF50", "REJECTED": "#f44336"}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, "#666"),
            obj.get_status_display(),
        )

    status_display.short_description = "Статус"
    status_display.admin_order_field = "status"

    def print_link(self, obj):
        from django.urls import reverse

        return format_html(
            '<a href="{}" target="_blank">🖨 Печать</a>', reverse("website:declaration_print", args=[obj.pk])
        )

    print_link.short_description = "Печатная форма"


class TransportRequestDocumentInline(admin.TabularInline):
    """Документы пакета (Беларусь), загруженные/сгенерированные в кабинете."""

    model = TransportRequestDocument
    extra = 0
    fields = ("car", "doc_type", "file_link", "is_generated", "uploaded_by", "created_at")
    readonly_fields = ("file_link", "uploaded_by", "created_at")

    def file_link(self, obj):
        if obj.pk and obj.file:
            return format_html('<a href="{}" target="_blank">{}</a>', obj.file.url, obj.filename)
        return "—"

    file_link.short_description = "Файл"


class TransportRequestMessageInline(admin.TabularInline):
    """Переписка с клиентом по заявке (читается, пишется на доске заявок)."""

    model = TransportRequestMessage
    extra = 0
    fields = ("author_kind", "kind", "author", "car", "body", "requested_doc_types", "created_at")
    readonly_fields = ("created_at",)


class TransportDeclarationGroupInline(admin.TabularInline):
    """Отдельные декларации заявки: авто вне групп идут по типу заявки.

    Удобнее собирать разбивку на доске заявок; здесь — для точечных правок.
    """

    model = TransportDeclarationGroup
    extra = 0
    fields = ("declaration_type", "cars", "note", "position")
    autocomplete_fields = ("cars",)


@admin.register(TransportRequest)
class TransportRequestAdmin(admin.ModelAdmin):
    """Заявки клиентов с данными автовозов.

    Основная работа с заявкой идёт на карточной доске ``/admin/requests/``
    (правка, документы, переписка, письмо складу); эта форма остаётся для
    точечных правок и поиска.
    """

    inlines = [TransportDeclarationGroupInline, TransportRequestDocumentInline, TransportRequestMessageInline]

    list_display = [
        "number",
        "client",
        "carrier_display",
        "truck_display",
        "driver_name",
        "border_crossing",
        "cars_count",
        "status_display",
        "board_link",
        "created_at",
    ]
    list_filter = [
        "status",
        "warehouse_state",
        "destination_country",
        "declaration_type",
        "awaiting_client_docs",
        "created_at",
    ]
    list_select_related = ("client", "auto_transport")
    search_fields = [
        "number",
        "client__name",
        "carrier_name",
        "carrier_eori",
        "truck_number",
        "driver_name",
        "cars__vin",
    ]
    autocomplete_fields = ["client", "cars", "auto_transport"]
    readonly_fields = ["number", "created_by", "created_at", "updated_at"]
    filter_horizontal = ()
    date_hierarchy = "created_at"

    fieldsets = (
        ("Заявка", {"fields": ("number", "client", "cars")}),
        (
            "Данные автовоза",
            {
                "fields": (
                    "carrier_name",
                    "carrier_eori",
                    "truck_number",
                    "trailer_number",
                    "driver_name",
                    "driver_phone",
                    "border_crossing",
                    "planned_loading_date",
                )
            },
        ),
        ("Комментарий клиента", {"fields": ("comment",)}),
        (
            "Обработка",
            {
                "fields": (
                    "status",
                    "destination_country",
                    "declaration_type",
                    "awaiting_client_docs",
                    "staff_comment",
                    "auto_transport",
                )
            },
        ),
        (
            "Склад (внутреннее, клиенту не видно)",
            {
                "fields": ("warehouse", "warehouse_state", "sent_to_warehouse_at", "warehouse_confirmed_at"),
                "classes": ("collapse",),
            },
        ),
        ("Метаданные", {"fields": ("created_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def board_link(self, obj):
        from django.urls import reverse

        return format_html('<a href="{}">Карточка на доске</a>', reverse("admin_request_card", args=[obj.pk]))

    board_link.short_description = "Доска заявок"

    def carrier_display(self, obj):
        if obj.carrier_name and obj.carrier_eori:
            return f"{obj.carrier_name} ({obj.carrier_eori})"
        return obj.carrier_name or "—"

    carrier_display.short_description = "Перевозчик"

    def truck_display(self, obj):
        if obj.trailer_number:
            return f"{obj.truck_number} / {obj.trailer_number}"
        return obj.truck_number

    truck_display.short_description = "Тягач / прицеп"

    def cars_count(self, obj):
        return obj.cars.count()

    cars_count.short_description = "Машин"

    def status_display(self, obj):
        colors = {
            "DRAFT": "#9e9e9e",
            "SUBMITTED": "#f0ad4e",
            "ACCEPTED": "#4CAF50",
            "IN_PROGRESS": "#2196F3",
            "COMPLETED": "#6a1b9a",
            "CANCELLED": "#d9534f",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, "#666"),
            obj.get_status_display(),
        )

    status_display.short_description = "Статус"
    status_display.admin_order_field = "status"


class TransportDocumentRuleForm(forms.ModelForm):
    """Обязательные документы — чекбоксами, а не JSON-строкой."""

    required_doc_types = forms.MultipleChoiceField(
        choices=TRANSPORT_DOCUMENT_TYPES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Обязательные документы",
        help_text="Отметьте, что таможня этой страны требует при этой процедуре.",
    )

    class Meta:
        model = TransportDocumentRule
        fields = "__all__"

    def clean_required_doc_types(self):
        # Порядок как в TRANSPORT_DOCUMENT_TYPES — списки одинаково выглядят везде.
        selected = set(self.cleaned_data.get("required_doc_types") or [])
        return [code for code, _label in TRANSPORT_DOCUMENT_TYPES if code in selected]


@admin.register(TransportDocumentRule)
class TransportDocumentRuleAdmin(admin.ModelAdmin):
    """Требования таможни к пакету документов по стране и процедуре.

    Пары, для которых строки нет, считаются по встроенному набору
    (``core.services.transport_request_check``).
    """

    form = TransportDocumentRuleForm
    list_display = ["country", "procedure", "documents_display", "is_active", "updated_at", "updated_by"]
    list_filter = ["country", "procedure", "is_active"]
    search_fields = ["note"]
    readonly_fields = ["updated_at", "updated_by"]

    fieldsets = (
        ("Направление", {"fields": ("country", "procedure", "is_active")}),
        ("Пакет документов", {"fields": ("required_doc_types",)}),
        ("Служебное", {"fields": ("note", "updated_by", "updated_at")}),
    )

    def documents_display(self, obj):
        return ", ".join(obj.required_doc_labels) or "—"

    documents_display.short_description = "Обязательные документы"

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AIChat)
class AIChatAdmin(admin.ModelAdmin):
    """История чатов с ИИ"""

    list_display = ["user_display", "message_preview", "created_at", "was_helpful"]
    list_filter = ["was_helpful", "created_at"]
    list_select_related = ("user", "client")
    search_fields = ["user__username", "client__name", "message", "response"]
    readonly_fields = ["session_id", "user", "client", "message", "response", "created_at", "processing_time"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Информация о чате", {"fields": ("session_id", "user", "client", "created_at")}),
        ("Сообщения", {"fields": ("message", "response")}),
        ("Метаданные", {"fields": ("processing_time", "was_helpful")}),
    )

    def user_display(self, obj):
        if obj.user:
            return obj.user.username
        return "Анонимный"

    user_display.short_description = "Пользователь"

    def message_preview(self, obj):
        return obj.message[:50] + "..." if len(obj.message) > 50 else obj.message

    message_preview.short_description = "Сообщение"


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    """Управление новостями"""

    list_display = ["title", "author", "published", "published_at", "views"]
    list_filter = ["published", "published_at", "author"]
    list_select_related = ("author",)
    search_fields = ["title", "content", "excerpt"]
    autocomplete_fields = ["author"]  # M5
    readonly_fields = ["views", "created_at", "updated_at"]
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ["published"]
    date_hierarchy = "published_at"

    fieldsets = (
        ("Основное", {"fields": ("title", "slug", "excerpt", "content", "image")}),
        ("Публикация", {"fields": ("published", "published_at", "author")}),
        ("Статистика", {"fields": ("views", "created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Управление сообщениями обратной связи"""

    list_display = ["name", "email", "subject", "created_at", "is_read", "replied"]
    list_filter = ["is_read", "replied", "created_at"]
    search_fields = ["name", "email", "subject", "message"]
    readonly_fields = ["created_at"]
    list_editable = ["is_read", "replied"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Отправитель", {"fields": ("name", "email", "phone")}),
        ("Сообщение", {"fields": ("subject", "message")}),
        ("Статус", {"fields": ("is_read", "replied", "created_at")}),
    )

    actions = ["mark_as_read", "mark_as_replied"]

    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)

    mark_as_read.short_description = "Отметить как прочитанное"

    def mark_as_replied(self, request, queryset):
        queryset.update(replied=True)

    mark_as_replied.short_description = "Отметить как отвеченное"


@admin.register(TrackingRequest)
class TrackingRequestAdmin(admin.ModelAdmin):
    """Запросы на отслеживание"""

    list_display = ["tracking_number", "result_display", "created_at", "ip_address"]
    list_filter = ["created_at"]
    list_select_related = ("car", "container")
    search_fields = ["tracking_number", "email"]
    autocomplete_fields = ["car", "container"]  # M5
    readonly_fields = ["created_at", "ip_address"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Запрос", {"fields": ("tracking_number", "email")}),
        ("Результат", {"fields": ("car", "container")}),
        ("Метаданные", {"fields": ("created_at", "ip_address")}),
    )

    def result_display(self, obj):
        if obj.car:
            return format_html('<span style="color: green;">✓ Авто: {}</span>', obj.car.vin)
        elif obj.container:
            return format_html('<span style="color: blue;">✓ Контейнер: {}</span>', obj.container.number)
        return format_html('<span style="color: red;">✗ Не найдено</span>')

    result_display.short_description = "Результат"


# ContainerPhotoArchiveAdmin УДАЛЁН
# Загрузка фото происходит автоматически с Google Drive


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    """Просмотр истории отправленных уведомлений"""

    list_display = [
        "sent_at",
        "notification_type_display",
        "channel_display",
        "container",
        "car",
        "client",
        "email_to",
        "success_display",
        "created_by",
    ]
    list_filter = ["notification_type", "channel", "success", "sent_at"]
    list_select_related = ("container", "car", "client", "created_by")
    search_fields = ["container__number", "car__vin", "client__name", "email_to", "subject"]
    readonly_fields = [
        "container",
        "car",
        "client",
        "notification_type",
        "channel",
        "email_to",
        "subject",
        "cars_info",
        "sent_at",
        "success",
        "error_message",
        "created_by",
    ]
    ordering = ["-sent_at"]
    date_hierarchy = "sent_at"

    fieldsets = (
        (
            "Уведомление",
            {"fields": ("notification_type", "channel", "container", "car", "client", "email_to", "subject")},
        ),
        ("Автомобили", {"fields": ("cars_info",), "classes": ("collapse",)}),
        ("Статус отправки", {"fields": ("sent_at", "success", "error_message", "created_by")}),
    )

    def notification_type_display(self, obj):
        """Красивое отображение типа уведомления"""
        colors = {
            "PLANNED": "#2196F3",  # синий
            "UNLOADED": "#4CAF50",  # зеленый
            "CAR_UNLOADED": "#4CAF50",  # зеленый
        }
        color = colors.get(obj.notification_type, "#666")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_notification_type_display(),
        )

    notification_type_display.short_description = "Тип"
    notification_type_display.admin_order_field = "notification_type"

    def channel_display(self, obj):
        """Канал доставки: Email / Telegram."""
        colors = {"EMAIL": "#607D8B", "TELEGRAM": "#229ED9"}
        color = colors.get(obj.channel, "#666")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_channel_display(),
        )

    channel_display.short_description = "Канал"
    channel_display.admin_order_field = "channel"

    def success_display(self, obj):
        """Красивое отображение статуса"""
        if obj.success:
            return format_html('<span style="color: green;">✓ Успешно</span>')
        return format_html('<span style="color: red;">✗ Ошибка</span>')

    success_display.short_description = "Статус"
    success_display.admin_order_field = "success"

    def has_add_permission(self, request):
        """Запрещаем создание записей вручную"""
        return False

    def has_change_permission(self, request, obj=None):
        """Запрещаем редактирование"""
        return False

    def has_delete_permission(self, request, obj=None):
        """Разрешаем удаление для очистки старых записей"""
        return True
