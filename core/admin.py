from django import forms
from django.contrib import admin
from .models import Container, Car, Line, Client, Warehouse, Declaration, Invoice, Payment, Accounting

admin.site.register(Line)
admin.site.register(Client)
admin.site.register(Warehouse)
admin.site.register(Declaration)
admin.site.register(Payment)
admin.site.register(Accounting)

class CarInline(admin.TabularInline):
    model = Car
    extra = 1
    can_delete = True
    fields = ('year', 'brand', 'vin', 'client', 'status', 'warehouse', 'unload_date', 'storage_cost', 'ths', 'sklad', 'dekl', 'prof', 'total_price')

@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = ('number', 'status', 'line', 'eta', 'client', 'warehouse', 'unload_date')
    list_filter = ('status', 'line', 'client')
    search_fields = ('number',)
    inlines = [CarInline]
    fieldsets = (
        (None, {
            'fields': (
                ('number', 'status'),
                ('line'),
                ('eta', 'client'),
                ('customs_procedure'),
                ('ths', 'sklad'),
                ('dekl', 'prof'),
                ('warehouse'),
                ('unload_date'),
                ('free_days', 'days', 'rate'),
                ('storage_cost',),
            )
        }),
    )

@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    list_display = ('vin', 'brand', 'year', 'client', 'status', 'warehouse', 'total_price', 'ths', 'sklad', 'dekl', 'prof', 'free_days', 'days', 'rate', 'storage_cost')
    list_filter = ('status', 'warehouse')
    search_fields = ('vin', 'brand')
    fieldsets = (
        (None, {
            'fields': (
                ('year', 'brand', 'vin'),
                ('client', 'status'),
                ('warehouse', 'unload_date'),
                'container',
                ('ths', 'sklad'),
                ('dekl', 'prof'),
                ('free_days', 'days', 'rate'),
                ('storage_cost',),
                'total_price',
            )
        }),
    )

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    change_form_template = 'admin/invoice_change.html'
    list_display = ('number', 'display_client', 'warehouse', 'total_amount', 'issue_date', 'paid', 'is_outgoing')
    list_filter = ('paid', 'is_outgoing', 'client', 'warehouse')
    search_fields = ('number',)
    fieldsets = (
        (None, {
            'fields': (
                ('number', 'client', 'warehouse'),  # Группируем в одну строку
                ('paid', 'is_outgoing'),           # Группируем чекбоксы в одну строку
                'total_amount',
            )
        }),
    )
    readonly_fields = ('total_amount',)

    def display_client(self, obj):
        return obj.client.name if obj.client else "-"
    display_client.short_description = "Клиент"

    def save_model(self, request, obj, form, change):
        print(f"Saving invoice - Number: {obj.number}, Paid: {obj.paid}, Is Outgoing: {obj.is_outgoing}")
        print(f"Full POST data: {request.POST}")
        client_id = request.POST.get('client')
        print(f"Client ID from form: {client_id}")
        if client_id and client_id.isdigit() and client_id != '':
            obj.client_id = int(client_id)
            print(f"Set client_id: {obj.client_id}")
        else:
            obj.client = None
            print("Client set to None. Checking form fields:", request.POST.keys())

        super().save_model(request, obj, form, change)
        print(f"After save - Client: {obj.client}, ID: {obj.id}")

        car_ids_str = request.POST.get('cars', '')
        print(f"Raw car_ids from form: '{car_ids_str}'")
        if car_ids_str:
            car_ids = [int(cid) for cid in car_ids_str.split(',') if cid.strip().isdigit()]
            print(f"Parsed car_ids: {car_ids}")
            if car_ids:
                obj.cars.set(car_ids)
                print(f"Set cars: {car_ids}")
            else:
                print("No valid car IDs found, leaving cars unchanged")
        else:
            print("No car IDs provided in form, leaving cars unchanged")

        obj.update_total_amount()
        obj.save(update_fields=['total_amount'])
        print(f"Updated Total Amount: {obj.total_amount}")
        print(f"After final save - Cars in DB: {list(obj.cars.all())}")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj:
            print(f"Object: {obj}, Client: {obj.client}, Cars: {obj.cars.all()}")
            form.base_fields['number'].initial = obj.number
            form.base_fields['warehouse'].initial = obj.warehouse
            form.base_fields['paid'].initial = obj.paid
            form.base_fields['is_outgoing'].initial = obj.is_outgoing
            form.base_fields['client'].initial = obj.client_id
        return form