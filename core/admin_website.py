"""
Админ-панель для клиентского сайта

УПРОЩЁННАЯ ВЕРСИЯ:
- Фотографии контейнеров теперь только в inline карточки контейнера
- CarPhoto, ContainerPhoto, ContainerPhotoArchive убраны из отдельного меню
- Загрузка фото происходит автоматически с Google Drive
"""
from django.contrib import admin
from django.utils.html import format_html
from .models_website import (
    ClientUser, AIChat, NewsPost, ContactMessage, TrackingRequest, NotificationLog
)


@admin.register(ClientUser)
class ClientUserAdmin(admin.ModelAdmin):
    """Управление клиентскими пользователями"""
    list_display = ['user', 'client', 'phone', 'language', 'is_verified', 'created_at']
    list_filter = ['is_verified', 'language', 'created_at']
    search_fields = ['user__username', 'user__email', 'client__name', 'phone']
    readonly_fields = ['created_at', 'last_login']
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('user', 'client', 'phone')
        }),
        ('Настройки', {
            'fields': ('language', 'is_verified')
        }),
        ('Даты', {
            'fields': ('created_at', 'last_login'),
            'classes': ('collapse',)
        }),
    )


# CarPhotoAdmin и ContainerPhotoAdmin УДАЛЕНЫ
# Фотографии теперь отображаются только в inline карточки контейнера
# Загрузка фото происходит автоматически с Google Drive

@admin.register(AIChat)
class AIChatAdmin(admin.ModelAdmin):
    """История чатов с ИИ"""
    list_display = ['user_display', 'message_preview', 'created_at', 'was_helpful']
    list_filter = ['was_helpful', 'created_at']
    search_fields = ['user__username', 'client__name', 'message', 'response']
    readonly_fields = ['session_id', 'user', 'client', 'message', 'response', 
                      'created_at', 'processing_time']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Информация о чате', {
            'fields': ('session_id', 'user', 'client', 'created_at')
        }),
        ('Сообщения', {
            'fields': ('message', 'response')
        }),
        ('Метаданные', {
            'fields': ('processing_time', 'was_helpful')
        }),
    )
    
    def user_display(self, obj):
        if obj.user:
            return obj.user.username
        return 'Анонимный'
    user_display.short_description = 'Пользователь'
    
    def message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_preview.short_description = 'Сообщение'


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    """Управление новостями"""
    list_display = ['title', 'author', 'published', 'published_at', 'views']
    list_filter = ['published', 'published_at', 'author']
    search_fields = ['title', 'content', 'excerpt']
    readonly_fields = ['views', 'created_at', 'updated_at']
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ['published']
    date_hierarchy = 'published_at'
    
    fieldsets = (
        ('Основное', {
            'fields': ('title', 'slug', 'excerpt', 'content', 'image')
        }),
        ('Публикация', {
            'fields': ('published', 'published_at', 'author')
        }),
        ('Статистика', {
            'fields': ('views', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = {
            'all': ('admin/css/news_admin.css',)
        }


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Управление сообщениями обратной связи"""
    list_display = ['name', 'email', 'subject', 'created_at', 'is_read', 'replied']
    list_filter = ['is_read', 'replied', 'created_at']
    search_fields = ['name', 'email', 'subject', 'message']
    readonly_fields = ['created_at']
    list_editable = ['is_read', 'replied']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Отправитель', {
            'fields': ('name', 'email', 'phone')
        }),
        ('Сообщение', {
            'fields': ('subject', 'message')
        }),
        ('Статус', {
            'fields': ('is_read', 'replied', 'created_at')
        }),
    )
    
    actions = ['mark_as_read', 'mark_as_replied']
    
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)
    mark_as_read.short_description = 'Отметить как прочитанное'
    
    def mark_as_replied(self, request, queryset):
        queryset.update(replied=True)
    mark_as_replied.short_description = 'Отметить как отвеченное'


@admin.register(TrackingRequest)
class TrackingRequestAdmin(admin.ModelAdmin):
    """Запросы на отслеживание"""
    list_display = ['tracking_number', 'result_display', 'created_at', 'ip_address']
    list_filter = ['created_at']
    search_fields = ['tracking_number', 'email']
    readonly_fields = ['created_at', 'ip_address']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Запрос', {
            'fields': ('tracking_number', 'email')
        }),
        ('Результат', {
            'fields': ('car', 'container')
        }),
        ('Метаданные', {
            'fields': ('created_at', 'ip_address')
        }),
    )
    
    def result_display(self, obj):
        if obj.car:
            return format_html('<span style="color: green;">✓ Авто: {}</span>', obj.car.vin)
        elif obj.container:
            return format_html('<span style="color: blue;">✓ Контейнер: {}</span>', obj.container.number)
        return format_html('<span style="color: red;">✗ Не найдено</span>')
    result_display.short_description = 'Результат'


# ContainerPhotoArchiveAdmin УДАЛЁН
# Загрузка фото происходит автоматически с Google Drive

@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    """Просмотр истории отправленных уведомлений"""
    list_display = ['sent_at', 'notification_type_display', 'container', 'client', 'email_to', 'success_display', 'created_by']
    list_filter = ['notification_type', 'success', 'sent_at']
    search_fields = ['container__number', 'client__name', 'email_to', 'subject']
    readonly_fields = ['container', 'client', 'notification_type', 'email_to', 'subject', 'cars_info', 'sent_at', 'success', 'error_message', 'created_by']
    ordering = ['-sent_at']
    date_hierarchy = 'sent_at'
    
    fieldsets = (
        ('Уведомление', {
            'fields': ('notification_type', 'container', 'client', 'email_to', 'subject')
        }),
        ('Автомобили', {
            'fields': ('cars_info',),
            'classes': ('collapse',)
        }),
        ('Статус отправки', {
            'fields': ('sent_at', 'success', 'error_message', 'created_by')
        }),
    )
    
    def notification_type_display(self, obj):
        """Красивое отображение типа уведомления"""
        colors = {
            'PLANNED': '#2196F3',  # синий
            'UNLOADED': '#4CAF50',  # зеленый
        }
        color = colors.get(obj.notification_type, '#666')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_notification_type_display()
        )
    notification_type_display.short_description = 'Тип'
    notification_type_display.admin_order_field = 'notification_type'
    
    def success_display(self, obj):
        """Красивое отображение статуса"""
        if obj.success:
            return format_html('<span style="color: green;">✓ Успешно</span>')
        return format_html('<span style="color: red;">✗ Ошибка</span>')
    success_display.short_description = 'Статус'
    success_display.admin_order_field = 'success'
    
    def has_add_permission(self, request):
        """Запрещаем создание записей вручную"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Запрещаем редактирование"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Разрешаем удаление для очистки старых записей"""
        return True

