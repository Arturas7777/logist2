"""
Админка для интеграции с site.pro (бухгалтерия)
=================================================

Управление подключением к site.pro API и просмотр логов синхронизации.

Авторы: AI Assistant
Дата: Февраль 2026
"""

from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
from django.utils import timezone

from .models_accounting import SiteProConnection, SiteProInvoiceSync


# ============================================================================
# SITE.PRO CONNECTION ADMIN
# ============================================================================

@admin.register(SiteProConnection)
class SiteProConnectionAdmin(admin.ModelAdmin):
    """Управление подключением к site.pro API."""

    list_display = (
        'name',
        'company',
        'is_active_display',
        'auto_push_display',
        'last_synced_display',
        'last_error_display',
    )

    list_filter = ('is_active',)

    fieldsets = (
        ('Основные настройки', {
            'fields': (
                'company',
                'name',
                'is_active',
            ),
        }),
        ('Учётные данные site.pro', {
            'fields': (
                '_username',
                '_password',
            ),
            'description': (
                'Введите логин и пароль от site.pro аккаунта. '
                'Данные хранятся в зашифрованном виде (Fernet).'
            ),
        }),
        ('Настройки инвойсов', {
            'fields': (
                'auto_push_on_issue',
                'default_vat_rate',
                'default_currency',
                'invoice_series',
            ),
        }),
        ('Состояние подключения (только чтение)', {
            'fields': (
                'sitepro_user_id',
                'sitepro_company_id',
                'last_synced_at',
                'last_error',
            ),
            'classes': ('collapse',),
        }),
    )

    readonly_fields = (
        'sitepro_user_id',
        'sitepro_company_id',
        'last_synced_at',
        'last_error',
    )

    actions = ['test_connection', 'sync_now']

    def is_active_display(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #28a745; font-weight: bold;">Активно</span>')
        return format_html('<span style="color: #dc3545;">Неактивно</span>')
    is_active_display.short_description = 'Статус'

    def auto_push_display(self, obj):
        if obj.auto_push_on_issue:
            return format_html('<span style="color: #28a745;">Вкл</span>')
        return format_html('<span style="color: #999;">Выкл</span>')
    auto_push_display.short_description = 'Авто-отправка'

    def last_synced_display(self, obj):
        if obj.last_synced_at:
            return obj.last_synced_at.strftime('%d.%m.%Y %H:%M')
        return format_html('<span style="color: #999;">Никогда</span>')
    last_synced_display.short_description = 'Последняя синхр.'

    def last_error_display(self, obj):
        if obj.last_error:
            error_text = obj.last_error[:80] + '...' if len(obj.last_error) > 80 else obj.last_error
            return format_html(
                '<span style="color: #dc3545;" title="{}">{}</span>',
                obj.last_error, error_text
            )
        return format_html('<span style="color: #28a745;">OK</span>')
    last_error_display.short_description = 'Ошибка'

    def test_connection(self, request, queryset):
        """Проверить подключение к site.pro."""
        for conn in queryset:
            from .services.sitepro_service import SiteProService
            service = SiteProService(conn)
            result = service.test_connection()

            if result['success']:
                messages.success(
                    request,
                    f'{conn.name}: подключение успешно! '
                    f'User ID: {result["user_id"]}, Company ID: {result["company_id"]}'
                )
            else:
                messages.error(request, f'{conn.name}: ошибка — {result["error"]}')
    test_connection.short_description = "Проверить подключение к site.pro"

    def sync_now(self, request, queryset):
        """Тестовая синхронизация — аутентификация и проверка."""
        for conn in queryset:
            from .services.sitepro_service import SiteProService
            service = SiteProService(conn)
            result = service.test_connection()

            if result['success']:
                conn.last_synced_at = timezone.now()
                conn.save(update_fields=['last_synced_at', 'updated_at'])
                messages.success(request, f'{conn.name}: синхронизация успешна!')
            else:
                messages.error(request, f'{conn.name}: ошибка — {result["error"]}')
    sync_now.short_description = "Sync Now — проверить подключение"


# ============================================================================
# SITE.PRO INVOICE SYNC ADMIN
# ============================================================================

@admin.register(SiteProInvoiceSync)
class SiteProInvoiceSyncAdmin(admin.ModelAdmin):
    """Просмотр логов синхронизации инвойсов с site.pro."""

    list_display = (
        'invoice_number',
        'sync_status_display',
        'external_id',
        'external_number',
        'pdf_link',
        'last_synced_at',
        'error_display',
    )

    list_filter = ('sync_status',)
    search_fields = ('invoice__number', 'external_id', 'external_number')
    readonly_fields = (
        'connection', 'invoice', 'external_id', 'external_number',
        'pdf_url', 'sync_status', 'error_message', 'last_synced_at',
        'created_at', 'updated_at',
    )

    def invoice_number(self, obj):
        return obj.invoice.number
    invoice_number.short_description = 'Инвойс'
    invoice_number.admin_order_field = 'invoice__number'

    def sync_status_display(self, obj):
        colors = {
            'PENDING': '#ffc107',
            'SENT': '#28a745',
            'FAILED': '#dc3545',
            'PDF_READY': '#17a2b8',
        }
        color = colors.get(obj.sync_status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; '
            'border-radius: 3px; font-size: 0.85em;">{}</span>',
            color, obj.get_sync_status_display()
        )
    sync_status_display.short_description = 'Статус'

    def pdf_link(self, obj):
        if obj.pdf_url:
            return format_html('<a href="{}" target="_blank">PDF</a>', obj.pdf_url)
        return '-'
    pdf_link.short_description = 'PDF'

    def error_display(self, obj):
        if obj.error_message:
            error_text = obj.error_message[:60] + '...' if len(obj.error_message) > 60 else obj.error_message
            return format_html(
                '<span style="color: #dc3545;" title="{}">{}</span>',
                obj.error_message, error_text
            )
        return '-'
    error_display.short_description = 'Ошибка'

    actions = ['retry_failed']

    def retry_failed(self, request, queryset):
        """Повторить отправку для неудачных синхронизаций."""
        retried = 0
        errors = 0

        for sync in queryset.filter(sync_status='FAILED'):
            try:
                from .services.sitepro_service import SiteProService
                service = SiteProService(sync.connection)
                # Сбрасываем статус для повторной отправки
                sync.sync_status = 'PENDING'
                sync.save(update_fields=['sync_status'])
                service.push_invoice(sync.invoice)
                retried += 1
            except Exception as e:
                errors += 1

        if retried:
            messages.success(request, f'Повторно отправлено: {retried}')
        if errors:
            messages.error(request, f'Ошибок при повторной отправке: {errors}')
    retry_failed.short_description = "Повторить отправку (неудачные)"
