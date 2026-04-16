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


@shared_task(bind=True, max_retries=1, default_retry_delay=300, time_limit=300)
def sync_sitepro_invoices(self):
    """
    Periodic task: sync new invoices to site.pro and pull updated payment status.
    Runs daily via celery beat.
    """
    from core.models_accounting import SiteProConnection, SiteProInvoiceSync
    from core.models_billing import NewInvoice
    from core.services.sitepro_service import SiteProService
    from decimal import Decimal

    conn = SiteProConnection.objects.filter(is_active=True).first()
    if not conn:
        logger.info('[sync_sitepro] No active SiteProConnection, skipping')
        return {'status': 'no_connection'}

    svc = SiteProService(conn)
    result = {'pushed': 0, 'updated_payments': 0, 'errors': []}

    if conn.auto_push_on_issue:
        unsent = NewInvoice.objects.filter(
            status='ISSUED',
            document_type='INVOICE',
        ).exclude(
            sitepro_syncs__connection=conn,
            sitepro_syncs__sync_status='SENT',
        )
        for inv in unsent[:50]:
            try:
                svc.push_invoice(inv)
                result['pushed'] += 1
            except Exception as e:
                result['errors'].append(f'push {inv.number}: {str(e)[:100]}')

    try:
        sp_sales = svc.list_all_sales()
        sp_real_by_id = {str(s['id']): s for s in sp_sales if s.get('isSale')}

        syncs = SiteProInvoiceSync.objects.filter(
            connection=conn, sync_status='SENT',
        ).select_related('invoice')

        for sync_obj in syncs:
            sp_sale = sp_real_by_id.get(sync_obj.external_id)
            if not sp_sale:
                continue
            sp_amount = Decimal(str(sp_sale.get('sumWithVat', 0) or 0))
            sp_balance = Decimal(str(sp_sale.get('currencyBalance', 0) or 0))
            sp_paid = max(sp_amount - sp_balance, Decimal('0'))

            inv = sync_obj.invoice
            if abs(inv.paid_amount - sp_paid) > Decimal('0.01'):
                inv.paid_amount = sp_paid
                inv.update_status()
                inv.save(update_fields=['paid_amount', 'status', 'updated_at'])
                result['updated_payments'] += 1

    except Exception as e:
        result['errors'].append(f'pull: {str(e)[:200]}')

    conn.last_synced_at = timezone.now()
    conn.last_error = '; '.join(result['errors']) if result['errors'] else ''
    conn.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

    logger.info(
        '[sync_sitepro] pushed=%d, updated_payments=%d, errors=%d',
        result['pushed'], result['updated_payments'], len(result['errors']),
    )
    return result


@shared_task(bind=True, max_retries=1, default_retry_delay=300, time_limit=600)
def sync_bank_and_reconcile(self):
    """
    Periodic task: sync Revolut bank transactions, then run both reconciliation flows.
    1. Revolut sync (fetch accounts + transactions)
    2. Outgoing reconciliation (we pay suppliers — match by external_number)
    3. Incoming reconciliation (clients pay us — match by invoice number/name+amount)
    """
    from core.models_banking import BankConnection
    from core.services.revolut_service import RevolutService
    from core.services.billing_service import BillingService
    from core.management.commands.auto_reconcile import reconcile_incoming_payments

    total_transactions = 0
    errors = 0

    for conn in BankConnection.objects.filter(is_active=True):
        if conn.bank_type != 'REVOLUT':
            continue
        try:
            service = RevolutService(conn)
            result = service.sync_all()
            if result['error']:
                errors += 1
                logger.error('[sync_bank] %s error: %s', conn, result['error'])
            else:
                n_tx = len(result['transactions'])
                total_transactions += n_tx
                logger.info('[sync_bank] %s: %d accounts, %d transactions, %d expenses',
                            conn, len(result['accounts']), n_tx,
                            result.get('expenses_updated', 0))
        except Exception as exc:
            errors += 1
            logger.error('[sync_bank] %s failed: %s', conn, exc, exc_info=True)

    outgoing = {'auto_paid': [], 'linked_only': [], 'errors': []}
    incoming = {'total': 0}

    if errors == 0:
        try:
            outgoing = BillingService.auto_reconcile_bank_transactions()
            logger.info('[sync_bank] outgoing reconcile: %d paid, %d linked',
                        len(outgoing['auto_paid']), len(outgoing['linked_only']))
        except Exception as exc:
            logger.error('[sync_bank] outgoing reconcile failed: %s', exc, exc_info=True)

        try:
            incoming = reconcile_incoming_payments()
            logger.info('[sync_bank] incoming reconcile: %d matched (R1=%d R2=%d R3=%d)',
                        incoming['total'], incoming['rule1'], incoming['rule2'], incoming['rule3'])
        except Exception as exc:
            logger.error('[sync_bank] incoming reconcile failed: %s', exc, exc_info=True)

    return {
        'transactions_synced': total_transactions,
        'sync_errors': errors,
        'outgoing_paid': len(outgoing['auto_paid']),
        'outgoing_linked': len(outgoing['linked_only']),
        'incoming_matched': incoming.get('total', 0),
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=60, time_limit=120)
def parse_receipt_task(self, transaction_id):
    """Parse receipt image attached to a personal expense transaction via Claude Vision."""
    from core.services.receipt_parser_service import parse_transaction_receipt
    try:
        result = parse_transaction_receipt(transaction_id)
        if result:
            logger.info("[parse_receipt] Transaction %d parsed: %s",
                        transaction_id, result.get('ai_summary', ''))
        return result
    except Exception as exc:
        logger.error("[parse_receipt] Failed for transaction %d: %s",
                     transaction_id, exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=0, time_limit=600)
def parse_pending_receipts(self):
    """Fallback: parse receipts that were missed (have attachment but no receipt_data)."""
    from core.models_billing import Transaction, ExpenseCategory

    personal_cats = list(ExpenseCategory.objects.filter(
        category_type='PERSONAL'
    ).values_list('id', flat=True))

    if not personal_cats:
        return {'parsed': 0}

    pending = Transaction.objects.filter(
        category_id__in=personal_cats,
        status='COMPLETED',
        receipt_data__isnull=True,
    ).exclude(attachment='').exclude(attachment__isnull=True)[:20]

    parsed = 0
    for tx in pending:
        try:
            parse_receipt_task.delay(tx.id)
            parsed += 1
        except Exception as e:
            logger.error("[parse_pending_receipts] Failed to queue tx %d: %s", tx.id, e)

    logger.info("[parse_pending_receipts] Queued %d receipts for parsing", parsed)
    return {'queued': parsed}


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
