import logging

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db import models
from decimal import Decimal

from core.models import (
    Car, CarService, WarehouseService, LineService,
    CarrierService, CompanyService, DeletedCarService,
)
from core.admin_filters import MultiStatusFilter, MultiWarehouseFilter, ClientAutocompleteFilter

logger = logging.getLogger('django')


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/car/change_form.html'
    change_list_template = 'admin/core/car/change_list.html'
    list_display = (
        'vin', 'brand', 'vehicle_type', 'year_display', 'client', 'colored_status', 'container_display', 'warehouse', 'line',
        'unload_date_display', 'days_display', 'storage_cost_display', 'total_price_display', 'markup_display', 'has_title'
    )
    list_editable = ('has_title',)
    list_filter = (MultiStatusFilter, ClientAutocompleteFilter, MultiWarehouseFilter)
    search_fields = ('vin', 'brand')
    list_per_page = 50
    show_full_result_count = False
    # OPTIMIZATION: Preload related objects for list view
    list_select_related = ('client', 'warehouse', 'line', 'carrier', 'container')

    def get_queryset(self, request):
        """Optimized queryset with select_related, prefetch_related, and annotation."""
        from django.db.models import Sum

        qs = super().get_queryset(request)
        qs = qs.select_related('client', 'warehouse', 'line', 'carrier', 'container')
        qs = qs.prefetch_related('car_services')
        qs = qs.annotate(_total_markup=Sum('car_services__markup_amount'))
        return qs
    readonly_fields = (
        'default_warehouse_prices_display', 'total_price', 'storage_cost', 'days', 'warehouse_payment_display',
        'free_days_display', 'rate_display', 'services_summary_display', 'warehouse_services_display', 'line_services_display', 'carrier_services_display', 'company_services_display'
    )
    # inlines = []  # Services managed through sections below
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
        ('Услуги', {
            'classes': ('collapse',),
            'fields': (
                'company_services_display',
            )
        }),
        ('Финансы', {
            'fields': (
                'services_summary_display',
            )
        }),
    )
    actions = ['set_status_floating', 'set_status_in_port', 'set_status_unloaded', 'set_status_transferred', 'set_transferred_today', 'set_title_with_us']

    def set_transferred_today(self, request, queryset):
        """Sets status to 'Transferred' and transfer date to today"""
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

    # --- Warehouse payment display ---
    def warehouse_payment_display(self, obj):
        return f"{obj.warehouse_payment_amount():.2f}"

    warehouse_payment_display.short_description = 'Оплата складу'


    def services_summary_display(self, obj):
        """Displays summary of all services with Caromoto Lithuania markup"""
        from decimal import Decimal
        from core.models import CarService
        from django.db.models import Sum

        # Split services: lines (all + THS from warehouse), warehouse (without THS), carrier, companies
        line_total = Decimal('0.00')  # All line services + THS (even if through warehouse)
        warehouse_total = Decimal('0.00')  # Warehouse services (without THS)
        carrier_total = obj.get_services_total_by_provider('CARRIER')
        company_total = obj.get_services_total_by_provider('COMPANY')

        # Get all services and split by categories
        # THS is always considered a line service, even if paid through warehouse
        for service in obj.car_services.all():
            service_name = service.get_service_name().upper()
            price = Decimal(str(service.final_price))

            if service.service_type == 'LINE' or 'THS' in service_name:
                line_total += price  # All line services + THS from warehouse
            elif service.service_type == 'WAREHOUSE':
                warehouse_total += price  # Warehouse services without THS

        # Paid days for display
        paid_days = obj.days or 0

        # Sum of distributed markups (from services)
        distributed_markup = obj.car_services.aggregate(total=Sum('markup_amount'))['total'] or Decimal('0')

        # Markup from proft field (if not distributed)
        proft_amount = obj.proft or Decimal('0.00')

        # Total markup = only distributed (proft no longer used)
        total_markup = distributed_markup

        # Base totals (without markup)
        base_total = line_total + warehouse_total + carrier_total + company_total

        html = ['<div style="margin-top:15px; background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">']
        html.append('<h3 style="margin-top:0; color:#495057;">Сводка по услугам</h3>')

        html.append('<div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr 1fr; gap:15px; margin-bottom:20px;">')

        # Line services (THS, Shipping to Georgia etc.) - with details
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги линий:</strong><br>')

        # Show each line service (including THS from warehouse)
        line_services_list = []
        for service in obj.car_services.all():
            service_name = service.get_service_name()
            # THS is considered a line service even if paid through warehouse
            if service.service_type == 'LINE' or 'THS' in service_name.upper():
                price = Decimal(str(service.final_price))
                line_services_list.append((service_name, price))

        for name, price in line_services_list:
            html.append(f'<span style="font-size:13px; color:#6c757d;">{name}: {price:.2f}</span><br>')

        html.append(f'<span style="font-size:18px; color:#007bff; font-weight:bold;">Итого: {line_total:.2f}</span>')
        html.append('</div>')

        # Warehouse (without THS) - with service details
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги склада:</strong><br>')

        # Show each warehouse service (except THS)
        warehouse_services_list = []
        for service in obj.car_services.filter(service_type='WAREHOUSE'):
            service_name = service.get_service_name()
            if 'THS' not in service_name.upper():
                price = Decimal(str(service.final_price))
                warehouse_services_list.append((service_name, price))

        for name, price in warehouse_services_list:
            html.append(f'<span style="font-size:13px; color:#6c757d;">{name}: {price:.2f}</span><br>')

        if obj.warehouse:
            free_days = obj.warehouse.free_days or 0
            html.append(f'<span style="font-size:12px; color:#adb5bd;">Беспл. дней: {free_days}, Плат. дней: {paid_days}</span><br>')

        html.append(f'<span style="font-size:18px; color:#28a745; font-weight:bold;">Итого: {warehouse_total:.2f}</span>')
        html.append('</div>')

        # Carrier
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Перевозчик:</strong><br>')
        html.append(f'<span style="font-size:18px; color:#ffc107;">{carrier_total:.2f}</span>')
        html.append('</div>')

        # Companies
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги компаний:</strong><br>')

        company_services_list = []
        for service in obj.car_services.filter(service_type='COMPANY'):
            try:
                company_service = CompanyService.objects.select_related('company').get(id=service.service_id)
                price = Decimal(str(service.final_price))
                company_services_list.append((company_service.company.name, company_service.name, price))
            except Exception:
                continue

        for company_name, name, price in company_services_list:
            html.append(f'<span style="font-size:13px; color:#6c757d;">{company_name}: {name}: {price:.2f}</span><br>')

        html.append(f'<span style="font-size:18px; color:#6f42c1; font-weight:bold;">Итого: {company_total:.2f}</span>')
        html.append('</div>')

        # Markup - show distributed amount
        html.append('<div style="background:#fffde7; padding:10px; border-radius:5px; border:1px solid #ffc107;">')
        html.append('<strong style="color:#ff8f00;">Скрытая наценка:</strong><br>')
        html.append(f'<span style="font-size:18px; font-weight:bold; color:#ff8f00;">{distributed_markup:.2f}</span>')
        if distributed_markup > 0:
            html.append(f'<br><span style="font-size:11px; color:#666;">(распределена по услугам)</span>')
        else:
            html.append(f'<br><span style="font-size:11px; color:#666;">(введите в жёлтых полях)</span>')
        html.append('</div>')

        html.append('</div>')

        # Grand total
        total_with_markup = base_total + total_markup
        html.append('<div style="background:white; padding:15px; border-radius:5px; border:2px solid #6c757d;">')
        html.append('<strong style="color:#6c757d;">Итого к оплате:</strong><br>')
        html.append(f'<span style="font-size:20px; font-weight:bold; color:#495057;">{total_with_markup:.2f} EUR</span>')
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
        """Displays container number with clickable link and status-based styling"""
        if not obj.container:
            return '-'

        # Use car status for color (like status)
        color = obj.get_status_color()

        # Create link to container
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
    unload_date_display.short_description = 'Дата разгрузки'
    unload_date_display.admin_order_field = 'unload_date'

    def transfer_date_display(self, obj):
        return obj.transfer_date
    transfer_date_display.short_description = 'Передан'
    transfer_date_display.admin_order_field = 'transfer_date'

    def storage_cost_display(self, obj):
        """Shows storage cost calculated from warehouse fields"""
        try:
            storage_cost = obj.calculate_storage_cost()
            return f"{storage_cost:.2f}"
        except Exception as e:
            logger.error(f"Ошибка расчета стоимости хранения: {e}")
            return f"{obj.storage_cost:.2f}"  # Fallback to old field
    storage_cost_display.short_description = 'Хран'
    storage_cost_display.admin_order_field = 'storage_cost'

    def days_display(self, obj):
        """Shows paid days accounting for free days from warehouse"""
        if obj.warehouse and obj.unload_date:
            # Calculate total storage days
            end_date = obj.transfer_date if obj.status == 'TRANSFERRED' and obj.transfer_date else timezone.now().date()
            total_days = (end_date - obj.unload_date).days + 1

            free_days = obj.warehouse.free_days or 0
            chargeable_days = max(0, total_days - free_days)
            return f"{chargeable_days} (из {total_days})"
        return obj.days if hasattr(obj, 'days') else 0
    days_display.short_description = 'Плат.дн.'
    days_display.admin_order_field = 'days'

    def total_price_display(self, obj):
        # For non-transferred cars calculate price dynamically
        if obj.status != 'TRANSFERRED':
            from decimal import Decimal

            # Calculate services total from preloaded car_services (no extra queries)
            base_total = Decimal('0.00')
            for cs in obj.car_services.all():
                base_total += Decimal(str(cs.final_price))

            # Use _total_markup annotation from get_queryset (if available)
            if hasattr(obj, '_total_markup') and obj._total_markup is not None:
                distributed_markup = obj._total_markup
            else:
                distributed_markup = sum(
                    (cs.markup_amount for cs in obj.car_services.all() if cs.markup_amount),
                    Decimal('0.00')
                )

            total = base_total + distributed_markup
            return f"{total:.2f}"

        return f"{obj.total_price:.2f}"
    total_price_display.short_description = 'Цена'
    total_price_display.admin_order_field = 'total_price'

    def markup_display(self, obj):
        """Displays total hidden markup for car.

        Uses annotation from get_queryset for performance.
        If annotation not available (car card), makes direct query.
        """
        # If annotation from get_queryset exists - use it (for list)
        if hasattr(obj, '_total_markup'):
            return f"{obj._total_markup:.2f}" if obj._total_markup else "0.00"

        # Fallback for car card or if annotation failed
        from decimal import Decimal
        from django.db.models import Sum

        total_markup = obj.car_services.aggregate(
            total=Sum('markup_amount')
        )['total'] or Decimal('0.00')

        return f"{total_markup:.2f}"

    markup_display.short_description = 'Н'  # Н = Наценка
    markup_display.admin_order_field = '_total_markup'  # Sort by annotation

    def changelist_view(self, request, extra_context=None):
        """Override changelist_view to add total markup sum.

        Calculates total markup sum for DISPLAYED cars on the page
        and passes it to template context.
        """
        from django.db.models import Sum
        from decimal import Decimal

        # Call parent method to get response
        response = super().changelist_view(request, extra_context)

        try:
            # Get changelist from response
            cl = response.context_data['cl']

            # Get filtered queryset (cars being displayed)
            queryset = cl.get_queryset(request)

            # Calculate total markup sum for displayed cars
            total_markup_sum = queryset.aggregate(
                total=Sum('car_services__markup_amount')
            )['total'] or Decimal('0.00')

            # Add to context
            response.context_data['total_markup_sum'] = total_markup_sum

        except (AttributeError, KeyError):
            # If something went wrong, just don't add the sum
            pass

        return response

    def free_days_display(self, obj):
        """Shows free days from warehouse"""
        if obj.warehouse:
            return obj.warehouse.free_days
        return obj.free_days  # Fallback to old field
    free_days_display.short_description = 'FREE'
    free_days_display.admin_order_field = 'free_days'

    def rate_display(self, obj):
        """Shows daily rate from warehouse 'Storage' service"""
        if obj.warehouse:
            daily_rate = obj._get_storage_daily_rate()
            return f"{daily_rate:.2f}"
        return "0.00"
    rate_display.short_description = 'Ставка/день'

    def set_status_floating(self, request, queryset):
        updated = queryset.update(status='FLOATING')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.save(update_fields=['days', 'storage_cost', 'total_price'])
        self.message_user(request, f"Статус изменён на 'В пути' для {updated} автомобилей.")
    set_status_floating.short_description = "Изменить статус на В пути"

    def set_status_in_port(self, request, queryset):
        updated = queryset.update(status='IN_PORT')
        for obj in queryset:
            obj.update_days_and_storage()
            obj.save(update_fields=['days', 'storage_cost', 'total_price'])
        self.message_user(request, f"Статус изменён на 'В порту' для {updated} автомобилей.")
    set_status_in_port.short_description = "Изменить статус на В порту"

    def set_status_unloaded(self, request, queryset):
        updated = 0
        for obj in queryset:
            if obj.warehouse and obj.unload_date:
                obj.status = 'UNLOADED'
                obj.update_days_and_storage()
                obj.save(update_fields=['status', 'days', 'storage_cost', 'total_price'])
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
            obj.save(update_fields=['transfer_date', 'days', 'storage_cost', 'total_price'])
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
                'style',
            )
        }
        js = ('js/htmx.min.js', 'js/logist2_htmx.js')

    def save_model(self, request, obj, form, change):
        """Saves model with service field processing"""
        super().save_model(request, obj, form, change)

        # First handle service deletions
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
                    # Add to blacklist
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service_id
                    )
                    logger.debug(f"Deleted warehouse service {service_id}: {deleted_count}")
                except Exception as e:
                    logger.error(f"Error deleting warehouse service {service_id}: {e}")
            elif key.startswith('remove_line_service_') and value == '1':
                service_id = key.replace('remove_line_service_', '')
                removed_services.add(f'line_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='LINE',
                        service_id=service_id
                    ).delete()
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='LINE',
                        service_id=service_id
                    )
                    logger.debug(f"Deleted line service {service_id}: {deleted_count}")
                except Exception as e:
                    logger.error(f"Error deleting line service {service_id}: {e}")
            elif key.startswith('remove_carrier_service_') and value == '1':
                service_id = key.replace('remove_carrier_service_', '')
                removed_services.add(f'carrier_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service_id
                    ).delete()
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service_id
                    )
                    logger.debug(f"Deleted carrier service {service_id}: {deleted_count}")
                except Exception as e:
                    logger.error(f"Error deleting carrier service {service_id}: {e}")
            elif key.startswith('remove_company_service_') and value == '1':
                service_id = key.replace('remove_company_service_', '')
                removed_services.add(f'company_{service_id}')
                try:
                    deleted_count = CarService.objects.filter(
                        car=obj,
                        service_type='COMPANY',
                        service_id=service_id
                    ).delete()
                    DeletedCarService.objects.get_or_create(
                        car=obj,
                        service_type='COMPANY',
                        service_id=service_id
                    )
                    logger.debug(f"Deleted company service {service_id}: {deleted_count}")
                except Exception as e:
                    logger.error(f"Error deleting company service {service_id}: {e}")

        logger.debug(f"Removed services: {removed_services}")

        # Process warehouse service fields
        # First update ALL existing warehouse services
        existing_warehouse_car_services = CarService.objects.filter(
            car=obj,
            service_type='WAREHOUSE'
        )

        for car_service in existing_warehouse_car_services:
            # Check if service was deleted
            if f'warehouse_{car_service.service_id}' in removed_services:
                continue

            field_name = f'warehouse_service_{car_service.service_id}'
            value = request.POST.get(field_name)

            if value:
                try:
                    car_service.custom_price = float(value)
                except (ValueError, TypeError):
                    pass

            # Save hidden markup
            markup_field = f'markup_warehouse_service_{car_service.service_id}'
            markup_value = request.POST.get(markup_field, '0')
            try:
                car_service.markup_amount = float(markup_value) if markup_value else 0
            except (ValueError, TypeError):
                car_service.markup_amount = 0
            car_service.save()

        # Then create new services from catalog (if needed)
        if obj.warehouse:
            warehouse_services = WarehouseService.objects.filter(
                warehouse=obj.warehouse,
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')

            existing_car_service_ids = set(existing_warehouse_car_services.values_list('service_id', flat=True))

            # Get blacklist of deleted services
            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='WAREHOUSE'
            ).values_list('service_id', flat=True)

            for service in warehouse_services:
                # Check if service was deleted
                if f'warehouse_{service.id}' in removed_services:
                    continue

                # Check blacklist
                if service.id in deleted_services:
                    continue

                # If service not yet in CarService, create automatically
                if service.id not in existing_car_service_ids:
                    field_name = f'warehouse_service_{service.id}'
                    value = request.POST.get(field_name) or service.default_price
                    # Get default_markup from service
                    default_markup = getattr(service, 'default_markup', 0) or 0
                    CarService.objects.create(
                        car=obj,
                        service_type='WAREHOUSE',
                        service_id=service.id,
                        custom_price=float(value),
                        markup_amount=float(default_markup)
                    )

        # Process line service fields
        # First update ALL existing line services (including THS)
        existing_line_car_services = CarService.objects.filter(
            car=obj,
            service_type='LINE'
        )

        for car_service in existing_line_car_services:
            if f'line_{car_service.service_id}' in removed_services:
                continue

            field_name = f'line_service_{car_service.service_id}'
            value = request.POST.get(field_name)

            if value:
                try:
                    car_service.custom_price = float(value)
                except (ValueError, TypeError):
                    pass

            # Save hidden markup
            markup_field = f'markup_line_service_{car_service.service_id}'
            markup_value = request.POST.get(markup_field, '0')
            try:
                car_service.markup_amount = float(markup_value) if markup_value else 0
            except (ValueError, TypeError):
                car_service.markup_amount = 0
            car_service.save()

        # Then create new services from catalog (if needed)
        if obj.line:
            line_services = LineService.objects.filter(
                line=obj.line,
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')

            existing_car_service_ids = set(existing_line_car_services.values_list('service_id', flat=True))

            # Get blacklist of deleted services
            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='LINE'
            ).values_list('service_id', flat=True)

            for service in line_services:
                if f'line_{service.id}' in removed_services:
                    continue

                if service.id in deleted_services:
                    continue

                if service.id not in existing_car_service_ids:
                    field_name = f'line_service_{service.id}'
                    value = request.POST.get(field_name) or service.default_price
                    default_markup = getattr(service, 'default_markup', 0) or 0
                    CarService.objects.create(
                        car=obj,
                        service_type='LINE',
                        service_id=service.id,
                        custom_price=float(value),
                        markup_amount=float(default_markup)
                    )

        # Process carrier service fields
        existing_carrier_car_services = CarService.objects.filter(
            car=obj,
            service_type='CARRIER'
        )

        for car_service in existing_carrier_car_services:
            if f'carrier_{car_service.service_id}' in removed_services:
                continue

            field_name = f'carrier_service_{car_service.service_id}'
            value = request.POST.get(field_name)

            if value:
                try:
                    car_service.custom_price = float(value)
                except (ValueError, TypeError):
                    pass

            markup_field = f'markup_carrier_service_{car_service.service_id}'
            markup_value = request.POST.get(markup_field, '0')
            try:
                car_service.markup_amount = float(markup_value) if markup_value else 0
            except (ValueError, TypeError):
                car_service.markup_amount = 0
            car_service.save()

        # Then create new services from catalog (if needed)
        if obj.carrier:
            carrier_services = CarrierService.objects.filter(
                carrier=obj.carrier,
                is_active=True,
                default_price__gt=0
            ).only('id', 'default_price')

            existing_car_service_ids = set(existing_carrier_car_services.values_list('service_id', flat=True))

            deleted_services = DeletedCarService.objects.filter(
                car=obj,
                service_type='CARRIER'
            ).values_list('service_id', flat=True)

            for service in carrier_services:
                if f'carrier_{service.id}' in removed_services:
                    continue

                if service.id in deleted_services:
                    continue

                if service.id not in existing_car_service_ids:
                    field_name = f'carrier_service_{service.id}'
                    value = request.POST.get(field_name) or service.default_price
                    default_markup = getattr(service, 'default_markup', 0) or 0
                    CarService.objects.create(
                        car=obj,
                        service_type='CARRIER',
                        service_id=service.id,
                        custom_price=float(value),
                        markup_amount=float(default_markup)
                    )

        # Process company services
        existing_company_car_services = CarService.objects.filter(
            car=obj,
            service_type='COMPANY'
        )

        for car_service in existing_company_car_services:
            if f'company_{car_service.service_id}' in removed_services:
                continue

            field_name = f'company_service_{car_service.service_id}'
            value = request.POST.get(field_name)

            if value:
                try:
                    car_service.custom_price = float(value)
                except (ValueError, TypeError):
                    pass

            markup_field = f'markup_company_service_{car_service.service_id}'
            markup_value = request.POST.get(markup_field, '0')
            try:
                car_service.markup_amount = float(markup_value) if markup_value else 0
            except (ValueError, TypeError):
                car_service.markup_amount = 0
            car_service.save()

        # Recalculate storage cost and days when warehouse changes
        if change and form and 'warehouse' in getattr(form, 'changed_data', []):
            logger.debug(f"Склад изменился для автомобиля {obj.vin}, пересчитываем стоимость хранения")
            try:
                # Update fields based on new warehouse
                obj.update_days_and_storage()
                obj.calculate_total_price()
                # Save updated fields
                obj.save(update_fields=['storage_cost', 'days', 'total_price'])
                logger.debug(f"Обновлены поля: storage_cost={obj.storage_cost}, days={obj.days}")
            except Exception as e:
                logger.error(f"Ошибка при пересчете стоимости хранения: {e}")

    def warehouse_services_display(self, obj):
        """Displays editable fields for all warehouse services"""
        try:
            # Get ALL warehouse services already linked to car (from any warehouses)
            car_services = CarService.objects.filter(
                car=obj,
                service_type='WAREHOUSE'
            ).select_related('car')

            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'

            if car_services:
                for car_service in car_services:
                    try:
                        # Get service and warehouse details
                        service = WarehouseService.objects.select_related('warehouse').get(id=car_service.service_id)
                        current_value = car_service.custom_price or service.default_price
                        markup_value = car_service.markup_amount or 0
                        warehouse_name = service.warehouse.name

                        # Highlight: main warehouse - green, others - yellow
                        bg_color = "#e8f5e9" if (obj.warehouse and service.warehouse.id == obj.warehouse.id) else "#fff9e6"

                        html += f'''
                        <div style="border: 1px solid #ddd; padding: 10px; background: {bg_color}; position: relative; min-width: 220px;">
                            <button type="button" onclick="removeService({service.id}, 'warehouse')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                            <div style="font-size: 11px; color: #666; margin-bottom: 3px;">📦 {warehouse_name}</div>
                            <strong>{service.name}</strong><br>
                            <div style="display: flex; gap: 5px; align-items: center; margin-top: 5px;">
                                <input type="number" name="warehouse_service_{service.id}" value="{current_value}" step="0.01" style="width: 80px;" title="Цена услуги">
                                <span style="color: #28a745; font-weight: bold;">+</span>
                                <input type="number" name="markup_warehouse_service_{service.id}" value="{markup_value}" step="0.01" style="width: 60px; background: #fffde7; border-color: #ffc107;" title="Скрытая наценка" placeholder="0">
                            </div>
                            <input type="hidden" name="remove_warehouse_service_{service.id}" id="remove_warehouse_service_{service.id}" value="">
                        </div>
                        '''
                    except Exception as e:
                        continue

            html += '</div>'

            # Button to add services - always available
            html += f'''
            <div style="margin-top: 10px;">
                <button type="button" class="add-service-btn" onclick="openModal('warehouseServicesModal', 'warehouse')" title="Добавить услуги любого склада">
                    +
                </button>
                <span style="margin-left: 5px; color: #666;">Добавить услуги склада</span>
            </div>
            '''

            # JavaScript for removing services
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
        """Displays editable fields for line services"""
        if not obj.line:
            return "Линия не выбрана"

        try:
            # Get line services already linked to car
            car_services = CarService.objects.filter(
                car=obj,
                service_type='LINE'
            )

            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'

            for car_service in car_services:
                try:
                    # Get service details
                    service = LineService.objects.get(id=car_service.service_id)
                    current_value = car_service.custom_price or service.default_price
                    markup_value = car_service.markup_amount or 0

                    html += f'''
                    <div style="border: 1px solid #ddd; padding: 10px; background: #e3f2fd; position: relative; min-width: 200px;">
                        <button type="button" onclick="removeService({service.id}, 'line')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                        <strong>{service.name}</strong><br>
                        <div style="display: flex; gap: 5px; align-items: center; margin-top: 5px;">
                            <input type="number" name="line_service_{service.id}" value="{current_value}" step="0.01" style="width: 80px;" title="Цена услуги">
                            <span style="color: #28a745; font-weight: bold;">+</span>
                            <input type="number" name="markup_line_service_{service.id}" value="{markup_value}" step="0.01" style="width: 60px; background: #fffde7; border-color: #ffc107;" title="Скрытая наценка" placeholder="0">
                        </div>
                        <input type="hidden" name="remove_line_service_{service.id}" id="remove_line_service_{service.id}" value="">
                    </div>
                    '''
                except:
                    continue

            html += '</div>'

            # Button to add new services
            if obj.line:
                html += f'''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('lineServicesModal', 'line')" title="Добавить услуги линии">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги линии</span>
                </div>
                '''

            if not car_services:
                html += '<div style="margin-top: 8px; color: #6c757d;">Услуги еще не добавлены. Используйте кнопку "+".</div>'

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    line_services_display.short_description = "Услуги линии"

    def carrier_services_display(self, obj):
        """Displays editable fields for carrier services"""
        if not obj.carrier:
            return "Перевозчик не выбран"

        try:
            # Get carrier services already linked to car
            car_services = CarService.objects.filter(
                car=obj,
                service_type='CARRIER'
            )

            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'

            for car_service in car_services:
                try:
                    # Get service details
                    service = CarrierService.objects.get(id=car_service.service_id)
                    current_value = car_service.custom_price or service.default_price
                    markup_value = car_service.markup_amount or 0

                    html += f'''
                    <div style="border: 1px solid #ddd; padding: 10px; background: #fff3e0; position: relative; min-width: 200px;">
                        <button type="button" onclick="removeService({service.id}, 'carrier')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                        <strong>{service.name}</strong><br>
                        <div style="display: flex; gap: 5px; align-items: center; margin-top: 5px;">
                            <input type="number" name="carrier_service_{service.id}" value="{current_value}" step="0.01" style="width: 80px;" title="Цена услуги">
                            <span style="color: #28a745; font-weight: bold;">+</span>
                            <input type="number" name="markup_carrier_service_{service.id}" value="{markup_value}" step="0.01" style="width: 60px; background: #fffde7; border-color: #ffc107;" title="Скрытая наценка" placeholder="0">
                        </div>
                        <input type="hidden" name="remove_carrier_service_{service.id}" id="remove_carrier_service_{service.id}" value="">
                    </div>
                    '''
                except:
                    continue

            html += '</div>'

            # Button to add new services
            if obj.carrier:
                html += f'''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('carrierServicesModal', 'carrier')" title="Добавить услуги перевозчика">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги перевозчика</span>
                </div>
                '''

            if not car_services:
                html += '<div style="margin-top: 8px; color: #6c757d;">Услуги еще не добавлены. Используйте кнопку "+".</div>'

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    carrier_services_display.short_description = "Услуги перевозчика"

    def company_services_display(self, obj):
        """Displays editable fields for company services"""
        try:
            car_services = CarService.objects.filter(
                car=obj,
                service_type='COMPANY'
            )

            html = '<div style="margin: 10px 0; display: flex; flex-wrap: wrap; gap: 10px;">'

            if car_services:
                for car_service in car_services:
                    try:
                        service = CompanyService.objects.select_related('company').get(id=car_service.service_id)
                        current_value = car_service.custom_price if car_service.custom_price is not None else service.default_price
                        markup_value = car_service.markup_amount or 0

                        html += f'''
                        <div style="border: 1px solid #ddd; padding: 10px; background: #f3e8ff; position: relative; min-width: 240px;">
                            <button type="button" onclick="removeService({service.id}, 'company')" style="position: absolute; top: 5px; right: 5px; background: #dc3545; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 12px;">×</button>
                            <div style="font-size: 11px; color: #666; margin-bottom: 3px;">🏢 {service.company.name}</div>
                            <strong>{service.name}</strong><br>
                            <div style="display: flex; gap: 5px; align-items: center; margin-top: 5px;">
                                <input type="number" name="company_service_{service.id}" value="{current_value}" step="0.01" style="width: 80px;" title="Цена услуги">
                                <span style="color: #28a745; font-weight: bold;">+</span>
                                <input type="number" name="markup_company_service_{service.id}" value="{markup_value}" step="0.01" style="width: 60px; background: #fffde7; border-color: #ffc107;" title="Скрытая наценка" placeholder="0">
                            </div>
                            <input type="hidden" name="remove_company_service_{service.id}" id="remove_company_service_{service.id}" value="">
                        </div>
                        '''
                    except Exception:
                        continue

            html += '</div>'

            html += f'''
            <div style="margin-top: 10px;">
                <button type="button" class="add-service-btn" onclick="openModal('companyServicesModal', 'company')" title="Добавить услуги компании">
                    +
                </button>
                <span style="margin-left: 5px; color: #666;">Добавить услуги компании</span>
            </div>
            '''

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    company_services_display.short_description = "Услуги компаний"

    def get_changelist(self, request, **kwargs):
        """Adds default filtering for statuses 'In Port' and 'Unloaded'"""
        if not request.GET.get('status_multi'):
            get_params = request.GET.copy()
            get_params.setlist('status_multi', ['IN_PORT', 'UNLOADED'])
            request.GET = get_params
        return super().get_changelist(request, **kwargs)
