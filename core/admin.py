from django.contrib import admin

# Импортируем админку для клиентского сайта
from .admin_website import (
    ClientUserAdmin, CarPhotoAdmin, ContainerPhotoAdmin, ContainerPhotoArchiveAdmin,
    AIChatAdmin, NewsPostAdmin, ContactMessageAdmin, TrackingRequestAdmin
)
from django.utils import timezone
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.core.exceptions import ValidationError
from django.db import models
from django import forms
from decimal import Decimal
from .models import Client, Warehouse, Car, Container, Line, Company, Carrier, LineService, CarrierService, WarehouseService, CarService, DeletedCarService
from .forms import LineForm, CarrierForm, WarehouseForm
from .admin_filters import MultiStatusFilter, MultiWarehouseFilter, ClientAutocompleteFilter


# Inline формы для управления услугами прямо в карточках контрагентов

class WarehouseServiceInline(admin.TabularInline):
    model = WarehouseService
    extra = 1
    fields = ('name', 'description', 'default_price', 'is_active', 'add_by_default')
    verbose_name = "Услуга склада"
    verbose_name_plural = "Услуги склада"
    
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


class LineServiceInline(admin.TabularInline):
    model = LineService
    extra = 1
    fields = ('name', 'description', 'default_price', 'is_active')
    verbose_name = "Услуга линии"
    verbose_name_plural = "Услуги линии"
    
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


class CarrierServiceInline(admin.TabularInline):
    model = CarrierService
    extra = 1
    fields = ('name', 'description', 'default_price', 'is_active')
    verbose_name = "Услуга перевозчика"
    verbose_name_plural = "Услуги перевозчика"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['description'].widget.attrs.update({'rows': 1})
        return formset


# Форма для CarServiceInline
class CarServiceInlineForm(forms.ModelForm):
    # Добавляем поле для выбора услуги
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
    
    class Meta:
        model = CarService
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Если редактируем существующую запись, устанавливаем значения
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
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Устанавливаем service_id в зависимости от выбранного типа
        if instance.service_type == 'WAREHOUSE' and self.cleaned_data.get('warehouse_service'):
            instance.service_id = self.cleaned_data['warehouse_service'].id
        elif instance.service_type == 'LINE' and self.cleaned_data.get('line_service'):
            instance.service_id = self.cleaned_data['line_service'].id
        elif instance.service_type == 'CARRIER' and self.cleaned_data.get('carrier_service'):
            instance.service_id = self.cleaned_data['carrier_service'].id
        
        if commit:
            instance.save()
        return instance


# Inline для управления дополнительными услугами автомобиля
class CarServiceInline(admin.TabularInline):
    model = CarService
    form = CarServiceInlineForm
    extra = 1
    can_delete = True
    fields = ('service_type', 'warehouse_service', 'line_service', 'carrier_service', 'service_display', 'warehouse_display', 'custom_price', 'quantity', 'final_price_display', 'notes')
    readonly_fields = ('service_display', 'warehouse_display', 'final_price_display')
    verbose_name = "Дополнительная услуга"
    verbose_name_plural = "Дополнительные услуги (от других складов/компаний)"
    
    def service_display(self, obj):
        """Отображает название услуги"""
        if obj and obj.pk:
            return obj.get_service_name()
        return "-"
    service_display.short_description = "Услуга"
    
    def warehouse_display(self, obj):
        """Отображает склад/компанию для услуги"""
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
        """Отображает итоговую цену"""
        if obj and obj.pk:
            return f"{obj.final_price:.2f}"
        return "0.00"
    final_price_display.short_description = "Итого"
    
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Добавляем пояснения
        formset.form.base_fields['service_type'].help_text = 'Выберите тип поставщика'
        formset.form.base_fields['custom_price'].help_text = 'Оставьте пустым для использования цены по умолчанию'
        formset.form.base_fields['quantity'].help_text = 'Количество услуг'
        return formset


import json
import logging
from django.db.models import Q
from django.contrib.admin import SimpleListFilter

logger = logging.getLogger('django')

CONTAINER_STATUS_COLORS = {
    'В пути': '#2772a8',  # Темнее синего
    'В порту': '#8B0000',  # Тёмно-красный
    'Разгружен': '#239f58',  # Темнее зелёного
    'Передан': '#78458c',  # Темнее фиолетового
}

class CarInline(admin.TabularInline):
    model = Car
    extra = 1
    can_delete = True
    fields = ('year', 'brand', 'vehicle_type', 'vin', 'client', 'total_price', 'has_title')  # добавили vehicle_type
    readonly_fields = ('total_price',)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        for field in formset.form.base_fields.values():
            field.help_text = ''
        return formset


# CarServiceInline удален

class ContainerAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/container/change_form.html'
    list_display = ('number', 'colored_status', 'eta', 'planned_unload_date', 'unload_date', 'line', 'warehouse')
    list_display_links = ('number',)  # Делаем номер контейнера кликабельным
    list_filter = (MultiStatusFilter, ClientAutocompleteFilter, MultiWarehouseFilter)
    search_fields = ('number',)
    ordering = ['-unload_date', '-id']  # Сначала по дате разгрузки (новые сверху), потом по ID
    inlines = [CarInline]
    fieldsets = (
        ('Основные данные', {
            'classes': ('collapse',),
            'fields': (
                ('number', 'status', 'line', 'warehouse', 'ths'),
                ('eta', 'planned_unload_date', 'unload_date'),
                'google_drive_folder_url',
            )
        }),
    )
    readonly_fields = ('days', 'storage_cost')
    actions = ['set_status_floating', 'set_status_in_port', 'set_status_unloaded', 'set_status_transferred', 'check_container_status', 'bulk_update_container_statuses', 'sync_photos_from_gdrive', 'resend_planned_notifications', 'resend_unload_notifications']

    class Media:
        css = {'all': ('css/logist2_custom_admin.css',)}
        js = ('js/htmx.min.js',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('line', 'client', 'warehouse').prefetch_related('container_cars')

    def save_model(self, request, obj, form, change):
        import time
        start_time = time.time()
        logger.info(f"[TIMING] Container save_model started for {obj.number}")
        
        # Если это новый объект и у него еще нет pk, сохраняем его
        if not change and not obj.pk:
            super().save_model(request, obj, form, change)
        elif change:
            # Для существующих объектов сохраняем как обычно
            super().save_model(request, obj, form, change)
        
        logger.info(f"[TIMING] Container saved in {time.time() - start_time:.2f}s")

        # Если изменили склад — разнесём новый склад во все авто
        if change and form and 'warehouse' in getattr(form, 'changed_data', []):
            try:
                logger.info(f"Warehouse changed for container {obj.id}, syncing cars...")
                obj.sync_cars_after_warehouse_change()
                logger.info(f"Successfully synced warehouse for {obj.container_cars.count()} cars")
            except Exception as e:
                logger.error(f"Failed to sync cars after warehouse change for container {obj.id}: {e}")

        # Если изменили статус — обновить статус у ВСЕХ автомобилей в контейнере
        if change and form and 'status' in getattr(form, 'changed_data', []):
            try:
                logger.info(f"Status changed for container {obj.id} to {obj.status}, bulk updating all cars...")
                updated_count = obj.container_cars.update(status=obj.status)
                logger.info(f"✅ Updated status to '{obj.status}' for {updated_count} cars in container {obj.number}")
            except Exception as e:
                logger.error(f"Failed to update car statuses for container {obj.id}: {e}")

        # Если изменили дату разгрузки — обновить дату у ВСЕХ автомобилей в контейнере
        if change and form and 'unload_date' in getattr(form, 'changed_data', []):
            try:
                from django.db.models.signals import post_save, post_delete
                from core.signals import update_related_on_car_save, create_car_services_on_car_save, recalculate_car_price_on_service_save, recalculate_car_price_on_service_delete
                
                logger.info(f"Unload date changed for container {obj.id} to {obj.unload_date}, bulk updating all cars...")
                
                # Обновляем контейнер из БД, чтобы быть уверенными в актуальности данных
                obj.refresh_from_db()
                
                # Временно отключаем ВСЕ сигналы для оптимизации
                post_save.disconnect(update_related_on_car_save, sender=Car)
                post_save.disconnect(create_car_services_on_car_save, sender=Car)
                post_save.disconnect(recalculate_car_price_on_service_save, sender=CarService)
                post_delete.disconnect(recalculate_car_price_on_service_delete, sender=CarService)
                
                cars_to_update = []
                affected_invoices = set()
                
                for car in obj.container_cars.select_related('warehouse').all():
                    # Обновляем дату разгрузки у ВСЕХ автомобилей
                    car.unload_date = obj.unload_date
                    car.update_days_and_storage()
                    car.calculate_total_price()
                    cars_to_update.append(car)
                    
                    # Собираем инвойсы для обновления (только новая система)
                    for invoice in car.newinvoice_set.all():
                        affected_invoices.add(invoice)
                
                # Массовое обновление одним запросом
                if cars_to_update:
                    Car.objects.bulk_update(
                        cars_to_update,
                        ['unload_date', 'days', 'storage_cost', 'current_price', 'total_price'],
                        batch_size=50
                    )
                    logger.info(f"✅ Bulk updated {len(cars_to_update)} cars in container {obj.number}")
                
                # Включаем сигналы обратно
                post_save.connect(update_related_on_car_save, sender=Car)
                post_save.connect(create_car_services_on_car_save, sender=Car)
                post_save.connect(recalculate_car_price_on_service_save, sender=CarService)
                post_delete.connect(recalculate_car_price_on_service_delete, sender=CarService)
                
                # Обновляем все затронутые инвойсы одним пакетом
                if affected_invoices:
                    logger.info(f"Updating {len(affected_invoices)} affected invoices...")
                    for invoice in affected_invoices:
                        try:
                            if hasattr(invoice, 'regenerate_items_from_cars'):
                                # NewInvoice
                                invoice.regenerate_items_from_cars()
                            else:
                                # InvoiceOLD
                                invoice.update_total_amount()
                        except Exception as e:
                            logger.error(f"Error updating invoice {invoice.id}: {e}")
                    logger.info(f"✅ Updated {len(affected_invoices)} invoices")
                
            except Exception as e:
                logger.error(f"Failed to update cars after unload_date change for container {obj.id}: {e}")
                # Убедимся, что сигналы включены даже в случае ошибки
                try:
                    from django.db.models.signals import post_save, post_delete
                    from core.signals import update_related_on_car_save, create_car_services_on_car_save, recalculate_car_price_on_service_save, recalculate_car_price_on_service_delete
                    post_save.connect(update_related_on_car_save, sender=Car)
                    post_save.connect(create_car_services_on_car_save, sender=Car)
                    post_save.connect(recalculate_car_price_on_service_save, sender=CarService)
                    post_delete.connect(recalculate_car_price_on_service_delete, sender=CarService)
                except:
                    pass

        # если изменили линию — обновить линию во всех автомобилях контейнера И пересоздать услуги линии
        if change and form and 'line' in getattr(form, 'changed_data', []):
            line_start = time.time()
            try:
                from django.db.models.signals import post_save, post_delete
                from core.signals import update_related_on_car_save, create_car_services_on_car_save, find_line_service_by_container_count, recalculate_invoices_on_car_service_save, recalculate_invoices_on_car_service_delete
                from core.models import recalculate_car_price_on_service_save, recalculate_car_price_on_service_delete, LineService
                from core.models_billing import NewInvoice
                
                logger.info(f"[TIMING] Line change started for container {obj.id}, new line: {obj.line}")
                
                # Временно отключаем ВСЕ сигналы чтобы избежать рекурсии и каскадных обновлений
                post_save.disconnect(update_related_on_car_save, sender=Car)
                post_save.disconnect(create_car_services_on_car_save, sender=Car)
                post_save.disconnect(recalculate_car_price_on_service_save, sender=CarService)
                post_delete.disconnect(recalculate_car_price_on_service_delete, sender=CarService)
                post_save.disconnect(recalculate_invoices_on_car_service_save, sender=CarService)
                post_delete.disconnect(recalculate_invoices_on_car_service_delete, sender=CarService)
                
                try:
                    # 1. Массовое обновление линии у всех авто
                    car_ids = list(obj.container_cars.values_list('id', flat=True))
                    updated_count = obj.container_cars.update(line=obj.line)
                    logger.info(f"[TIMING] Line updated for {updated_count} cars")
                    
                    # 2. Удаляем старые услуги линии для всех авто контейнера (BULK)
                    deleted_services = CarService.objects.filter(
                        car_id__in=car_ids,
                        service_type='LINE'
                    ).delete()
                    logger.info(f"[TIMING] Deleted {deleted_services[0]} old line services")
                    
                    # 3. Создаём новые услуги линии если линия указана
                    if obj.line:
                        new_services = []
                        for car in obj.container_cars.select_related('container').all():
                            vehicle_type = getattr(car, 'vehicle_type', 'CAR')
                            line_service = find_line_service_by_container_count(obj.line, obj, vehicle_type)
                            
                            if line_service:
                                new_services.append(CarService(
                                    car=car,
                                    service_type='LINE',
                                    service_id=line_service.id,
                                    custom_price=line_service.default_price,
                                    quantity=1
                                ))
                        
                        if new_services:
                            CarService.objects.bulk_create(new_services, ignore_conflicts=True)
                            logger.info(f"[TIMING] Created {len(new_services)} new line services")
                    
                    # 4. Пересчитываем цены для всех авто (BULK)
                    cars_to_update = []
                    affected_invoices = set()
                    for car in obj.container_cars.select_related('warehouse').all():
                        car.update_days_and_storage()
                        car.calculate_total_price()
                        cars_to_update.append(car)
                        # Собираем связанные инвойсы
                        for invoice in NewInvoice.objects.filter(cars=car, status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE']):
                            affected_invoices.add(invoice)
                    
                    if cars_to_update:
                        Car.objects.bulk_update(
                            cars_to_update,
                            ['days', 'storage_cost', 'current_price', 'total_price'],
                            batch_size=50
                        )
                        logger.info(f"[TIMING] Recalculated prices for {len(cars_to_update)} cars")
                    
                    # 5. Обновляем связанные инвойсы
                    if affected_invoices:
                        logger.info(f"[TIMING] Updating {len(affected_invoices)} affected invoices...")
                        for invoice in affected_invoices:
                            try:
                                invoice.regenerate_items_from_cars()
                            except Exception as e:
                                logger.error(f"Error updating invoice {invoice.number}: {e}")
                        logger.info(f"[TIMING] Invoices updated")
                    
                    logger.info(f"[TIMING] Line change completed in {time.time() - line_start:.2f}s")
                    
                finally:
                    # Включаем сигналы обратно
                    post_save.connect(update_related_on_car_save, sender=Car)
                    post_save.connect(create_car_services_on_car_save, sender=Car)
                    post_save.connect(recalculate_car_price_on_service_save, sender=CarService)
                    post_delete.connect(recalculate_car_price_on_service_delete, sender=CarService)
                    post_save.connect(recalculate_invoices_on_car_service_save, sender=CarService)
                    post_delete.connect(recalculate_invoices_on_car_service_delete, sender=CarService)
                    
            except Exception as e:
                logger.error(f"Failed to update cars after line change for container {obj.id}: {e}", exc_info=True)

    def save_formset(self, request, form, formset, change):
        import time
        formset_start = time.time()
        logger.info(f"[TIMING] save_formset started for {formset.model.__name__}")
        
        instances = formset.save(commit=False)
        logger.info(f"[TIMING] formset.save(commit=False) took {time.time() - formset_start:.2f}s")
        
        parent = form.instance  # контейнер

        # Проверяем, что у родительского объекта есть первичный ключ
        if not parent.pk:
            logger.error("Parent container doesn't have a primary key - saving parent first")
            # Сохраняем родительский объект сначала
            parent.save()
            logger.info(f"Saved parent container {parent.pk}")

        # Пропускаем если нет изменённых инстансов и нет удалённых объектов
        if not instances and not formset.deleted_objects:
            logger.info(f"[TIMING] No changes in formset, skipping. Total: {time.time() - formset_start:.2f}s")
            formset.save_m2m()
            return

        logger.info(f"[TIMING] Processing {len(instances)} changed instances")

        for obj in instances:
            if isinstance(obj, Car):
                # привязываем к контейнеру
                if not obj.container_id:
                    obj.container = parent

                # статус всегда как у контейнера
                obj.status = parent.status

                # склад/клиент/линия по контейнеру, если не заданы
                if not obj.warehouse_id and parent.warehouse_id:
                    obj.warehouse = parent.warehouse
                if not obj.client_id and parent.client_id:
                    obj.client = parent.client
                if not obj.line_id and parent.line_id:
                    obj.line = parent.line
                
                # Дата разгрузки ВСЕГДА берется из контейнера (наследуется принудительно)
                if parent.unload_date:
                    obj.unload_date = parent.unload_date
                    logger.debug(f"Car {obj.vin}: inherited unload_date={obj.unload_date} from container {parent.number}")

                creating = obj.pk is None
                if creating and obj.warehouse_id:
                    # подтянуть дефолты склада (rate/free_days и пр.) ДО первого save()
                    obj.set_initial_warehouse_values()

                # пересчёт перед сохранением
                obj.update_days_and_storage()
                
                # Сохраняем объект - сигнал post_save обработает calculate_total_price
                obj.save()
                
                logger.debug(f"Saved Car {obj.vin} (creating={creating}, has_title={obj.has_title})")
            else:
                obj.save()

        for o in formset.deleted_objects:
            o.delete()

        formset.save_m2m()

        # Временно отключено для диагностики
        # try:
        #     cars_qs = parent.container_cars.all()
        #     count = cars_qs.count()
        #     if count:
        #         from decimal import Decimal, ROUND_HALF_UP
        #         share = (parent.ths or 0) / Decimal(count)
        #         # округлим до 2 знаков банкинг-методом
        #         share = share.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        #         for car in cars_qs:
        #             car.ths = share
        #             # пересчёт итогов с учётом новой ths
        #             car.calculate_total_price()
        #             car.save(update_fields=['ths', 'current_price', 'total_price'])
        # except Exception as e:
        #     logger.error(f"Failed to distribute THS for container {parent.id}: {e}")


    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in form.base_fields.values():
            field.help_text = ''
        return form

    def colored_status(self, obj):
        color = obj.get_status_color()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span>',
            color,
            obj.get_status_display()
        )
    colored_status.short_description = 'Статус'

    def set_status_floating(self, request, queryset):
        updated = queryset.update(status='FLOATING')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
            # Обновляем статус у всех авто в контейнере
            obj.container_cars.update(status='FLOATING')
        self.message_user(request, f"Статус изменён на 'В пути' для {updated} контейнеров и их авто.")
    set_status_floating.short_description = "Изменить статус на В пути"

    def set_status_in_port(self, request, queryset):
        updated = queryset.update(status='IN_PORT')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
            # Обновляем статус у всех авто в контейнере
            obj.container_cars.update(status='IN_PORT')
        self.message_user(request, f"Статус изменён на 'В порту' для {updated} контейнеров и их авто.")
    set_status_in_port.short_description = "Изменить статус на В порту"

    def set_status_unloaded(self, request, queryset):
        updated = 0
        for obj in queryset:
            if obj.warehouse and obj.unload_date:
                obj.status = 'UNLOADED'
                obj.update_days_and_storage()
                obj.sync_cars()
                obj.save(update_fields=['status', 'days', 'storage_cost'])
                # Обновляем статус у всех авто в контейнере
                obj.container_cars.update(status='UNLOADED')
                updated += 1
            else:
                self.message_user(request, f"Контейнер {obj.number} не обновлён: требуются поля 'Склад' и 'Дата разгрузки'.", level='warning')
        self.message_user(request, f"Статус изменён на 'Разгружен' для {updated} контейнеров и их авто.")
    set_status_unloaded.short_description = "Изменить статус на Разгружен"

    def set_status_transferred(self, request, queryset):
        updated = queryset.update(status='TRANSFERRED')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
            # Обновляем статус у всех авто в контейнере
            obj.container_cars.update(status='TRANSFERRED')
        self.message_user(request, f"Статус изменён на 'Передан' для {updated} контейнеров и их авто.")
    set_status_transferred.short_description = "Изменить статус на Передан"

    def check_container_status(self, request, queryset):
        """Проверяет и обновляет статус контейнеров на основе статуса автомобилей"""
        updated_count = 0
        for obj in queryset:
            try:
                old_status = obj.status
                obj.check_and_update_status_from_cars()
                if obj.status != old_status:
                    updated_count += 1
            except Exception as e:
                logger.error(f"Failed to check status for container {obj.number}: {e}")
        
        if updated_count > 0:
            self.message_user(request, f"Статус автоматически обновлён для {updated_count} контейнеров.")
        else:
            self.message_user(request, "Статус контейнеров не требует обновления.")
    check_container_status.short_description = "Проверить статус контейнера"

    def bulk_update_container_statuses(self, request, queryset):
        """Массовое обновление статусов контейнеров на основе статуса автомобилей"""
        updated_count = 0
        skipped_count = 0
        error_count = 0
        
        for container in queryset:
            try:
                cars = container.container_cars.all()
                if not cars.exists():
                    skipped_count += 1
                    continue
                
                # Проверяем, все ли автомобили переданы
                all_transferred = all(car.status == 'TRANSFERRED' for car in cars)
                transferred_cars_count = sum(1 for car in cars if car.status == 'TRANSFERRED')
                total_cars_count = cars.count()
                
                if all_transferred and container.status != 'TRANSFERRED':
                    old_status = container.status
                    container.status = 'TRANSFERRED'
                    container.save(update_fields=['status'])
                    logger.info(f"Container {container.number} status updated from {old_status} to TRANSFERRED")
                    updated_count += 1
                else:
                    skipped_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to update container {container.number}: {e}")
                error_count += 1
        
        # Формируем сообщение для пользователя
        messages = []
        if updated_count > 0:
            messages.append(f"Обновлено контейнеров: {updated_count}")
        if skipped_count > 0:
            messages.append(f"Пропущено контейнеров: {skipped_count}")
        if error_count > 0:
            messages.append(f"Ошибок: {error_count}")
        
        if messages:
            self.message_user(request, "; ".join(messages))
        else:
            self.message_user(request, "Нет контейнеров для обновления.")
    bulk_update_container_statuses.short_description = "Массовое обновление статусов контейнеров"
    
    def sync_photos_from_gdrive(self, request, queryset):
        """Синхронизирует фотографии с Google Drive для выбранных контейнеров"""
        from .google_drive_sync import GoogleDriveSync
        
        total_photos = 0
        success_count = 0
        error_count = 0
        
        for container in queryset:
            try:
                # Пробуем найти папку контейнера в обеих основных папках
                container_number = container.number
                photos_added = 0
                
                # Проверяем обе папки (выгруженные и в контейнере)
                for folder_type, folder_id in [
                    ('unloaded', '1711SSTZ3_YgUcZfNrgNzhscbmlHXlsKb'),
                    ('in_container', '11poTWYYG3uKTuGTYDWS2m8uA52mlzP6f')
                ]:
                    # Получаем папки месяцев
                    month_folders = GoogleDriveSync.get_public_folder_files(folder_id)
                    
                    for month_folder in month_folders:
                        if not month_folder.get('is_folder'):
                            continue
                        
                        # Получаем папки контейнеров в этом месяце
                        container_folders = GoogleDriveSync.get_public_folder_files(month_folder['id'])
                        
                        for container_folder in container_folders:
                            if container_folder['name'] == container_number:
                                # Нашли папку контейнера!
                                photo_type = 'UNLOADING' if folder_type == 'unloaded' else 'GENERAL'
                                count = GoogleDriveSync.sync_container_folder(
                                    container_number,
                                    container_folder['id'],
                                    photo_type
                                )
                                photos_added += count
                
                if photos_added > 0:
                    success_count += 1
                    total_photos += photos_added
                    logger.info(f"Контейнер {container_number}: добавлено {photos_added} фото")
                else:
                    logger.warning(f"Контейнер {container_number}: папка не найдена на Google Drive")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"Ошибка синхронизации контейнера {container.number}: {e}")
        
        # Сообщение пользователю
        if total_photos > 0:
            self.message_user(
                request,
                f"Синхронизация завершена! Добавлено {total_photos} фото для {success_count} контейнеров. Ошибок: {error_count}",
                level='SUCCESS'
            )
        else:
            self.message_user(
                request,
                f"Фотографии не найдены. Проверьте наличие папок контейнеров на Google Drive. Ошибок: {error_count}",
                level='WARNING'
            )
    
    sync_photos_from_gdrive.short_description = "📥 Загрузить фото с Google Drive"

    def resend_planned_notifications(self, request, queryset):
        """Повторно отправляет уведомления клиентам о планируемой дате разгрузки"""
        from core.services.email_service import ContainerNotificationService
        
        total_sent = 0
        total_failed = 0
        containers_processed = 0
        
        for container in queryset:
            if not container.planned_unload_date:
                self.message_user(
                    request,
                    f"Контейнер {container.number}: не указана планируемая дата разгрузки",
                    level='WARNING'
                )
                continue
            
            sent, failed = ContainerNotificationService.send_planned_to_all_clients(container, user=request.user)
            total_sent += sent
            total_failed += failed
            if sent > 0:
                containers_processed += 1
        
        if total_sent > 0:
            self.message_user(
                request,
                f"✅ Отправлено {total_sent} уведомлений о планируемой разгрузке для {containers_processed} контейнеров. Ошибок: {total_failed}",
                level='SUCCESS'
            )
        elif total_failed > 0:
            self.message_user(
                request,
                f"❌ Не удалось отправить уведомления. Ошибок: {total_failed}. Проверьте email клиентов.",
                level='ERROR'
            )
        else:
            self.message_user(
                request,
                "Нет клиентов с email для отправки уведомлений (или уведомления уже были отправлены)",
                level='WARNING'
            )
    
    resend_planned_notifications.short_description = "📧 Повторить уведомление о планируемой разгрузке"

    def resend_unload_notifications(self, request, queryset):
        """Повторно отправляет уведомления клиентам о разгрузке контейнера"""
        from core.services.email_service import ContainerNotificationService
        
        total_sent = 0
        total_failed = 0
        containers_processed = 0
        
        for container in queryset:
            if not container.unload_date:
                self.message_user(
                    request,
                    f"Контейнер {container.number}: не указана дата разгрузки",
                    level='WARNING'
                )
                continue
            
            sent, failed = ContainerNotificationService.send_unload_to_all_clients(container, user=request.user)
            total_sent += sent
            total_failed += failed
            if sent > 0:
                containers_processed += 1
        
        if total_sent > 0:
            self.message_user(
                request,
                f"✅ Отправлено {total_sent} уведомлений о разгрузке для {containers_processed} контейнеров. Ошибок: {total_failed}",
                level='SUCCESS'
            )
        elif total_failed > 0:
            self.message_user(
                request,
                f"❌ Не удалось отправить уведомления. Ошибок: {total_failed}. Проверьте email клиентов.",
                level='ERROR'
            )
        else:
            self.message_user(
                request,
                "Нет клиентов с email для отправки уведомлений (или уведомления уже были отправлены)",
                level='WARNING'
            )
    
    resend_unload_notifications.short_description = "📧 Повторить уведомление о разгрузке"

    def get_changelist(self, request, **kwargs):
        """Добавляет фильтрацию по умолчанию для статусов 'В порту' и 'Разгружен'"""
        # Если нет параметров фильтрации, добавляем фильтр по умолчанию
        if not request.GET.get('status_multi'):
            # Создаем копию GET параметров
            get_params = request.GET.copy()
            get_params.setlist('status_multi', ['IN_PORT', 'UNLOADED'])
            request.GET = get_params
        return super().get_changelist(request, **kwargs)

@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/car/change_form.html'
    list_display = (
        'vin', 'brand', 'vehicle_type', 'year_display', 'client', 'colored_status', 'container_display', 'warehouse', 'line',
        'unload_date_display', 'total_price_display', 'current_price_display',
        'storage_cost_display', 'days_display', 'has_title'
    )
    list_editable = ('has_title',)
    list_filter = (MultiStatusFilter, ClientAutocompleteFilter, MultiWarehouseFilter)
    search_fields = ('vin', 'brand')
    # ОПТИМИЗАЦИЯ: Предзагрузка связанных объектов для list view
    list_select_related = ('client', 'warehouse', 'line', 'carrier', 'container')
    list_prefetch_related = ('car_services',)
    readonly_fields = (
        'default_warehouse_prices_display', 'total_price', 'current_price', 'storage_cost', 'days', 'warehouse_payment_display',
        'free_days_display', 'rate_display', 'services_summary_display', 'warehouse_services_display', 'line_services_display', 'carrier_services_display'
    )
    # inlines = []  # Услуги управляются через разделы ниже
    fieldsets = (
        ('Основные данные', {
            'fields': (
                ('year', 'brand', 'vehicle_type', 'vin', 'client', 'status'),
                ('unload_date', 'transfer_date'),
                ('has_title', 'title_notes'),
            )
        }),
        ('Линии', {
            'classes': ('collapse',),
            'fields': (
                'line',
                'line_services_display',
            )
        }),
        ('Склад', {
            'classes': ('collapse',),
            'fields': (
                'warehouse',
                'warehouse_services_display',
            )
        }),
        ('Перевозчик', {
            'classes': ('collapse',),
            'fields': (
                'carrier',
                'carrier_services_display',
            )
        }),
        ('Финансы', {
            'classes': ('collapse',),
            'fields': (
                ('proft',),
                'services_summary_display',
            )
        }),
    )
    actions = ['set_status_floating', 'set_status_in_port', 'set_status_unloaded', 'set_status_transferred', 'set_transferred_today', 'set_title_with_us']

    def set_transferred_today(self, request, queryset):
        """Устанавливает статус 'Передан' и дату передачи на сегодня"""
        from django.utils import timezone
        
        today = timezone.now().date()
        updated = 0
        
        for car in queryset:
            car.status = 'TRANSFERRED'
            car.transfer_date = today
            car.save()
            updated += 1
        
        self.message_user(request, f"Статус изменён на 'Передан' для {updated} автомобилей. Дата передачи: {today}")
    set_transferred_today.short_description = "Передан сегодня"

    def default_warehouse_prices_display(self, obj):
        details = obj.warehouse_details()
        if "message" in details:
            return details["message"]
        html = '<table style="width:100%; border:1px solid #ddd; border-collapse:collapse;">'
        html += '<tr><th style="border:1px solid #ddd; padding:8px;">Поле</th><th style="border:1px solid #ddd; padding:8px;">Цена</th></tr>'
        for key, value in details.items():
            html += f'<tr><td style="border:1px solid #ddd; padding:8px;">{key}</td><td style="border:1px solid #ddd; padding:8px;">{value}</td></tr>'
        html += '</table>'
        return format_html(html)
    default_warehouse_prices_display.short_description = "Дефолтные цены на услуги склада"

    # --- NEW: renderer для поля "Оплата складу" ---
    def warehouse_payment_display(self, obj):
        return f"{obj.warehouse_payment_amount():.2f}"

    warehouse_payment_display.short_description = 'Оплата складу'


    def services_summary_display(self, obj):
        """Отображает сводку по всем услугам с наценкой Caromoto Lithuania"""
        from decimal import Decimal
        
        # Получаем суммы по поставщикам
        line_total = obj.get_services_total_by_provider('LINE')
        carrier_total = obj.get_services_total_by_provider('CARRIER')
        
        # Склад - разделяем хранение и услуги
        try:
            storage_cost = obj.calculate_storage_cost()
            # Получаем только услуги склада (без хранения)
            warehouse_services_only = obj.get_warehouse_services_total()
            
            # Рассчитываем платные дни
            if obj.warehouse and obj.unload_date:
                from django.utils import timezone
                end_date = obj.transfer_date if obj.status == 'TRANSFERRED' and obj.transfer_date else timezone.now().date()
                total_days = (end_date - obj.unload_date).days + 1
                free_days = obj.warehouse.free_days or 0
                paid_days = max(0, total_days - free_days)
            else:
                paid_days = 0
        except Exception as e:
            storage_cost = Decimal('0.00')
            warehouse_services_only = Decimal('0.00')
            paid_days = 0
            print(f"Ошибка расчета стоимости хранения: {e}")
        
        warehouse_total = storage_cost + warehouse_services_only
        
        # Наценка Caromoto Lithuania из поля proft автомобиля
        markup_amount = obj.proft or Decimal('0.00')
        
        # Проверяем статус автомобиля
        is_transferred = obj.status == 'TRANSFERRED' and obj.transfer_date
        
        # Базовые суммы (без наценки)
        base_total = line_total + warehouse_total + carrier_total
        
        html = ['<div style="margin-top:15px; background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">']
        html.append('<h3 style="margin-top:0; color:#495057;">Сводка по услугам</h3>')
        
        html.append('<div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:15px; margin-bottom:20px;">')
        
        # Линии
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги линий:</strong><br>')
        html.append(f'<span style="font-size:18px; color:#007bff;">{line_total:.2f}</span>')
        html.append('</div>')
        
        # Склад
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Склад:</strong><br>')
        if obj.warehouse:
            free_days = obj.warehouse.free_days or 0
            html.append(f'<span style="font-size:14px; color:#6c757d;">Беспл. дней: {free_days}</span><br>')
        html.append(f'<span style="font-size:14px; color:#6c757d;">Плат. дней: {paid_days}</span><br>')
        html.append(f'<span style="font-size:14px; color:#6c757d;">Хранение: {storage_cost:.2f}</span><br>')
        html.append(f'<span style="font-size:14px; color:#6c757d;">Услуги: {warehouse_services_only:.2f}</span><br>')
        html.append(f'<span style="font-size:18px; color:#28a745; font-weight:bold;">Итого: {warehouse_total:.2f}</span>')
        html.append('</div>')
        
        # Перевозчик
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги перевозчика:</strong><br>')
        html.append(f'<span style="font-size:18px; color:#ffc107;">{carrier_total:.2f}</span>')
        html.append('</div>')
        
        # Наценка Caromoto Lithuania
        html.append('<div style="background:#e8f5e8; padding:10px; border-radius:5px; border:1px solid #28a745;">')
        html.append('<strong style="color:#28a745;">Наценка Caromoto Lithuania:</strong><br>')
        html.append(f'<span style="font-size:18px; font-weight:bold; color:#28a745;">{markup_amount:.2f}</span>')
        html.append('</div>')
        
        html.append('</div>')
        
        # Общий итог
        html.append('<div style="background:white; padding:15px; border-radius:5px; border:2px solid #6c757d;">')
        if is_transferred:
            # Если передан - показываем итоговую цену с наценкой
            total_final = base_total + markup_amount
            html.append('<strong style="color:#6c757d;">Итоговая стоимость услуг:</strong><br>')
            html.append(f'<span style="font-size:20px; color:#6c757d;">{total_final:.2f}</span>')
        else:
            # Если не передан - показываем текущую цену с наценкой
            total_current_with_markup = base_total + markup_amount
            html.append('<strong style="color:#6c757d;">Текущая стоимость услуг:</strong><br>')
            html.append(f'<span style="font-size:18px; color:#6c757d;">Базовая: {base_total:.2f}</span><br>')
            html.append(f'<span style="font-size:20px; color:#6c757d;">С наценкой: {total_current_with_markup:.2f}</span>')
        html.append('</div>')
        
        html.append('</div>')
        
        return format_html(''.join(html))
    services_summary_display.short_description = 'Сводка по услугам'

    def colored_status(self, obj):
        color = obj.get_status_color()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span>',
            color,
            obj.get_status_display()
        )
    colored_status.short_description = 'Статус'

    def container_display(self, obj):
        """Отображает номер контейнера с кликабельной ссылкой и стилизацией по статусу машины"""
        if not obj.container:
            return '-'
        
        # Используем статус машины для определения цвета (как у статуса)
        color = obj.get_status_color()
        
        # Создаем ссылку на контейнер
        container_url = f'/admin/core/container/{obj.container.id}/change/'
        
        return format_html(
            '<a href="{}" target="_blank" style="text-decoration: none;"><span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span></a>',
            container_url,
            color,
            obj.container.number
        )
    container_display.short_description = 'Контейнер'
    container_display.admin_order_field = 'container__number'

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

    def storage_cost_display(self, obj):
        """Показывает стоимость хранения, рассчитанную на основе полей склада"""
        try:
            storage_cost = obj.calculate_storage_cost()
            return f"{storage_cost:.2f}"
        except Exception as e:
            print(f"Ошибка расчета стоимости хранения: {e}")
            return f"{obj.storage_cost:.2f}"  # Fallback на старое поле
    storage_cost_display.short_description = 'Складирование'
    storage_cost_display.admin_order_field = 'storage_cost'

    def days_display(self, obj):
        """Показывает платные дни с учетом бесплатных дней из склада"""
        if obj.warehouse and obj.unload_date:
            # Рассчитываем общее количество дней хранения
            end_date = obj.transfer_date if obj.status == 'TRANSFERRED' and obj.transfer_date else timezone.now().date()
            total_days = (end_date - obj.unload_date).days + 1
            
            free_days = obj.warehouse.free_days or 0
            chargeable_days = max(0, total_days - free_days)
            return f"{chargeable_days} (из {total_days})"
        return obj.days if hasattr(obj, 'days') else 0
    days_display.short_description = 'Платные дни'
    days_display.admin_order_field = 'days'

    def total_price_display(self, obj):
        return f"{obj.total_price:.2f}"
    total_price_display.short_description = 'Итоговая цена'
    total_price_display.admin_order_field = 'total_price'

    def current_price_display(self, obj):
        return f"{obj.current_price:.2f}"
    current_price_display.short_description = 'Текущая цена'
    current_price_display.admin_order_field = 'current_price'

    def free_days_display(self, obj):
        """Показывает бесплатные дни из склада"""
        if obj.warehouse:
            return obj.warehouse.free_days
        return obj.free_days  # Fallback на старое поле
    free_days_display.short_description = 'FREE'
    free_days_display.admin_order_field = 'free_days'
    
    def rate_display(self, obj):
        """Показывает ставку за сутки из склада"""
        if obj.warehouse:
            return f"{obj.warehouse.rate:.2f}"
        return f"{obj.rate:.2f}"  # Fallback на старое поле
    rate_display.short_description = 'Ставка/день'
    rate_display.admin_order_field = 'rate'

    def set_status_floating(self, request, queryset):
        updated = queryset.update(status='FLOATING')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.save(update_fields=['days', 'storage_cost', 'total_price', 'current_price'])
        self.message_user(request, f"Статус изменён на 'В пути' для {updated} автомобилей.")
    set_status_floating.short_description = "Изменить статус на В пути"

    def set_status_in_port(self, request, queryset):
        updated = queryset.update(status='IN_PORT')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.save(update_fields=['days', 'storage_cost', 'total_price', 'current_price'])
        self.message_user(request, f"Статус изменён на 'В порту' для {updated} автомобилей.")
    set_status_in_port.short_description = "Изменить статус на В порту"

    def set_status_unloaded(self, request, queryset):
        updated = 0
        for obj in queryset:
            if obj.warehouse and obj.unload_date:
                obj.status = 'UNLOADED'
                obj.update_days_and_storage()
                obj.save(update_fields=['status', 'days', 'storage_cost', 'total_price', 'current_price'])
                updated += 1
            else:
                self.message_user(request, f"Автомобиль {obj.vin} не обновлён: требуются поля 'Склад' и 'Дата разгрузки'.", level='warning')
        self.message_user(request, f"Статус изменён на 'Разгружен' для {updated} автомобилей.")
    set_status_unloaded.short_description = "Изменить статус на Разгружен"

    def set_status_transferred(self, request, queryset):
        updated = queryset.update(status='TRANSFERRED')
        for obj in queryset:
            if obj.status == 'TRANSFERRED' and not obj.transfer_date:
                obj.transfer_date = timezone.now().date()
            obj.update_days_and_storage()
            obj.save(update_fields=['transfer_date', 'days', 'storage_cost', 'total_price', 'current_price'])
        self.message_user(request, f"Статус изменён на 'Передан' для {updated} автомобилей.")
    set_status_transferred.short_description = "Изменить статус на Передан"

    def set_title_with_us(self, request, queryset):
        logger.info(f"Setting has_title=True for {queryset.count()} cars")
        updated = queryset.update(has_title=True)
        for obj in queryset:
            logger.debug(f"Updating car {obj.vin} with has_title=True")
            obj.save()
        self.message_user(request, f"Тайтл установлен как 'У нас' для {updated} автомобилей.")
    set_title_with_us.short_description = "Тайтл у нас"

    class Media:
        css = {
            'all': (
                'css/logist2_custom_admin.css',
                'style',  # Добавляем inline стили
            )
        }
        js = ('js/htmx.min.js', 'js/logist2_htmx.js')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('client', 'warehouse', 'container')

    def save_model(self, request, obj, form, change):
        """Сохраняет модель с обработкой полей услуг"""
        super().save_model(request, obj, form, change)
        
        # Сначала обрабатываем удаление услуг
        removed_services = set()
        for key, value in request.POST.items():
            if key.startswith('remove_warehouse_service_') and value == '1':
                service_id = key.replace('remove_warehouse_service_', '')
                removed_services.add(f'warehouse_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service_id
                    ).delete()
                    # Добавляем в черный список
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service_id
                    )
                    print(f"Deleted warehouse service {service_id}: {deleted_count}")
                except Exception as e:
                    print(f"Error deleting warehouse service {service_id}: {e}")
            elif key.startswith('remove_line_service_') and value == '1':
                service_id = key.replace('remove_line_service_', '')
                removed_services.add(f'line_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='LINE',
                        service_id=service_id
                    ).delete()
                    # Добавляем в черный список
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='LINE',
                        service_id=service_id
                    )
                    print(f"Deleted line service {service_id}: {deleted_count}")
                except Exception as e:
                    print(f"Error deleting line service {service_id}: {e}")
            elif key.startswith('remove_carrier_service_') and value == '1':
                service_id = key.replace('remove_carrier_service_', '')
                removed_services.add(f'carrier_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service_id
                    ).delete()
                    # Добавляем в черный список
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service_id
                    )
                    print(f"Deleted carrier service {service_id}: {deleted_count}")
                except Exception as e:
                    print(f"Error deleting carrier service {service_id}: {e}")
        
        print(f"Removed services: {removed_services}")
        
        # Обрабатываем поля услуг склада
        if obj.warehouse:
            warehouse_services = WarehouseService.objects.filter(
                warehouse=obj.warehouse, 
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')
            
            # Если нет записей CarService для этого склада, создаем их автоматически
            existing_car_services = CarService.objects.filter(
                car=obj,
                service_type='WAREHOUSE'
            ).values_list('service_id', flat=True)
            
            # Получаем черный список удаленных услуг
            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='WAREHOUSE'
            ).values_list('service_id', flat=True)
            
            for service in warehouse_services:
                # Проверяем, не была ли услуга удалена
                if f'warehouse_{service.id}' in removed_services:
                    continue  # Пропускаем удаленную услугу
                
                # Проверяем черный список
                if service.id in deleted_services:
                    continue  # Пропускаем услуги из черного списка
                
                field_name = f'warehouse_service_{service.id}'
                value = request.POST.get(field_name)
                
                # Если услуги еще нет в CarService, создаем её автоматически
                if service.id not in existing_car_services:
                    value = value or service.default_price
                    CarService.objects.create(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        custom_price=float(value)
                    )
                elif value:
                    # Обновляем существующую услугу
                    car_service, created = CarService.objects.get_or_create(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        defaults={'custom_price': float(value)}
                    )
                    if not created:
                        car_service.custom_price = float(value)
                        car_service.save()
        
        # Обрабатываем поля услуг линии
        if obj.line:
            line_services = LineService.objects.filter(
                line=obj.line, 
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')
            
            # Если нет записей CarService для этой линии, создаем их автоматически
            existing_car_services = CarService.objects.filter(
                car=obj,
                service_type='LINE'
            ).values_list('service_id', flat=True)
            
            # Получаем черный список удаленных услуг
            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='LINE'
            ).values_list('service_id', flat=True)
            
            for service in line_services:
                # Проверяем, не была ли услуга удалена
                if f'line_{service.id}' in removed_services:
                    continue  # Пропускаем удаленную услугу
                
                # Проверяем черный список
                if service.id in deleted_services:
                    continue  # Пропускаем услуги из черного списка
                
                field_name = f'line_service_{service.id}'
                value = request.POST.get(field_name)
                
                # Если услуги еще нет в CarService, создаем её автоматически
                if service.id not in existing_car_services:
                    value = value or service.default_price
                    CarService.objects.create(
                        car=obj,
                        service_type='LINE',
                        service_id=service.id,
                        custom_price=float(value)
                    )
                elif value:
                    # Обновляем существующую услугу
                    car_service, created = CarService.objects.get_or_create(
                        car=obj,
                        service_type='LINE',
                        service_id=service.id,
                        defaults={'custom_price': float(value)}
                    )
                    if not created:
                        car_service.custom_price = float(value)
                        car_service.save()
        
        # Обрабатываем поля услуг перевозчика
        if obj.carrier:
            carrier_services = CarrierService.objects.filter(
                carrier=obj.carrier, 
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')
            
            # Если нет записей CarService для этого перевозчика, создаем их автоматически
            existing_car_services = CarService.objects.filter(
                car=obj,
                service_type='CARRIER'
            ).values_list('service_id', flat=True)
            
            # Получаем черный список удаленных услуг
            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='CARRIER'
            ).values_list('service_id', flat=True)
            
            for service in carrier_services:
                # Проверяем, не была ли услуга удалена
                if f'carrier_{service.id}' in removed_services:
                    continue  # Пропускаем удаленную услугу
                
                # Проверяем черный список
                if service.id in deleted_services:
                    continue  # Пропускаем услуги из черного списка
                
                field_name = f'carrier_service_{service.id}'
                value = request.POST.get(field_name)
                
                # Если услуги еще нет в CarService, создаем её автоматически
                if service.id not in existing_car_services:
                    value = value or service.default_price
                    CarService.objects.create(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service.id,
                        custom_price=float(value)
                    )
                elif value:
                    # Обновляем существующую услугу
                    car_service, created = CarService.objects.get_or_create(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service.id,
                        defaults={'custom_price': float(value)}
                    )
                    if not created:
                        car_service.custom_price = float(value)
                        car_service.save()
        
        # Пересчитываем стоимость хранения и дни при смене склада
        if change and form and 'warehouse' in getattr(form, 'changed_data', []):
            print(f"Склад изменился для автомобиля {obj.vin}, пересчитываем стоимость хранения")
            try:
                # Обновляем поля на основе нового склада
                obj.update_days_and_storage()
                obj.calculate_total_price()
                # Сохраняем обновленные поля
                obj.save(update_fields=['storage_cost', 'days', 'current_price', 'total_price'])
                print(f"Обновлены поля: storage_cost={obj.storage_cost}, days={obj.days}")
            except Exception as e:
                print(f"Ошибка при пересчете стоимости хранения: {e}")

    def warehouse_services_display(self, obj):
        """Отображает редактируемые поля для услуг всех складов"""
        try:
            # Получаем ВСЕ услуги склада, которые уже связаны с автомобилем (от любых складов)
            car_services = CarService.objects.filter(
                car=obj, 
                service_type='WAREHOUSE'
            ).select_related('car')
            
            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'
            
            if car_services:
                for car_service in car_services:
                    try:
                        # Получаем детали услуги и склада
                        service = WarehouseService.objects.select_related('warehouse').get(id=car_service.service_id)
                        current_value = car_service.custom_price or service.default_price
                        warehouse_name = service.warehouse.name
                        
                        # Подсветка: основной склад - зеленый, другие - желтый
                        bg_color = "#e8f5e9" if (obj.warehouse and service.warehouse.id == obj.warehouse.id) else "#fff9e6"
                        
                        html += f'''
                        <div style="border: 1px solid #ddd; padding: 10px; background: {bg_color}; position: relative; min-width: 220px;">
                            <button type="button" onclick="removeService({service.id}, 'warehouse')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                            <div style="font-size: 11px; color: #666; margin-bottom: 3px;">📦 {warehouse_name}</div>
                            <strong>{service.name}</strong><br>
                            <input type="number" name="warehouse_service_{service.id}" value="{current_value}" step="0.01" style="width: 100px; margin-top: 5px;">
                            <input type="hidden" name="remove_warehouse_service_{service.id}" id="remove_warehouse_service_{service.id}" value="">
                        </div>
                        '''
                    except Exception as e:
                        continue
            
            html += '</div>'
            
            # Кнопка для добавления услуг - теперь всегда доступна
            html += f'''
            <div style="margin-top: 10px;">
                <button type="button" class="add-service-btn" onclick="openModal('warehouseServicesModal', 'warehouse')" title="Добавить услуги любого склада">
                    +
                </button>
                <span style="margin-left: 5px; color: #666;">Добавить услуги склада</span>
            </div>
            '''
            
            # Добавляем JavaScript для удаления услуг
            html += '''
            <script>
            function removeService(serviceId, serviceType) {
                const serviceDiv = event.target.closest('div');
                serviceDiv.style.display = 'none';
                const hiddenField = document.getElementById('remove_' + serviceType + '_service_' + serviceId);
                hiddenField.value = '1';
                console.log('Removed service:', serviceType, serviceId, 'Field value:', hiddenField.value);
            }
            </script>
            '''
            
            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    warehouse_services_display.short_description = "Услуги склада"

    def line_services_display(self, obj):
        """Отображает редактируемые поля для услуг линии"""
        if not obj.line:
            return "Линия не выбрана"
        
        try:
            # Получаем услуги линии, которые уже связаны с автомобилем
            car_services = CarService.objects.filter(
                car=obj, 
                service_type='LINE'
            )
            
            if not car_services:
                return "Услуги будут созданы при сохранении"
            
            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'
            
            for car_service in car_services:
                try:
                    # Получаем детали услуги
                    service = LineService.objects.get(id=car_service.service_id)
                    current_value = car_service.custom_price or service.default_price
                    
                    html += f'''
                    <div style="border: 1px solid #ddd; padding: 10px; background: #f9f9f9; position: relative; min-width: 200px;">
                        <button type="button" onclick="removeService({service.id}, 'line')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                        <strong>{service.name}</strong><br>
                        <input type="number" name="line_service_{service.id}" value="{current_value}" step="0.01" style="width: 100px; margin-top: 5px;">
                        <input type="hidden" name="remove_line_service_{service.id}" id="remove_line_service_{service.id}" value="">
                    </div>
                    '''
                except:
                    continue
            
            html += '</div>'
            
            # Добавляем кнопку для добавления новых услуг
            if obj.line:
                html += f'''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('lineServicesModal', 'line')" title="Добавить услуги линии">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги линии</span>
                </div>
                '''
            
            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    line_services_display.short_description = "Услуги линии"

    def carrier_services_display(self, obj):
        """Отображает редактируемые поля для услуг перевозчика"""
        if not obj.carrier:
            return "Перевозчик не выбран"
        
        try:
            # Получаем услуги перевозчика, которые уже связаны с автомобилем
            car_services = CarService.objects.filter(
                car=obj, 
                service_type='CARRIER'
            )
            
            if not car_services:
                return "Услуги будут созданы при сохранении"
            
            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'
            
            for car_service in car_services:
                try:
                    # Получаем детали услуги
                    service = CarrierService.objects.get(id=car_service.service_id)
                    current_value = car_service.custom_price or service.default_price
                    
                    html += f'''
                    <div style="border: 1px solid #ddd; padding: 10px; background: #f9f9f9; position: relative; min-width: 200px;">
                        <button type="button" onclick="removeService({service.id}, 'carrier')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                        <strong>{service.name}</strong><br>
                        <input type="number" name="carrier_service_{service.id}" value="{current_value}" step="0.01" style="width: 100px; margin-top: 5px;">
                        <input type="hidden" name="remove_carrier_service_{service.id}" id="remove_carrier_service_{service.id}" value="">
                    </div>
                    '''
                except:
                    continue
            
            html += '</div>'
            
            # Добавляем кнопку для добавления новых услуг
            if obj.carrier:
                html += f'''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('carrierServicesModal', 'carrier')" title="Добавить услуги перевозчика">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги перевозчика</span>
                </div>
                '''
            
            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    carrier_services_display.short_description = "Услуги перевозчика"

    def get_changelist(self, request, **kwargs):
        """Добавляет фильтрацию по умолчанию для статусов 'В порту' и 'Разгружен'"""
        # Если нет параметров фильтрации, добавляем фильтр по умолчанию
        if not request.GET.get('status_multi'):
            # Создаем копию GET параметров
            get_params = request.GET.copy()
            get_params.setlist('status_multi', ['IN_PORT', 'UNLOADED'])
            request.GET = get_params
        return super().get_changelist(request, **kwargs)

# WarehouseServiceInline удален - используется кастомный раздел "Управление услугами"


# Временно отключаем старый админ
# @admin.register(Warehouse)
# class WarehouseAdmin(admin.ModelAdmin):

@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'free_days', 'rate', 'balance_display')
    search_fields = ('name', 'address')
    readonly_fields = ('balance',)
    exclude = (
        'default_unloading_fee', 'delivery_to_warehouse', 'loading_on_trawl',
        'documents_fee', 'transfer_fee', 'transit_declaration', 'export_declaration',
        'additional_expenses', 'complex_fee'
    )
    inlines = [WarehouseServiceInline]
    fieldsets = (
        ('Основные данные', {
            'fields': ('name', 'address')
        }),
        ('Ставки хранения', {
            'fields': ('free_days', 'rate'),
            'description': 'Настройки для расчета стоимости хранения на складе. Ставка за сутки умножается на количество дней хранения минус бесплатные дни.'
        }),
        ('Баланс', {
            'fields': ('balance',),
            'description': 'Баланс склада'
        }),
    )

    def balance_display(self, obj):
        """Показывает баланс склада"""
        try:
            balance = obj.balance or 0
            color = '#28a745' if balance >= 0 else '#dc3545'
            sign = '+' if balance >= 0 else ''
            return format_html(
                '<span style="color:{}; font-weight:bold;">{} {:.2f}</span>',
                color, sign, balance
            )
        except:
            return '-'
    balance_display.short_description = 'Баланс'

    def balance_summary_display(self, obj):
        """Показывает сводку по балансу склада"""
        try:
            balance = obj.balance or 0
            
            html = f"""
            <div style="background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">
                <h3 style="margin-top:0; color:#495057;">Баланс склада</h3>
                
                <div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">
                    <strong>Баланс:</strong><br>
                    <span style="font-size:18px; color:{'#28a745' if balance >= 0 else '#dc3545'};">{balance:.2f}</span>
                </div>
                </div>
                
                <div style="background:white; padding:15px; border-radius:5px; border:2px solid {'#28a745' if total_balance >= 0 else '#dc3545'};">
                    <strong style="color:{'#28a745' if total_balance >= 0 else '#dc3545'};">Общий баланс:</strong><br>
                    <span style="font-size:24px; font-weight:bold; color:{'#28a745' if total_balance >= 0 else '#dc3545'};">{total_balance:.2f}</span>
                </div>
            </div>
            """
            
            return format_html(html)
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки баланса: {e}</p>')
    balance_summary_display.short_description = 'Сводка по балансу'

    def balance_transactions_display(self, obj):
        """Показывает платежи склада"""
        try:
            # Получаем все платежи для склада
            payments = Payment.objects.filter(
                models.Q(from_warehouse=obj) | models.Q(to_warehouse=obj)
            ).order_by('-date', '-id')[:20]  # Последние 20 платежей
            
            if not payments.exists():
                return format_html('<p style="color:#6c757d;">Нет платежей</p>')
            
            html = ['<div style="margin-top:15px;">']
            html.append('<h4 style="margin-bottom:10px; color:#495057;">Последние платежи</h4>')
            html.append('<table style="width:100%; border-collapse:collapse; font-size:12px;">')
            html.append('<tr style="background-color:#f8f9fa;">')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Дата</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Тип</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Отправитель</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Получатель</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Описание</th>')
            html.append('</tr>')
            
            for payment in payments:
                amount_color = '#28a745' if payment.to_warehouse == obj else '#dc3545'
                html.append('<tr>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.date.strftime("%d.%m.%Y")}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.payment_type}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px; color:{amount_color}; font-weight:bold;">{payment.amount:.2f}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.sender}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.recipient}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.description or "-"}</td>')
                html.append('</tr>')
            
            html.append('</table>')
            html.append('</div>')
            
            return format_html(''.join(html))
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки платежей: {e}</p>')
    balance_transactions_display.short_description = 'Платежи'
    
    def reset_warehouse_balance(self, request, queryset):
        """Обнуляет балансы выбранных складов"""
        from django.contrib import messages
        
        try:
            for warehouse in queryset:
                warehouse.balance = 0
                warehouse.save()
            
            messages.success(request, f'Балансы {queryset.count()} складов успешно обнулены')
        except Exception as e:
            messages.error(request, f'Ошибка при обнулении балансов: {e}')
    
    reset_warehouse_balance.short_description = 'Обнулить балансы выбранных складов'



    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Переопределяем change_view для обработки услуг"""
        extra_context = extra_context or {}
        
        if object_id:
            obj = self.get_object(request, object_id)
            
            if request.method == 'POST':
                # Обрабатываем существующие услуги
                for key, value in request.POST.items():
                    if key.startswith('service_name_'):
                        service_id = key.replace('service_name_', '')
                        # Проверяем, что это действительно ID услуги (число)
                        if service_id.isdigit():
                            try:
                                service = WarehouseService.objects.get(id=service_id, warehouse=obj)
                                service.name = value
                                service.save()
                            except WarehouseService.DoesNotExist:
                                pass
                    elif key.startswith('service_price_'):
                        service_id = key.replace('service_price_', '')
                        # Проверяем, что это действительно ID услуги (число)
                        if service_id.isdigit():
                            try:
                                service = WarehouseService.objects.get(id=service_id, warehouse=obj)
                                service.default_price = float(value) if value else 0
                                service.save()
                            except (WarehouseService.DoesNotExist, ValueError):
                                pass
                    elif key.startswith('delete_service_'):
                        service_id = key.replace('delete_service_', '')
                        try:
                            service = WarehouseService.objects.get(id=service_id, warehouse=obj)
                            service.delete()
                        except WarehouseService.DoesNotExist:
                                pass
                
                # Обрабатываем старые поля услуг
                old_fields_mapping = {
                    'service_price_complex': 'complex_fee',
                    'service_price_unloading': 'default_unloading_fee',
                    'service_price_delivery': 'delivery_to_warehouse',
                    'service_price_loading': 'loading_on_trawl',
                    'service_price_documents': 'documents_fee',
                    'service_price_transfer': 'transfer_fee',
                    'service_price_transit': 'transit_declaration',
                    'service_price_export': 'export_declaration',
                    'service_price_additional': 'additional_expenses',
                    'service_price_rate': 'rate',
                }
                
                # Сначала проверяем, какие поля нужно обнулить
                for key, value in request.POST.items():
                    if key.startswith('clear_field_'):
                        field_name = key.replace('clear_field_', '')
                        setattr(obj, field_name, 0)
                        obj.save()
                
                # Затем обновляем значения полей
                for field_name, model_field in old_fields_mapping.items():
                    if field_name in request.POST:
                        try:
                            value = float(request.POST[field_name]) if request.POST[field_name] else 0
                            setattr(obj, model_field, value)
                            obj.save()
                        except ValueError:
                            pass
                
                # Обрабатываем новые услуги
                for key, value in request.POST.items():
                    if key.startswith('new_service_name_'):
                        index = key.replace('new_service_name_', '')
                        name = value
                        price = request.POST.get(f'new_service_price_{index}', 0)
                        
                        if name:
                            try:
                                WarehouseService.objects.create(
                                    warehouse=obj,
                                    name=name,
                                    default_price=float(price) if price else 0
                                )
                            except ValueError:
                                pass
        
        return super().change_view(request, object_id, form_url, extra_context)


# ============================================================================
# СТАРАЯ АДМИНКА INVOICE - УДАЛЕНА
# ============================================================================
# Используйте NewInvoiceAdmin из admin_billing.py
# ============================================================================

# @admin.register(InvoiceOLD)  # Отключено

# ============================================================================
# СТАРАЯ АДМИНКА PAYMENT - УДАЛЕНА
# ============================================================================
# Используйте TransactionAdmin из admin_billing.py
# ============================================================================

# @admin.register(PaymentOLD)  # Отключено

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    change_form_template = 'admin/client_change.html'
    list_display = ('name', 'emails_display', 'notification_enabled', 'new_balance_display', 'balance_status_new')
    list_filter = ('name', 'notification_enabled')
    search_fields = ('name', 'email', 'email2', 'email3', 'email4')
    actions = ['reset_balances', 'recalculate_balance', 'reset_client_balance']
    readonly_fields = ('balance', 'balance_updated_at', 'new_invoices_display', 'new_transactions_display')

    def get_queryset(self, request):
        """ОПТИМИЗАЦИЯ: Используем with_balance_info для предрасчета данных"""
        qs = super().get_queryset(request)
        # Для list view используем оптимизированный менеджер с annotate
        if 'changelist' in request.path:
            return qs.with_balance_info()
        return qs

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'notification_enabled')
        }),
        ('📧 Email-адреса для уведомлений', {
            'fields': ('email', 'email2', 'email3', 'email4'),
            'description': 'Уведомления о разгрузке контейнеров будут отправлены на все указанные адреса'
        }),
        ('💰 Баланс', {
            'fields': ('balance', 'balance_updated_at', 'new_invoices_display', 'new_transactions_display'),
            'description': 'Единый баланс клиента с историей транзакций'
        }),
    )
    
    def emails_display(self, obj):
        """Отображает количество email-адресов"""
        emails = obj.get_notification_emails()
        count = len(emails)
        if count == 0:
            return format_html('<span style="color: #999;">—</span>')
        elif count == 1:
            return format_html('<span title="{}">{}</span>', emails[0], emails[0])
        else:
            all_emails = ', '.join(emails)
            return format_html('<span title="{}">{} (+{})</span>', all_emails, emails[0], count - 1)
    emails_display.short_description = 'Email'

    def real_balance_display(self, obj):
        """Показывает инвойс-баланс клиента (инвойсы - платежи)"""
        balance = obj.real_balance
        color = obj.balance_color
        sign = '' if balance == 0 else ('+' if balance > 0 else '')
        formatted = f"{balance:.2f}"
        
        return format_html(
            '<span style="color:{}; font-weight:bold; font-size:14px;">{} {}</span>',
            color, sign, formatted
        )
    real_balance_display.short_description = 'Инвойс-баланс (Долг/Переплата)'
    real_balance_display.admin_order_field = '_real_balance_annotated'

    def balance_status_display(self, obj):
        """Показывает статус баланса цветным бейджем"""
        status = obj.balance_status
        color = obj.balance_color
        bg_color = color.replace('#', '')
        
        return format_html(
            '<span style="background-color:{}; color:white; padding:4px 8px; border-radius:4px; font-size:11px; font-weight:bold;">{}</span>',
            color, status
        )
    balance_status_display.short_description = 'Статус'

    def new_balance_display(self, obj):
        """НОВАЯ СИСТЕМА - единый баланс"""
        balance = obj.balance
        if balance > 0:
            color = '#28a745'
            text = f'+{balance:.2f}'
        elif balance < 0:
            color = '#dc3545'
            text = f'{balance:.2f}'
        else:
            color = '#6c757d'
            text = '0.00'
        
        return format_html(
            '<span style="color:{}; font-weight:bold; font-size:15px;">{}</span>',
            color, text
        )
    new_balance_display.short_description = 'Баланс'
    new_balance_display.admin_order_field = 'balance'
    
    def balance_status_new(self, obj):
        """Статус нового баланса"""
        balance = obj.balance
        if balance > 0:
            return format_html('<span style="background:#28a745; color:white; padding:3px 8px; border-radius:3px;">ПЕРЕПЛАТА</span>')
        elif balance < 0:
            return format_html('<span style="background:#dc3545; color:white; padding:3px 8px; border-radius:3px;">ДОЛГ</span>')
        else:
            return format_html('<span style="background:#6c757d; color:white; padding:3px 8px; border-radius:3px;">OK</span>')
    balance_status_new.short_description = 'Статус'
    
    def new_invoices_display(self, obj):
        """Показывает инвойсы из новой системы"""
        from core.models_billing import NewInvoice
        
        invoices = NewInvoice.objects.filter(recipient_client=obj).order_by('-date')[:10]
        
        if not invoices:
            return format_html('<p style="color:#999;">Инвойсов еще нет. <a href="/admin/core/newinvoice/add/">Создать первый инвойс</a></p>')
        
        html = '<table style="width:100%; border-collapse:collapse;">'
        html += '<tr style="background:#f5f5f5;"><th style="padding:8px;">Номер</th><th>Дата</th><th>Сумма</th><th>Оплачено</th><th>Статус</th></tr>'
        
        for inv in invoices:
            status_color = {'PAID': '#28a745', 'ISSUED': '#007bff', 'OVERDUE': '#dc3545'}.get(inv.status, '#6c757d')
            html += f'''<tr style="border-bottom:1px solid #ddd;">
                <td style="padding:8px;"><a href="/admin/core/newinvoice/{inv.pk}/change/">{inv.number}</a></td>
                <td style="padding:8px;">{inv.date.strftime("%d.%m.%Y")}</td>
                <td style="padding:8px;">{inv.total:.2f}</td>
                <td style="padding:8px;">{inv.paid_amount:.2f}</td>
                <td style="padding:8px;"><span style="background:{status_color}; color:white; padding:2px 6px; border-radius:3px;">{inv.get_status_display()}</span></td>
            </tr>'''
        
        html += '</table>'
        return format_html(html)
    new_invoices_display.short_description = 'Инвойсы'
    
    def new_transactions_display(self, obj):
        """Показывает транзакции из новой системы"""
        from core.models_billing import Transaction
        
        transactions = Transaction.objects.filter(
            models.Q(from_client=obj) | models.Q(to_client=obj)
        ).order_by('-date')[:10]
        
        if not transactions:
            return format_html('<p style="color:#999;">Транзакций еще нет</p>')
        
        html = '<table style="width:100%; border-collapse:collapse;">'
        html += '<tr style="background:#f5f5f5;"><th style="padding:8px;">Номер</th><th>Дата</th><th>Тип</th><th>Сумма</th><th>Направление</th></tr>'
        
        for trx in transactions:
            type_color = {'PAYMENT': '#28a745', 'REFUND': '#dc3545'}.get(trx.type, '#007bff')
            direction = '↑ Получено' if trx.to_client == obj else '↓ Отправлено'
            html += f'''<tr style="border-bottom:1px solid #ddd;">
                <td style="padding:8px;"><a href="/admin/core/transaction/{trx.pk}/change/">{trx.number}</a></td>
                <td style="padding:8px;">{trx.date.strftime("%d.%m.%Y %H:%M")}</td>
                <td style="padding:8px;"><span style="background:{type_color}; color:white; padding:2px 6px; border-radius:3px;">{trx.get_type_display()}</span></td>
                <td style="padding:8px; font-weight:bold;">{trx.amount:.2f}</td>
                <td style="padding:8px;">{direction}</td>
            </tr>'''
        
        html += '</table>'
        return format_html(html)
    new_transactions_display.short_description = 'Транзакции'

    def recalculate_balance(self, request, queryset):
        """Пересчитывает инвойс-баланс для выбранных клиентов"""
        from django.contrib import messages
        
        count = 0
        for client in queryset:
            try:
                # Пересчитываем инвойс-баланс клиента
                client.sync_balance_fields()
                count += 1
                messages.success(request, f'Инвойс-баланс клиента {client.name} пересчитан')
            except Exception as e:
                messages.error(request, f'Ошибка при пересчете инвойс-баланса клиента {client.name}: {e}')
        
        if count > 0:
            messages.success(request, f'Успешно пересчитан инвойс-баланс для {count} клиентов')
        else:
            messages.warning(request, 'Не удалось пересчитать инвойс-баланс ни для одного клиента')
    
    recalculate_balance.short_description = 'Пересчитать инвойс-баланс'

    def sync_all_balances(self, request, queryset):
        """Синхронизирует поля баланса с инвойс-балансом для выбранных клиентов"""
        from django.contrib import messages
        
        count = 0
        for client in queryset:
            try:
                client.sync_balance_fields()
                count += 1
                messages.success(request, f'Инвойс-баланс клиента {client.name} синхронизирован')
            except Exception as e:
                messages.error(request, f'Ошибка при синхронизации инвойс-баланса клиента {client.name}: {e}')
        
        if count > 0:
            messages.success(request, f'Успешно синхронизирован инвойс-баланс для {count} клиентов')
        else:
            messages.warning(request, 'Не удалось синхронизировать инвойс-баланс ни для одного клиента')
    
    sync_all_balances.short_description = 'Синхронизировать инвойс-балансы'

    def get_queryset(self, request):
        """Получаем queryset с оптимизацией"""
        return Client.objects.with_balance_info()

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        client = self.get_object(request, object_id)
        if client:
            try:
                summary = client.get_balance_summary()
                extra_context['balance_summary'] = summary
            except Exception as e:
                logger.error(f"Failed to get balance summary for client {client.name}: {e}")
                extra_context['balance_summary_error'] = f"Ошибка загрузки сводки баланса: {str(e)}"
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
    
    def reset_client_balance(self, request, queryset):
        """Обнуляет балансы выбранных клиентов"""
        from django.contrib import messages
        
        try:
            for client in queryset:
                client.balance = 0
                client.save()
            
            messages.success(request, f'Балансы {queryset.count()} клиентов успешно обнулены')
        except Exception as e:
            messages.error(request, f'Ошибка при обнулении балансов: {e}')
    
    reset_client_balance.short_description = 'Обнулить балансы выбранных клиентов'
    
    # ========================================================================
    # ПОПОЛНЕНИЕ И УПРАВЛЕНИЕ БАЛАНСОМ КЛИЕНТА
    # ========================================================================
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('<int:client_id>/topup/', self.admin_site.admin_view(self.topup_balance_view), name='client_topup'),
            path('<int:client_id>/reset-balance/', self.admin_site.admin_view(self.reset_balance_view), name='client_reset_balance'),
            path('<int:client_id>/recalc-balance/', self.admin_site.admin_view(self.recalc_balance_view), name='client_recalc_balance'),
            path('<int:client_id>/cars-in-warehouse/', self.admin_site.admin_view(self.cars_in_warehouse_view), name='client_cars_in_warehouse'),
        ]
        return custom_urls + urls
    
    def topup_balance_view(self, request, client_id):
        """Страница пополнения баланса клиента"""
        from django.shortcuts import render, redirect
        from django.contrib import messages
        from decimal import Decimal
        from core.services.billing_service import BillingService
        
        client = Client.objects.get(pk=client_id)
        
        if request.method == 'POST':
            try:
                amount = Decimal(request.POST.get('amount', 0))
                method = request.POST.get('method', 'CASH')
                description = request.POST.get('description', '')
                
                if amount <= 0:
                    raise ValueError("Сумма должна быть положительной")
                
                trx = BillingService.topup_balance(
                    entity=client,
                    amount=amount,
                    method=method,
                    description=description or f"Пополнение баланса клиента {client.name}",
                    created_by=request.user
                )
                
                messages.success(
                    request, 
                    f'✅ Баланс пополнен! Транзакция: {trx.number}, сумма: {amount}€'
                )
                
                return redirect('admin:core_client_change', client_id)
                
            except Exception as e:
                messages.error(request, f'❌ Ошибка: {e}')
        
        context = {
            'client': client,
            'opts': self.model._meta,
            'title': f'Пополнение баланса - {client.name}',
        }
        
        return render(request, 'admin/client_topup.html', context)
    
    def reset_balance_view(self, request, client_id):
        """Обнулить баланс клиента"""
        from django.shortcuts import redirect
        from django.contrib import messages
        from decimal import Decimal
        
        client = Client.objects.get(pk=client_id)
        old_balance = client.balance
        
        client.balance = Decimal('0.00')
        client.save(update_fields=['balance'])
        
        messages.success(request, f'✅ Баланс клиента {client.name} обнулён (был: {old_balance}€)')
        return redirect('admin:core_client_change', client_id)
    
    def recalc_balance_view(self, request, client_id):
        """Пересчитать баланс клиента на основе транзакций"""
        from django.shortcuts import redirect
        from django.contrib import messages
        from django.db.models import Sum
        from decimal import Decimal
        from core.models_billing import Transaction
        
        client = Client.objects.get(pk=client_id)
        old_balance = client.balance
        
        # Пополнения (TOPUP)
        topups = Transaction.objects.filter(
            to_client=client,
            type='TOPUP',
            status='COMPLETED'
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        # Платежи (PAYMENT)
        payments = Transaction.objects.filter(
            from_client=client,
            type='PAYMENT',
            status='COMPLETED'
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        # Новый баланс
        new_balance = topups - payments
        
        client.balance = new_balance
        client.save(update_fields=['balance'])
        
        messages.success(
            request, 
            f'✅ Баланс пересчитан: {old_balance}€ → {new_balance}€ (пополнения: {topups}€, платежи: {payments}€)'
        )
        return redirect('admin:core_client_change', client_id)
    
    def cars_in_warehouse_view(self, request, client_id):
        """Показывает список всех разгруженных авто клиента на складе"""
        from django.shortcuts import render
        from django.http import JsonResponse
        
        client = Client.objects.get(pk=client_id)
        
        # Получаем все авто клиента со статусом UNLOADED (на складе)
        cars = Car.objects.filter(
            client=client,
            status='UNLOADED'
        ).select_related('warehouse', 'container').order_by('warehouse__name', '-unload_date')
        
        # Группируем по складам
        warehouses_data = {}
        for car in cars:
            wh_name = car.warehouse.name if car.warehouse else 'Без склада'
            if wh_name not in warehouses_data:
                warehouses_data[wh_name] = []
            warehouses_data[wh_name].append(car)
        
        # Формируем текст для копирования
        text_for_copy = f"Авто на складе - {client.name}\n"
        text_for_copy += f"Дата: {timezone.now().strftime('%d.%m.%Y')}\n"
        text_for_copy += "=" * 40 + "\n\n"
        
        for wh_name, wh_cars in warehouses_data.items():
            text_for_copy += f"📍 {wh_name} ({len(wh_cars)} авто)\n"
            text_for_copy += "-" * 30 + "\n"
            for car in wh_cars:
                text_for_copy += f"• {car.vin} - {car.brand} {car.year}"
                if car.unload_date:
                    text_for_copy += f" (разгр. {car.unload_date.strftime('%d.%m.%Y')})"
                text_for_copy += "\n"
            text_for_copy += "\n"
        
        text_for_copy += f"Итого: {cars.count()} авто"
        
        context = {
            'client': client,
            'cars': cars,
            'warehouses_data': warehouses_data,
            'text_for_copy': text_for_copy,
            'total_count': cars.count(),
            'opts': self.model._meta,
            'title': f'Авто на складе - {client.name}',
        }
        
        return render(request, 'admin/client_cars_in_warehouse.html', context)

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    change_form_template = 'admin/company_change.html'
    list_display = ('name', 'balance_display', 'is_main_company', 'created_at', 'updated_at')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at', 'balance')
    actions = ['reset_company_balance']
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('name',)
        }),
        ('Баланс', {
            'fields': ('balance',),
            'description': 'Баланс компании'
        }),
        ('Системная информация', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at')
        }),
    )

    def balance_display(self, obj):
        """Показывает баланс компании"""
        try:
            balance = obj.balance or 0
            color = '#28a745' if balance >= 0 else '#dc3545'
            sign = '+' if balance >= 0 else ''
            return format_html(
                '<span style="color:{}; font-weight:bold;">{} {:.2f}</span>',
                color, sign, balance
            )
        except:
            return '-'
    balance_display.short_description = 'Баланс'
    
    def is_main_company(self, obj):
        """Показывает, является ли компания главной"""
        return obj.name == "Caromoto Lithuania"
    is_main_company.boolean = True
    is_main_company.short_description = "Главная компания"
    
    def invoices_display(self, obj):
        """Показывает связанные инвойсы"""
        try:
            # Инвойсы, выставляемые компанией
            outgoing_invoices = Invoice.objects.filter(
                from_entity_type='COMPANY',
                from_entity_id=obj.id
            ).order_by('-issue_date')[:10]
            
            # Инвойсы, получаемые компанией
            incoming_invoices = Invoice.objects.filter(
                to_entity_type='COMPANY',
                to_entity_id=obj.id
            ).order_by('-issue_date')[:10]
            
            html = ['<div style="margin-top:15px;">']
            
            # Исходящие инвойсы
            html.append('<h4 style="margin-bottom:10px; color:#495057;">Инвойсы, выставляемые компанией</h4>')
            if outgoing_invoices.exists():
                html.append('<table style="width:100%; border-collapse:collapse; font-size:12px;">')
                html.append('<tr style="background-color:#f8f9fa;">')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Номер</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Кому</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Статус</th>')
                html.append('</tr>')
                
                for invoice in outgoing_invoices:
                    status_color = '#28a745' if invoice.paid else '#dc3545'
                    status_text = 'Оплачен' if invoice.paid else 'Не оплачен'
                    html.append('<tr>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;"><a href="/admin/core/invoice/{invoice.id}/change/">{invoice.number}</a></td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{invoice.to_entity_name}</td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{invoice.total_amount:.2f}</td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px; color:{status_color};">{status_text}</td>')
                    html.append('</tr>')
                
                html.append('</table>')
            else:
                html.append('<p style="color:#6c757d;">Нет исходящих инвойсов</p>')
            
            # Входящие инвойсы
            html.append('<h4 style="margin-top:20px; margin-bottom:10px; color:#495057;">Инвойсы, получаемые компанией</h4>')
            if incoming_invoices.exists():
                html.append('<table style="width:100%; border-collapse:collapse; font-size:12px;">')
                html.append('<tr style="background-color:#f8f9fa;">')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Номер</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">От кого</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>')
                html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Статус</th>')
                html.append('</tr>')
                
                for invoice in incoming_invoices:
                    status_color = '#28a745' if invoice.paid else '#dc3545'
                    status_text = 'Оплачен' if invoice.paid else 'Не оплачен'
                    html.append('<tr>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;"><a href="/admin/core/invoice/{invoice.id}/change/">{invoice.number}</a></td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{invoice.from_entity_name}</td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{invoice.total_amount:.2f}</td>')
                    html.append(f'<td style="border:1px solid #dee2e6; padding:8px; color:{status_color};">{status_text}</td>')
                    html.append('</tr>')
                
                html.append('</table>')
            else:
                html.append('<p style="color:#6c757d;">Нет входящих инвойсов</p>')
            
            html.append('</div>')
            return format_html(''.join(html))
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки инвойсов: {e}</p>')
    invoices_display.short_description = 'Связанные инвойсы'
    
    def payments_display(self, obj):
        """Показывает связанные платежи"""
        try:
            # Платежи, где компания является отправителем или получателем
            payments = Payment.objects.filter(
                models.Q(from_company=obj) | models.Q(to_company=obj)
            ).order_by('-date')[:20]
            
            if not payments.exists():
                return format_html('<p style="color:#6c757d;">Нет связанных платежей</p>')
            
            html = ['<div style="margin-top:15px;">']
            html.append('<h4 style="margin-bottom:10px; color:#495057;">Последние платежи</h4>')
            html.append('<table style="width:100%; border-collapse:collapse; font-size:12px;">')
            html.append('<tr style="background-color:#f8f9fa;">')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Дата</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Тип</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">От кого</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Кому</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Описание</th>')
            html.append('</tr>')
            
            for payment in payments:
                # Определяем, является ли компания отправителем или получателем
                if payment.from_company == obj:
                    # Компания отправляет деньги
                    amount_color = '#dc3545'  # Красный для исходящих
                    amount_sign = '-'
                    amount_display = f"{amount_sign}{payment.amount:.2f}"
                else:
                    # Компания получает деньги
                    amount_color = '#28a745'  # Зеленый для входящих
                    amount_sign = '+'
                    amount_display = f"{amount_sign}{payment.amount:.2f}"
                
                html.append('<tr>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.date}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.get_payment_type_display()}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px; color:{amount_color}; font-weight:bold;">{amount_display}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.sender or "-"}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.recipient or "-"}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.description or "-"}</td>')
                html.append('</tr>')
            
            html.append('</table>')
            html.append('</div>')
            return format_html(''.join(html))
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки платежей: {e}</p>')
    payments_display.short_description = 'Платежи'
    
    def balance_summary_display(self, obj):
        """Показывает сводку по балансу компании"""
        try:
            cash_balance = obj.cash_balance or 0
            card_balance = obj.card_balance or 0
            invoice_balance = obj.invoice_balance or 0
            total_balance = cash_balance + card_balance + invoice_balance
            
            html = f"""
            <div style="background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">
                <h3 style="margin-top:0; color:#495057;">Сводка по балансу компании</h3>
                
                <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; margin-bottom:20px;">
                    <div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">
                        <strong>Наличный баланс:</strong><br>
                        <span style="font-size:18px; color:{'#28a745' if cash_balance >= 0 else '#dc3545'};">{cash_balance:.2f}</span>
                    </div>
                    <div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">
                        <strong>Безналичный баланс:</strong><br>
                        <span style="font-size:18px; color:{'#28a745' if card_balance >= 0 else '#dc3545'};">{card_balance:.2f}</span>
                    </div>
                    <div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">
                        <strong>Инвойс-баланс:</strong><br>
                        <span style="font-size:18px; color:{'#28a745' if invoice_balance >= 0 else '#dc3545'};">{invoice_balance:.2f}</span>
                    </div>
                </div>
                
                <div style="background:white; padding:15px; border-radius:5px; border:2px solid {'#28a745' if total_balance >= 0 else '#dc3545'};">
                    <strong style="color:{'#28a745' if total_balance >= 0 else '#dc3545'};">Общий баланс:</strong><br>
                    <span style="font-size:24px; font-weight:bold; color:{'#28a745' if total_balance >= 0 else '#dc3545'};">{total_balance:.2f}</span>
                </div>
                
                <!-- Кнопка для перехода к дашборду (только для Caromoto Lithuania) -->
                {f'''
                <div style="margin-top:20px; text-align:center;">
                    <a href="/company-dashboard/" style="display:inline-block; padding:12px 24px; background:#667eea; color:white; text-decoration:none; border-radius:8px; font-weight:600; font-size:16px;">
                        🏢 Открыть дашборд компании
                    </a>
                </div>
                ''' if obj.name == "Caromoto Lithuania" else ""}
            </div>
            """
            
            return format_html(html)
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки баланса: {e}</p>')
    balance_summary_display.short_description = 'Сводка по балансу'

    def balance_transactions_display(self, obj):
        """Показывает платежи компании"""
        try:
            # Получаем все платежи для компании
            payments = Payment.objects.filter(
                models.Q(from_company=obj) | models.Q(to_company=obj)
            ).order_by('-date', '-id')[:20]  # Последние 20 платежей
            
            if not payments.exists():
                return format_html('<p style="color:#6c757d;">Нет платежей</p>')
            
            html = ['<div style="margin-top:15px;">']
            html.append('<h4 style="margin-bottom:10px; color:#495057;">Последние платежи</h4>')
            html.append('<table style="width:100%; border-collapse:collapse; font-size:12px;">')
            html.append('<tr style="background-color:#f8f9fa;">')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Дата</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Тип</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Отправитель</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Получатель</th>')
            html.append('<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Описание</th>')
            html.append('</tr>')
            
            for payment in payments:
                amount_color = '#28a745' if payment.to_company == obj else '#dc3545'
                html.append('<tr>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.date.strftime("%d.%m.%Y")}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.get_payment_type_display()}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px; color:{amount_color}; font-weight:bold;">{payment.amount:.2f}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.sender or "-"}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.recipient or "-"}</td>')
                html.append(f'<td style="border:1px solid #dee2e6; padding:8px;">{payment.description or "-"}</td>')
                html.append('</tr>')
            
            html.append('</table>')
            html.append('</div>')
            
            return format_html(''.join(html))
        except Exception as e:
            return format_html(f'<p style="color:#dc3545;">Ошибка загрузки платежей: {e}</p>')
    balance_transactions_display.short_description = 'Платежи'
    
    def reset_company_balance(self, request, queryset):
        """Обнуляет балансы выбранных компаний"""
        from django.contrib import messages
        
        try:
            for company in queryset:
                company.balance = 0
                company.save()
            
            messages.success(request, f'Балансы {queryset.count()} компаний успешно обнулены')
        except Exception as e:
            messages.error(request, f'Ошибка при обнулении балансов: {e}')
    
    reset_company_balance.short_description = 'Обнулить балансы выбранных компаний'


# Регистрация моделей в админке Django
admin.site.register(Container, ContainerAdmin)
# LineServiceInline удален - используется кастомный раздел "Управление услугами"


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    change_form_template = 'admin/line_change.html'
    form = LineForm
    list_display = ('name', 'balance_display')
    search_fields = ('name',)
    readonly_fields = ('balance',)
    actions = ['reset_line_balance']
    exclude = ('ocean_freight_rate', 'documentation_fee', 'handling_fee', 'ths_fee', 'additional_fees')
    inlines = [LineServiceInline]
    fieldsets = (
        ('Основные данные', {
            'fields': ('name',)
        }),
        ('Баланс', {
            'fields': ('balance',),
            'description': 'Баланс линии'
        }),
    )

    def balance_display(self, obj):
        """Показывает баланс линии"""
        try:
            balance = obj.balance or 0
            color = '#28a745' if balance >= 0 else '#dc3545'
            sign = '+' if balance >= 0 else ''
            return format_html(
                '<span style="color:{}; font-weight:bold;">{} {:.2f}</span>',
                color, sign, balance
            )
        except Exception as e:
            return '-'
    balance_display.short_description = 'Баланс'
    
    def reset_line_balance(self, request, queryset):
        """Обнуляет балансы выбранных линий"""
        from django.contrib import messages
        
        try:
            for line in queryset:
                line.balance = 0
                line.save()
            
            messages.success(request, f'Балансы {queryset.count()} линий успешно обнулены')
        except Exception as e:
            messages.error(request, f'Ошибка при обнулении балансов: {e}')
    
    reset_line_balance.short_description = 'Обнулить балансы выбранных линий'


    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Переопределяем change_view для обработки услуг"""
        extra_context = extra_context or {}
        
        if object_id:
            obj = self.get_object(request, object_id)
            
            if request.method == 'POST':
                # Обрабатываем существующие услуги
                for key, value in request.POST.items():
                    if key.startswith('service_name_'):
                        service_id = key.replace('service_name_', '')
                        try:
                            service = LineService.objects.get(id=service_id, line=obj)
                            service.name = value
                            service.save()
                        except LineService.DoesNotExist:
                            pass
                    elif key.startswith('service_price_'):
                        service_id = key.replace('service_price_', '')
                        try:
                            service = LineService.objects.get(id=service_id, line=obj)
                            service.default_price = float(value) if value else 0
                            service.save()
                        except (LineService.DoesNotExist, ValueError):
                            pass
                    elif key.startswith('delete_service_'):
                        service_id = key.replace('delete_service_', '')
                        try:
                            service = LineService.objects.get(id=service_id, line=obj)
                            service.delete()
                        except LineService.DoesNotExist:
                            pass
                
                # Обрабатываем новые услуги
                for key, value in request.POST.items():
                    if key.startswith('new_service_name_'):
                        index = key.replace('new_service_name_', '')
                        name = value
                        price = request.POST.get(f'new_service_price_{index}', 0)
                        
                        if name:
                            try:
                                LineService.objects.create(
                                    line=obj,
                                    name=name,
                                    default_price=float(price) if price else 0
                                )
                            except ValueError:
                                pass
        
        return super().change_view(request, object_id, form_url, extra_context)




# CarServiceAdmin удален

    def get_form(self, request, obj=None, **kwargs):
        """Переопределяем форму для добавления динамических полей"""
        form = super().get_form(request, obj, **kwargs)
        
        if obj and obj.pk:
            # Добавляем динамические поля в fieldsets
            dynamic_fields = []
            for service in obj.services.all():
                field_name = f'service_{service.id}'
                dynamic_fields.append(field_name)
            
            if dynamic_fields:
                # Обновляем fieldsets для добавления динамических полей
                self.fieldsets = list(self.fieldsets)
                for i, (title, options) in enumerate(self.fieldsets):
                    if title == 'Услуги и цены':
                        fields = list(options['fields'])
                        fields.append(tuple(dynamic_fields))
                        self.fieldsets[i] = (title, {**options, 'fields': tuple(fields)})
                        break
        
        return form


# CarrierServiceInline удален - используется кастомный раздел "Управление услугами"


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    change_form_template = 'admin/carrier_change.html'
    form = CarrierForm
    list_display = ('name', 'contact_person', 'phone', 'balance_display')
    search_fields = ('name', 'contact_person', 'phone', 'email')
    list_filter = ('created_at',)
    readonly_fields = ('created_at', 'updated_at', 'balance')
    exclude = ('transport_rate', 'loading_fee', 'unloading_fee', 'fuel_surcharge', 'additional_fees')
    inlines = [CarrierServiceInline]
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'short_name', 'contact_person', 'phone', 'email')
        }),
        ('Баланс', {
            'fields': ('balance',)
        }),
        ('Системная информация', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def balance_display(self, obj):
        """Показывает баланс перевозчика"""
        try:
            balance = obj.balance or 0
            color = '#28a745' if balance >= 0 else '#dc3545'
            sign = '+' if balance >= 0 else ''
            return format_html(
                '<span style="color:{}; font-weight:bold;">{} {:.2f}</span>',
                color, sign, balance
            )
        except:
            return '-'
    balance_display.short_description = 'Баланс'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Переопределяем change_view для обработки услуг"""
        extra_context = extra_context or {}
        
        if object_id:
            obj = self.get_object(request, object_id)
            
            if request.method == 'POST':
                # Обрабатываем существующие услуги
                for key, value in request.POST.items():
                    if key.startswith('service_name_'):
                        service_id = key.replace('service_name_', '')
                        try:
                            service = CarrierService.objects.get(id=service_id, carrier=obj)
                            service.name = value
                            service.save()
                        except CarrierService.DoesNotExist:
                            pass
                    elif key.startswith('service_price_'):
                        service_id = key.replace('service_price_', '')
                        try:
                            service = CarrierService.objects.get(id=service_id, carrier=obj)
                            service.default_price = float(value) if value else 0
                            service.save()
                        except (CarrierService.DoesNotExist, ValueError):
                            pass
                    elif key.startswith('delete_service_'):
                        service_id = key.replace('delete_service_', '')
                        try:
                            service = CarrierService.objects.get(id=service_id, carrier=obj)
                            service.delete()
                        except CarrierService.DoesNotExist:
                            pass
                
                # Обрабатываем новые услуги
                for key, value in request.POST.items():
                    if key.startswith('new_service_name_'):
                        index = key.replace('new_service_name_', '')
                        name = value
                        price = request.POST.get(f'new_service_price_{index}', 0)
                        
                        if name:
                            try:
                                CarrierService.objects.create(
                                    carrier=obj,
                                    name=name,
                                    default_price=float(price) if price else 0
                                )
                            except ValueError:
                                pass
        
        return super().change_view(request, object_id, form_url, extra_context)



# Company уже зарегистрирован через @admin.register выше


# ==============================================================================
# 🎉 НОВАЯ СИСТЕМА ИНВОЙСОВ И ПЛАТЕЖЕЙ
# ==============================================================================
# Импортируем админку для новой системы
# Модели автоматически регистрируются через @admin.register() в admin_billing.py

try:
    from .admin_billing import NewInvoiceAdmin, TransactionAdmin
    # Регистрация происходит автоматически при импорте
except ImportError as e:
    import logging
    logger = logging.getLogger('django')
    logger.warning(f"⚠ Не удалось загрузить админку новой системы: {e}")
    logger.warning("Убедитесь, что файлы admin_billing.py и models_billing.py существуют")

# Остальные модели уже зарегистрированы через декораторы @admin.register