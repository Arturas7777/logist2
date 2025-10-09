"""
Админ-панель для клиентского сайта
"""
from django.contrib import admin
from django.utils.html import format_html
from .models_website import (
    ClientUser, CarPhoto, ContainerPhoto, ContainerPhotoArchive, AIChat,
    NewsPost, ContactMessage, TrackingRequest
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


@admin.register(CarPhoto)
class CarPhotoAdmin(admin.ModelAdmin):
    """Управление фотографиями автомобилей"""
    list_display = ['car', 'photo_type', 'photo_preview', 'uploaded_at', 'is_public']
    list_filter = ['photo_type', 'is_public', 'uploaded_at']
    search_fields = ['car__vin', 'car__brand', 'description']
    readonly_fields = ['uploaded_at', 'photo_preview']
    list_editable = ['is_public']
    
    fieldsets = (
        ('Фотография', {
            'fields': ('car', 'photo', 'photo_preview', 'photo_type', 'description')
        }),
        ('Настройки', {
            'fields': ('is_public', 'uploaded_by', 'uploaded_at')
        }),
    )
    
    def photo_preview(self, obj):
        if obj.photo:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" />',
                obj.photo.url
            )
        return '-'
    photo_preview.short_description = 'Предпросмотр'


@admin.register(ContainerPhoto)
class ContainerPhotoAdmin(admin.ModelAdmin):
    """Управление фотографиями контейнеров"""
    list_display = ['container', 'photo_type', 'photo_preview', 'uploaded_at', 'is_public']
    list_filter = ['photo_type', 'is_public', 'uploaded_at']
    search_fields = ['container__number', 'description']
    readonly_fields = ['uploaded_at', 'photo_preview']
    list_editable = ['is_public']
    
    fieldsets = (
        ('Фотография', {
            'fields': ('container', 'photo', 'photo_preview', 'photo_type', 'description')
        }),
        ('Настройки', {
            'fields': ('is_public', 'uploaded_by', 'uploaded_at')
        }),
    )
    
    def photo_preview(self, obj):
        if obj.photo:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" />',
                obj.photo.url
            )
        return '-'
    photo_preview.short_description = 'Предпросмотр'


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


@admin.register(ContainerPhotoArchive)
class ContainerPhotoArchiveAdmin(admin.ModelAdmin):
    """Управление архивами фотографий контейнеров"""
    list_display = ['container', 'uploaded_by', 'uploaded_at', 'is_processed', 'photos_count', 'process_button']
    list_filter = ['is_processed', 'uploaded_at']
    search_fields = ['container__number', 'description']
    readonly_fields = ['uploaded_at', 'photos_count', 'is_processed']
    actions = ['process_archive']
    
    fieldsets = (
        ('Архив', {
            'fields': ('container', 'archive_file', 'description')
        }),
        ('Статус обработки', {
            'fields': ('is_processed', 'photos_count', 'uploaded_by', 'uploaded_at')
        }),
    )
    
    def process_button(self, obj):
        """Кнопка для обработки архива"""
        if not obj.is_processed:
            return format_html(
                '<a class="button" href="#" onclick="if(confirm(\'Обработать этот архив?\')) {{ '
                'fetch(\'/admin/process-archive/{}/\', {{method: \'POST\', headers: {{\'X-CSRFToken\': document.querySelector(\'[name=csrfmiddlewaretoken]\').value}}}})'
                '.then(() => location.reload()); }} return false;">Обработать</a>',
                obj.pk
            )
        return format_html('<span style="color: green;">✓ Обработан</span>')
    process_button.short_description = 'Действие'
    
    def process_archive(self, request, queryset):
        """Обработать выбранные архивы"""
        processed_count = 0
        total_photos = 0
        for archive in queryset:
            if not archive.is_processed:
                photos = archive.extract_photos()
                total_photos += len(photos)
                processed_count += 1
        
        self.message_user(request, f'Обработано архивов: {processed_count}, извлечено фотографий: {total_photos}')
    process_archive.short_description = "Обработать выбранные архивы"
    
    def save_model(self, request, obj, form, change):
        """Автоматически обрабатываем архив при сохранении"""
        # Устанавливаем текущего пользователя
        if not obj.uploaded_by:
            obj.uploaded_by = request.user
        
        # Сохраняем объект
        super().save_model(request, obj, form, change)
        
        # Если это новый архив, автоматически обрабатываем его
        if not change or not obj.is_processed:
            try:
                photos = obj.extract_photos()
                self.message_user(
                    request, 
                    f'Архив успешно обработан! Извлечено фотографий: {len(photos)}',
                    level='SUCCESS'
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f'Ошибка при обработке архива: {str(e)}',
                    level='ERROR'
                )

