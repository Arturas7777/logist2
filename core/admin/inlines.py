from django import forms
from django.contrib import admin
from django.utils.html import format_html

from core.models import (
    Car,
    CarrierDriver,
    CarrierService,
    CarrierTruck,
    CarService,
    ClientTariffRate,
    CompanyService,
    LineService,
    WarehouseService,
)
from core.models_website import ContainerPhoto

# Inline forms for managing services directly in partner cards

class WarehouseServiceInline(admin.TabularInline):
    model = WarehouseService
    extra = 1
    fields = ('name', 'code', 'short_name', 'description', 'default_price', 'default_markup', 'is_active', 'add_by_default')
    verbose_name = "Услуга склада"
    verbose_name_plural = "Услуги склада"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


class LineServiceInline(admin.TabularInline):
    model = LineService
    extra = 1
    fields = ('name', 'code', 'short_name', 'description', 'default_price', 'default_markup', 'is_active', 'add_by_default')
    verbose_name = "Услуга линии"
    verbose_name_plural = "Услуги линии"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


class LineTHSCoefficientInline(admin.TabularInline):
    """Inline for THS coefficients per vehicle type

    Coefficient determines the "weight" of vehicle type in THS distribution:
    - 1.0 = standard (sedan)
    - 2.0 = double (SUV, RV)
    - 0.5 = half (motorcycle)
    """
    from core.models import LineTHSCoefficient
    model = LineTHSCoefficient
    extra = 0
    fields = ('vehicle_type', 'coefficient')
    verbose_name = "Коэффициент THS для типа ТС"
    verbose_name_plural = "Коэффициенты THS для типов ТС"

    def get_extra(self, request, obj=None, **kwargs):
        """If no records - show all 11 vehicle types for filling"""
        if obj and obj.ths_coefficients.exists():
            return 0
        return 11  # Number of vehicle types


class CarrierServiceInline(admin.TabularInline):
    model = CarrierService
    extra = 1
    fields = ('name', 'code', 'short_name', 'description', 'default_price', 'default_markup', 'is_active', 'add_by_default')
    verbose_name = "Услуга перевозчика"
    verbose_name_plural = "Услуги перевозчика"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


class CarrierTruckInline(admin.TabularInline):
    """Inline for carrier trucks"""
    model = CarrierTruck
    extra = 1
    fields = ('truck_number', 'trailer_number', 'is_active', 'notes')
    verbose_name = "Автовоз"
    verbose_name_plural = "Автовозы перевозчика"


class CarrierDriverInline(admin.TabularInline):
    """Inline for carrier drivers"""
    model = CarrierDriver
    extra = 1
    fields = ('first_name', 'last_name', 'phone', 'is_active', 'notes')
    verbose_name = "Водитель"
    verbose_name_plural = "Водители перевозчика"


class CompanyServiceInline(admin.TabularInline):
    model = CompanyService
    extra = 1
    fields = ('name', 'code', 'short_name', 'description', 'default_price', 'default_markup', 'is_active', 'add_by_default')
    verbose_name = "Услуга компании"
    verbose_name_plural = "Услуги компании"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


# Form for CarServiceInline
class CarServiceInlineForm(forms.ModelForm):
    # Fields for service selection
    warehouse_service = forms.ModelChoiceField(
        queryset=WarehouseService.objects.select_related('warehouse').filter(is_active=True),
        required=False,
        label="Услуга склада",
        help_text="Выберите услугу склада"
    )
    line_service = forms.ModelChoiceField(
        queryset=LineService.objects.select_related('line').filter(is_active=True),
        required=False,
        label="Услуга линии",
        help_text="Выберите услугу линии"
    )
    carrier_service = forms.ModelChoiceField(
        queryset=CarrierService.objects.select_related('carrier').filter(is_active=True),
        required=False,
        label="Услуга перевозчика",
        help_text="Выберите услугу перевозчика"
    )
    company_service = forms.ModelChoiceField(
        queryset=CompanyService.objects.select_related('company').filter(is_active=True),
        required=False,
        label="Услуга компании",
        help_text="Выберите услугу компании"
    )

    class Meta:
        model = CarService
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If editing existing record, set initial values
        if self.instance and self.instance.pk:
            if self.instance.service_type == 'WAREHOUSE':
                try:
                    self.fields['warehouse_service'].initial = self.instance.service_id
                except:
                    pass
            elif self.instance.service_type == 'LINE':
                try:
                    self.fields['line_service'].initial = self.instance.service_id
                except:
                    pass
            elif self.instance.service_type == 'CARRIER':
                try:
                    self.fields['carrier_service'].initial = self.instance.service_id
                except:
                    pass
            elif self.instance.service_type == 'COMPANY':
                try:
                    self.fields['company_service'].initial = self.instance.service_id
                except:
                    pass

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Set service_id based on selected type
        if instance.service_type == 'WAREHOUSE' and self.cleaned_data.get('warehouse_service'):
            instance.service_id = self.cleaned_data['warehouse_service'].id
        elif instance.service_type == 'LINE' and self.cleaned_data.get('line_service'):
            instance.service_id = self.cleaned_data['line_service'].id
        elif instance.service_type == 'CARRIER' and self.cleaned_data.get('carrier_service'):
            instance.service_id = self.cleaned_data['carrier_service'].id
        elif instance.service_type == 'COMPANY' and self.cleaned_data.get('company_service'):
            instance.service_id = self.cleaned_data['company_service'].id

        if commit:
            instance.save()
        return instance


# Inline for managing additional car services
class CarServiceInline(admin.TabularInline):
    model = CarService
    form = CarServiceInlineForm
    extra = 1
    can_delete = True
    fields = ('service_type', 'warehouse_service', 'line_service', 'carrier_service', 'service_display', 'warehouse_display', 'custom_price', 'markup_amount', 'quantity', 'final_price_display', 'invoice_price_display', 'notes')
    readonly_fields = ('service_display', 'warehouse_display', 'final_price_display', 'invoice_price_display')
    verbose_name = "Дополнительная услуга"
    verbose_name_plural = "Дополнительные услуги (от других складов/компаний)"

    def service_display(self, obj):
        """Displays service name"""
        if obj and obj.pk:
            return obj.get_service_name()
        return "-"
    service_display.short_description = "Услуга"

    def warehouse_display(self, obj):
        """Displays warehouse/company for service"""
        if not obj or not obj.pk:
            return "-"

        if obj.service_type == 'WAREHOUSE':
            try:
                service = WarehouseService.objects.select_related('warehouse').get(id=obj.service_id)
                return service.warehouse.name
            except WarehouseService.DoesNotExist:
                return "Склад не найден"
        elif obj.service_type == 'LINE':
            try:
                service = LineService.objects.select_related('line').get(id=obj.service_id)
                return service.line.name
            except LineService.DoesNotExist:
                return "Линия не найдена"
        elif obj.service_type == 'CARRIER':
            try:
                service = CarrierService.objects.select_related('carrier').get(id=obj.service_id)
                return service.carrier.name
            except CarrierService.DoesNotExist:
                return "Перевозчик не найден"
        return "-"
    warehouse_display.short_description = "Компания/Склад"

    def final_price_display(self, obj):
        """Displays final price (without hidden markup)"""
        if obj and obj.pk:
            return f"{obj.final_price:.2f}"
        return "0.00"
    final_price_display.short_description = "Итого"

    def invoice_price_display(self, obj):
        """Displays invoice price (with hidden markup)"""
        if obj and obj.pk:
            return f"{obj.invoice_price:.2f}"
        return "0.00"
    invoice_price_display.short_description = "В инвойсе"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Add hints
        formset.form.base_fields['service_type'].help_text = 'Выберите тип поставщика'
        formset.form.base_fields['custom_price'].help_text = 'Оставьте пустым для использования цены по умолчанию'
        formset.form.base_fields['quantity'].help_text = 'Количество услуг'
        return formset


class CarInline(admin.TabularInline):
    model = Car
    extra = 1
    can_delete = True
    show_change_link = True
    fields = ('year', 'brand', 'vehicle_type', 'vin', 'client', 'total_price', 'has_title')
    readonly_fields = ('total_price',)
    autocomplete_fields = ['client']

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        for field in formset.form.base_fields.values():
            field.help_text = ''
        return formset


class ContainerPhotoInline(admin.TabularInline):
    """
    Inline for displaying container photos directly in container card.
    Photos are uploaded automatically from Google Drive.
    """
    model = ContainerPhoto
    extra = 0
    can_delete = True
    max_num = 100
    fields = ('thumbnail_preview', 'photo', 'photo_type', 'is_public')
    readonly_fields = ('thumbnail_preview',)
    verbose_name = "Фотография"
    verbose_name_plural = "📷 Фотографии контейнера"

    def thumbnail_preview(self, obj):
        """Photo thumbnail"""
        if obj.thumbnail:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-width: 80px; max-height: 80px; border-radius: 4px; cursor: pointer;" /></a>',
                obj.photo.url if obj.photo else '#',
                obj.thumbnail.url
            )
        elif obj.photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-width: 80px; max-height: 80px; border-radius: 4px; cursor: pointer;" /></a>',
                obj.photo.url,
                obj.photo.url
            )
        return '-'
    thumbnail_preview.short_description = 'Превью'

    def get_queryset(self, request):
        """Optimize query - load only needed fields"""
        return super().get_queryset(request).only('id', 'container', 'photo', 'thumbnail', 'photo_type', 'is_public')


class ClientTariffRateInline(admin.TabularInline):
    """Client tariffs: total price per car by vehicle type and quantity"""
    model = ClientTariffRate
    extra = 1
    fields = ('vehicle_type', 'min_cars', 'max_cars', 'agreed_total_price')
    verbose_name = "Тариф"
    verbose_name_plural = "Тарифы: тип ТС, кол-во авто в контейнере → общая цена за авто (без хранения)"
