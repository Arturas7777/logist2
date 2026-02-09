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

from .models_billing import NewInvoice, InvoiceItem, Transaction, ExpenseCategory
from .services.billing_service import BillingService


# ============================================================================
# АДМИНКА ДЛЯ КАТЕГОРИЙ РАСХОДОВ
# ============================================================================

@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    """Управление категориями расходов/доходов"""
    
    list_display = ('name', 'short_name', 'category_type', 'order', 'is_active')
    list_editable = ('short_name', 'order', 'is_active')
    list_filter = ('category_type', 'is_active')
    search_fields = ('name', 'short_name')
    ordering = ('order', 'name')


# ============================================================================
# ФИЛЬТР ПО НАПРАВЛЕНИЮ ИНВОЙСА
# ============================================================================

class InvoiceDirectionFilter(admin.SimpleListFilter):
    """Фильтр входящих/исходящих инвойсов"""
    title = 'Направление'
    parameter_name = 'direction'
    
    def lookups(self, request, model_admin):
        return [
            ('outgoing', 'Исходящие (мы выставили)'),
            ('incoming', 'Входящие (нам выставили)'),
        ]
    
    def queryset(self, request, queryset):
        if self.value() == 'outgoing':
            return queryset.filter(issuer_company_id=1)
        if self.value() == 'incoming':
            return queryset.filter(recipient_company_id=1)
        return queryset


# ============================================================================
# INLINE для позиций инвойса
# ============================================================================

class InvoiceItemInline(admin.TabularInline):
    """Inline для редактирования позиций инвойса"""
    
    model = InvoiceItem
    extra = 3  # 3 пустые строки для новых позиций
    fields = ('description', 'car', 'quantity', 'unit_price', 'total_price')
    readonly_fields = ('total_price',)
    autocomplete_fields = ['car']
    
    verbose_name = "Позиция инвойса"
    verbose_name_plural = "📦 Позиции инвойса (редактируемые)"
    
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

    change_form_template = 'admin/core/newinvoice/change_form.html'
    list_per_page = 50
    show_full_result_count = False
    
    class Media:
        css = {
            'all': ('admin/css/widgets.css',)
        }
        js = ('admin/js/SelectBox.js', 'admin/js/SelectFilter2.js',)
    
    list_display = (
        'number_display',
        'direction_badge',
        'category_display',
        'notes_display',
        'recipient_display',
        'total_display',
        'paid_amount_display',
        'remaining_display',
        'status_display',
        'actions_display'
    )
    
    list_filter = (
        InvoiceDirectionFilter,
        'status',
        'category',
        'date',
        'recipient_client',
    )
    
    search_fields = (
        'number',
        'recipient_client__name',
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
    )
    
    fieldsets = (
        ('📋 Основная информация', {
            'fields': (
                ('date', 'due_date', 'status'),
                'category',
            )
        }),
        ('🏢 Выставитель инвойса', {
            'fields': ('issuer_company',),
            'description': 'По умолчанию: Caromoto Lithuania. Для входящих инвойсов — укажите контрагента ниже.'
        }),
        ('👤 Получатель инвойса', {
            'fields': ('recipient_client',),
        }),
        ('🚗 Автомобили', {
            'fields': ('cars',),
            'description': 'Выберите автомобили - позиции создадутся автоматически из их услуг. Для общих расходов (аренда и т.д.) оставьте пустым.'
        }),
        ('💰 Финансы', {
            'fields': (
                ('subtotal', 'discount', 'tax'),
                ('total', 'paid_amount'),
            ),
            'classes': ('collapse',),
        }),
        ('📎 Дополнительно', {
            'fields': ('notes', 'attachment'),
        }),
        ('⚙️ Прочие получатели (если не клиент)', {
            'fields': (
                ('recipient_warehouse', 'recipient_line'),
                ('recipient_carrier', 'recipient_company'),
            ),
            'classes': ('collapse',),
            'description': 'Для входящих инвойсов: укажите Caromoto Lithuania как получателя-компанию'
        }),
        ('⚙️ Прочие выставители (если не компания)', {
            'fields': (
                ('issuer_warehouse', 'issuer_line', 'issuer_carrier'),
            ),
            'classes': ('collapse',),
        }),
    )
    
    inlines = [InvoiceItemInline]
    
    filter_horizontal = ('cars',)
    
    actions = ['mark_as_issued', 'mark_as_paid', 'cancel_invoices', 'regenerate_items']

    def add_view(self, request, form_url='', extra_context=None):
        """Кастомная обработка добавления инвойса"""
        from core.models import Company, Client, Car
        
        if request.method == 'POST':
            return self._handle_custom_form(request, None)
        
        extra_context = self._get_extra_context(None, extra_context)
        return super().add_view(request, form_url, extra_context)
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Кастомная обработка изменения инвойса"""
        if request.method == 'POST':
            return self._handle_custom_form(request, object_id)
        
        extra_context = self._get_extra_context(object_id, extra_context)
        return super().change_view(request, object_id, form_url, extra_context)
    
    def _get_extra_context(self, object_id, extra_context=None):
        """Получаем контекст для шаблона"""
        from core.models import Company, Client, Car
        
        extra_context = extra_context or {}
        
        # Получаем queryset для всех полей
        extra_context['companies'] = Company.objects.all().order_by('name')
        extra_context['clients'] = Client.objects.all().order_by('name')
        extra_context['cars'] = Car.objects.all().select_related('client').order_by('-id')[:500]
        extra_context['expense_categories'] = ExpenseCategory.objects.filter(is_active=True).order_by('order', 'name')
        
        # Определяем Caromoto Lithuania по умолчанию
        try:
            caromoto = Company.objects.get(name="Caromoto Lithuania")
            extra_context['default_company_id'] = caromoto.pk
        except Company.DoesNotExist:
            extra_context['default_company_id'] = None
        
        # Получаем ID выбранных машин для редактирования
        selected_car_ids = []
        if object_id:
            try:
                invoice = NewInvoice.objects.get(pk=object_id)
                selected_car_ids = list(invoice.cars.values_list('pk', flat=True))
                # Данные для pivot-таблицы
                extra_context['pivot_table'] = invoice.get_items_pivot_table()
            except NewInvoice.DoesNotExist:
                pass
        extra_context['selected_car_ids'] = selected_car_ids
        
        return extra_context
    
    def _handle_custom_form(self, request, object_id):
        """Обрабатываем кастомную форму"""
        from core.models import Company, Client, Car
        from django.utils import timezone
        from datetime import datetime
        
        try:
            # Получаем или создаем инвойс
            if object_id:
                invoice = NewInvoice.objects.get(pk=object_id)
            else:
                invoice = NewInvoice()
            
            # Заполняем поля из POST
            date_str = request.POST.get('date')
            if date_str:
                invoice.date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            due_date_str = request.POST.get('due_date')
            if due_date_str:
                invoice.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            else:
                invoice.due_date = None
            
            invoice.status = request.POST.get('status', 'ISSUED')
            invoice.notes = request.POST.get('notes', '')
            
            # Категория
            category_id = request.POST.get('category')
            if category_id:
                invoice.category = ExpenseCategory.objects.filter(pk=category_id).first()
            else:
                invoice.category = None
            
            # Вложение
            if 'attachment' in request.FILES:
                invoice.attachment = request.FILES['attachment']
            
            # Очищаем все поля выставителя и получателя перед установкой новых
            invoice.issuer_company = None
            invoice.issuer_warehouse = None
            invoice.issuer_line = None
            invoice.issuer_carrier = None
            invoice.recipient_client = None
            invoice.recipient_company = None
            invoice.recipient_warehouse = None
            invoice.recipient_line = None
            invoice.recipient_carrier = None
            
            # Выставитель (парсим формат "type_id", например "company_123")
            issuer_value = request.POST.get('issuer', '')
            if issuer_value and '_' in issuer_value:
                issuer_type, issuer_id = issuer_value.rsplit('_', 1)
                if issuer_type == 'company':
                    invoice.issuer_company = Company.objects.get(pk=issuer_id)
                elif issuer_type == 'warehouse':
                    from core.models import Warehouse
                    invoice.issuer_warehouse = Warehouse.objects.get(pk=issuer_id)
                elif issuer_type == 'line':
                    from core.models import Line
                    invoice.issuer_line = Line.objects.get(pk=issuer_id)
                elif issuer_type == 'carrier':
                    from core.models import Carrier
                    invoice.issuer_carrier = Carrier.objects.get(pk=issuer_id)
            else:
                # По умолчанию Caromoto Lithuania
                try:
                    invoice.issuer_company = Company.objects.get(name="Caromoto Lithuania")
                except Company.DoesNotExist:
                    pass
            
            # Получатель (парсим формат "type_id", например "client_456")
            recipient_value = request.POST.get('recipient', '')
            if recipient_value and '_' in recipient_value:
                recipient_type, recipient_id = recipient_value.rsplit('_', 1)
                if recipient_type == 'client':
                    invoice.recipient_client = Client.objects.get(pk=recipient_id)
                elif recipient_type == 'company':
                    invoice.recipient_company = Company.objects.get(pk=recipient_id)
                elif recipient_type == 'warehouse':
                    from core.models import Warehouse
                    invoice.recipient_warehouse = Warehouse.objects.get(pk=recipient_id)
                elif recipient_type == 'line':
                    from core.models import Line
                    invoice.recipient_line = Line.objects.get(pk=recipient_id)
                elif recipient_type == 'carrier':
                    from core.models import Carrier
                    invoice.recipient_carrier = Carrier.objects.get(pk=recipient_id)
            
            # Сохраняем инвойс
            invoice.save()
            
            # Обрабатываем автомобили (ManyToMany)
            car_ids = request.POST.getlist('cars')
            if car_ids:
                cars = Car.objects.filter(pk__in=car_ids)
                invoice.cars.set(cars)
                # Генерируем позиции из услуг автомобилей
                invoice.regenerate_items_from_cars()
                messages.success(request, f'✅ Инвойс {invoice.number} сохранен! Создано {invoice.items.count()} позиций.')
            else:
                invoice.cars.clear()
                messages.success(request, f'✅ Инвойс {invoice.number} сохранен!')
            
            # Определяем куда редиректить
            if '_save' in request.POST:
                return redirect('admin:core_newinvoice_changelist')
            elif '_continue' in request.POST:
                return redirect('admin:core_newinvoice_change', invoice.pk)
            elif '_addanother' in request.POST:
                return redirect('admin:core_newinvoice_add')
            else:
                return redirect('admin:core_newinvoice_changelist')
                
        except Exception as e:
            messages.error(request, f'Ошибка при сохранении инвойса: {str(e)}')
            import traceback
            traceback.print_exc()
            
            if object_id:
                return redirect('admin:core_newinvoice_change', object_id)
            else:
                return redirect('admin:core_newinvoice_add')
    
    def save_model(self, request, obj, form, change):
        """Сохраняем инвойс и автоматически генерируем позиции из автомобилей"""
        # Автоматически устанавливаем Caromoto Lithuania как выставителя по умолчанию
        if not obj.issuer_company and not obj.issuer_warehouse and not obj.issuer_line and not obj.issuer_carrier:
            try:
                from core.models import Company
                caromoto = Company.objects.get(name="Caromoto Lithuania")
                obj.issuer_company = caromoto
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"Автоматически установлена компания Caromoto Lithuania как выставитель инвойса {obj.number}")
            except Company.DoesNotExist:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning("Компания Caromoto Lithuania не найдена в базе данных")
        
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
    
    actions = ['mark_as_issued', 'mark_as_paid', 'cancel_invoices', 'regenerate_items', 'push_to_sitepro']

    # ========================================================================
    # ОТОБРАЖЕНИЕ ПОЛЕЙ В СПИСКЕ
    # ========================================================================
    
    def number_display(self, obj):
        """Номер инвойса с ссылкой"""
        url = reverse('admin:core_newinvoice_change', args=[obj.pk])
        return format_html('<a href="{}" style="font-weight: bold;">{}</a>', url, obj.number)
    number_display.short_description = 'Номер'
    number_display.admin_order_field = 'number'
    
    def direction_badge(self, obj):
        """Бейдж направления: Исходящий / Входящий / Внутренний"""
        direction = obj.direction
        styles = {
            'OUTGOING': ('background:#007bff;', '↗ Исх'),
            'INCOMING': ('background:#fd7e14;', '↙ Вх'),
            'INTERNAL': ('background:#6c757d;', '↔ Внутр'),
        }
        style, label = styles.get(direction, ('background:#6c757d;', '?'))
        return format_html(
            '<span style="{}color:white;padding:2px 6px;border-radius:3px;font-size:0.85em;white-space:nowrap;">{}</span>',
            style, label
        )
    direction_badge.short_description = 'Напр.'
    
    def category_display(self, obj):
        """Категория расхода/дохода"""
        if obj.category:
            return format_html(
                '<span style="color:#555;" title="{}">{}</span>',
                obj.category.get_category_type_display(),
                obj.category.short_name or obj.category.name
            )
        return format_html('<span style="color:#ccc;">—</span>')
    category_display.short_description = 'Кат.'
    category_display.admin_order_field = 'category'
    
    def notes_display(self, obj):
        """Примечания инвойса"""
        if obj.notes:
            # Обрезаем длинный текст до 50 символов
            notes_text = obj.notes[:50] + '...' if len(obj.notes) > 50 else obj.notes
            return format_html('<span title="{}">{}</span>', obj.notes, notes_text)
        return format_html('<span style="color: #999;">—</span>')
    notes_display.short_description = 'Примечания'
    notes_display.admin_order_field = 'notes'
    
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
    
    def mark_as_issued(self, request, queryset):
        """Пометить как выставленные"""
        updated = 0
        for invoice in queryset:
            if invoice.status not in ('ISSUED', 'PAID', 'CANCELLED'):
                invoice.status = 'ISSUED'
                invoice.save(update_fields=['status', 'updated_at'])
                updated += 1
        
        self.message_user(request, f'Выставлено: {updated} инвойсов', messages.SUCCESS)
    mark_as_issued.short_description = "📤 Пометить как выставленные"
    
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
    
    def push_to_sitepro(self, request, queryset):
        """Отправить выбранные инвойсы в site.pro (бухгалтерия)"""
        from .models_accounting import SiteProConnection
        
        # Находим активное подключение к site.pro
        connection = SiteProConnection.objects.filter(is_active=True).first()
        if not connection:
            self.message_user(
                request,
                'Нет активного подключения к site.pro. '
                'Настройте подключение в разделе "Подключения site.pro".',
                messages.ERROR
            )
            return
        
        from .services.sitepro_service import SiteProService, SiteProAPIError
        service = SiteProService(connection)
        
        # Отправляем только выставленные инвойсы
        eligible = queryset.filter(status__in=['ISSUED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE'])
        if not eligible.exists():
            self.message_user(
                request,
                'Выберите инвойсы со статусом "Выставлен", "Частично оплачен", '
                '"Оплачен" или "Просрочен".',
                messages.WARNING
            )
            return
        
        result = service.push_invoices(eligible)
        
        if result['sent'] > 0:
            self.message_user(
                request,
                f'Отправлено в site.pro: {result["sent"]} инвойсов',
                messages.SUCCESS
            )
        if result['skipped'] > 0:
            self.message_user(
                request,
                f'Пропущено (уже отправлены): {result["skipped"]}',
                messages.INFO
            )
        if result['failed'] > 0:
            error_details = '; '.join(result['errors'][:3])
            self.message_user(
                request,
                f'Ошибок: {result["failed"]}. {error_details}',
                messages.ERROR
            )
    push_to_sitepro.short_description = "📤 Отправить в site.pro (бухгалтерия)"
    
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
        
        # Получаем баланс клиента если есть
        client_balance = None
        if invoice.recipient_client:
            client_balance = invoice.recipient_client.balance
        
        context = {
            'invoice': invoice,
            'remaining': invoice.remaining_amount,
            'client_balance': client_balance,
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
    list_per_page = 50
    show_full_result_count = False

    list_display = (
        'number_display',
        'date',
        'type_display',
        'method_display',
        'sender_display',
        'recipient_display',
        'amount_display',
        'trx_category_display',
        'status_display',
        'invoice_link',
    )
    
    list_filter = (
        'type',
        'method',
        'status',
        'category',
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
                'category',
                'attachment',
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
    
    def trx_category_display(self, obj):
        """Категория транзакции"""
        if obj.category:
            return format_html(
                '<span style="color:#555;">{}</span>',
                obj.category.short_name or obj.category.name
            )
        return format_html('<span style="color:#ccc;">—</span>')
    trx_category_display.short_description = 'Кат.'
    trx_category_display.admin_order_field = 'category'
    
    def invoice_link(self, obj):
        """Ссылка на инвойс"""
        if obj.invoice:
            url = reverse('admin:core_newinvoice_change', args=[obj.invoice.pk])
            return format_html('<a href="{}">{}</a>', url, obj.invoice.number)
        return '-'
    invoice_link.short_description = 'Инвойс'
    
    def save_model(self, request, obj, form, change):
        """Автозаполнение категории из связанного инвойса"""
        if not obj.category and obj.invoice and obj.invoice.category:
            obj.category = obj.invoice.category
        super().save_model(request, obj, form, change)
    
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
