"""
Django Admin для банковских интеграций (Revolut и др.)
"""
import logging
from decimal import Decimal

from django.contrib import admin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.contrib import messages
from django.db import transaction

from .models_banking import BankConnection, BankAccount, BankTransaction

logger = logging.getLogger(__name__)


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

class BankDirectionFilter(admin.SimpleListFilter):
    """Фильтр: входящие / исходящие"""
    title = 'Направление'
    parameter_name = 'direction'

    def lookups(self, request, model_admin):
        return [
            ('incoming', '↓ Входящие'),
            ('outgoing', '↑ Исходящие'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'incoming':
            return queryset.filter(amount__gt=0)
        if self.value() == 'outgoing':
            return queryset.filter(amount__lt=0)
        return queryset


class BankReceiptFilter(admin.SimpleListFilter):
    """Фильтр: наличие чека из Revolut"""
    title = 'Чек из Revolut'
    parameter_name = 'has_receipt'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Есть чек'),
            ('no_file', 'Expense есть, чек не прикреплён'),
            ('no', 'Нет данных Expense'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(receipt_file='')
        if self.value() == 'no_file':
            return queryset.filter(receipt_file='').exclude(expense_id='')
        if self.value() == 'no':
            return queryset.filter(expense_id='')
        return queryset


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
        'created_at', 'transaction_type',
        'display_amount', 'display_counterparty', 'display_description',
        'display_receipt', 'display_reconciled', 'display_action',
    )
    list_filter = (BankReconciliationFilter, BankDirectionFilter, BankReceiptFilter, 'transaction_type', 'state', 'currency', 'connection')
    search_fields = ('description', 'counterparty_name', 'external_id')
    readonly_fields = (
        'connection', 'external_id', 'transaction_type', 'amount', 'currency',
        'description', 'counterparty_name', 'state', 'created_at', 'fetched_at',
        'expense_id', 'receipt_fetched_at', 'revolut_category', 'display_receipt_detail',
    )
    autocomplete_fields = ['matched_invoice', 'matched_transaction']
    date_hierarchy = 'created_at'
    list_per_page = 50
    ordering = ('-created_at',)
    actions = [
        'mark_skip_reconciliation', 'unmark_skip_reconciliation',
        'link_to_invoice', 'create_expenses_bulk',
        'download_revolut_receipts',
    ]

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
        ('Revolut Expenses (чек и категория из приложения)', {
            'fields': (
                'expense_id', 'revolut_category',
                'receipt_fetched_at', 'display_receipt_detail',
            ),
            'classes': ('collapse',),
            'description': 'Данные, подгруженные из Revolut Expenses API',
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

    def display_counterparty(self, obj):
        from django.utils.safestring import mark_safe
        name = obj.counterparty_name or ''
        if not name:
            return format_html('<span style="color:#9898b0;">—</span>')
        if obj.amount >= 0:
            return format_html(
                '<span style="display:inline-flex;align-items:center;gap:6px;">'
                '<span style="display:inline-flex;align-items:center;justify-content:center;'
                'width:24px;height:24px;border-radius:50%;background:#dcfce7;color:#16a34a;'
                'font-size:14px;font-weight:700;flex-shrink:0;" title="Входящий">&#8595;</span>'
                '<span style="font-weight:600;">{}</span></span>',
                name
            )
        else:
            return format_html(
                '<span style="display:inline-flex;align-items:center;gap:6px;">'
                '<span style="display:inline-flex;align-items:center;justify-content:center;'
                'width:24px;height:24px;border-radius:50%;background:#fee2e2;color:#dc2626;'
                'font-size:14px;font-weight:700;flex-shrink:0;" title="Исходящий">&#8593;</span>'
                '<span style="font-weight:600;">{}</span></span>',
                name
            )
    display_counterparty.short_description = 'Контрагент'
    display_counterparty.admin_order_field = 'counterparty_name'

    def display_description(self, obj):
        desc = obj.description or ''
        if not desc:
            return format_html('<span style="color:#9898b0;">—</span>')
        if len(desc) <= 60:
            return format_html('<span>{}</span>', desc)
        short = desc[:60]
        return format_html(
            '<span style="display:inline;">{}&hellip; '
            '<a href="#" onclick="'
            "var full=this.parentElement.nextElementSibling;"
            "full.style.display='block';this.parentElement.style.display='none';"
            'return false;"'
            ' style="color:#2563eb;font-size:11px;">&#9660;</a>'
            '</span>'
            '<span style="display:none;white-space:normal;max-width:400px;">{} '
            '<a href="#" onclick="'
            "var short=this.parentElement.previousElementSibling;"
            "short.style.display='inline';this.parentElement.style.display='none';"
            'return false;"'
            ' style="color:#2563eb;font-size:11px;">&#9650;</a>'
            '</span>',
            short, desc
        )
    display_description.short_description = 'Назначение платежа'
    display_description.admin_order_field = 'description'

    def display_reconciled(self, obj):
        if obj.matched_invoice_id or obj.matched_transaction_id:
            parts = []
            if obj.matched_invoice:
                parts.append(obj.matched_invoice.number)
            if obj.matched_transaction:
                parts.append(f'TRX {obj.matched_transaction.number}')
            label = ', '.join(parts)
            return format_html(
                '<span style="display:inline-flex;align-items:center;gap:4px;'
                'background:#dcfce7;color:#166534;padding:2px 8px;border-radius:10px;'
                'font-size:12px;font-weight:600;" title="{}">'
                '&#10003; {}</span>',
                label, parts[0] if parts else 'Сопоставлено'
            )
        if obj.reconciliation_skipped:
            note = obj.reconciliation_note or 'Не требует привязки'
            short_note = note.replace('Авто-пропуск: ', '')
            return format_html(
                '<span style="display:inline-flex;align-items:center;gap:4px;'
                'background:#f3f4f6;color:#6b7280;padding:2px 8px;border-radius:10px;'
                'font-size:12px;" title="{}">'
                '&#8709; {}</span>',
                note, short_note[:25]
            )
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:4px;'
            'background:#fef2f2;color:#dc2626;padding:2px 8px;border-radius:10px;'
            'font-size:12px;font-weight:600;">'
            '&#10007; Не привязано</span>'
        )
    display_reconciled.short_description = 'Сверка'

    def display_action(self, obj):
        # Привязано — ссылка на инвойс
        if obj.matched_invoice_id:
            url = reverse('admin:core_newinvoice_change', args=[obj.matched_invoice_id])
            return format_html(
                '<a href="{}" style="color:#2563eb;text-decoration:none;">📄 {}</a>',
                url, obj.matched_invoice.number
            )
        # Не привязано и не пропущено — кнопки "Создать расход" и "Привязать"
        if not obj.reconciliation_skipped:
            expense_url = reverse('admin:banktransaction_create_expense', args=[obj.pk])
            link_url = reverse('admin:core_banktransaction_change', args=[obj.pk])
            return format_html(
                '<a href="{}" style="color:#16a34a;font-weight:600;text-decoration:none;margin-right:8px;">'
                '💰 Расход</a>'
                '<a href="{}" style="color:#7c3aed;text-decoration:none;">'
                '🔗 Привязать</a>',
                expense_url, link_url
            )
        return format_html('<span style="color:#9898b0;">—</span>')
    display_action.short_description = 'Действие'

    def display_receipt(self, obj):
        """Иконка-ссылка на чек из Revolut в списке."""
        if obj.receipt_file:
            return format_html(
                '<a href="{}" target="_blank" title="Чек из Revolut" '
                'style="text-decoration:none;font-size:16px;">📎</a>',
                obj.receipt_file.url,
            )
        if obj.expense_id:
            return format_html(
                '<span title="Revolut Expense без чека" style="color:#d1d5db;font-size:14px;">—</span>'
            )
        return ''
    display_receipt.short_description = '📎'

    def display_receipt_detail(self, obj):
        """Preview чека на странице редактирования."""
        if not obj.receipt_file:
            if obj.expense_id:
                return format_html(
                    '<span style="color:#6b7280;">Expense {} — чек не прикреплён в приложении</span>',
                    obj.expense_id[:12] + '…',
                )
            return format_html('<span style="color:#9ca3af;">Нет данных из Revolut Expenses</span>')

        url = obj.receipt_file.url
        name = obj.receipt_file.name.rsplit('/', 1)[-1]
        is_image = any(name.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp'))
        if is_image:
            return format_html(
                '<div><a href="{}" target="_blank">'
                '<img src="{}" style="max-width:300px;max-height:400px;border:1px solid #e5e7eb;'
                'border-radius:6px;"></a><br>'
                '<a href="{}" target="_blank" style="font-size:12px;">{}</a></div>',
                url, url, url, name,
            )
        return format_html(
            '<a href="{}" target="_blank" '
            'style="display:inline-block;padding:8px 14px;background:#4f46e5;color:#fff;'
            'border-radius:6px;text-decoration:none;font-weight:600;">📎 {}</a>',
            url, name,
        )
    display_receipt_detail.short_description = 'Чек из Revolut'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:pk>/create-expense/',
                self.admin_site.admin_view(self.create_expense_view),
                name='banktransaction_create_expense',
            ),
        ]
        return custom_urls + urls

    def create_expense_view(self, request, pk):
        """Создать расход из банковской транзакции.

        Два режима:
        1. Привязать к существующему входящему инвойсу (FACT/INCBLC/AV). После привязки
           сигнал auto_create_payment_on_bt_match создаёт Transaction и инвойс становится PAID.
        2. Создать новый FACT-инвойс (входящий от контрагента) с прикреплённым чеком/PDF,
           затем Transaction(PAYMENT, TRANSFER, COMPLETED) → статус PAID.

        Если у банковской транзакции уже есть чек из Revolut (receipt_file),
        он автоматически прикрепляется к новому инвойсу при создании FACT.
        """
        from core.models_billing import NewInvoice, InvoiceItem, ExpenseCategory
        from core.models import Company, Warehouse, Line, Carrier

        bank_trx = get_object_or_404(BankTransaction, pk=pk)
        expense_amount = abs(bank_trx.amount)

        # Поиск существующих кандидатов — входящих инвойсов на ту же сумму (±1€),
        # без привязанных банковских платежей, не CANCELLED/PAID.
        candidates = self._find_expense_invoice_candidates(bank_trx, expense_amount)

        categories = ExpenseCategory.objects.filter(is_active=True).order_by('order', 'name')
        companies = Company.objects.all().order_by('name')
        warehouses = Warehouse.objects.all().order_by('name')
        lines = Line.objects.all().order_by('name')
        carriers = Carrier.objects.all().order_by('name')

        # Авто-подбор поставщика по counterparty_name среди всех типов
        suggested_issuer_type = ''
        suggested_issuer_id = ''
        if bank_trx.counterparty_name:
            ct = bank_trx.counterparty_name.lower()
            for qs, ttype in (
                (warehouses, 'warehouse'), (lines, 'line'),
                (carriers, 'carrier'), (companies, 'company'),
            ):
                for ent in qs:
                    if ent.name and ent.name.lower() in ct:
                        suggested_issuer_type = ttype
                        suggested_issuer_id = ent.pk
                        break
                if suggested_issuer_type:
                    break

        default_description = bank_trx.description or bank_trx.counterparty_name or ''

        context = {
            **self.admin_site.each_context(request),
            'bank_trx': bank_trx,
            'expense_amount': f'{expense_amount:,.2f}',
            'candidates': candidates,
            'categories': categories,
            'companies': companies,
            'warehouses': warehouses,
            'lines': lines,
            'carriers': carriers,
            'suggested_issuer_type': suggested_issuer_type,
            'suggested_issuer_id': suggested_issuer_id,
            'default_description': default_description,
            'title': 'Создать расход',
            'opts': self.model._meta,
            'has_view_permission': True,
        }

        if request.method != 'POST':
            return render(request, 'admin/core/banktransaction/create_expense.html', context)

        action = request.POST.get('action', '')

        # ── Режим 1: Привязать к существующему инвойсу ─────────────────────────
        if action == 'link':
            invoice_id = request.POST.get('candidate_invoice_id')
            if not invoice_id:
                context['error'] = 'Выберите инвойс из списка кандидатов.'
                return render(request, 'admin/core/banktransaction/create_expense.html', context)
            try:
                invoice = NewInvoice.objects.get(pk=invoice_id)
            except NewInvoice.DoesNotExist:
                context['error'] = 'Выбранный инвойс не найден.'
                return render(request, 'admin/core/banktransaction/create_expense.html', context)

            bank_trx.matched_invoice = invoice
            bank_trx.reconciliation_note = f'Привязано вручную к {invoice.number}'
            bank_trx.save(update_fields=['matched_invoice', 'reconciliation_note', 'fetched_at'])

            messages.success(
                request,
                f'Транзакция привязана к инвойсу {invoice.number}. '
                f'Платёж будет создан автоматически, инвойс станет «Оплачен».',
            )
            return redirect('admin:core_banktransaction_changelist')

        # ── Режим 2: Создать новый FACT-инвойс ─────────────────────────────────
        category_id = request.POST.get('category')
        issuer_type = request.POST.get('issuer_type', '').strip()
        issuer_id = request.POST.get('issuer_id', '').strip()
        description = request.POST.get('description', '').strip()
        attachment = request.FILES.get('attachment')

        if not category_id:
            context['error'] = 'Выберите категорию расхода.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)
        if not issuer_type or not issuer_id:
            context['error'] = 'Укажите контрагента-выставителя счёта.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)

        try:
            category = ExpenseCategory.objects.get(pk=category_id)
        except ExpenseCategory.DoesNotExist:
            context['error'] = 'Категория не найдена.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)

        model_map = {'company': Company, 'warehouse': Warehouse, 'line': Line, 'carrier': Carrier}
        issuer_model = model_map.get(issuer_type)
        if not issuer_model:
            context['error'] = 'Неверный тип контрагента.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)
        try:
            issuer = issuer_model.objects.get(pk=issuer_id)
        except issuer_model.DoesNotExist:
            context['error'] = 'Контрагент не найден.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)

        try:
            with transaction.atomic():
                caromoto = Company.get_default()
                if not caromoto:
                    raise Company.DoesNotExist('Компания по умолчанию не найдена')

                invoice = NewInvoice(
                    document_type='INVOICE_FACT',
                    date=bank_trx.created_at.date(),
                    status='ISSUED',
                    category=category,
                    recipient_company=caromoto,
                    currency=bank_trx.currency or 'EUR',
                    notes=f'Авто-создано из банковской операции {bank_trx.external_id}',
                )
                setattr(invoice, f'issuer_{issuer_type}', issuer)
                if attachment:
                    invoice.attachment = attachment
                elif bank_trx.receipt_file:
                    # Автоматически прикрепляем чек, подгруженный из Revolut
                    from django.core.files.base import ContentFile
                    import os
                    bank_trx.receipt_file.open('rb')
                    try:
                        content = bank_trx.receipt_file.read()
                    finally:
                        bank_trx.receipt_file.close()
                    fname = os.path.basename(bank_trx.receipt_file.name)
                    invoice.attachment.save(fname, ContentFile(content), save=False)
                invoice.save()  # генерирует номер серии FACT

                item_desc = description or bank_trx.counterparty_name or f'Расход ({category.name})'
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=item_desc,
                    quantity=Decimal('1'),
                    unit_price=expense_amount,
                    total_price=expense_amount,
                    order=0,
                )
                invoice.calculate_totals()
                invoice.save(update_fields=['subtotal', 'total', 'updated_at'])

                bank_trx.matched_invoice = invoice
                bank_trx.reconciliation_note = f'FACT-расход создан: {category.name}'
                bank_trx.save(update_fields=['matched_invoice', 'reconciliation_note', 'fetched_at'])

                logger.info(
                    '[create_expense] BT %s → FACT %s (%s %s, issuer=%s:%s, attachment=%s)',
                    bank_trx.pk, invoice.number, expense_amount, bank_trx.currency,
                    issuer_type, issuer_id, bool(attachment),
                )

            messages.success(
                request,
                f'FACT-инвойс {invoice.number} создан на сумму {expense_amount:,.2f} '
                f'{bank_trx.currency}. Платёж будет зарегистрирован автоматически.',
            )
            return redirect('admin:core_banktransaction_changelist')

        except Company.DoesNotExist:
            context['error'] = 'Компания Caromoto Lithuania не найдена в базе.'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)
        except Exception as e:
            logger.error('[create_expense] Ошибка: %s', e, exc_info=True)
            context['error'] = f'Ошибка при создании расхода: {e}'
            return render(request, 'admin/core/banktransaction/create_expense.html', context)

    def _find_expense_invoice_candidates(self, bank_trx, expense_amount, tolerance=Decimal('1.00'), limit=10):
        """Найти входящие инвойсы — кандидаты на привязку к расходной банковской транзакции.

        Критерии:
        - Входящий инвойс (recipient_company=Caromoto, либо direction=INCOMING)
        - Не CANCELLED, не PAID
        - Не привязан к другой банковской транзакции
        - Остаток к оплате совпадает с суммой BT в пределах tolerance
        - Предпочтение сериям FACT / INVOICE_FACT
        - Сортировка: сначала совпадение по имени контрагента, затем по дате
        """
        from core.models_billing import NewInvoice
        from core.models import Company
        from django.db.models import F, Q

        caromoto = Company.get_default()
        if not caromoto:
            return []

        qs = NewInvoice.objects.filter(
            recipient_company=caromoto,
        ).exclude(
            status__in=['CANCELLED', 'PAID'],
        ).exclude(
            bank_transactions__isnull=False,
        )

        low = expense_amount - tolerance
        high = expense_amount + tolerance
        qs = qs.annotate(
            remaining=F('total') - F('paid_amount'),
        ).filter(
            remaining__gte=low,
            remaining__lte=high,
        )

        ct_name = (bank_trx.counterparty_name or '').strip().lower()
        qs = qs.select_related(
            'issuer_company', 'issuer_warehouse', 'issuer_line', 'issuer_carrier',
        ).order_by('-date')[:limit * 3]

        results = []
        for inv in qs:
            issuer = inv.issuer
            issuer_name = getattr(issuer, 'name', '') or ''
            name_match = 0
            if ct_name and issuer_name:
                low_issuer = issuer_name.lower()
                if low_issuer in ct_name or ct_name in low_issuer:
                    name_match = 2
                else:
                    parts = {p for p in low_issuer.split() if len(p) > 2}
                    if parts and any(p in ct_name for p in parts):
                        name_match = 1
            type_boost = 1 if inv.document_type == 'INVOICE_FACT' else 0
            inv.match_score = name_match * 10 + type_boost
            inv.issuer_display_name = issuer_name
            results.append(inv)

        results.sort(key=lambda i: (-i.match_score, -i.date.toordinal()))
        return results[:limit]

    @admin.action(description='Привязать к инвойсу')
    def link_to_invoice(self, request, queryset):
        """Привязка выбранных транзакций к конкретному инвойсу через промежуточную страницу"""
        from core.models_billing import NewInvoice, Transaction as BillingTransaction
        from core.models import Company

        eligible = queryset.filter(
            matched_invoice__isnull=True,
            reconciliation_skipped=False,
        )

        if not eligible.exists():
            messages.warning(request, 'Нет подходящих транзакций (все уже сопоставлены или пропущены).')
            return None

        if request.POST.get('confirm_link') == 'yes':
            invoice_id = request.POST.get('invoice_id')
            if not invoice_id:
                messages.error(request, 'Выберите инвойс.')
                return None
            try:
                invoice = NewInvoice.objects.get(pk=invoice_id)
            except NewInvoice.DoesNotExist:
                messages.error(request, 'Инвойс не найден.')
                return None

            company = Company.get_default()
            linked = 0
            for bt in eligible:
                with transaction.atomic():
                    bt.matched_invoice = invoice
                    bt.reconciliation_note = f'Привязано вручную к {invoice.number}'
                    bt.save(update_fields=['matched_invoice', 'reconciliation_note', 'fetched_at'])

                    payment_amount = min(abs(bt.amount), invoice.total - invoice.paid_amount)
                    if payment_amount > 0 and bt.amount > 0:
                        tx = BillingTransaction(
                            type='PAYMENT',
                            method='TRANSFER',
                            status='COMPLETED',
                            amount=payment_amount,
                            currency=invoice.currency or 'EUR',
                            invoice=invoice,
                            from_client=invoice.recipient_client,
                            to_company=company,
                            description=(
                                f'Ручная привязка банковского платежа '
                                f'{bt.counterparty_name} -> {invoice.number}'
                            ),
                            date=bt.created_at,
                        )
                        tx.save()
                        bt.matched_transaction = tx
                        bt.save(update_fields=['matched_transaction', 'fetched_at'])

                    linked += 1

            messages.success(request, f'{linked} транзакций привязано к {invoice.number}.')
            return None

        invoices = (
            NewInvoice.objects
            .exclude(status='CANCELLED')
            .select_related('recipient_client')
            .order_by('-date')[:200]
        )

        context = {
            **self.admin_site.each_context(request),
            'transactions': list(eligible),
            'invoices': invoices,
            'title': 'Привязать транзакции к инвойсу',
            'opts': self.model._meta,
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
        }
        return render(request, 'admin/core/banktransaction/link_to_invoice.html', context)

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

    @admin.action(description='Подгрузить чеки из Revolut')
    def download_revolut_receipts(self, request, queryset):
        """Массово подтягивает expenses/чеки из Revolut для выбранных транзакций."""
        from core.services.revolut_service import RevolutService, RevolutAPIError

        connections = {}
        for bt in queryset.select_related('connection'):
            if bt.connection.bank_type == 'REVOLUT' and bt.connection.is_active:
                connections.setdefault(bt.connection.pk, bt.connection)

        if not connections:
            messages.warning(request, 'Среди выбранных транзакций нет активных Revolut-подключений.')
            return None

        total_downloaded = 0
        total_updated = 0
        errors = 0

        for conn in connections.values():
            service = RevolutService(conn)
            try:
                updated = service.fetch_expenses(days=90)
                total_updated += updated
                downloaded = service.fetch_receipts_for_existing()
                total_downloaded += downloaded
            except RevolutAPIError as e:
                errors += 1
                if e.status_code == 403:
                    messages.error(
                        request,
                        f'{conn}: Expenses API недоступен (403). Проверьте, что план Grow/Scale/Enterprise.',
                    )
                else:
                    messages.error(request, f'{conn}: {e}')
            except Exception as e:
                errors += 1
                messages.error(request, f'{conn}: {e}')

        if total_downloaded or total_updated:
            messages.success(
                request,
                f'Revolut: обновлено {total_updated} expenses, скачано {total_downloaded} чеков.',
            )
        elif not errors:
            messages.info(request, 'Новых чеков из Revolut не найдено.')

    @admin.action(description='Создать расходы (массово)')
    def create_expenses_bulk(self, request, queryset):
        """Массовое создание расходов из банковских транзакций"""
        from core.models_billing import NewInvoice, InvoiceItem, ExpenseCategory
        from core.models import Company

        # Фильтруем только несопоставленные транзакции
        eligible = queryset.filter(
            matched_invoice__isnull=True,
            matched_transaction__isnull=True,
            reconciliation_skipped=False,
        )

        if not eligible.exists():
            messages.warning(request, 'Нет подходящих транзакций (все уже сопоставлены или пропущены).')
            return None

        categories = ExpenseCategory.objects.filter(is_active=True).order_by('order', 'name')

        # Подготовим данные для шаблона
        transactions_data = []
        total = Decimal('0')
        for trx in eligible:
            trx.expense_amount = f'{abs(trx.amount):,.2f}'
            transactions_data.append(trx)
            total += abs(trx.amount)

        # POST с подтверждением — создаём расходы
        if request.POST.get('confirm') == 'yes':
            category_id = request.POST.get('category')
            if not category_id:
                messages.error(request, 'Выберите категорию расхода.')
                return None

            try:
                category = ExpenseCategory.objects.get(pk=category_id)
                caromoto = Company.get_default()
                if not caromoto:
                    raise Company.DoesNotExist("Компания по умолчанию не найдена")
            except (ExpenseCategory.DoesNotExist, Company.DoesNotExist) as e:
                messages.error(request, f'Ошибка: {e}')
                return None

            created_count = 0
            errors = 0

            for bank_trx in eligible:
                try:
                    with transaction.atomic():
                        expense_amount = abs(bank_trx.amount)
                        invoice = NewInvoice(
                            document_type='INVOICE_FACT',
                            date=bank_trx.created_at.date(),
                            status='ISSUED',
                            category=category,
                            recipient_company=caromoto,
                            currency=bank_trx.currency or 'EUR',
                            notes=f'Авто-создано (массово) из банковской операции {bank_trx.external_id}',
                        )
                        invoice.save()

                        item_desc = bank_trx.description or bank_trx.counterparty_name or f'Расход ({category.name})'
                        InvoiceItem.objects.create(
                            invoice=invoice,
                            description=item_desc,
                            quantity=Decimal('1'),
                            unit_price=expense_amount,
                            total_price=expense_amount,
                            order=0,
                        )
                        invoice.calculate_totals()
                        invoice.save(update_fields=['subtotal', 'total', 'updated_at'])

                        bank_trx.matched_invoice = invoice
                        bank_trx.reconciliation_note = f'FACT-расход (массово): {category.name}'
                        bank_trx.save(update_fields=['matched_invoice', 'reconciliation_note', 'fetched_at'])
                        created_count += 1
                except Exception as e:
                    logger.error(f'[create_expenses_bulk] BankTrx {bank_trx.pk}: {e}')
                    errors += 1

            if created_count:
                messages.success(request, f'Создано {created_count} расходов ({category.name}).')
            if errors:
                messages.error(request, f'{errors} транзакций не удалось обработать.')
            return None

        # GET — показываем промежуточную страницу
        context = {
            **self.admin_site.each_context(request),
            'transactions': transactions_data,
            'total_amount': f'{total:,.2f}',
            'categories': categories,
            'title': 'Массовое создание расходов',
            'opts': self.model._meta,
        }
        return render(request, 'admin/core/banktransaction/create_expenses_bulk.html', context)
