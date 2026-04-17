"""
Signal handlers for core models.

Each handler is a thin wrapper that delegates to the appropriate service.
Business logic lives in core.services.*, NOT here.
"""
import logging
from decimal import Decimal

from django.db import OperationalError, transaction
from django.db.models import Q
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    Car,
    CarrierService,
    CarService,
    CompanyService,
    Container,
    DeletedCarService,
    LineService,
    WarehouseService,
)
from .models_banking import BankTransaction
from .models_billing import NewInvoice, Transaction
from .service_codes import ServiceCode, is_storage_service

logger = logging.getLogger(__name__)


# ============================================================================
# SERVICE CACHE INVALIDATION
# ============================================================================

def invalidate_service_cache(sender, instance, **kwargs):
    from django.core.cache import cache
    type_map = {
        LineService: 'LINE', WarehouseService: 'WAREHOUSE',
        CarrierService: 'CARRIER', CompanyService: 'COMPANY',
    }
    svc_type = type_map.get(sender)
    if svc_type:
        cache.delete(f"svc:{svc_type}:{instance.id}")


for _model in (LineService, WarehouseService, CarrierService, CompanyService):
    post_save.connect(invalidate_service_cache, sender=_model)
    post_delete.connect(invalidate_service_cache, sender=_model)


# ============================================================================
# CONTAINER PRE_SAVE / POST_SAVE
# ============================================================================

@receiver(pre_save, sender=Container)
def save_old_container_values(sender, instance, **kwargs):
    if instance.unload_date and instance.status in ('FLOATING', 'IN_PORT'):
        instance.status = 'UNLOADED'
        logger.info("[PRE_SAVE] Auto-set status to UNLOADED for container %s", instance.number)

    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        instance._pre_save_values = None
        instance._pre_save_notification = None
        return

    if instance.pk:
        try:
            old = Container.objects.filter(pk=instance.pk).values(
                'status', 'unload_date', 'planned_unload_date'
            ).first()
            if old:
                instance._pre_save_values = old
                instance._pre_save_notification = {
                    'planned_unload_date': old.get('planned_unload_date'),
                    'unload_date': old.get('unload_date'),
                }
                old_status = old.get('status')
                if (
                    instance.status == 'UNLOADED'
                    and old_status != 'UNLOADED'
                    and not instance.unloaded_status_at
                ):
                    instance.unloaded_status_at = timezone.now()
            else:
                instance._pre_save_values = None
                instance._pre_save_notification = None
        except Exception as e:
            logger.error("[PRE_SAVE] Error: %s", e)
            instance._pre_save_values = None
            instance._pre_save_notification = None
    else:
        instance._pre_save_values = None
        instance._pre_save_notification = None
        if instance.status == 'UNLOADED' and not instance.unloaded_status_at:
            instance.unloaded_status_at = timezone.now()


@receiver(post_save, sender=Container)
def update_related_on_container_save(sender, instance, created, **kwargs):
    old_values = getattr(instance, '_pre_save_values', None)
    instance._pre_save_values = None

    if not instance.pk:
        return

    if old_values:
        old_unload_date = old_values.get('unload_date')
        new_unload_date = instance.unload_date

        if old_unload_date != new_unload_date and new_unload_date is not None:
            logger.info(
                "[SIGNAL] unload_date changed for container %s: %s -> %s",
                instance.number, old_unload_date, new_unload_date,
            )
            try:
                out_of_sync = instance.container_cars.exclude(
                    unload_date=new_unload_date
                ).count()
                if out_of_sync == 0:
                    return

                updated_count = instance.container_cars.update(unload_date=new_unload_date)
                logger.info(
                    "[SIGNAL] Updated unload_date to %s for %d cars in container %s",
                    new_unload_date, updated_count, instance.number,
                )

                if updated_count > 0:
                    cars_to_update = []
                    for car in instance.container_cars.select_related('warehouse').prefetch_related('car_services').all():
                        car.update_days_and_storage()
                        car.calculate_total_price()
                        cars_to_update.append(car)

                    if cars_to_update:
                        Car.objects.bulk_update(
                            cars_to_update,
                            ['days', 'storage_cost', 'total_price'],
                            batch_size=50,
                        )
            except Exception as e:
                logger.error(
                    "[SIGNAL] Failed to update cars for container %s: %s",
                    instance.number, e, exc_info=True,
                )


# ============================================================================
# CAR PRE_SAVE / POST_SAVE
# ============================================================================

@receiver(pre_save, sender=Car)
def save_old_car_values(sender, instance, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        tracked = {'warehouse_id', 'line_id', 'carrier_id', 'unload_date', 'container_id', 'status'}
        if not tracked.intersection(update_fields):
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
            instance._pre_save_status = None
            return

    if instance.pk:
        try:
            old = Car.objects.filter(pk=instance.pk).values(
                'warehouse_id', 'line_id', 'carrier_id', 'unload_date', 'container_id', 'status'
            ).first()
            if old:
                instance._pre_save_contractors = {
                    'warehouse_id': old['warehouse_id'],
                    'line_id': old['line_id'],
                    'carrier_id': old['carrier_id'],
                }
                instance._pre_save_car_notification = {
                    'unload_date': old['unload_date'],
                    'container_id': old['container_id'],
                }
                instance._pre_save_status = old['status']
            else:
                instance._pre_save_contractors = None
                instance._pre_save_car_notification = None
                instance._pre_save_status = None
        except Exception:
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
            instance._pre_save_status = None
    else:
        instance._pre_save_contractors = None
        instance._pre_save_car_notification = None
        instance._pre_save_status = None


@receiver(post_save, sender=Car)
def car_post_save(sender, instance, **kwargs):
    """Consolidated post_save handler for Car.

    Responsibilities (in order):
    1. Create/update CarService records when contractors change.
    2. Deferred invoice regeneration.
    3. Email notifications (standalone cars).
    4. Container status auto-update on transfer.
    """
    if not instance.pk:
        return

    created = kwargs.get('created', False)

    # --- 1. Invoice regeneration ---
    if not getattr(instance, '_updating_invoices', False):
        invoice_ids = list(NewInvoice.objects.filter(cars=instance).values_list('id', flat=True))
        if invoice_ids:
            _deferred_invoice_regeneration_for_car(instance.pk, invoice_ids)

    # --- 2. CarService creation (delegates to helper) ---
    _create_car_services_if_needed(instance, created=created, kwargs=kwargs)

    # --- 3. Email notification for standalone cars ---
    _maybe_send_car_unload_notification(instance, created=created)

    # --- 4. Container status auto-update ---
    old_status = getattr(instance, '_pre_save_status', None)
    instance._pre_save_status = None
    if instance.status == 'TRANSFERRED' and old_status != 'TRANSFERRED' and instance.container_id:
        _update_container_status_if_all_transferred(instance.container_id)


def _deferred_invoice_regeneration_for_car(car_id, invoice_ids):
    """Schedule invoice regeneration after commit."""
    def _do():
        for inv_id in invoice_ids:
            try:
                with transaction.atomic():
                    inv = NewInvoice.objects.select_for_update(nowait=True).get(id=inv_id)
                    inv.regenerate_items_from_cars()
            except OperationalError:
                logger.warning("Skipping invoice %s - locked", inv_id)
            except NewInvoice.DoesNotExist:
                pass
            except Exception as e:
                logger.error("Failed to regenerate invoice %s: %s", inv_id, e)
    transaction.on_commit(_do)


def _maybe_send_car_unload_notification(instance, *, created):
    """Send unload notification for standalone (non-container) cars."""
    if instance.container_id:
        return
    old_values = getattr(instance, '_pre_save_car_notification', None) or {}
    instance._pre_save_car_notification = None
    if old_values.get('container_id'):
        return
    old_unload_date = old_values.get('unload_date')
    if instance.unload_date and (created or old_unload_date is None):
        def _enqueue():
            try:
                from core.tasks import send_car_unload_notification_task
                send_car_unload_notification_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import CarNotificationService
                if not CarNotificationService.was_car_unload_notification_sent(instance):
                    CarNotificationService.send_car_unload_notification(instance)
        transaction.on_commit(_enqueue)


def _create_car_services_if_needed(instance, *, created, kwargs):
    """Create CarService records when a car's contractors change.

    Only recreates services for the contractor type that actually changed,
    preserving services (and manual edits) of unaffected types.
    """
    from .services.car_service_manager import (
        find_carrier_services_for_car,
        find_company_services_for_car,
        find_line_services_for_car,
        find_warehouse_services_for_car,
        get_main_company,
    )

    if not instance.pk:
        return
    if getattr(instance, '_creating_services', False):
        return

    warehouse_changed = False
    line_changed = False
    carrier_changed = False

    if not created:
        old_contractors = getattr(instance, '_pre_save_contractors', None)
        instance._pre_save_contractors = None
        if old_contractors:
            warehouse_changed = old_contractors.get('warehouse_id') != instance.warehouse_id
            line_changed = old_contractors.get('line_id') != instance.line_id
            carrier_changed = old_contractors.get('carrier_id') != instance.carrier_id
            if not (warehouse_changed or line_changed or carrier_changed):
                return
        else:
            return
    else:
        warehouse_changed = True
        line_changed = True
        carrier_changed = True

    instance._creating_services = True
    try:
        deleted_by_type = {}
        for stype in ('WAREHOUSE', 'LINE', 'CARRIER', 'COMPANY'):
            deleted_by_type[stype] = set(
                DeletedCarService.objects.filter(car=instance, service_type=stype)
                .values_list('service_id', flat=True)
            )

        # WAREHOUSE — only when warehouse actually changed
        if warehouse_changed:
            instance.car_services.filter(service_type='WAREHOUSE').delete()
            if instance.warehouse:
                for service in find_warehouse_services_for_car(instance.warehouse):
                    if service.id in deleted_by_type['WAREHOUSE']:
                        continue
                    if is_storage_service(service):
                        days = Decimal(str(instance.days or 0))
                        custom_price = days * Decimal(str(service.default_price or 0))
                        default_markup = days * Decimal(str(getattr(service, 'default_markup', 0) or 0))
                    else:
                        custom_price = service.default_price
                        default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance, service_type='WAREHOUSE', service_id=service.id,
                        defaults={'custom_price': custom_price, 'markup_amount': default_markup},
                    )

        # LINE (non-THS only; THS managed by create_ths_services_for_container)
        # Only when line actually changed
        if line_changed:
            ths_line_ids = LineService.objects.filter(
                Q(code=ServiceCode.THS) | Q(name__icontains='THS')
            ).values_list('id', flat=True)
            instance.car_services.filter(service_type='LINE').exclude(
                service_id__in=ths_line_ids
            ).delete()
            if instance.line:
                for service in find_line_services_for_car(instance.line):
                    if service.id in deleted_by_type['LINE']:
                        continue
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance, service_type='LINE', service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup},
                    )

        # CARRIER — only when carrier actually changed
        if carrier_changed:
            instance.car_services.filter(service_type='CARRIER').delete()
            if instance.carrier:
                for service in find_carrier_services_for_car(instance.carrier):
                    if service.id in deleted_by_type['CARRIER']:
                        continue
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance, service_type='CARRIER', service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup},
                    )

        # COMPANY (only for newly created cars)
        if created:
            main_company = get_main_company()
            if main_company:
                for service in find_company_services_for_car(main_company):
                    if service.id in deleted_by_type['COMPANY']:
                        continue
                    default_markup = getattr(service, 'default_markup', None) or Decimal('0')
                    CarService.objects.get_or_create(
                        car=instance, service_type='COMPANY', service_id=service.id,
                        defaults={'custom_price': service.default_price, 'markup_amount': default_markup},
                    )
    except Exception as e:
        logger.error("Error creating car services: %s", e)
    finally:
        instance._creating_services = False


# ============================================================================
# CARSERVICE PRICE RECALCULATION
# ============================================================================

@receiver(post_save, sender=CarService)
def recalculate_car_price_on_service_save(sender, instance, **kwargs):
    if getattr(instance.car, '_creating_services', False):
        return
    try:
        instance.car.calculate_total_price()
        Car.objects.filter(id=instance.car.id).update(
            total_price=instance.car.total_price,
            days=instance.car.days,
            storage_cost=instance.car.storage_cost,
        )
    except Exception as e:
        logger.error("Error recalculating price on service save: %s", e)


@receiver(post_delete, sender=CarService)
def recalculate_car_price_on_service_delete(sender, instance, **kwargs):
    if getattr(instance.car, '_creating_services', False):
        return
    try:
        instance.car.calculate_total_price()
        Car.objects.filter(id=instance.car.id).update(
            total_price=instance.car.total_price,
            days=instance.car.days,
            storage_cost=instance.car.storage_cost,
        )
    except Exception as e:
        logger.error("Error recalculating price on service delete: %s", e)


# ============================================================================
# CARSERVICE -> INVOICE REGENERATION
# ============================================================================

def _deferred_invoice_regeneration(car_id):
    def _do_regenerate():
        try:
            invoice_ids = list(
                NewInvoice.objects.filter(
                    cars__id=car_id,
                    status__in=['DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'OVERDUE'],
                ).values_list('id', flat=True)
            )
            for invoice_id in invoice_ids:
                try:
                    with transaction.atomic():
                        invoice = NewInvoice.objects.select_for_update(nowait=True).get(id=invoice_id)
                        invoice.regenerate_items_from_cars()
                except OperationalError:
                    logger.warning("Skipping invoice %s - locked", invoice_id)
                except NewInvoice.DoesNotExist:
                    pass
        except Exception as e:
            logger.error("Error in deferred invoice regeneration for car %s: %s", car_id, e)
    transaction.on_commit(_do_regenerate)


@receiver(post_save, sender=CarService)
def recalculate_invoices_on_car_service_save(sender, instance, **kwargs):
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)


@receiver(post_delete, sender=CarService)
def recalculate_invoices_on_car_service_delete(sender, instance, **kwargs):
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)


# ============================================================================
# SERVICE CATALOG CHANGES -> UPDATE EXISTING CARSERVICE RECORDS
# ============================================================================

@receiver(post_save, sender=WarehouseService)
def update_cars_on_warehouse_service_change(sender, instance, **kwargs):
    try:
        if instance.is_active and instance.default_price > 0:
            car_services = list(CarService.objects.filter(
                service_type='WAREHOUSE', service_id=instance.id, car__warehouse=instance.warehouse
            ).select_related('car'))
            if not car_services:
                return
            default_markup_val = getattr(instance, 'default_markup', None) or Decimal('0')
            for cs in car_services:
                if is_storage_service(instance):
                    days = Decimal(str(cs.car.days or 0))
                    cs.custom_price = days * Decimal(str(instance.default_price or 0))
                    cs.markup_amount = days * Decimal(str(default_markup_val))
                else:
                    cs.custom_price = instance.default_price
                    cs.markup_amount = default_markup_val
            CarService.objects.bulk_update(car_services, ['custom_price', 'markup_amount'], batch_size=100)
        else:
            affected_car_ids = list(CarService.objects.filter(
                service_type='WAREHOUSE', service_id=instance.id
            ).values_list('car_id', flat=True))
            CarService.objects.filter(service_type='WAREHOUSE', service_id=instance.id).delete()
            if affected_car_ids:
                cars_to_update = []
                for car in Car.objects.filter(pk__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error("Error updating cars on warehouse service change: %s", e)


@receiver(post_save, sender=LineService)
def update_cars_on_line_service_change(sender, instance, **kwargs):
    if not instance.is_active:
        try:
            affected_car_ids = list(CarService.objects.filter(
                service_type='LINE', service_id=instance.id
            ).values_list('car_id', flat=True))
            deleted = CarService.objects.filter(service_type='LINE', service_id=instance.id).delete()
            if deleted[0] > 0:
                cars_to_update = []
                for car in Car.objects.filter(id__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
        except Exception as e:
            logger.error("Error deleting inactive line service: %s", e)


@receiver(post_save, sender=CarrierService)
def update_cars_on_carrier_service_change(sender, instance, **kwargs):
    try:
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, 'default_markup', None) or Decimal('0')
            CarService.objects.filter(
                service_type='CARRIER', service_id=instance.id, car__carrier=instance.carrier
            ).update(custom_price=instance.default_price, markup_amount=default_markup)
        else:
            affected_car_ids = list(CarService.objects.filter(
                service_type='CARRIER', service_id=instance.id
            ).values_list('car_id', flat=True))
            CarService.objects.filter(service_type='CARRIER', service_id=instance.id).delete()
            if affected_car_ids:
                cars_to_update = []
                for car in Car.objects.filter(pk__in=affected_car_ids):
                    car.calculate_total_price()
                    cars_to_update.append(car)
                if cars_to_update:
                    Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error("Error updating cars on carrier service change: %s", e)


@receiver(post_save, sender=CompanyService)
def update_cars_on_company_service_change(sender, instance, **kwargs):
    try:
        car_services = CarService.objects.filter(service_type='COMPANY', service_id=instance.id)
        affected_car_ids = list(car_services.values_list('car_id', flat=True).distinct())
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, 'default_markup', None) or Decimal('0')
            car_services.update(custom_price=instance.default_price, markup_amount=default_markup)
        else:
            car_services.delete()
        if affected_car_ids:
            cars_to_update = []
            for car in Car.objects.filter(id__in=affected_car_ids):
                car.calculate_total_price()
                cars_to_update.append(car)
            if cars_to_update:
                Car.objects.bulk_update(cars_to_update, ['total_price'], batch_size=100)
    except Exception as e:
        logger.error("Error updating cars on company service change: %s", e)


# ============================================================================
# CASCADE DELETE CARSERVICE ON CATALOG SERVICE DELETION
# ============================================================================

@receiver(pre_delete, sender=LineService)
def delete_car_services_on_line_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type='LINE', service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on LineService delete: %s", e)


@receiver(pre_delete, sender=WarehouseService)
def delete_car_services_on_warehouse_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type='WAREHOUSE', service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on WarehouseService delete: %s", e)


@receiver(pre_delete, sender=CarrierService)
def delete_car_services_on_carrier_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type='CARRIER', service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on CarrierService delete: %s", e)


@receiver(pre_delete, sender=CompanyService)
def delete_car_services_on_company_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type='COMPANY', service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on CompanyService delete: %s", e)


# ============================================================================
# INVOICE AUTO-CATEGORIZATION + SITEPRO PUSH
# ============================================================================

@receiver(pre_save, sender=NewInvoice)
def auto_categorize_invoice(sender, instance, **kwargs):
    if instance.category_id:
        return
    if instance.issuer_warehouse_id or instance.issuer_line_id or instance.issuer_carrier_id:
        try:
            from .models_billing import ExpenseCategory
            logistics_cat = ExpenseCategory.objects.filter(category_type='OPERATIONAL').first()
            if logistics_cat:
                instance.category = logistics_cat
        except Exception as e:
            logger.warning("Не удалось назначить категорию: %s", e)


@receiver(pre_save, sender=NewInvoice)
def save_old_invoice_status(sender, instance, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        instance._pre_save_status = None
        return
    if instance.pk:
        try:
            old = NewInvoice.objects.filter(pk=instance.pk).values('status').first()
            instance._pre_save_status = old['status'] if old else None
        except Exception:
            instance._pre_save_status = None
    else:
        instance._pre_save_status = None


@receiver(post_save, sender=NewInvoice)
def auto_push_invoice_to_sitepro(sender, instance, created, **kwargs):
    """Ставит пуш в site.pro в очередь Celery после commit транзакции.

    Синхронный fallback — если Celery недоступен.
    """
    if not instance.pk:
        return

    old_status = getattr(instance, '_pre_save_status', None)
    instance._pre_save_status = None
    if instance.status != 'ISSUED' or old_status == 'ISSUED':
        return
    if getattr(instance, 'document_type', 'PROFORMA') != 'INVOICE':
        return
    if getattr(instance, '_pushing_to_sitepro', False):
        return

    invoice_id = instance.pk
    invoice_number = instance.number

    def _queue():
        try:
            from core.tasks import push_invoice_to_sitepro_task
            push_invoice_to_sitepro_task.delay(invoice_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SitePro] Celery unavailable for invoice %s, pushing inline: %s",
                invoice_number, exc,
            )
            try:
                from core.models_accounting import SiteProConnection
                connection = SiteProConnection.objects.filter(
                    is_active=True, auto_push_on_issue=True
                ).first()
                if not connection:
                    return
                instance._pushing_to_sitepro = True
                try:
                    from core.services.sitepro_service import SiteProService
                    SiteProService(connection).push_invoice(instance)
                    logger.info('[SitePro] Auto-pushed invoice %s on ISSUED (sync)', invoice_number)
                finally:
                    instance._pushing_to_sitepro = False
            except Exception as e:  # noqa: BLE001
                logger.error('[SitePro] Error auto-pushing invoice %s: %s', invoice_number, e)

    transaction.on_commit(_queue)


@receiver(post_save, sender=NewInvoice)
def sync_linked_invoice_status(sender, instance, **kwargs):
    """When an invoice becomes PAID, mark its linked pair as LINKED_PAID.

    LINKED_PAID — отдельный статус, показывающий что инвойс закрыт не
    собственным платежом, а через связанный документ (BLC ↔ PARDP/FACT).
    Он не учитывается как обычный PAID в `check_balance_consistency` —
    оплата уже прошла по парному инвойсу, дублирующий Transaction
    создавать нельзя, иначе поедет баланс склада/линии/перевозчика.
    """
    if instance.status not in ('PAID', 'LINKED_PAID'):
        return
    if getattr(instance, '_syncing_linked', False):
        return

    linked = None
    if instance.linked_invoice_id:
        linked = instance.linked_invoice
    else:
        linked = getattr(instance, 'linked_from', None)
        if linked is not None:
            try:
                linked = NewInvoice.objects.get(pk=linked.pk)
            except NewInvoice.DoesNotExist:
                linked = None

    if linked and linked.status not in ('PAID', 'LINKED_PAID', 'CANCELLED'):
        linked._syncing_linked = True
        linked.paid_amount = linked.total
        linked.status = 'LINKED_PAID'
        linked.save(update_fields=['paid_amount', 'status', 'updated_at'])
        logger.info(
            'Linked invoice %s marked LINKED_PAID (paired with %s)',
            linked.number, instance.number,
        )


# ============================================================================
# TRANSACTION -> BALANCE RECALCULATION
# ============================================================================

def _recalc_transaction_effects(instance):
    if instance.status != 'COMPLETED':
        return
    for entity in (instance.sender, instance.recipient):
        try:
            Transaction.recalculate_entity_balance(entity)
        except Exception as e:
            logger.error("Error recalculating balance for %s: %s", entity, e)
    if instance.invoice_id:
        try:
            instance.invoice.recalculate_paid_amount()
        except Exception as e:
            logger.error("Error recalculating paid_amount for invoice %s: %s", instance.invoice_id, e)


@receiver(post_save, sender=Transaction)
def recalculate_on_transaction_save(sender, instance, **kwargs):
    if getattr(instance, '_skip_balance_recalc', False):
        return
    _recalc_transaction_effects(instance)


@receiver(post_delete, sender=Transaction)
def recalculate_on_transaction_delete(sender, instance, **kwargs):
    _recalc_transaction_effects(instance)


# ============================================================================
# BANK TRANSACTION MANUAL MATCHING -> AUTO-CREATE PAYMENT
# ============================================================================
# Когда пользователь вручную проставляет matched_invoice у банковской транзакции
# (в админ-форме, через API и т.д.) — автоматически создаётся Transaction(PAYMENT)
# так, чтобы инвойс корректно пересчитал paid_amount и сменил статус на PAID.
# Логика повторяет admin action BankTransactionAdmin.link_to_invoice.


@receiver(pre_save, sender=BankTransaction)
def _track_bt_matched_invoice_change(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_matched_invoice_id = None
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'matched_invoice' not in update_fields and 'matched_invoice_id' not in update_fields:
        instance._old_matched_invoice_id = getattr(instance, '_old_matched_invoice_id', None)
        return
    try:
        old = BankTransaction.objects.filter(pk=instance.pk).values('matched_invoice_id').first()
        instance._old_matched_invoice_id = old['matched_invoice_id'] if old else None
    except Exception:
        instance._old_matched_invoice_id = None


@receiver(post_save, sender=BankTransaction)
def auto_create_payment_on_bt_match(sender, instance, **kwargs):
    """Create a COMPLETED PAYMENT Transaction when matched_invoice is set manually.

    Supports both directions:
    - Incoming bank (bt.amount > 0) + OUTGOING invoice (client pays us):
        from_client=recipient_client, to_company=Caromoto
    - Outgoing bank (bt.amount < 0) + INCOMING invoice (we pay supplier):
        from_company=Caromoto, to_<issuer_type>=issuer entity

    Conditions:
    - matched_invoice changed from NULL to a real invoice
    - matched_transaction is still NULL
    - reconciliation_skipped is False
    - invoice not CANCELLED, remaining amount > 0
    - bank amount direction matches invoice direction
    """
    if getattr(instance, '_creating_payment', False):
        return

    old_invoice_id = getattr(instance, '_old_matched_invoice_id', None)
    instance._old_matched_invoice_id = None

    if not instance.matched_invoice_id:
        return
    if old_invoice_id == instance.matched_invoice_id:
        return
    if instance.matched_transaction_id:
        return
    if instance.reconciliation_skipped:
        return

    bt_pk = instance.pk

    def _do():
        try:
            from core.models import Company

            with transaction.atomic():
                bt = BankTransaction.objects.select_for_update().get(pk=bt_pk)
                if bt.matched_transaction_id or not bt.matched_invoice_id:
                    return
                try:
                    invoice = NewInvoice.objects.select_for_update().get(pk=bt.matched_invoice_id)
                except NewInvoice.DoesNotExist:
                    return
                if invoice.status == 'CANCELLED':
                    return

                remaining = invoice.total - invoice.paid_amount
                payment_amount = min(abs(bt.amount), remaining)
                if payment_amount <= 0:
                    return

                company = Company.get_default()
                direction = invoice.direction
                tx_kwargs = dict(
                    type='PAYMENT',
                    method='TRANSFER',
                    status='COMPLETED',
                    amount=payment_amount,
                    currency=invoice.currency or 'EUR',
                    invoice=invoice,
                    description=(
                        f'Авто-привязка банковского платежа '
                        f'{bt.counterparty_name} -> {invoice.number}'
                    ),
                    date=bt.created_at,
                )

                if bt.amount > 0 and direction == 'OUTGOING':
                    recipient = invoice.recipient
                    if not recipient:
                        logger.info(
                            '[BT auto-pay] Skipping BT %s: invoice %s has no recipient',
                            bt.pk, invoice.number,
                        )
                        return
                    tx_kwargs['to_company'] = company
                    from_field = f'from_{recipient.__class__.__name__.lower()}'
                    tx_kwargs[from_field] = recipient

                elif bt.amount < 0 and direction == 'INCOMING':
                    issuer = invoice.issuer
                    if not issuer:
                        logger.info(
                            '[BT auto-pay] Skipping BT %s: invoice %s has no issuer',
                            bt.pk, invoice.number,
                        )
                        return
                    tx_kwargs['from_company'] = company
                    to_field = f'to_{issuer.__class__.__name__.lower()}'
                    tx_kwargs[to_field] = issuer

                else:
                    logger.info(
                        '[BT auto-pay] Skipping BT %s: direction mismatch (amount=%s, invoice direction=%s)',
                        bt.pk, bt.amount, direction,
                    )
                    return

                tx = Transaction(**tx_kwargs)
                tx.save()

                bt._creating_payment = True
                try:
                    bt.matched_transaction = tx
                    if not bt.reconciliation_note:
                        bt.reconciliation_note = f'Привязано вручную к {invoice.number}'
                    bt.save(update_fields=['matched_transaction', 'reconciliation_note', 'fetched_at'])
                finally:
                    bt._creating_payment = False

                logger.info(
                    '[BT auto-pay] Created Transaction %s for invoice %s (%.2f %s) from BT %s',
                    tx.number, invoice.number, float(payment_amount), tx.currency, bt.pk,
                )
        except Exception as e:
            logger.error('[BT auto-pay] Error for BT %s: %s', bt_pk, e, exc_info=True)

    transaction.on_commit(_do)


# ============================================================================
# EMAIL NOTIFICATIONS
# ============================================================================

@receiver(post_save, sender=Container)
def send_container_notifications_on_save(sender, instance, created, **kwargs):
    if not instance.pk:
        return

    old_values = getattr(instance, '_pre_save_notification', None) or {}
    instance._pre_save_notification = None
    old_planned = old_values.get('planned_unload_date')
    old_unload = old_values.get('unload_date')

    should_notify_planned = False
    if instance.planned_unload_date:
        if created or old_planned is None:
            should_notify_planned = True

    should_notify_unload = False
    if instance.unload_date:
        if created or old_unload is None:
            should_notify_unload = True

    if should_notify_planned:
        def _enqueue_planned():
            try:
                from core.tasks import send_planned_notifications_task
                send_planned_notifications_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import ContainerNotificationService
                if not ContainerNotificationService.was_planned_notification_sent(instance):
                    ContainerNotificationService.send_planned_to_all_clients(instance)
        transaction.on_commit(_enqueue_planned)

    if should_notify_unload:
        def _enqueue_unload():
            try:
                from core.tasks import send_unload_notifications_task
                send_unload_notifications_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import ContainerNotificationService
                if not ContainerNotificationService.was_unload_notification_sent(instance):
                    ContainerNotificationService.send_unload_to_all_clients(instance)
        transaction.on_commit(_enqueue_unload)



# (Car notification and container status handlers consolidated into car_post_save above)


# ============================================================================
# GDRIVE SYNC NOTE
# ============================================================================

@receiver(post_save, sender=Container)
def auto_sync_photos_on_container_change(sender, instance, created, **kwargs):
    if not instance.pk:
        return
    if instance.status == 'UNLOADED':
        logger.info(
            "Container %s: status UNLOADED. Photo sync will run via cron.",
            instance.number,
        )


# ============================================================================
# AUTOTRANSPORT
# ============================================================================

def _queue_or_run_generate_invoices(autotransport):
    """Ставит задачу в Celery; при недоступности брокера — выполняет синхронно."""
    from core.tasks import generate_autotransport_invoices_task
    try:
        transaction.on_commit(
            lambda: generate_autotransport_invoices_task.delay(autotransport.pk)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "AutoTransport %s: Celery unavailable, generating invoices inline: %s",
            autotransport.number, exc,
        )
        try:
            invoices = autotransport.generate_invoices()
            if invoices:
                logger.info(
                    "AutoTransport %s: created/updated %d invoices (sync fallback)",
                    autotransport.number, len(invoices),
                )
        except Exception as e:  # noqa: BLE001
            logger.error("AutoTransport %s invoice error: %s", autotransport.number, e)


@receiver(post_save, sender='core.AutoTransport')
def autotransport_post_save(sender, instance, created, **kwargs):
    if instance.status == 'FORMED':
        _queue_or_run_generate_invoices(instance)

    if instance.status in ('LOADED', 'IN_TRANSIT', 'DELIVERED'):
        transfer_date = getattr(instance, '_transfer_date_override', None)
        _mark_cars_as_transferred(instance, transfer_date)


def _mark_cars_as_transferred(autotransport, transfer_date=None):
    from django.utils import timezone as tz
    if transfer_date is None:
        transfer_date = tz.now().date()
    affected_cars = list(
        autotransport.cars.exclude(status='TRANSFERRED').values_list('id', 'container_id')
    )
    if not affected_cars:
        return
    car_ids = [c[0] for c in affected_cars]
    container_ids = {c[1] for c in affected_cars if c[1]}
    Car.objects.filter(id__in=car_ids).update(
        status='TRANSFERRED', transfer_date=transfer_date
    )
    logger.info(
        "AutoTransport %s: %d cars -> TRANSFERRED (date: %s)",
        autotransport.number, len(car_ids), transfer_date,
    )
    for cid in container_ids:
        _update_container_status_if_all_transferred(cid)


def _update_container_status_if_all_transferred(container_id):
    """Set container to TRANSFERRED if all its cars are TRANSFERRED.

    Один агрегат вместо 3-4 отдельных запросов.
    """
    from django.db.models import Count, Q
    try:
        container = Container.objects.only('id', 'status', 'number').get(pk=container_id)
    except Container.DoesNotExist:
        return
    if container.status == 'TRANSFERRED':
        return
    stats = container.container_cars.aggregate(
        total=Count('id'),
        transferred=Count('id', filter=Q(status='TRANSFERRED')),
    )
    total = stats['total'] or 0
    if total == 0 or stats['transferred'] != total:
        return
    container.status = 'TRANSFERRED'
    container.save(update_fields=['status'])
    logger.info(
        "Container %s -> TRANSFERRED (all %d cars transferred)",
        container.number, total,
    )


def autotransport_cars_changed_handler(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        if instance.status == 'FORMED':
            _queue_or_run_generate_invoices(instance)


def connect_autotransport_signals():
    try:
        from .models import AutoTransport
        m2m_changed.connect(autotransport_cars_changed_handler, sender=AutoTransport.cars.through)
    except Exception as e:
        logger.warning("Failed to connect AutoTransport signals: %s", e)


# ============================================================================
# CACHE INVALIDATION (company/client/warehouse stats, comparison, payment_objects)
# ============================================================================

_CACHE_INVALIDATION_MODELS = {
    'Client', 'Warehouse', 'Company', 'Line', 'Carrier',
    'NewInvoice', 'Transaction', 'Car', 'Container',
}


def _invalidate_stats_cache(sender, instance, **kwargs):
    """Инвалидирует кэш статистики/отчётов при изменении ключевых моделей.

    Раньше `invalidate_related_cache` был написан, но нигде не вызывался,
    поэтому `company_stats`, `client_stats`, `warehouse_stats` и
    `payment_objects:*` жили до TTL (30 минут) и показывали устаревшие цифры.
    """
    model_name = sender.__name__
    if model_name not in _CACHE_INVALIDATION_MODELS:
        return
    try:
        from .cache_utils import invalidate_related_cache
        # Откладываем до commit, чтобы инвалидация происходила после записи в БД.
        instance_id = getattr(instance, 'pk', None)
        transaction.on_commit(lambda: invalidate_related_cache(model_name, instance_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Cache invalidation skipped for %s: %s", model_name, exc)


def connect_cache_invalidation_signals():
    from django.apps import apps as _apps
    for model_name in _CACHE_INVALIDATION_MODELS:
        try:
            model = _apps.get_model('core', model_name)
        except LookupError:
            continue
        post_save.connect(
            _invalidate_stats_cache,
            sender=model,
            dispatch_uid=f'cache_invalidate_save_{model_name}',
            weak=False,
        )
        post_delete.connect(
            _invalidate_stats_cache,
            sender=model,
            dispatch_uid=f'cache_invalidate_delete_{model_name}',
            weak=False,
        )


from django.apps import apps

if apps.ready:
    connect_autotransport_signals()
    connect_cache_invalidation_signals()
else:
    from django.db.models.signals import post_migrate

    def setup_autotransport_signals(sender, **kwargs):
        if sender.name == 'core':
            connect_autotransport_signals()
            connect_cache_invalidation_signals()

    post_migrate.connect(setup_autotransport_signals)
