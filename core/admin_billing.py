"""
Админка для новой системы инвойсов, платежей и балансов
=========================================================

Простой и интуитивный интерфейс для работы с:
- Инвойсами
- Транзакциями
- Балансами

Авторы: AI Assistant
Дата: 30 сентября 2025
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum, Q
from django.utils import timezone
from decimal import Decimal

from .models_billing import NewInvoice, InvoiceItem, Transaction
from .services.billing_service import BillingService


# ============================================================================
# INLINE для позиций инвойса
# ============================================================================

class InvoiceItemInline(admin.TabularInline):
    """Inline для редактирования позиций инвойса"""
    
    model = InvoiceItem
    extra = 1
    fields = ('description', 'car', 'quantity', 'unit_price', 'total_price')
    readonly_fields = ('total_price',)
    
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Убираем help_text для компактности
        for field in formset.form.base_fields.values():
            field.help_text = ''
        return formset


# ============================================================================
# АДМИНКА ДЛЯ ИНВОЙСОВ
# ============================================================================

@admin.register(NewInvoice)
class NewInvoiceAdmin(admin.ModelAdmin):
    """
    Простая и понятная админка для инвойсов
    """
    
    class Media:
        css = {
            'all': ('admin/css/widgets.css', 'css/invoice_admin.css',)
        }
        js = ('admin/js/SelectBox.js', 'admin/js/SelectFilter2.js',)
    
    list_display = (
        'number_display',
        'date',
        'issuer_display',
        'recipient_display',
        'total_display',
        'paid_amount_display',
        'remaining_display',
        'status_display',
        'due_date',
        'actions_display'
    )
    
    list_filter = (
        'status',
        'date',
        'due_date',
        'issuer_company',
        'issuer_warehouse',
        'issuer_line',
        'issuer_carrier',
    )
    
    search_fields = (
        'number',
        'recipient_client__name',
        'recipient_warehouse__name',
        'recipient_line__name',
        'recipient_carrier__name',
        'recipient_company__name',
        'issuer_company__name',
        'issuer_warehouse__name',
        'issuer_line__name',
        'issuer_carrier__name',
        'notes',
    )
    
    readonly_fields = (
        'number',
        'subtotal',
        'total',
        'paid_amount',
        'created_at',
        'updated_at',
        'created_by',
        'remaining_amount_display',
        'status_info_display',
        'payment_history_display',
    )
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'number',
                'date',
                'due_date',
                'status',
            )
        }),
        ('Кто выставил (укажите ОДНО)', {
            'fields': (
                ('issuer_company', 'issuer_warehouse', 'issuer_line', 'issuer_carrier'),
            ),
            'classes': ('issuer-fields',),
        }),
        ('Кому выставлен (укажите ОДНО)', {
            'fields': (
                ('recipient_company', 'recipient_client', 'recipient_warehouse', 'recipient_line', 'recipient_carrier'),
            ),
            'classes': ('recipient-fields',),
        }),
        ('🚗 Выберите автомобили (позиции создадутся автоматически!)', {
            'fields': ('cars',),
        }),
        ('Финансы', {
            'fields': (
                'subtotal',
                'discount',
                'tax',
                'total',
                'paid_amount',
                'remaining_amount_display',
                'status_info_display',
            )
        }),
        ('История платежей', {
            'fields': ('payment_history_display',),
            'classes': ('collapse',),
        }),
        ('Дополнительно', {
            'fields': (
                'notes',
                'created_at',
                'updated_at',
                'created_by',
            ),
            'classes': ('collapse',),
        }),
    )
    
    inlines = [InvoiceItemInline]
    
    filter_horizontal = ('cars',)  # Удобный виджет для выбора множества авто
    
    actions = ['mark_as_paid', 'cancel_invoices', 'export_to_pdf', 'regenerate_items']
    
    def save_model(self, request, obj, form, change):
        """Сохраняем инвойс и автоматически генерируем позиции из автомобилей"""
        # Сначала сохраняем объект
        super().save_model(request, obj, form, change)
        
        # Сохраняем связь cars (ManyToMany сохраняется в save_related)
    
    def save_related(self, request, form, formsets, change):
        """После сохранения ManyToMany создаем позиции из автомобилей"""
        # Сначала сохраняем все связи
        super().save_related(request, form, formsets, change)
        
        # Если выбраны автомобили - генерируем позиции
        if form.instance.cars.exists():
            form.instance.regenerate_items_from_cars()
            messages.success(request, f"✅ Автоматически создано {form.instance.items.count()} позиций из услуг автомобилей!")
    
    actions = ['mark_as_paid', 'cancel_invoices', 'export_to_pdf', 'regenerate_items']
    
    # ========================================================================
    # ОТОБРАЖЕНИЕ ПОЛЕЙ В СПИСКЕ
    # ========================================================================
    
    def number_display(self, obj):
        """Номер инвойса с ссылкой"""
        url = reverse('admin:core_newinvoice_change', args=[obj.pk])
        return format_html('<a href="{}" style="font-weight: bold;">{}</a>', url, obj.number)
    number_display.short_description = 'Номер'
    number_display.admin_order_field = 'number'
    
    def issuer_display(self, obj):
        """Выставитель"""
        issuer = obj.issuer
        if issuer:
            return format_html(
                '<strong>{}</strong>',
                str(issuer)
            )
        return '-'
    issuer_display.short_description = 'Выставитель'
    
    def recipient_display(self, obj):
        """Получатель"""
        recipient = obj.recipient
        if recipient:
            return format_html(
                '<strong>{}</strong>',
                str(recipient)
            )
        return '-'
    recipient_display.short_description = 'Получатель'
    
    def total_display(self, obj):
        """Итого с форматированием"""
        amount = f"{obj.total:.2f}"
        return format_html(
            '<span style="font-weight: bold; font-size: 1.1em;">{}</span>',
            amount
        )
    total_display.short_description = 'Итого'
    total_display.admin_order_field = 'total'
    
    def paid_amount_display(self, obj):
        """Оплачено"""
        if obj.paid_amount > 0:
            color = '#28a745' if obj.paid_amount >= obj.total else '#ffc107'
            amount = f"{obj.paid_amount:.2f}"
            return format_html(
                '<span style="color: {}; font-weight: bold;">{}</span>',
                color,
                amount
            )
        return format_html('<span style="color: #999;">0.00</span>')
    paid_amount_display.short_description = 'Оплачено'
    paid_amount_display.admin_order_field = 'paid_amount'
    
    def remaining_display(self, obj):
        """Остаток"""
        remaining = obj.remaining_amount
        if remaining > 0:
            amount = f"{remaining:.2f}"
            return format_html(
                '<span style="color: #dc3545; font-weight: bold;">{}</span>',
                amount
            )
        return format_html('<span style="color: #28a745;">✓</span>')
    remaining_display.short_description = 'Остаток'
    
    def status_display(self, obj):
        """Статус с цветом"""
        colors = {
            'DRAFT': '#6c757d',
            'ISSUED': '#007bff',
            'PARTIALLY_PAID': '#ffc107',
            'PAID': '#28a745',
            'OVERDUE': '#dc3545',
            'CANCELLED': '#6c757d',
        }
        color = colors.get(obj.status, '#6c757d')
        
        # Добавляем предупреждение для просроченных
        icon = ''
        if obj.is_overdue:
            icon = '⚠ '
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 0.9em;">{}{}</span>',
            color,
            icon,
            obj.get_status_display()
        )
    status_display.short_description = 'Статус'
    status_display.admin_order_field = 'status'
    
    def actions_display(self, obj):
        """Быстрые действия"""
        if obj.status in ['ISSUED', 'PARTIALLY_PAID', 'OVERDUE']:
            pay_url = reverse('admin:pay_invoice', args=[obj.pk])
            return format_html(
                '<a href="{}" class="button" style="background: #28a745; color: white; padding: 3px 10px; border-radius: 3px; text-decoration: none;">💳 Оплатить</a>',
                pay_url
            )
        elif obj.status == 'PAID':
            return format_html('<span style="color: #28a745;">✓ Оплачен</span>')
        return '-'
    actions_display.short_description = 'Действия'
    
    # ========================================================================
    # ДОПОЛНИТЕЛЬНЫЕ READONLY ПОЛЯ
    # ========================================================================
    
    def remaining_amount_display(self, obj):
        """Остаток к оплате"""
        remaining = obj.remaining_amount
        if remaining > 0:
            amount = f"{remaining:.2f}"
            return format_html(
                '<span style="font-size: 1.2em; color: #dc3545; font-weight: bold;">{}</span>',
                amount
            )
        return format_html('<span style="color: #28a745; font-size: 1.2em;">✓ Полностью оплачен</span>')
    remaining_amount_display.short_description = 'Остаток к оплате'
    
    def status_info_display(self, obj):
        """Информация о статусе"""
        info = []
        
        if obj.is_overdue:
            days_overdue = abs(obj.days_until_due)
            info.append(format_html(
                '<div style="background: #fff3cd; border-left: 4px solid #dc3545; padding: 10px; margin: 5px 0;">'
                '<strong>⚠ ПРОСРОЧЕН</strong><br>'
                'Просрочка: {} дн.'
                '</div>',
                days_overdue
            ))
        elif obj.days_until_due <= 3 and obj.status not in ['PAID', 'CANCELLED']:
            info.append(format_html(
                '<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 5px 0;">'
                '<strong>⚠ СРОЧНО</strong><br>'
                'До срока оплаты: {} дн.'
                '</div>',
                obj.days_until_due
            ))
        
        if obj.paid_amount > obj.total:
            overpayment = obj.paid_amount - obj.total
            overpayment_str = f"{overpayment:.2f}"
            info.append(format_html(
                '<div style="background: #d1ecf1; border-left: 4px solid #17a2b8; padding: 10px; margin: 5px 0;">'
                '<strong>ℹ ПЕРЕПЛАТА</strong><br>'
                'Переплачено: {}'
                '</div>',
                overpayment_str
            ))
        
        return format_html(''.join(info)) if info else 'Нет предупреждений'
    status_info_display.short_description = 'Статус и предупреждения'
    
    def payment_history_display(self, obj):
        """История платежей"""
        transactions = obj.transactions.all().order_by('-date')
        
        if not transactions:
            return format_html('<p style="color: #999;">Платежей еще не было</p>')
        
        html = '<table style="width: 100%; border-collapse: collapse;">'
        html += '<tr style="background: #f5f5f5;"><th style="padding: 8px; text-align: left;">Дата</th><th style="padding: 8px; text-align: left;">Номер</th><th style="padding: 8px; text-align: left;">Тип</th><th style="padding: 8px; text-align: left;">Способ</th><th style="padding: 8px; text-align: right;">Сумма</th></tr>'
        
        for trx in transactions:
            color = '#28a745' if trx.type == 'PAYMENT' else '#dc3545'
            trx_amount = f"{trx.amount:.2f}"
            html += f'''
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px;">{trx.date.strftime("%d.%m.%Y %H:%M")}</td>
                <td style="padding: 8px;">{trx.number}</td>
                <td style="padding: 8px;">{trx.get_type_display()}</td>
                <td style="padding: 8px;">{trx.get_method_display()}</td>
                <td style="padding: 8px; text-align: right; color: {color}; font-weight: bold;">{trx_amount}</td>
            </tr>
            '''
        
        html += '</table>'
        return format_html(html)
    payment_history_display.short_description = 'История платежей'
    
    # ========================================================================
    # ДЕЙСТВИЯ
    # ========================================================================
    
    def mark_as_paid(self, request, queryset):
        """Пометить как оплаченные"""
        updated = 0
        for invoice in queryset:
            if invoice.status != 'PAID':
                invoice.paid_amount = invoice.total
                invoice.status = 'PAID'
                invoice.save()
                updated += 1
        
        self.message_user(request, f'Помечено как оплаченные: {updated} инвойсов', messages.SUCCESS)
    mark_as_paid.short_description = "✓ Пометить как оплаченные"
    
    def cancel_invoices(self, request, queryset):
        """Отменить инвойсы"""
        cancelled = 0
        errors = 0
        
        for invoice in queryset:
            try:
                BillingService.cancel_invoice(invoice, reason="Массовая отмена через админку")
                cancelled += 1
            except ValueError as e:
                errors += 1
        
        if cancelled > 0:
            self.message_user(request, f'Отменено: {cancelled} инвойсов', messages.SUCCESS)
        if errors > 0:
            self.message_user(request, f'Ошибок: {errors} инвойсов (возможно, уже были платежи)', messages.WARNING)
    cancel_invoices.short_description = "✗ Отменить инвойсы"
    
    def export_to_pdf(self, request, queryset):
        """Экспорт в PDF (заглушка)"""
        self.message_user(request, 'Экспорт в PDF будет реализован в следующей версии', messages.INFO)
    export_to_pdf.short_description = "📄 Экспорт в PDF"
    
    def regenerate_items(self, request, queryset):
        """Пересоздать позиции из автомобилей"""
        count = 0
        for invoice in queryset:
            if invoice.cars.exists():
                invoice.regenerate_items_from_cars()
                count += 1
        
        if count > 0:
            self.message_user(request, f'✅ Позиции пересозданы для {count} инвойсов', messages.SUCCESS)
        else:
            self.message_user(request, '⚠ Выберите инвойсы с автомобилями', messages.WARNING)
    regenerate_items.short_description = "🔄 Пересоздать позиции из автомобилей"
    
    # ========================================================================
    # КАСТОМНЫЕ УРЛЫ
    # ========================================================================
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:invoice_id>/pay/', self.admin_site.admin_view(self.pay_invoice_view), name='pay_invoice'),
        ]
        return custom_urls + urls
    
    def pay_invoice_view(self, request, invoice_id):
        """Форма оплаты инвойса"""
        invoice = NewInvoice.objects.get(pk=invoice_id)
        
        if request.method == 'POST':
            try:
                amount = Decimal(request.POST.get('amount', 0))
                method = request.POST.get('method', 'CASH')
                description = request.POST.get('description', '')
                
                # Определяем плательщика
                payer = invoice.recipient
                
                result = BillingService.pay_invoice(
                    invoice=invoice,
                    amount=amount,
                    method=method,
                    payer=payer,
                    description=description,
                    created_by=request.user
                )
                
                messages.success(request, f'Платеж успешно проведен! Транзакция: {result["transaction"].number}')
                
                if result['overpayment'] > 0:
                    messages.warning(request, f'Внимание: переплата {result["overpayment"]:.2f}')
                
                return redirect('admin:core_newinvoice_change', invoice_id)
                
            except Exception as e:
                messages.error(request, f'Ошибка при проведении платежа: {str(e)}')
        
        context = {
            'invoice': invoice,
            'remaining': invoice.remaining_amount,
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request),
        }
        
        return render(request, 'admin/invoice_pay.html', context)


# ============================================================================
# АДМИНКА ДЛЯ ТРАНЗАКЦИЙ
# ============================================================================

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """
    Простая админка для транзакций
    """
    
    list_display = (
        'number_display',
        'date',
        'type_display',
        'method_display',
        'sender_display',
        'recipient_display',
        'amount_display',
        'status_display',
        'invoice_link',
    )
    
    list_filter = (
        'type',
        'method',
        'status',
        'date',
    )
    
    search_fields = (
        'number',
        'description',
        'invoice__number',
    )
    
    readonly_fields = (
        'number',
        'date',
        'created_at',
        'created_by',
        'sender_info_display',
        'recipient_info_display',
    )
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'number',
                'date',
                'type',
                'method',
                'status',
            )
        }),
        ('Отправитель', {
            'fields': (
                ('from_client', 'from_warehouse'),
                ('from_line', 'from_carrier', 'from_company'),
                'sender_info_display',
            )
        }),
        ('Получатель', {
            'fields': (
                ('to_client', 'to_warehouse'),
                ('to_line', 'to_carrier', 'to_company'),
                'recipient_info_display',
            )
        }),
        ('Детали', {
            'fields': (
                'amount',
                'invoice',
                'description',
            )
        }),
        ('Метаданные', {
            'fields': (
                'created_at',
                'created_by',
            ),
            'classes': ('collapse',),
        }),
    )
    
    # ========================================================================
    # ОТОБРАЖЕНИЕ ПОЛЕЙ
    # ========================================================================
    
    def number_display(self, obj):
        """Номер транзакции"""
        return format_html('<strong>{}</strong>', obj.number)
    number_display.short_description = 'Номер'
    number_display.admin_order_field = 'number'
    
    def type_display(self, obj):
        """Тип с иконкой"""
        icons = {
            'PAYMENT': '💳',
            'REFUND': '↩',
            'ADJUSTMENT': '⚙',
            'TRANSFER': '↔',
            'BALANCE_TOPUP': '💰',
        }
        icon = icons.get(obj.type, '')
        return format_html('{} {}', icon, obj.get_type_display())
    type_display.short_description = 'Тип'
    type_display.admin_order_field = 'type'
    
    def method_display(self, obj):
        """Способ оплаты"""
        return obj.get_method_display()
    method_display.short_description = 'Способ'
    method_display.admin_order_field = 'method'
    
    def sender_display(self, obj):
        """Отправитель"""
        sender = obj.sender
        if sender:
            return format_html(
                '<strong>{}</strong>',
                str(sender)
            )
        return '-'
    sender_display.short_description = 'Отправитель'
    
    def recipient_display(self, obj):
        """Получатель"""
        recipient = obj.recipient
        if recipient:
            return format_html(
                '<strong>{}</strong>',
                str(recipient)
            )
        return '-'
    recipient_display.short_description = 'Получатель'
    
    def amount_display(self, obj):
        """Сумма с форматированием"""
        color = '#28a745' if obj.type == 'PAYMENT' else '#dc3545' if obj.type == 'REFUND' else '#007bff'
        amount = f"{obj.amount:.2f}"
        return format_html(
            '<span style="color: {}; font-weight: bold; font-size: 1.1em;">{}</span>',
            color,
            amount
        )
    amount_display.short_description = 'Сумма'
    amount_display.admin_order_field = 'amount'
    
    def status_display(self, obj):
        """Статус"""
        colors = {
            'PENDING': '#ffc107',
            'COMPLETED': '#28a745',
            'FAILED': '#dc3545',
            'CANCELLED': '#6c757d',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.85em;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_display.short_description = 'Статус'
    status_display.admin_order_field = 'status'
    
    def invoice_link(self, obj):
        """Ссылка на инвойс"""
        if obj.invoice:
            url = reverse('admin:core_newinvoice_change', args=[obj.invoice.pk])
            return format_html('<a href="{}">{}</a>', url, obj.invoice.number)
        return '-'
    invoice_link.short_description = 'Инвойс'
    
    def sender_info_display(self, obj):
        """Детальная информация об отправителе"""
        sender = obj.sender
        if not sender:
            return 'Не указан'
        
        info = f'<strong>{sender}</strong><br>'
        info += f'Тип: {sender.__class__.__name__}<br>'
        
        if hasattr(sender, 'balance'):
            balance_str = f"{sender.balance:.2f}"
            info += f'Баланс: {balance_str}'
        
        return format_html(info)
    sender_info_display.short_description = 'Информация об отправителе'
    
    def recipient_info_display(self, obj):
        """Детальная информация о получателе"""
        recipient = obj.recipient
        if not recipient:
            return 'Не указан'
        
        info = f'<strong>{recipient}</strong><br>'
        info += f'Тип: {recipient.__class__.__name__}<br>'
        
        if hasattr(recipient, 'balance'):
            balance_str = f"{recipient.balance:.2f}"
            info += f'Баланс: {balance_str}'
        
        return format_html(info)
    recipient_info_display.short_description = 'Информация о получателе'


# ============================================================================
# InvoiceItem НЕ регистрируется отдельно - только inline в NewInvoice
# ============================================================================
# Позиции создаются автоматически из услуг поставщиков (CarService)
