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
    from core.mixins import OVERDUE_CANDIDATE_STATUSES
    from core.models_billing import NewInvoice

    today = timezone.now().date()
    overdue_qs = NewInvoice.objects.filter(
        status__in=OVERDUE_CANDIDATE_STATUSES,
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
    except Container.DoesNotExist:
        logger.warning("Container %s not found, skipping GDrive sync", container_id)
        return
    try:
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
    except Container.DoesNotExist:
        logger.warning("Container %s not found, skipping planned notifications", container_id)
        return
    try:
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
    except Container.DoesNotExist:
        logger.warning("Container %s not found, skipping unload notifications", container_id)
        return
    try:
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


def _collect_balance_mismatches():
    """Shared logic: computes expected vs stored balances and invoice paid_amounts.

    Returns (balance_mismatches, invoice_mismatches) where each is a list of
    dicts with entity/invoice info and expected values.
    """
    from decimal import Decimal

    from django.db.models import DecimalField, Q, Sum, Value
    from django.db.models.functions import Coalesce

    from core.models import Carrier, Client, Company, Line, Warehouse
    from core.models_billing import NewInvoice, Transaction

    balance_mismatches = []
    ZERO = Decimal('0.00')

    ENTITY_MODELS = [
        ('client',    Client),
        ('warehouse', Warehouse),
        ('line',      Line),
        ('company',   Company),
        ('carrier',   Carrier),
    ]

    for key, model in ENTITY_MODELS:
        to_field = f'to_{key}'
        from_field = f'from_{key}'

        incoming_by_id = dict(
            Transaction.objects
            .filter(status='COMPLETED', **{f'{to_field}__isnull': False})
            .values_list(to_field)
            .annotate(s=Sum('amount'))
        )
        outgoing_by_id = dict(
            Transaction.objects
            .filter(status='COMPLETED', **{f'{from_field}__isnull': False})
            .values_list(from_field)
            .annotate(s=Sum('amount'))
        )

        all_ids = set(incoming_by_id) | set(outgoing_by_id)
        stored = model.objects.filter(
            Q(pk__in=all_ids) | ~Q(balance=0)
        ).only('pk', 'balance')

        for entity in stored:
            incoming = incoming_by_id.get(entity.pk) or ZERO
            outgoing = outgoing_by_id.get(entity.pk) or ZERO
            expected = incoming - outgoing
            if entity.balance != expected:
                balance_mismatches.append({
                    'model': model,
                    'pk': entity.pk,
                    'stored': entity.balance,
                    'expected': expected,
                    'label': f'{model.__name__} id={entity.pk}: stored={entity.balance}, expected={expected}',
                })

    invoice_mismatches = []
    qs = (
        NewInvoice.objects
        .exclude(status__in=['CANCELLED', 'LINKED_PAID'])
        .annotate(
            _paid=Coalesce(
                Sum(
                    'transactions__amount',
                    filter=Q(
                        transactions__status='COMPLETED',
                        transactions__type='PAYMENT',
                    ),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=15, decimal_places=2),
            ),
            _refund=Coalesce(
                Sum(
                    'transactions__amount',
                    filter=Q(
                        transactions__status='COMPLETED',
                        transactions__type='REFUND',
                    ),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=15, decimal_places=2),
            ),
        )
        .only('id', 'number', 'status', 'paid_amount', 'total', 'updated_at', 'due_date')
    )

    for inv in qs.iterator():
        expected_paid = max(ZERO, (inv._paid or ZERO) - (inv._refund or ZERO))
        if inv.paid_amount != expected_paid:
            invoice_mismatches.append({
                'pk': inv.pk,
                'number': inv.number,
                'stored': inv.paid_amount,
                'expected': expected_paid,
            })

    return balance_mismatches, invoice_mismatches


@shared_task(bind=True, max_retries=0, time_limit=300)
def check_balance_consistency(self):
    """Read-only check: reports mismatches without modifying data.

    Run weekly via celery beat. Use repair_balance_consistency to fix.
    """
    balance_mismatches, invoice_mismatches = _collect_balance_mismatches()

    if balance_mismatches:
        labels = [m['label'] for m in balance_mismatches]
        logger.warning(
            '[check_balance_consistency] Found %d balance mismatches:\n%s',
            len(balance_mismatches), '\n'.join(labels[:50]),
        )
    if invoice_mismatches:
        logger.warning(
            '[check_balance_consistency] Found %d invoice paid_amount mismatches',
            len(invoice_mismatches),
        )
    total = len(balance_mismatches) + len(invoice_mismatches)
    if total == 0:
        logger.info('[check_balance_consistency] All balances and paid_amounts are consistent')
    return {'balance_mismatches': len(balance_mismatches), 'invoice_mismatches': len(invoice_mismatches)}


@shared_task(bind=True, max_retries=0, time_limit=600)
def repair_balance_consistency(self):
    """Repair task: fixes mismatches using select_for_update. Run manually only."""
    from django.db import transaction as db_transaction

    from core.models_billing import NewInvoice

    balance_mismatches, invoice_mismatches = _collect_balance_mismatches()
    balance_fixes = 0
    invoice_fixes = 0

    for m in balance_mismatches:
        with db_transaction.atomic():
            entity = m['model'].objects.select_for_update().get(pk=m['pk'])
            entity.balance = m['expected']
            entity.save(update_fields=['balance', 'balance_updated_at'])
            balance_fixes += 1

    for m in invoice_mismatches:
        with db_transaction.atomic():
            inv = NewInvoice.objects.select_for_update().get(pk=m['pk'])
            inv.paid_amount = m['expected']
            inv.update_status()
            inv.save(update_fields=['paid_amount', 'status', 'updated_at'])
            invoice_fixes += 1

    if balance_fixes:
        logger.warning(
            '[repair_balance_consistency] Fixed %d balance mismatches', balance_fixes,
        )
    if invoice_fixes:
        logger.warning(
            '[repair_balance_consistency] Fixed %d invoice paid_amount mismatches', invoice_fixes,
        )
    if balance_fixes == 0 and invoice_fixes == 0:
        logger.info('[repair_balance_consistency] Nothing to repair')
    return {'balance_fixes': balance_fixes, 'invoice_fixes': invoice_fixes}


@shared_task(bind=True, max_retries=1, default_retry_delay=300, time_limit=300)
def sync_sitepro_invoices(self):
    """
    Periodic task: sync new invoices to site.pro and pull updated payment status.
    Runs daily via celery beat.
    """
    from decimal import Decimal

    from core.models_accounting import SiteProConnection, SiteProInvoiceSync
    from core.models_billing import NewInvoice
    from core.services.sitepro_service import SiteProService

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
        from django.db import transaction as db_transaction

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

            # Перечитываем инвойс с row-level lock внутри короткой транзакции:
            # между чтением paid_amount и сохранением могут проскочить сигналы
            # post_save Transaction → recalculate_paid_amount (если в этот
            # момент кто-то регистрирует платёж параллельно).
            with db_transaction.atomic():
                inv = NewInvoice.objects.select_for_update().get(pk=sync_obj.invoice_id)
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


@shared_task(bind=True, max_retries=0, time_limit=60)
def check_revolut_jwt_expiry(self):
    """Мониторинг срока жизни JWT-assertion для всех Revolut-подключений.

    JWT в Revolut Business API подписывается приватным ключом и имеет срок
    жизни (по умолчанию 90 дней — см. `setup_revolut.py::_generate_jwt`).
    После истечения каждое обращение к `/api/1.0/auth/token` возвращает 401,
    и `sync_bank_and_reconcile` начинает молча падать.

    Эта задача декодирует payload JWT и:
      • если осталось ≤ 14 дней — пишет WARNING в лог и в `last_error`
        (Sentry поднимет issue, в админке появится красный бейдж);
      • если JWT уже истёк — пишет ERROR с инструкцией по восстановлению.

    Запуск ежедневно через celery beat (см. `logist2/celery.py`).
    """
    from core.models_banking import BankConnection

    THRESHOLD_DAYS = 14
    summary = {'expired': [], 'warning': [], 'ok': []}

    for conn in BankConnection.objects.filter(bank_type='REVOLUT', is_active=True):
        days = conn.jwt_days_until_expiry
        if days is None:
            continue

        item = {'id': conn.pk, 'name': str(conn), 'days': days}

        if days < 0:
            summary['expired'].append(item)
            msg = (
                f'JWT-assertion истёк {-days} дн. назад. Синхронизация Revolut '
                f'не работает. Перегенерируйте: python manage.py '
                f'regenerate_revolut_jwt --private-key certs/privatecert.pem'
            )
            logger.error('[check_revolut_jwt_expiry] %s: %s', conn, msg)
            # Перетираем last_error только если там нет уже более свежей ошибки
            # о просроченном JWT (чтобы не плодить save'ы).
            if 'JWT' not in (conn.last_error or ''):
                conn.last_error = msg[:500]
                conn.save(update_fields=['last_error', 'updated_at'])
        elif days <= THRESHOLD_DAYS:
            summary['warning'].append(item)
            logger.warning(
                '[check_revolut_jwt_expiry] %s: JWT истекает через %d дн. — '
                'пересоздайте заранее командой regenerate_revolut_jwt',
                conn, days,
            )
        else:
            summary['ok'].append(item)

    if not summary['expired'] and not summary['warning']:
        logger.info(
            '[check_revolut_jwt_expiry] OK — у всех %d подключений JWT валиден',
            len(summary['ok']),
        )

    return summary


@shared_task(bind=True, max_retries=1, default_retry_delay=300, time_limit=600)
def sync_bank_and_reconcile(self):
    """
    Periodic task: sync Revolut bank transactions, then run both reconciliation flows.
    1. Revolut sync (fetch accounts + transactions)
    2. Outgoing reconciliation (we pay suppliers — match by external_number)
    3. Incoming reconciliation (clients pay us — match by invoice number/name+amount)
    """
    from core.management.commands.auto_reconcile import reconcile_incoming_payments
    from core.models_banking import BankConnection
    from core.services.billing_service import BillingService
    from core.services.revolut_service import RevolutService

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
    from core.models_billing import ExpenseCategory, Transaction

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


@shared_task(bind=True, max_retries=2, default_retry_delay=60, time_limit=300)
def generate_autotransport_invoices_task(self, autotransport_id):
    """Фоновая генерация инвойсов автовоза.

    Переносит тяжёлый `AutoTransport.generate_invoices()` из сигнала
    `post_save` / `m2m_changed` в Celery, чтобы сохранение автовоза
    не блокировало пользователя (генерация инвойсов на десятки машин
    может занимать секунды).
    """
    from core.models import AutoTransport
    try:
        at = AutoTransport.objects.get(pk=autotransport_id)
    except AutoTransport.DoesNotExist:
        logger.warning("generate_autotransport_invoices_task: AutoTransport %s not found",
                       autotransport_id)
        return {'created': 0, 'missing': True}

    if at.status != 'FORMED':
        logger.info(
            "generate_autotransport_invoices_task: AT %s not FORMED (%s), skip",
            at.number, at.status,
        )
        return {'created': 0, 'skipped': True}

    try:
        invoices = at.generate_invoices()
        count = len(invoices) if invoices else 0
        logger.info("AutoTransport %s: %d invoices generated (async)", at.number, count)
        return {'created': count}
    except Exception as exc:
        logger.error(
            "AutoTransport %s invoice error (async): %s",
            at.number, exc, exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=120, soft_time_limit=180, time_limit=240)
def process_invoice_audit_task(self, audit_id):
    """Асинхронный AI-разбор PDF инвойса через Anthropic.

    Выносит долгий LLM-вызов из threading.Thread (который умирает вместе
    с web-воркером и не имеет ретраев) в Celery. Retry — при сетевых
    сбоях или временных 5xx Anthropic.
    """
    from core.models_invoice_audit import InvoiceAudit
    from core.services.invoice_audit_service import process_invoice_audit

    try:
        audit = InvoiceAudit.objects.get(pk=audit_id)
    except InvoiceAudit.DoesNotExist:
        logger.warning("process_invoice_audit_task: audit %s not found", audit_id)
        return {'ok': False, 'missing': True}

    if audit.status in ('OK', 'HAS_ISSUES'):
        return {'ok': True, 'skipped': f'already {audit.status}'}

    try:
        process_invoice_audit(audit_id)
        audit.refresh_from_db()
        return {'ok': True, 'status': audit.status}
    except Exception as exc:
        logger.exception("process_invoice_audit_task failed for audit %s", audit_id)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, time_limit=120)
def push_invoice_to_sitepro_task(self, invoice_id):
    """Асинхронный пуш инвойса в site.pro (серия PARDP).

    Выносит внешний HTTP-вызов из request cycle в фон, чтобы админка
    не ждала сетевого ответа site.pro при сохранении инвойса.
    """
    from core.models_accounting import SiteProConnection
    from core.models_billing import NewInvoice
    from core.services.sitepro_service import SiteProService

    try:
        invoice = NewInvoice.objects.get(pk=invoice_id)
    except NewInvoice.DoesNotExist:
        logger.warning("push_invoice_to_sitepro_task: invoice %s not found", invoice_id)
        return {'ok': False, 'missing': True}

    if invoice.document_type != 'INVOICE':
        return {'ok': False, 'skipped': 'not PARDP'}
    if invoice.status != 'ISSUED':
        return {'ok': False, 'skipped': f'status={invoice.status}'}

    try:
        conn = SiteProConnection.objects.filter(is_active=True).first()
        if not conn or not getattr(conn, 'auto_push_on_issue', False):
            return {'ok': False, 'skipped': 'auto_push disabled'}
        service = SiteProService(conn)
        result = service.push_invoice(invoice)
        logger.info("site.pro push for %s: %s", invoice.number, result)
        return {'ok': True, 'result': str(result)[:200]}
    except Exception as exc:
        logger.error("site.pro push failed for invoice %s: %s", invoice_id, exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, time_limit=180)
def process_scan_job(self, job_id):
    """AI-обработка одного отсканированного PDF (Title или Dock Receipt).

    Этапы:
      1. status PROCESSING.
      2. Конвертируем PDF → PNG, отправляем в Claude Vision.
      3. Сохраняем extracted_data, статус NEEDS_REVIEW.
      4. При ошибке: status=ERROR, error_message заполнено.

    Применение к карточкам Car/Container — НЕ здесь, а руками через
    админку (см. ScanProcessingJobAdmin.action 'apply_jobs') — это
    осознанный workflow review-then-apply.
    """
    from core.models_scans import ScanProcessingJob
    from core.services.scan_extractor import (
        extract_dock_receipt,
        extract_title,
    )

    try:
        job = ScanProcessingJob.objects.get(pk=job_id)
    except ScanProcessingJob.DoesNotExist:
        logger.warning("process_scan_job: ScanProcessingJob #%s не найден", job_id)
        return {'ok': False, 'reason': 'not_found'}

    if job.status not in (ScanProcessingJob.STATUS_PENDING, ScanProcessingJob.STATUS_ERROR):
        logger.info("process_scan_job: skipping job #%s in status=%s", job_id, job.status)
        return {'ok': False, 'reason': f'status_{job.status}'}

    if not job.original_file:
        job.status = ScanProcessingJob.STATUS_ERROR
        job.error_message = "Нет исходного PDF"
        job.save(update_fields=['status', 'error_message'])
        return {'ok': False, 'reason': 'no_file'}

    job.status = ScanProcessingJob.STATUS_PROCESSING
    job.error_message = ''
    job.save(update_fields=['status', 'error_message'])

    try:
        # FileField даёт нам путь, только если используется FileSystemStorage.
        # Если хранилище удалённое (S3) — лучше скачать локально. У нас сейчас
        # FileSystemStorage, поэтому идём напрямую через .path.
        pdf_path = job.original_file.path
        if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
            extracted = extract_title(pdf_path)
        elif job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
            extracted = extract_dock_receipt(pdf_path)
        else:
            raise ValueError(f"Unknown scan_type: {job.scan_type}")
    except Exception as exc:
        logger.exception("process_scan_job #%s failed", job_id)
        job.status = ScanProcessingJob.STATUS_ERROR
        job.error_message = f"{type(exc).__name__}: {exc}"[:500]
        job.save(update_fields=['status', 'error_message'])
        # retry только для сетевых/транзиентных, чтобы не зацикливаться на
        # повреждённых PDF.
        if 'rate limit' in str(exc).lower() or 'timeout' in str(exc).lower():
            raise self.retry(exc=exc)
        return {'ok': False, 'error': str(exc)[:200]}

    job.extracted_data = extracted
    job.processed_at = timezone.now()
    if not extracted:
        job.status = ScanProcessingJob.STATUS_ERROR
        job.error_message = "AI вернул пустой ответ — попробуйте улучшить качество скана"
    else:
        job.status = ScanProcessingJob.STATUS_NEEDS_REVIEW
    job.save(update_fields=['extracted_data', 'processed_at', 'status', 'error_message'])
    return {'ok': True, 'job_id': job.id, 'status': job.status}


@shared_task(bind=True, max_retries=1, default_retry_delay=600, time_limit=180)
def check_business_rules(self):
    """
    Проверяет соблюдение трёх бизнес-правил учёта (см.
    `scripts/debug/_business_rules_audit.py` и
    `docs/accounting_session_handoff.md`):

    1. FACT (INVOICE_FACT, INCOMING) — должен иметь Transaction(PAYMENT,
       COMPLETED) + attachment.
    2. AV (PROFORMA, OUTGOING) — НЕ должно быть транзакций.
    3. PARDP (INVOICE, OUTGOING) — должен иметь Transaction(PAYMENT,
       COMPLETED) на сумму ≥ total + attachment.

    Запускается ежедневно через celery beat. Возвращает счётчики
    нарушений и id первых 30 объектов в каждой категории. Лог-уровень:
    INFO если нарушений «как обычно», WARNING если их стало больше
    baseline'а (Sentry поднимет issue).

    `direction` — property, поэтому итерируем в Python (как и в исходном
    audit-скрипте).
    """
    from decimal import Decimal

    from core.models_billing import NewInvoice

    BASELINE = {
        # baseline = «известные нарушения, оставленные сознательно» на
        # 2026-04-21. Сверх этого — повод алертить.
        'fact_no_tx': 1,
        'fact_no_file': 41,
        'av_with_tx': 1,
        'pardp_no_tx': 6,
        'pardp_tx_mismatch': 2,
        'pardp_no_file': 0,
    }

    def _has_file(inv):
        if not inv.attachment:
            return False
        try:
            from pathlib import Path
            p = Path(inv.attachment.path)
            return p.exists() and p.stat().st_size > 0
        except (ValueError, NotImplementedError):
            return True  # remote storage — считаем что файл есть

    violations: dict[str, list[int]] = {k: [] for k in BASELINE}

    fact_qs = (
        NewInvoice.objects.filter(document_type='INVOICE_FACT')
        .prefetch_related('transactions')
    )
    for inv in fact_qs:
        if inv.direction != 'INCOMING':
            continue
        txs = [t for t in inv.transactions.all() if t.type == 'PAYMENT' and t.status == 'COMPLETED']
        if not txs:
            violations['fact_no_tx'].append(inv.id)
        if not _has_file(inv):
            violations['fact_no_file'].append(inv.id)

    av_qs = (
        NewInvoice.objects.filter(document_type='PROFORMA')
        .prefetch_related('transactions')
    )
    for inv in av_qs:
        if inv.direction != 'OUTGOING':
            continue
        if inv.transactions.exists():
            violations['av_with_tx'].append(inv.id)

    pardp_qs = (
        NewInvoice.objects.filter(document_type='INVOICE')
        .prefetch_related('transactions')
    )
    for inv in pardp_qs:
        if inv.direction != 'OUTGOING':
            continue
        txs_completed = [
            t for t in inv.transactions.all()
            if t.type == 'PAYMENT' and t.status == 'COMPLETED'
        ]
        if not txs_completed:
            violations['pardp_no_tx'].append(inv.id)
        else:
            paid = sum((t.amount for t in txs_completed), Decimal('0'))
            if abs(paid - inv.total) > Decimal('0.01') and inv.status != 'PAID':
                violations['pardp_tx_mismatch'].append(inv.id)
        if not _has_file(inv):
            violations['pardp_no_file'].append(inv.id)

    counts = {k: len(v) for k, v in violations.items()}
    overflow = {k: counts[k] for k in BASELINE if counts[k] > BASELINE[k]}

    msg = (
        '[check_business_rules] fact_no_tx=%(fact_no_tx)d '
        'fact_no_file=%(fact_no_file)d av_with_tx=%(av_with_tx)d '
        'pardp_no_tx=%(pardp_no_tx)d pardp_tx_mismatch=%(pardp_tx_mismatch)d '
        'pardp_no_file=%(pardp_no_file)d' % counts
    )
    if overflow:
        logger.warning('%s — над baseline: %s', msg, overflow)
    else:
        logger.info('%s — без новых нарушений', msg)

    return {
        'counts': counts,
        'baseline': BASELINE,
        'overflow': overflow,
        # сохраняем первые 30 id, чтобы dashboards / Sentry могли
        # показывать конкретные инвойсы.
        'sample_ids': {k: v[:30] for k, v in violations.items()},
    }


# ============================================================================
# DEFERRED RECALCULATIONS (CarService → Invoice, Catalog → Cars)
# ============================================================================

@shared_task(
    bind=True, max_retries=2, default_retry_delay=30, time_limit=120,
    autoretry_for=(Exception,), retry_backoff=True,
)
def regenerate_invoices_for_car_task(self, car_id):
    """Пересоздать позиции всех открытых инвойсов, связанных с указанной машиной.

    Ранее эта работа делалась в `transaction.on_commit` синхронно — каждое
    сохранение CarService приводило к N SQL-запросов в HTTP-потоке.
    Вынесено в Celery: HTTP отвечает быстрее, ретраи на ошибках,
    дедупликация на стороне сигнала.
    """
    from django.db import transaction as db_transaction
    from django.db.utils import OperationalError

    from core.mixins import REGENERATABLE_INVOICE_STATUSES
    from core.models_billing import NewInvoice
    invoice_ids = list(
        NewInvoice.objects.filter(
            cars__id=car_id, status__in=REGENERATABLE_INVOICE_STATUSES,
        ).values_list('id', flat=True).distinct()
    )
    skipped = 0
    regenerated = 0
    for invoice_id in invoice_ids:
        try:
            with db_transaction.atomic():
                invoice = (
                    NewInvoice.objects
                    .select_for_update(nowait=True)
                    .get(id=invoice_id)
                )
                invoice.regenerate_items_from_cars()
                regenerated += 1
        except OperationalError:
            logger.warning(
                "[regenerate_invoices_for_car] invoice %s locked, skipping",
                invoice_id,
            )
            skipped += 1
        except NewInvoice.DoesNotExist:
            pass
    if regenerated or skipped:
        logger.info(
            "[regenerate_invoices_for_car] car=%s regenerated=%s skipped=%s",
            car_id, regenerated, skipped,
        )
    return {'car_id': car_id, 'regenerated': regenerated, 'skipped': skipped}


@shared_task(
    bind=True, max_retries=2, default_retry_delay=30, time_limit=180,
    autoretry_for=(Exception,), retry_backoff=True,
)
def recalculate_cars_total_price_task(self, car_ids):
    """Пересчитать Car.total_price / days / storage_cost для пачки машин.

    Используется после массовых правок прайса каталога (WarehouseService /
    LineService / CarrierService / CompanyService) и после `Car.save()`
    через сигнал. Синхронно делать `for car in qs: car.calculate_total_price()`
    в post_save / сигнале — это N+1 в HTTP-потоке. Celery-таска делает то же
    самое, но в фоне.

    `calculate_total_price()` обновляет days/storage_cost через
    `update_days_and_storage()`, поэтому bulk_update тянет все три поля.
    """
    from core.models import Car

    if not car_ids:
        return {'updated': 0}
    cars_to_update = []
    for car in Car.objects.filter(pk__in=car_ids).prefetch_related('car_services', 'warehouse'):
        try:
            car.calculate_total_price()
            cars_to_update.append(car)
        except Exception as exc:
            logger.error(
                "[recalculate_cars_total_price] car=%s failed: %s", car.pk, exc,
                exc_info=True,
            )
    if cars_to_update:
        Car.objects.bulk_update(
            cars_to_update,
            ['total_price', 'days', 'storage_cost'],
            batch_size=200,
        )
    logger.info(
        "[recalculate_cars_total_price] requested=%s updated=%s",
        len(car_ids), len(cars_to_update),
    )
    return {'requested': len(car_ids), 'updated': len(cars_to_update)}
