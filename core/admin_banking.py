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

class BankReconciliationFilter(admin.SimpleListFilter):
    """Фильтр: статус сопоставления банковской операции"""
    title = 'Сопоставление'
    parameter_name = 'reconciled'

    def lookups(self, request, model_admin):
        return [
            ('matched', 'Сопоставлены (привязан инвойс)'),
            ('skipped', 'Не требует привязки'),
            ('unmatched', 'Не сопоставлены'),
        ]

    def queryset(self, request, queryset):
        from django.db.models import Q
        if self.value() == 'matched':
            return queryset.filter(
                Q(matched_transaction__isnull=False) | Q(matched_invoice__isnull=False)
            )
        if self.value() == 'skipped':
            return queryset.filter(reconciliation_skipped=True)
        if self.value() == 'unmatched':
            return queryset.filter(
                matched_transaction__isnull=True,
                matched_invoice__isnull=True,
                reconciliation_skipped=False,
            )
        return queryset


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'connection', 'transaction_type',
        'display_amount', 'currency', 'counterparty_name',
        'display_reconciled', 'display_action', 'state',
    )
    list_filter = (BankReconciliationFilter, 'transaction_type', 'state', 'currency', 'connection')
    search_fields = ('description', 'counterparty_name', 'external_id')
    readonly_fields = (
        'connection', 'external_id', 'transaction_type', 'amount', 'currency',
        'description', 'counterparty_name', 'state', 'created_at', 'fetched_at',
    )
    autocomplete_fields = ['matched_invoice', 'matched_transaction']
    date_hierarchy = 'created_at'
    actions = ['mark_skip_reconciliation', 'unmark_skip_reconciliation']

    fieldsets = (
        ('Банковская операция', {
            'fields': (
                'connection', 'external_id', 'transaction_type',
                ('amount', 'currency'), 'description',
                'counterparty_name', 'state',
                ('created_at', 'fetched_at'),
            ),
        }),
        ('Сопоставление с внутренними операциями', {
            'fields': (
                'matched_invoice', 'matched_transaction',
                'reconciliation_skipped', 'reconciliation_note',
            ),
            'description': 'Привяжите банковскую операцию к инвойсу и/или транзакции для сверки',
        }),
    )

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

    def display_reconciled(self, obj):
        # 1. Привязано к инвойсу/транзакции
        if obj.matched_invoice_id or obj.matched_transaction_id:
            parts = []
            if obj.matched_invoice:
                parts.append(f'Инв: {obj.matched_invoice.number}')
            if obj.matched_transaction:
                parts.append(f'Трх: {obj.matched_transaction.number}')
            label = ', '.join(parts)
            return format_html(
                '<span style="color:#16a34a;font-weight:600" title="{}">✓ Сопоставлено</span>',
                label
            )
        # 2. Помечено как "не требует привязки"
        if obj.reconciliation_skipped:
            note = obj.reconciliation_note or 'Не требует привязки'
            return format_html(
                '<span style="color:#9898b0;" title="{}">⊘ Пропуск</span>',
                note
            )
        # 3. Не сопоставлено — требует внимания
        return format_html(
            '<span style="color:#dc2626;font-weight:600;">✗ Не привязано</span>'
        )
    display_reconciled.short_description = 'Сверка'

    def display_action(self, obj):
        from django.urls import reverse
        # Привязано — ссылка на инвойс
        if obj.matched_invoice_id:
            url = reverse('admin:core_newinvoice_change', args=[obj.matched_invoice_id])
            return format_html(
                '<a href="{}" style="color:#2563eb;text-decoration:none;">📄 {}</a>',
                url, obj.matched_invoice.number
            )
        # Не привязано и не пропущено — кнопка "Привязать"
        if not obj.reconciliation_skipped:
            url = reverse('admin:core_banktransaction_change', args=[obj.pk])
            return format_html(
                '<a href="{}" style="color:#7c3aed;font-weight:600;text-decoration:none;">'
                '🔗 Привязать</a>',
                url
            )
        return format_html('<span style="color:#9898b0;">—</span>')
    display_action.short_description = 'Действие'

    @admin.action(description='Пометить: не требует привязки')
    def mark_skip_reconciliation(self, request, queryset):
        count = queryset.update(
            reconciliation_skipped=True,
            reconciliation_note='Помечено вручную: не требует привязки'
        )
        messages.success(request, f'{count} операций помечены как не требующие привязки.')

    @admin.action(description='Снять пометку "не требует привязки"')
    def unmark_skip_reconciliation(self, request, queryset):
        count = queryset.update(reconciliation_skipped=False)
        messages.success(request, f'Пометка снята с {count} операций.')
