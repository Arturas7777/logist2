import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=300, time_limit=120)
def check_overdue_invoices(self):
    """
    Периодическая задача: переводит просроченные инвойсы в статус OVERDUE.
    Рекомендуется запускать через celery beat раз в день.
    """
    from core.models_billing import NewInvoice

    today = timezone.now().date()
    overdue_qs = NewInvoice.objects.filter(
        status__in=['ISSUED', 'PARTIALLY_PAID'],
        due_date__lt=today,
    )
    updated = overdue_qs.update(status='OVERDUE')
    if updated:
        logger.info(f"[check_overdue_invoices] Marked {updated} invoices as OVERDUE")
    return updated


@shared_task(bind=True, max_retries=2, default_retry_delay=120, time_limit=600)
def sync_container_photos_gdrive_task(self, container_id, folder_url=None):
    """Синхронизирует фотографии контейнера с Google Drive в фоне через Celery."""
    from core.google_drive_sync import GoogleDriveSync
    from core.models import Container
    try:
        container = Container.objects.get(id=container_id)
        if folder_url:
            GoogleDriveSync.download_folder_photos(folder_url, container)
        else:
            GoogleDriveSync.sync_container_by_number(container.number)
        logger.info(f"Google Drive sync completed for container {container.number}")
    except Exception as exc:
        logger.error(f"Failed Google Drive sync for container {container_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_planned_notifications_task(self, container_id):
    from core.models import Container
    from core.services.email_service import ContainerNotificationService
    try:
        container = Container.objects.get(id=container_id)
        if not ContainerNotificationService.was_planned_notification_sent(container):
            sent, failed = ContainerNotificationService.send_planned_to_all_clients(container)
            logger.info(f"Planned notifications for {container.number}: {sent} sent, {failed} failed")
    except Exception as exc:
        logger.error(f"Failed planned notifications for container {container_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_unload_notifications_task(self, container_id):
    from core.models import Container
    from core.services.email_service import ContainerNotificationService
    try:
        container = Container.objects.get(id=container_id)
        if not ContainerNotificationService.was_unload_notification_sent(container):
            sent, failed = ContainerNotificationService.send_unload_to_all_clients(container)
            logger.info(f"Unload notifications for {container.number}: {sent} sent, {failed} failed")
    except Exception as exc:
        logger.error(f"Failed unload notifications for container {container_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30, time_limit=60)
def create_container_photo_thumbnail_task(self, photo_pk):
    """Creates thumbnail for a ContainerPhoto in background."""
    from core.models_website import ContainerPhoto
    try:
        photo = ContainerPhoto.objects.get(pk=photo_pk)
        if photo.photo and not photo.thumbnail:
            if photo.create_thumbnail():
                ContainerPhoto.objects.filter(pk=photo_pk).update(thumbnail=photo.thumbnail)
                logger.info(f"Thumbnail created for ContainerPhoto {photo_pk}")
    except ContainerPhoto.DoesNotExist:
        logger.warning(f"ContainerPhoto {photo_pk} not found for thumbnail")
    except Exception as exc:
        logger.error(f"Thumbnail creation failed for {photo_pk}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=0, time_limit=300)
def check_balance_consistency(self):
    """
    Periodic task: validates that stored balances match transaction history.
    Logs warnings for any mismatches found. Run weekly via celery beat.
    """
    from decimal import Decimal
    from django.db.models import Sum
    from core.models import Client, Warehouse, Line, Company, Carrier
    from core.models_billing import Transaction, NewInvoice

    mismatches = []

    for model in [Client, Warehouse, Line, Company, Carrier]:
        model_name = model.__name__.lower()
        for entity in model.objects.all():
            incoming = Transaction.objects.filter(
                status='COMPLETED', **{f'to_{model_name}': entity}
            ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            outgoing = Transaction.objects.filter(
                status='COMPLETED', **{f'from_{model_name}': entity}
            ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            expected = incoming - outgoing
            if entity.balance != expected:
                mismatches.append(
                    f'{model.__name__} "{entity}" (id={entity.pk}): '
                    f'stored={entity.balance}, expected={expected}'
                )
                entity.balance = expected
                entity.save(update_fields=['balance', 'balance_updated_at'])

    invoice_issues = 0
    for inv in NewInvoice.objects.exclude(status='CANCELLED').iterator():
        payments = inv.transactions.filter(
            type='PAYMENT', status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        refunds = inv.transactions.filter(
            type='REFUND', status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        expected_paid = max(Decimal('0.00'), payments - refunds)
        if inv.paid_amount != expected_paid:
            invoice_issues += 1
            inv.paid_amount = expected_paid
            inv.update_status()
            inv.save(update_fields=['paid_amount', 'status', 'updated_at'])

    if mismatches:
        logger.warning(
            '[check_balance_consistency] Fixed %d balance mismatches:\n%s',
            len(mismatches), '\n'.join(mismatches),
        )
    if invoice_issues:
        logger.warning(
            '[check_balance_consistency] Fixed %d invoice paid_amount mismatches',
            invoice_issues,
        )
    total = len(mismatches) + invoice_issues
    if total == 0:
        logger.info('[check_balance_consistency] All balances and paid_amounts are consistent')
    return {'balance_fixes': len(mismatches), 'invoice_fixes': invoice_issues}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_car_unload_notification_task(self, car_id):
    from core.models import Car
    from core.services.email_service import CarNotificationService
    try:
        car = Car.objects.select_related('client', 'warehouse').get(id=car_id)
        if not CarNotificationService.was_car_unload_notification_sent(car):
            CarNotificationService.send_car_unload_notification(car)
            logger.info(f"Car unload notification sent for {car.vin}")
    except Exception as exc:
        logger.error(f"Failed car unload notification for car {car_id}: {exc}")
        raise self.retry(exc=exc)
