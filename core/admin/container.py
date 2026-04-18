import logging
import time
from contextlib import contextmanager

from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.utils import timezone
from django.utils.html import format_html

from core.admin.inlines import CarInline
from core.admin_filters import ClientAutocompleteFilter, MultiStatusFilter, MultiWarehouseFilter
from core.models import (
    Car,
    CarService,
    Container,
    LineService,
    WarehouseService,
)
from core.signals import (
    car_post_save,
    recalculate_car_price_on_service_delete,
    recalculate_car_price_on_service_save,
    recalculate_invoices_on_car_service_delete,
    recalculate_invoices_on_car_service_save,
)

logger = logging.getLogger(__name__)


@contextmanager
def signals_disabled(*signal_pairs):
    """Temporarily disconnect signals, guaranteed reconnection even on exception."""
    for signal, handler, sender in signal_pairs:
        signal.disconnect(handler, sender=sender)
    try:
        yield
    finally:
        for signal, handler, sender in signal_pairs:
            signal.connect(handler, sender=sender)


CAR_SIGNALS = [
    (post_save, car_post_save, Car),
    (post_save, recalculate_car_price_on_service_save, CarService),
    (post_delete, recalculate_car_price_on_service_delete, CarService),
]

INVOICE_SIGNALS = [
    (post_save, recalculate_invoices_on_car_service_save, CarService),
    (post_delete, recalculate_invoices_on_car_service_delete, CarService),
]

CONTAINER_STATUS_COLORS = {
    'В пути': '#2772a8',  # Darker blue
    'В порту': '#8B0000',  # Dark red
    'Разгружен': '#239f58',  # Darker green
    'Передан': '#78458c',  # Darker purple
}


class LabelsPrintedFilter(SimpleListFilter):
    title = 'Наклейки'
    parameter_name = 'labels_printed'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Напечатаны'),
            ('no', 'Не напечатаны'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(labels_printed_at__isnull=False)
        if self.value() == 'no':
            return queryset.filter(labels_printed_at__isnull=True)
        return queryset


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    change_form_template = 'admin/core/container/change_form.html'
    list_display = ('number', 'colored_status', 'eta', 'planned_unload_date', 'unload_date', 'line', 'warehouse', 'photos_count_display', 'labels_printed_display')
    list_display_links = ('number',)
    list_filter = (MultiStatusFilter, ClientAutocompleteFilter, MultiWarehouseFilter, LabelsPrintedFilter)
    search_fields = ('number',)
    ordering = ['-unload_date', '-id']
    list_per_page = 50
    show_full_result_count = False
    inlines = [CarInline]
    fieldsets = (
        ('Основные данные', {
            'fields': (
                ('number', 'line', 'ths', 'ths_payer', 'warehouse', 'unload_site', 'status'),
                ('eta', 'planned_unload_date', 'unload_date'),
                'google_drive_folder_url',
            )
        }),
    )
    readonly_fields = ('days', 'storage_cost')
    actions = ['print_labels_action', 'reset_labels_printed_action', 'set_status_floating', 'set_status_in_port', 'set_status_unloaded', 'set_status_transferred', 'check_container_status', 'bulk_update_container_statuses', 'sync_photos_from_gdrive', 'resend_planned_notifications', 'resend_unload_notifications']

    class Media:
        css = {'all': ('css/dashboard_admin.css',)}
        js = ('js/htmx.min.js', 'js/warehouse_address.js')

    def get_queryset(self, request):
        from django.db.models import Count
        qs = super().get_queryset(request)
        return qs.select_related('line', 'client', 'warehouse').prefetch_related(
            'container_cars'
        ).annotate(_photos_count=Count('photos'))

    def save_model(self, request, obj, form, change):
        start_time = time.time()
        logger.info(f"[TIMING] Container save_model started for {obj.number}")

        with transaction.atomic():
            self._save_model_inner(request, obj, form, change)

        logger.info(f"[TIMING] Container save_model completed in {time.time() - start_time:.2f}s")

    def _save_model_inner(self, request, obj, form, change):
        """Внутренний метод save_model, выполняемый внутри transaction.atomic()"""

        # Auto-set status to UNLOADED when unload_date is filled and status is still before unloading
        obj._status_auto_changed = False
        if obj.unload_date and obj.status in ('FLOATING', 'IN_PORT'):
            obj.status = 'UNLOADED'
            obj._status_auto_changed = True
            logger.info(f"Auto-set status to UNLOADED for container {obj.number} (unload_date={obj.unload_date})")

        # If new object without pk, save it
        if not change and not obj.pk:
            super().save_model(request, obj, form, change)
        elif change:
            # For existing objects save as usual
            super().save_model(request, obj, form, change)

        logger.info(f"[TIMING] Container saved to DB")

        # If warehouse changed - sync warehouse to all cars
        if change and form and 'warehouse' in getattr(form, 'changed_data', []):
            try:
                logger.info(f"Warehouse changed for container {obj.id}, syncing cars...")
                obj.sync_cars_after_warehouse_change()
                logger.info(f"Successfully synced warehouse for {obj.container_cars.count()} cars")
            except Exception as e:
                logger.error(f"Failed to sync cars after warehouse change for container {obj.id}: {e}")

        # If status changed (manually or auto-set) - update status for ALL cars in container
        changed_data = getattr(form, 'changed_data', []) if form else []
        status_changed_by_user = change and 'status' in changed_data
        status_changed_auto = change and getattr(obj, '_status_auto_changed', False)
        if status_changed_by_user or status_changed_auto:
            try:
                logger.info(f"Status changed for container {obj.id} to {obj.status}, bulk updating all cars...")
                updated_count = obj.container_cars.update(status=obj.status)
                logger.info(f"Updated status to '{obj.status}' for {updated_count} cars in container {obj.number}")
            except Exception as e:
                logger.error(f"Failed to update car statuses for container {obj.id}: {e}")

        # If unload date changed - update date for ALL cars in container
        if change and form and 'unload_date' in getattr(form, 'changed_data', []):
            try:
                logger.info(f"Unload date changed for container {obj.id} to {obj.unload_date}, bulk updating all cars...")
                obj.refresh_from_db()

                with signals_disabled(*CAR_SIGNALS):
                    cars_to_update = []
                    affected_invoices = set()
                    update_fields = ['unload_date', 'days', 'storage_cost', 'total_price']

                    for car in obj.container_cars.select_related('warehouse').all():
                        car.unload_date = obj.unload_date
                        if not obj.unload_date and car.status == 'UNLOADED':
                            car.status = obj.status or 'IN_PORT'
                            if 'status' not in update_fields:
                                update_fields.append('status')
                        car.update_days_and_storage()
                        car.calculate_total_price()
                        cars_to_update.append(car)

                        for invoice in car.newinvoice_set.all():
                            affected_invoices.add(invoice)

                    if cars_to_update:
                        Car.objects.bulk_update(
                            cars_to_update,
                            update_fields,
                            batch_size=50
                        )
                        logger.info(f"Bulk updated {len(cars_to_update)} cars in container {obj.number}")

                if affected_invoices:
                    logger.info(f"Updating {len(affected_invoices)} affected invoices...")
                    for invoice in affected_invoices:
                        try:
                            if hasattr(invoice, 'regenerate_items_from_cars'):
                                invoice.regenerate_items_from_cars()
                            else:
                                invoice.update_total_amount()
                        except Exception as e:
                            logger.error(f"Error updating invoice {invoice.id}: {e}")
                    logger.info(f"Updated {len(affected_invoices)} invoices")

            except Exception as e:
                logger.error(f"Failed to update cars after unload_date change for container {obj.id}: {e}")

        # Check THS-related changes: line, THS amount, THS payer, warehouse
        changed_data = getattr(form, 'changed_data', []) if form else []
        ths_related_changed = any(field in changed_data for field in ['line', 'ths', 'ths_payer', 'warehouse'])

        # For new container - create THS if line and ths are set
        # For existing - only if THS fields or warehouse changed
        should_create_ths = (not change and obj.line and obj.ths) or (change and ths_related_changed)

        # If creating new container with THS or changed THS fields - update THS services
        if should_create_ths:
            line_start = time.time()
            try:
                from core.models_billing import NewInvoice
                from core.services.car_service_manager import (
                    apply_client_tariffs_for_container,
                    create_ths_services_for_container,
                )

                logger.info(f"[TIMING] THS-related change started for container {obj.id}, line: {obj.line}, ths: {obj.ths}, ths_payer: {obj.ths_payer}")

                with signals_disabled(*(CAR_SIGNALS + INVOICE_SIGNALS)):
                    if 'line' in changed_data:
                        updated_count = obj.container_cars.update(line=obj.line)
                        logger.info(f"[TIMING] Line updated for {updated_count} cars")

                    if obj.line and obj.ths:
                        created_count = create_ths_services_for_container(obj)
                        logger.info(f"[TIMING] Created {created_count} THS services with proportional distribution")
                        apply_client_tariffs_for_container(obj)
                    else:
                        car_ids = list(obj.container_cars.values_list('id', flat=True))
                        deleted_line = CarService.objects.filter(
                            car_id__in=car_ids,
                            service_type='LINE'
                        ).filter(
                            service_id__in=LineService.objects.filter(name__icontains='THS').values_list('id', flat=True)
                        ).delete()
                        deleted_wh = CarService.objects.filter(
                            car_id__in=car_ids,
                            service_type='WAREHOUSE'
                        ).filter(
                            service_id__in=WarehouseService.objects.filter(name__icontains='THS').values_list('id', flat=True)
                        ).delete()
                        logger.info(f"[TIMING] Deleted {deleted_line[0]} line THS services and {deleted_wh[0]} warehouse THS services")

                    cars_to_update = []
                    affected_invoices = set()
                    for car in obj.container_cars.select_related('warehouse').all():
                        car.update_days_and_storage()
                        car.calculate_total_price()
                        cars_to_update.append(car)
                        for invoice in NewInvoice.objects.filter(cars=car, status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE']):
                            affected_invoices.add(invoice)

                    if cars_to_update:
                        Car.objects.bulk_update(
                            cars_to_update,
                            ['days', 'storage_cost', 'total_price'],
                            batch_size=50
                        )
                        logger.info(f"[TIMING] Recalculated prices for {len(cars_to_update)} cars")

                    if affected_invoices:
                        logger.info(f"[TIMING] Updating {len(affected_invoices)} affected invoices...")
                        for invoice in affected_invoices:
                            try:
                                invoice.regenerate_items_from_cars()
                            except Exception as e:
                                logger.error(f"Error updating invoice {invoice.number}: {e}")
                        logger.info(f"[TIMING] Invoices updated")

                    logger.info(f"[TIMING] THS-related change completed in {time.time() - line_start:.2f}s")

            except Exception as e:
                logger.error(f"Failed to update cars after line change for container {obj.id}: {e}", exc_info=True)

    def save_formset(self, request, form, formset, change):
        formset_start = time.time()
        logger.info(f"[TIMING] save_formset started for {formset.model.__name__}")

        instances = formset.save(commit=False)
        logger.info(f"[TIMING] formset.save(commit=False) took {time.time() - formset_start:.2f}s")

        parent = form.instance  # container

        # Check that parent has primary key
        if not parent.pk:
            logger.error("Parent container doesn't have a primary key - saving parent first")
            parent.save()
            logger.info(f"Saved parent container {parent.pk}")

        # Skip if no changed instances and no deleted objects
        if not instances and not formset.deleted_objects:
            logger.info(f"[TIMING] No changes in formset, skipping. Total: {time.time() - formset_start:.2f}s")
            formset.save_m2m()
            return

        logger.info(f"[TIMING] Processing {len(instances)} changed instances")

        for obj in instances:
            if isinstance(obj, Car):
                # Bind to container
                if not obj.container_id:
                    obj.container = parent

                # Status always matches container
                obj.status = parent.status

                # Warehouse/client/line from container if not set
                if not obj.warehouse_id and parent.warehouse_id:
                    obj.warehouse = parent.warehouse
                if not obj.client_id and parent.client_id:
                    obj.client = parent.client
                if not obj.line_id and parent.line_id:
                    obj.line = parent.line

                # Unload date ALWAYS inherited from container
                if parent.unload_date:
                    obj.unload_date = parent.unload_date
                    logger.debug(f"Car {obj.vin}: inherited unload_date={obj.unload_date} from container {parent.number}")

                creating = obj.pk is None
                if creating and obj.warehouse_id:
                    # Pull warehouse defaults (rate/free_days etc.) BEFORE first save()
                    obj.set_initial_warehouse_values()

                # Recalculate before saving
                obj.update_days_and_storage()

                # Save object - post_save signal handles calculate_total_price
                obj.save()

                logger.debug(f"Saved Car {obj.vin} (creating={creating}, has_title={obj.has_title})")

                # For new cars, force create warehouse services with markup
                if creating and obj.warehouse_id:
                    from decimal import Decimal

                    from core.models import CarService, WarehouseService

                    warehouse_services = WarehouseService.objects.filter(
                        warehouse=obj.warehouse,
                        is_active=True,
                        add_by_default=True
                    )

                    for service in warehouse_services:
                        if not CarService.objects.filter(car=obj, service_type='WAREHOUSE', service_id=service.id).exists():
                            from core.service_codes import is_storage_service
                            if is_storage_service(service):
                                days = Decimal(str(obj.days or 0))
                                custom_price = days * Decimal(str(service.default_price or 0))
                                default_markup = days * Decimal(str(service.default_markup or 0))
                            else:
                                custom_price = service.default_price
                                default_markup = service.default_markup or Decimal('0')

                            CarService.objects.create(
                                car=obj,
                                service_type='WAREHOUSE',
                                service_id=service.id,
                                custom_price=custom_price,
                                markup_amount=default_markup
                            )
                            logger.info(f"[FORMSET] Created warehouse service '{service.name}' for {obj.vin} (price: {custom_price}, markup: {default_markup})")
            else:
                obj.save()

        # Delete objects marked for deletion
        deleted_cars = [o for o in formset.deleted_objects if isinstance(o, Car)]
        logger.info(f"[FORMSET] deleted_objects count: {len(formset.deleted_objects)}, deleted_cars: {len(deleted_cars)}")

        for o in formset.deleted_objects:
            try:
                # First delete related CarService
                if isinstance(o, Car):
                    o.car_services.all().delete()
                o.delete()
                logger.info(f"[FORMSET] Deleted object: {o}")
            except Exception as e:
                logger.error(f"Error deleting object {o}: {e}")

        formset.save_m2m()

        # After any changes to cars (add/change/delete) - recalculate THS
        cars_changed = bool(instances) or bool(deleted_cars)
        logger.info(f"[FORMSET] instances count: {len(instances)}, cars_changed: {cars_changed}")
        logger.info(f"[FORMSET] parent.line: {parent.line}, parent.ths: {parent.ths}")

        # Recalculate THS only when cars changed (added/removed/deleted)
        # to avoid wiping manual THS edits on unrelated container saves
        if cars_changed and parent.line and parent.ths:
            try:
                from django.db import transaction

                from core.services.car_service_manager import (
                    apply_client_tariffs_for_container,
                    create_ths_services_for_container,
                )

                # Force refresh container data from DB
                parent.refresh_from_db()

                logger.info(f"[FORMSET] Starting THS recalculation for container {parent.number}")

                # Use savepoint for safe recalculation
                with transaction.atomic():
                    # Recalculate THS for ALL cars in container
                    cars_in_container = list(parent.container_cars.all())
                    logger.info(f"[FORMSET] Found {len(cars_in_container)} cars in container")

                    if cars_in_container:
                        created = create_ths_services_for_container(parent)
                        logger.info(f"[FORMSET] Created/updated {created} THS services for container {parent.number}")
                        # Apply client tariffs
                        apply_client_tariffs_for_container(parent)

                        # Recalculate prices for ALL cars in container after THS update
                        for car in cars_in_container:
                            car.refresh_from_db()  # Refresh car data from DB
                            car.calculate_total_price()
                            car.save(update_fields=['total_price', 'storage_cost', 'days'])
                            logger.info(f"[FORMSET] Recalculated price for car {car.vin}: {car.total_price}")
                        logger.info(f"[FORMSET] Recalculated prices for all {len(cars_in_container)} cars")
                    else:
                        logger.info(f"[FORMSET] No cars left in container {parent.number}")
            except Exception as e:
                logger.error(f"Failed to create THS services in formset for container {parent.id}: {e}", exc_info=True)


    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in form.base_fields.values():
            field.help_text = ''
        if 'line' in form.base_fields:
            form.base_fields['line'].label = 'Линия'
        return form

    def colored_status(self, obj):
        color = obj.get_status_color()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span>',
            color,
            obj.get_status_display()
        )
    colored_status.short_description = 'Статус'

    def photos_count_display(self, obj):
        """Displays count of container photos (uses annotation when available)"""
        count = getattr(obj, '_photos_count', None)
        if count is None:
            count = obj.photos.count()
        if count > 0:
            return format_html(
                '<span style="background-color: #4285f4; color: white; padding: 2px 8px; border-radius: 10px;">📷 {}</span>',
                count
            )
        return '-'
    photos_count_display.short_description = 'Фото'
    photos_count_display.admin_order_field = '_photos_count'

    def set_status_floating(self, request, queryset):
        pks = list(queryset.values_list('pk', flat=True))
        updated = queryset.update(status='FLOATING')
        for obj in Container.objects.filter(pk__in=pks):
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
            obj.container_cars.update(status='FLOATING')
        self.message_user(request, f"Статус изменён на 'В пути' для {updated} контейнеров и их авто.")
    set_status_floating.short_description = "Изменить статус на В пути"

    def set_status_in_port(self, request, queryset):
        pks = list(queryset.values_list('pk', flat=True))
        updated = queryset.update(status='IN_PORT')
        for obj in Container.objects.filter(pk__in=pks):
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
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
                obj.container_cars.update(status='UNLOADED')
                updated += 1
            else:
                self.message_user(request, f"Контейнер {obj.number} не обновлён: требуются поля 'Склад' и 'Дата разгрузки'.", level='warning')
        self.message_user(request, f"Статус изменён на 'Разгружен' для {updated} контейнеров и их авто.")
    set_status_unloaded.short_description = "Изменить статус на Разгружен"

    def set_status_transferred(self, request, queryset):
        pks = list(queryset.values_list('pk', flat=True))
        updated = queryset.update(status='TRANSFERRED')
        for obj in Container.objects.filter(pk__in=pks):
            obj.update_days_and_storage()
            obj.sync_cars()
            obj.save(update_fields=['days', 'storage_cost'])
            obj.container_cars.update(status='TRANSFERRED')
        self.message_user(request, f"Статус изменён на 'Передан' для {updated} контейнеров и их авто.")
    set_status_transferred.short_description = "Изменить статус на Передан"

    def check_container_status(self, request, queryset):
        """Checks and updates container status based on car statuses"""
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
        """Bulk update container statuses based on car statuses"""
        updated_count = 0
        skipped_count = 0
        error_count = 0

        for container in queryset:
            try:
                cars = container.container_cars.all()
                if not cars.exists():
                    skipped_count += 1
                    continue

                # Check if all cars are transferred
                all_transferred = all(car.status == 'TRANSFERRED' for car in cars)
                sum(1 for car in cars if car.status == 'TRANSFERRED')
                cars.count()

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

        # Form message for user
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
        """Syncs photos from Google Drive for selected containers"""
        from core.google_drive_sync import GoogleDriveSync

        total_photos = 0
        success_count = 0
        error_count = 0

        for container in queryset:
            try:
                photos_added = GoogleDriveSync.sync_container_by_number(
                    container.number, verbose=True
                )

                if photos_added > 0:
                    success_count += 1
                    total_photos += photos_added
                    logger.info(f"Container {container.number}: added {photos_added} photos")
                else:
                    logger.warning(f"Container {container.number}: no new photos found on Google Drive")

            except Exception as e:
                error_count += 1
                logger.error(f"Error syncing container {container.number}: {e}")

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
        """Resends planned unload date notifications to clients"""
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
                f"Отправлено {total_sent} уведомлений о планируемой разгрузке для {containers_processed} контейнеров. Ошибок: {total_failed}",
                level='SUCCESS'
            )
        elif total_failed > 0:
            self.message_user(
                request,
                f"Не удалось отправить уведомления. Ошибок: {total_failed}. Проверьте email клиентов.",
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
        """Resends unload notifications to clients"""
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
                f"Отправлено {total_sent} уведомлений о разгрузке для {containers_processed} контейнеров. Ошибок: {total_failed}",
                level='SUCCESS'
            )
        elif total_failed > 0:
            self.message_user(
                request,
                f"Не удалось отправить уведомления. Ошибок: {total_failed}. Проверьте email клиентов.",
                level='ERROR'
            )
        else:
            self.message_user(
                request,
                "Нет клиентов с email для отправки уведомлений (или уведомления уже были отправлены)",
                level='WARNING'
            )

    resend_unload_notifications.short_description = "📧 Повторить уведомление о разгрузке"

    def print_labels_action(self, request, queryset):
        """Редиректит на страницу настройки печати наклеек с выбранными контейнерами."""
        from core.views.labels import redirect_to_print_settings
        ids = list(queryset.values_list('id', flat=True))
        if not ids:
            self.message_user(request, "Выберите хотя бы один контейнер.", level='WARNING')
            return None
        return redirect_to_print_settings(ids)
    print_labels_action.short_description = "🏷️ Распечатать наклейки"

    def labels_printed_display(self, obj):
        """Колонка-индикатор: напечатаны ли наклейки для контейнера."""
        if obj.labels_printed_at:
            local = timezone.localtime(obj.labels_printed_at)
            return format_html(
                '<span title="{}" style="background-color: #2e7d32; color: #fff; padding: 2px 8px; border-radius: 10px; white-space: nowrap;">🏷️ {}</span>',
                local.strftime('%d.%m.%Y %H:%M'),
                local.strftime('%d.%m'),
            )
        return format_html(
            '<span style="color: #9999b5;">—</span>'
        )
    labels_printed_display.short_description = 'Наклейки'
    labels_printed_display.admin_order_field = 'labels_printed_at'

    def reset_labels_printed_action(self, request, queryset):
        """Сбрасывает отметку о печати наклеек — чтобы можно было перепечатать с «чистого листа»."""
        updated = queryset.filter(labels_printed_at__isnull=False).update(labels_printed_at=None)
        if updated:
            self.message_user(
                request,
                f"Отметка о печати наклеек сброшена для {updated} контейнеров.",
                level='SUCCESS',
            )
        else:
            self.message_user(
                request,
                "Выбранные контейнеры не имели отметки о печати.",
                level='INFO',
            )
    reset_labels_printed_action.short_description = "🏷️ Сбросить отметку о печати наклеек"

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Override change_view to pass photo data to template"""
        extra_context = extra_context or {}

        if object_id:
            obj = self.get_object(request, object_id)
            if obj:
                # Only count photos - fast COUNT query
                # Photo data loaded via AJAX on click
                extra_context['photos_count'] = obj.photos.count()
                extra_context['container_id'] = object_id

        return super().change_view(request, object_id, form_url, extra_context)

    def get_changelist(self, request, **kwargs):
        """Adds default filtering for statuses 'In Port' and 'Unloaded'"""
        if not request.GET.get('status_multi'):
            get_params = request.GET.copy()
            get_params.setlist('status_multi', ['IN_PORT', 'UNLOADED'])
            request.GET = get_params
        return super().get_changelist(request, **kwargs)
