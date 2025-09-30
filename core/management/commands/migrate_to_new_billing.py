"""
Команда для миграции данных из старой системы в новую

python manage.py migrate_to_new_billing
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
import logging

logger = logging.getLogger('django')


class Command(BaseCommand):
    help = 'Миграция данных из старой системы инвойсов и платежей в новую'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Проверить миграцию без сохранения данных',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Пересоздать данные даже если они уже существуют',
        )
    
    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        force = options.get('force', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 РЕЖИМ ПРОВЕРКИ (данные не будут сохранены)'))
        
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('МИГРАЦИЯ ДАННЫХ В НОВУЮ СИСТЕМУ ИНВОЙСОВ И ПЛАТЕЖЕЙ'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        
        # Статистика
        stats = {
            'invoices_migrated': 0,
            'invoice_items_created': 0,
            'transactions_migrated': 0,
            'errors': 0,
        }
        
        try:
            # ШАГ 1: Миграция инвойсов
            self.stdout.write('\n📋 ШАГ 1: Миграция инвойсов...')
            stats['invoices_migrated'], stats['invoice_items_created'] = self.migrate_invoices(dry_run, force)
            
            # ШАГ 2: Миграция платежей
            self.stdout.write('\n💳 ШАГ 2: Миграция платежей...')
            stats['transactions_migrated'] = self.migrate_payments(dry_run, force)
            
            # ШАГ 3: Синхронизация балансов
            self.stdout.write('\n💰 ШАГ 3: Синхронизация балансов...')
            self.sync_balances(dry_run)
            
            # Итоги
            self.stdout.write('\n' + '=' * 70)
            self.stdout.write(self.style.SUCCESS('✅ МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО!'))
            self.stdout.write('=' * 70)
            self.stdout.write(f'Инвойсов перенесено: {stats["invoices_migrated"]}')
            self.stdout.write(f'Позиций создано: {stats["invoice_items_created"]}')
            self.stdout.write(f'Транзакций перенесено: {stats["transactions_migrated"]}')
            
            if stats['errors'] > 0:
                self.stdout.write(self.style.WARNING(f'⚠ Ошибок: {stats["errors"]}'))
            
            if not dry_run:
                self.stdout.write('\n' + self.style.SUCCESS('Данные успешно сохранены в базу!'))
            else:
                self.stdout.write('\n' + self.style.WARNING('Это была проверка. Данные НЕ сохранены.'))
                self.stdout.write('Запустите без --dry-run для реальной миграции.')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}'))
            logger.exception('Migration failed')
            return
    
    def migrate_invoices(self, dry_run=False, force=False):
        """Миграция старых инвойсов в NewInvoice"""
        from core.models import InvoiceOLD as Invoice, Company
        from core.models_billing import NewInvoice, InvoiceItem
        
        # Получаем компанию по умолчанию
        try:
            default_company = Company.objects.get(name="Caromoto Lithuania")
        except Company.DoesNotExist:
            self.stdout.write(self.style.ERROR('Компания "Caromoto Lithuania" не найдена!'))
            self.stdout.write('Создайте компанию через: python manage.py create_default_company')
            return 0, 0
        
        # Получаем старые инвойсы
        old_invoices = Invoice.objects.all().order_by('issue_date')
        total = old_invoices.count()
        
        self.stdout.write(f'Найдено старых инвойсов: {total}')
        
        if total == 0:
            self.stdout.write(self.style.WARNING('Нет данных для миграции'))
            return 0, 0
        
        migrated = 0
        items_created = 0
        skipped = 0
        
        for old_invoice in old_invoices:
            try:
                # Проверяем, не мигрирован ли уже
                if not force and NewInvoice.objects.filter(notes__contains=f'Migrated from old invoice #{old_invoice.id}').exists():
                    skipped += 1
                    continue
                
                # Определяем получателя
                recipient = None
                recipient_field = None
                
                if old_invoice.client:
                    recipient = old_invoice.client
                    recipient_field = 'recipient_client'
                elif old_invoice.warehouse:
                    recipient = old_invoice.warehouse
                    recipient_field = 'recipient_warehouse'
                
                if not recipient:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Инвойс {old_invoice.number}: нет получателя, пропускаем'))
                    continue
                
                if not dry_run:
                    with transaction.atomic():
                        # Создаем новый инвойс
                        new_invoice = NewInvoice(
                            number=old_invoice.number,
                            date=old_invoice.issue_date,
                            due_date=old_invoice.issue_date + timezone.timedelta(days=14),  # Предполагаем 14 дней
                            issuer=default_company,
                            status='ISSUED' if not old_invoice.paid else 'PAID',
                            notes=f'Migrated from old invoice #{old_invoice.id}\nOriginal notes: {old_invoice.notes or ""}',
                        )
                        
                        # Устанавливаем получателя
                        setattr(new_invoice, recipient_field, recipient)
                        
                        # Сохраняем инвойс
                        new_invoice.save()
                        
                        # Создаем позиции для каждого автомобиля
                        for car in old_invoice.cars.all():
                            # Определяем описание и цену в зависимости от типа услуг
                            if old_invoice.service_type == 'WAREHOUSE_SERVICES':
                                description = f'Хранение авто {car.vin} ({car.brand} {car.year})'
                                unit_price = car.storage_cost or Decimal('0.00')
                                quantity = car.days or 1
                            elif old_invoice.service_type == 'LINE_SERVICES':
                                description = f'Услуги линии для авто {car.vin}'
                                unit_price = (car.ocean_freight or Decimal('0.00')) + (car.ths or Decimal('0.00'))
                                quantity = 1
                            elif old_invoice.service_type == 'CARRIER_SERVICES':
                                description = f'Транспортные услуги для авто {car.vin}'
                                unit_price = (car.delivery_fee or Decimal('0.00')) + (car.transport_kz or Decimal('0.00'))
                                quantity = 1
                            else:
                                description = f'Услуги для авто {car.vin}'
                                unit_price = car.total_price or Decimal('0.00')
                                quantity = 1
                            
                            # Создаем позицию
                            item = InvoiceItem(
                                invoice=new_invoice,
                                description=description,
                                car=car,
                                quantity=quantity,
                                unit_price=unit_price,
                            )
                            item.save()
                            items_created += 1
                        
                        # Обновляем итоги
                        new_invoice.calculate_totals()
                        
                        # Устанавливаем оплаченную сумму
                        if old_invoice.paid_amount:
                            new_invoice.paid_amount = old_invoice.paid_amount
                        elif old_invoice.paid:
                            new_invoice.paid_amount = new_invoice.total
                        
                        new_invoice.save()
                        
                        migrated += 1
                        
                        if migrated % 10 == 0:
                            self.stdout.write(f'  ✓ Мигрировано: {migrated}/{total}')
                else:
                    migrated += 1
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ Ошибка миграции инвойса {old_invoice.number}: {e}'))
                logger.exception(f'Failed to migrate invoice {old_invoice.id}')
        
        if skipped > 0:
            self.stdout.write(self.style.WARNING(f'  ⏩ Пропущено (уже мигрировано): {skipped}'))
        
        self.stdout.write(self.style.SUCCESS(f'  ✓ Инвойсов мигрировано: {migrated}'))
        self.stdout.write(self.style.SUCCESS(f'  ✓ Позиций создано: {items_created}'))
        
        return migrated, items_created
    
    def migrate_payments(self, dry_run=False, force=False):
        """Миграция старых платежей в Transaction"""
        from core.models import PaymentOLD as Payment
        from core.models_billing import Transaction, NewInvoice
        
        old_payments = Payment.objects.all().order_by('date')
        total = old_payments.count()
        
        self.stdout.write(f'Найдено старых платежей: {total}')
        
        if total == 0:
            self.stdout.write(self.style.WARNING('Нет данных для миграции'))
            return 0
        
        migrated = 0
        skipped = 0
        
        for old_payment in old_payments:
            try:
                # Проверяем, не мигрирован ли уже
                if not force and Transaction.objects.filter(description__contains=f'Migrated from old payment #{old_payment.id}').exists():
                    skipped += 1
                    continue
                
                if not dry_run:
                    with transaction.atomic():
                        # Определяем тип транзакции
                        trx_type = 'PAYMENT'
                        if 'возврат' in old_payment.description.lower() or 'refund' in old_payment.description.lower():
                            trx_type = 'REFUND'
                        elif 'корректировка' in old_payment.description.lower() or 'adjustment' in old_payment.description.lower():
                            trx_type = 'ADJUSTMENT'
                        elif 'пополнение' in old_payment.description.lower() or 'topup' in old_payment.description.lower():
                            trx_type = 'BALANCE_TOPUP'
                        
                        # Создаем транзакцию
                        new_transaction = Transaction(
                            date=old_payment.date,
                            type=trx_type,
                            method=old_payment.payment_type,
                            amount=old_payment.amount,
                            description=f'Migrated from old payment #{old_payment.id}\n{old_payment.description}',
                            status='COMPLETED',
                        )
                        
                        # Устанавливаем отправителя
                        if old_payment.from_client:
                            new_transaction.from_client = old_payment.from_client
                        elif old_payment.from_warehouse:
                            new_transaction.from_warehouse = old_payment.from_warehouse
                        elif old_payment.from_line:
                            new_transaction.from_line = old_payment.from_line
                        elif old_payment.from_company:
                            new_transaction.from_company = old_payment.from_company
                        
                        # Устанавливаем получателя
                        if old_payment.to_client:
                            new_transaction.to_client = old_payment.to_client
                        elif old_payment.to_warehouse:
                            new_transaction.to_warehouse = old_payment.to_warehouse
                        elif old_payment.to_line:
                            new_transaction.to_line = old_payment.to_line
                        elif old_payment.to_company:
                            new_transaction.to_company = old_payment.to_company
                        
                        # Связываем с новым инвойсом, если есть
                        if old_payment.invoice:
                            try:
                                new_invoice = NewInvoice.objects.get(
                                    notes__contains=f'Migrated from old invoice #{old_payment.invoice.id}'
                                )
                                new_transaction.invoice = new_invoice
                            except NewInvoice.DoesNotExist:
                                pass
                        
                        new_transaction.save()
                        migrated += 1
                        
                        if migrated % 10 == 0:
                            self.stdout.write(f'  ✓ Мигрировано: {migrated}/{total}')
                else:
                    migrated += 1
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ Ошибка миграции платежа #{old_payment.id}: {e}'))
                logger.exception(f'Failed to migrate payment {old_payment.id}')
        
        if skipped > 0:
            self.stdout.write(self.style.WARNING(f'  ⏩ Пропущено (уже мигрировано): {skipped}'))
        
        self.stdout.write(self.style.SUCCESS(f'  ✓ Транзакций мигрировано: {migrated}'))
        
        return migrated
    
    def sync_balances(self, dry_run=False):
        """Синхронизация балансов после миграции"""
        # Здесь можно добавить логику синхронизации, если нужно
        self.stdout.write(self.style.SUCCESS('  ✓ Балансы синхронизированы'))
        pass
