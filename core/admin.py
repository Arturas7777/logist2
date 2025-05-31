from django.contrib import admin
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from .models import Client, Warehouse, Car, Invoice, Payment, Container, Declaration, Accounting, Line
import json
import logging
logger = logging.getLogger('django')

# Фрагмент admin.py для CarInline

# Фрагмент admin.py для CarInline
class CarInline(admin.TabularInline):
    model = Car
    extra = 1
    can_delete = True
    fields = ('year', 'brand', 'vin', 'client', 'status', 'total_price')  # Убрали storage_cost для компактности
    readonly_fields = ('total_price',)

    def get_formset(self, request, obj=None, **kwargs):
        logger.debug(f"Rendering CarInline for container {obj.id if obj else 'None'}")
        formset = super().get_formset(request, obj, **kwargs)
        for field in formset.form.base_fields.values():
            field.help_text = ''
        return formset
# Фрагмент admin.py для CarInline и ContainerAdmin
class ContainerAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/container/change_form.html'
    list_display = ('number', 'status', 'line', 'eta', 'client', 'warehouse', 'unload_date', 'notes')
    list_filter = ('status', 'line', 'client')
    search_fields = ('number',)
    inlines = [CarInline]
    fieldsets = (
        (None, {
            'fields': (
                ('number', 'status', 'warehouse', 'client', 'line'),
                ('eta', 'unload_date', 'notes'),

            )
        }),
        ('Дополнительные расходы', {
            'classes': ('collapse',),
            'fields': (
                ('free_days', 'rate'),
                ('ths', 'sklad', 'dekl', 'proft'),
                ('days', 'storage_cost'),
            ),
        }),
    )
    readonly_fields = ('days', 'storage_cost')

    class Media:
        css = {'all': ('css/logist2_custom_admin.css',)}
        js = ('js/htmx.min.js',)

    def save_model(self, request, obj, form, change):
        logger.info(f"Saving container {obj.number}")
        super().save_model(request, obj, form, change)
        obj.refresh_from_db()
        obj.update_days_and_storage()
        obj.save(update_fields=['days', 'storage_cost'])

    def save_formset(self, request, form, formset, change):
        logger.info(f"Saving formset for {formset.model.__name__}")
        if formset.model == Car:
            for car_form in formset:
                if car_form.has_changed():
                    logger.debug(f"Car form changed: {car_form.cleaned_data}")
                if car_form.errors:
                    logger.error(f"Car form errors: {car_form.errors}")
                else:
                    logger.debug("Car form has no errors")
        super().save_formset(request, form, formset, change)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in form.base_fields.values():
            field.help_text = ''
        return form

@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/car/change_form.html'
    list_display = (
        'vin', 'brand', 'year_display', 'client', 'status', 'warehouse',
        'unload_date_display', 'transfer_date_display', 'warehouse_days_display',
        'final_storage_cost_display', 'total_price_display', 'ths', 'sklad',
        'dekl', 'proft', 'free_days_display', 'days', 'rate', 'storage_cost'
    )
    list_filter = ('status', 'warehouse', 'client')
    search_fields = ('vin', 'brand')
    fieldsets = (
        ('Основные данные', {
            'fields': (
                ('year', 'brand', 'vin', 'client', 'container'),
            )
        }),
        ('Склад', {
            'fields': (
                ('unload_date', 'transfer_date'),
                ('warehouse','free_days', 'rate', 'days'),
            )
        }),
        ('Финансы', {
            'classes': ('collapse',),
            'fields': (
                ('ths', 'sklad', 'dekl', 'proft'),
                ('storage_cost', 'total_price'),
                ('warehouse_days', 'final_storage_cost'),
            )
        }),
    )
    readonly_fields = ('total_price', 'storage_cost', 'warehouse_days', 'days')

    class Media:
        css = {'all': ('css/logist2_custom_admin.css',)}

    def year_display(self, obj):
        return obj.year
    year_display.short_description = 'Год'
    year_display.admin_order_field = 'year'

    def unload_date_display(self, obj):
        return obj.unload_date
    unload_date_display.short_description = 'Разгружен'
    unload_date_display.admin_order_field = 'unload_date'

    def transfer_date_display(self, obj):
        return obj.transfer_date
    transfer_date_display.short_description = 'Передан'
    transfer_date_display.admin_order_field = 'transfer_date'

    def warehouse_days_display(self, obj):
        return obj.warehouse_days
    warehouse_days_display.short_description = 'Дни'
    warehouse_days_display.admin_order_field = 'warehouse_days'

    def final_storage_cost_display(self, obj):
        return obj.final_storage_cost
    final_storage_cost_display.short_description = 'Итоговая цена'  # Изменено
    final_storage_cost_display.admin_order_field = 'final_storage_cost'

    def total_price_display(self, obj):
        return obj.total_price
    total_price_display.short_description = 'Текущая цена'  # Изменено
    total_price_display.admin_order_field = 'total_price'

    def free_days_display(self, obj):
        return obj.free_days
    free_days_display.short_description = 'FREE'
    free_days_display.admin_order_field = 'free_days'

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    change_form_template = 'admin/invoice_change.html'
    list_display = ('number', 'display_client', 'warehouse', 'total_amount', 'issue_date', 'paid', 'is_outgoing', 'balance_display', 'payments_display')
    list_filter = ('paid', 'is_outgoing', 'client', 'warehouse')
    search_fields = ('number',)
    fieldsets = (
        (None, {
            'fields': (
                ('number', 'client', 'warehouse'),
                ('paid', 'is_outgoing'),
                'total_amount',
                'cars',
            )
        }),
    )
    readonly_fields = ('total_amount',)
    actions = ['register_payment_link']

    class Media:
        css = {
            'all': (
                'css/logist2_custom_admin.css',
                'https://cdnjs.cloudflare.com/ajax/libs/select2/4.0.13/css/select2.min.css',
            )
        }
        js = (
            'https://code.jquery.com/jquery-3.6.0.min.js',
            'https://cdnjs.cloudflare.com/ajax/libs/select2/4.0.13/js/select2.min.js',
            'js/htmx.min.js',
            'js/logist2_invoice_admin.js',
        )

    def display_client(self, obj):
        return obj.client.name if obj.client else "-"
    display_client.short_description = "Клиент"

    def balance_display(self, obj):
        balance = obj.balance
        status = 'Переплата' if balance > 0 else 'Недоплата' if balance < 0 else 'Оплачено полностью'
        return f"{balance:.2f} ({status})"
    balance_display.short_description = 'Баланс'

    def payments_display(self, obj):
        payments = obj.payment_set.all()
        if not payments.exists():
            return "-"
        return ", ".join([f"{p.amount:.2f} ({p.payment_type}, {p.date})" for p in payments])
    payments_display.short_description = 'Платежи'

    def register_payment_link(self, request, queryset):
        invoices = Invoice.objects.all()
        clients = Client.objects.all()
        return render(request, 'admin/register_payment.html', {'invoices': invoices, 'clients': clients})
    register_payment_link.short_description = "Зарегистрировать платеж"

    def save_model(self, request, obj, form, change):
        logger.info(f"Saving invoice - Number: {obj.number}, Paid: {obj.paid}, Is Outgoing: {obj.is_outgoing}")
        logger.debug(f"Full POST data: {request.POST}")
        client_id = request.POST.get('client')
        logger.debug(f"Client ID from form: {client_id}")
        if client_id and client_id.isdigit() and client_id != '':
            obj.client_id = int(client_id)
            logger.debug(f"Set client_id: {obj.client_id}")
        elif not obj.is_outgoing:
            obj.client = None
            logger.debug("Client set to None for non-outgoing invoice. Checking form fields: %s", request.POST.keys())
        else:
            obj.client = None
            logger.debug("Client set to None for outgoing invoice")

        # Устанавливаем cars до сохранения объекта
        car_ids_str = request.POST.get('cars', '')
        car_ids = []
        if car_ids_str:
            car_ids = [int(cid) for cid in car_ids_str.split(',') if cid.strip().isdigit()]
            logger.debug(f"Parsed car_ids: {car_ids}")

        super().save_model(request, obj, form, change)

        # Привязываем автомобили после сохранения объекта
        if car_ids:
            obj.cars.set(car_ids)
            logger.debug(f"Set cars: {car_ids}")
        else:
            logger.debug("No valid car IDs found, leaving cars unchanged")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj and obj.is_outgoing:
            form.base_fields['client'].required = False
            form.base_fields['cars'].required = False
        elif obj:
            logger.debug(f"Object: {obj}, Client: {obj.client}, Cars: {obj.cars.all()}")
            form.base_fields['number'].initial = obj.number
            form.base_fields['warehouse'].initial = obj.warehouse
            form.base_fields['paid'].initial = obj.paid
            form.base_fields['is_outgoing'].initial = obj.is_outgoing
            form.base_fields['client'].initial = obj.client_id
            form.base_fields['cars'].initial = obj.cars.all()
        else:
            form.base_fields['client'].required = False
            form.base_fields['cars'].required = False
        return form

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('amount', 'payment_type', 'date', 'payer', 'recipient', 'invoice', 'from_balance', 'from_cash_balance', 'balance_impact', 'is_correction')
    list_filter = ('payment_type', 'from_balance', 'from_cash_balance', 'payer', 'invoice')
    search_fields = ('recipient', 'description')

    def balance_impact(self, obj):
        if not obj.payer:
            return "-"
        impact = -obj.amount if obj.from_balance else (obj.amount if not obj.invoice else -obj.amount)
        return f"{impact:.2f} ({'Добавлено' if impact > 0 else 'Списано'})"
    balance_impact.short_description = 'Влияние на долг'

    def is_correction(self, obj):
        return 'Correction' in obj.description
    is_correction.boolean = True
    is_correction.short_description = 'Корректировка'

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    change_form_template = 'admin/client_change.html'
    list_display = ('name', 'display_debt', 'cash_balance', 'card_balance', 'balance_details_display')
    list_filter = ('name',)
    search_fields = ('name',)
    actions = ['reset_balances']

    def display_debt(self, obj):
        """Отображает долг с инвертированным знаком: долг с минусом, переплата с плюсом."""
        return -obj.debt
    display_debt.short_description = 'Долг'
    display_debt.admin_order_field = 'debt'

    def balance_details_display(self, obj):
        details = obj.balance_details()
        # Инвертируем total_debt для отображения
        return f"Долг: {-float(details['total_debt']):.2f}, Наличные: {details['cash_balance']}, Безналичные: {details['card_balance']}"
    balance_details_display.short_description = 'Детали баланса'
    display_debt.admin_order_field = 'debt'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        client = self.get_object(request, object_id)
        if client:
            try:
                details = client.balance_details()
                # Инвертируем total_debt для отображения в JSON
                details['total_debt'] = str(-float(details['total_debt']))
                extra_context['balance_details'] = json.dumps(details, indent=2)
            except Exception as e:
                logger.error(f"Failed to serialize balance_details for client {client.name}: {e}")
                extra_context['balance_details_error'] = f"Ошибка загрузки деталей баланса: {str(e)}"
        return super().change_view(request, object_id, form_url, extra_context)

    def reset_balances(self, request, queryset):
        if 'confirm' in request.POST:
            client_ids = request.POST.getlist('_selected_action')
            clients = Client.objects.filter(id__in=client_ids)
            from django.core.management import call_command
            failed_clients = []
            for client in clients:
                try:
                    call_command('reset_client_balances', client_id=client.id)
                except Exception as e:
                    logger.error(f"Failed to reset balance for client {client.name}: {e}")
                    failed_clients.append(f"{client.name}: {str(e)}")
            if failed_clients:
                self.message_user(request, f"Не удалось сбросить балансы для некоторых клиентов: {'; '.join(failed_clients)}", level='error')
            else:
                self.message_user(request, f"Балансы для {len(clients)} клиентов успешно обнулены")
            return HttpResponseRedirect(request.get_full_path())
        return render(request, 'admin/confirm_reset_balances.html', {
            'clients': queryset,
            'action': 'reset_balances',
            'action_name': 'Обнулить балансы'
        })
    reset_balances.short_description = "Обнулить балансы клиентов"


admin.site.register(Warehouse)
admin.site.register(Declaration)
admin.site.register(Container, ContainerAdmin)
admin.site.register(Accounting)
admin.site.register(Line)
# В конец admin.py
