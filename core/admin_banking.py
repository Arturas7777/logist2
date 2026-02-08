"""
Django Admin для банковских интеграций (Revolut и др.)
"""

from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
from .models_banking import BankConnection, BankAccount, BankTransaction


# ============================================================================
# INLINES
# ============================================================================

class BankAccountInline(admin.TabularInline):
    model = BankAccount
    extra = 0
    readonly_fields = ('external_id', 'name', 'currency', 'balance', 'state', 'last_updated_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ============================================================================
# BANK CONNECTION
# ============================================================================

@admin.register(BankConnection)
class BankConnectionAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'bank_type', 'company', 'is_active',
        'display_accounts_count', 'display_last_synced', 'display_status',
    )
    list_filter = ('bank_type', 'is_active', 'use_sandbox')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at', 'last_synced_at', 'last_error')
    inlines = [BankAccountInline]
    actions = ['sync_now']

    fieldsets = (
        ('Основное', {
            'fields': ('bank_type', 'company', 'name', 'is_active', 'use_sandbox'),
        }),
        ('Credentials (зашифрованы в БД)', {
            'classes': ('collapse',),
            'description': (
                'Токены хранятся в зашифрованном виде. '
                'Используйте команду <code>python manage.py setup_revolut</code> для настройки.'
            ),
            'fields': ('_client_id', '_refresh_token', '_access_token',
                       'access_token_expires_at', '_jwt_assertion'),
        }),
        ('Статус', {
            'fields': ('last_synced_at', 'last_error', 'created_at', 'updated_at'),
        }),
    )

    def display_accounts_count(self, obj):
        count = obj.accounts.filter(state='active').count()
        return f'{count} счетов'
    display_accounts_count.short_description = 'Счета'

    def display_last_synced(self, obj):
        if obj.last_synced_at:
            from django.utils.timesince import timesince
            return f'{timesince(obj.last_synced_at)} назад'
        return '—'
    display_last_synced.short_description = 'Синхронизация'

    def display_status(self, obj):
        if obj.last_error:
            return format_html(
                '<span style="color:#dc2626;font-weight:600">Ошибка</span>'
            )
        if obj.last_synced_at:
            return format_html(
                '<span style="color:#16a34a;font-weight:600">OK</span>'
            )
        return format_html(
            '<span style="color:#9898b0">Не синхронизировано</span>'
        )
    display_status.short_description = 'Статус'

    @admin.action(description='Синхронизировать сейчас')
    def sync_now(self, request, queryset):
        from .services.revolut_service import RevolutService

        total = 0
        errors = 0
        for conn in queryset.filter(is_active=True):
            if conn.bank_type == 'REVOLUT':
                service = RevolutService(conn)
                result = service.sync_all()
                if result['error']:
                    errors += 1
                    messages.error(request, f'{conn}: {result["error"]}')
                else:
                    total += len(result['accounts'])
                    messages.success(
                        request,
                        f'{conn}: {len(result["accounts"])} счетов, '
                        f'{len(result["transactions"])} транзакций обновлено'
                    )
            else:
                messages.warning(request, f'{conn}: тип банка не поддерживается')

        if not errors:
            messages.info(request, f'Синхронизация завершена: {total} счетов обновлено')


# ============================================================================
# BANK ACCOUNT (read-only)
# ============================================================================

@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'connection', 'currency', 'display_balance', 'state', 'last_updated_at')
    list_filter = ('currency', 'state', 'connection')
    search_fields = ('name', 'external_id')
    readonly_fields = ('connection', 'external_id', 'name', 'currency', 'balance', 'state', 'last_updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def display_balance(self, obj):
        color = '#16a34a' if obj.balance >= 0 else '#dc2626'
        return format_html(
            '<span style="font-weight:700;color:{}">{} {}</span>',
            color, f'{obj.balance:,.2f}', obj.currency
        )
    display_balance.short_description = 'Баланс'
    display_balance.admin_order_field = 'balance'


# ============================================================================
# BANK TRANSACTION (read-only)
# ============================================================================

@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'connection', 'transaction_type',
        'display_amount', 'currency', 'counterparty_name', 'state',
    )
    list_filter = ('transaction_type', 'state', 'currency', 'connection')
    search_fields = ('description', 'counterparty_name', 'external_id')
    readonly_fields = (
        'connection', 'external_id', 'transaction_type', 'amount', 'currency',
        'description', 'counterparty_name', 'state', 'created_at', 'fetched_at',
    )
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def display_amount(self, obj):
        color = '#16a34a' if obj.amount >= 0 else '#dc2626'
        sign = '+' if obj.amount >= 0 else ''
        return format_html(
            '<span style="font-weight:700;color:{}">{}{} {}</span>',
            color, sign, f'{obj.amount:,.2f}', obj.currency
        )
    display_amount.short_description = 'Сумма'
    display_amount.admin_order_field = 'amount'
