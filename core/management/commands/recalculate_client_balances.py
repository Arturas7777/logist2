from django.core.management.base import BaseCommand
from django.db import transaction, models
from django.db.models import Sum
from core.models import Client, InvoiceOLD as Invoice, PaymentOLD as Payment
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Пересчитывает баланс всех клиентов на основе реальных инвойсов и платежей'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=int,
            help='ID конкретного клиента для пересчета (если не указан - все клиенты)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет изменено без сохранения'
        )

    def handle(self, *args, **options):
        client_id = options.get('client_id')
        dry_run = options.get('dry_run')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('РЕЖИМ ПРЕДПРОСМОТРА - изменения не будут сохранены'))
        
        if client_id:
            try:
                client = Client.objects.get(id=client_id)
                self.recalculate_client_balance(client, dry_run)
            except Client.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Клиент с ID {client_id} не найден'))
        else:
            clients = Client.objects.all()
            self.stdout.write(f'Пересчитываю баланс для {clients.count()} клиентов...')
            
            for client in clients:
                self.recalculate_client_balance(client, dry_run)
        
        if not dry_run:
            self.stdout.write(self.style.SUCCESS('Балансы клиентов успешно пересчитаны'))
        else:
            self.stdout.write(self.style.SUCCESS('Предпросмотр завершен'))

    def recalculate_client_balance(self, client, dry_run=False):
        """Пересчитывает баланс конкретного клиента"""
        self.stdout.write(f'\nКлиент: {client.name} (ID: {client.id})')
        
        # Текущие значения в базе
        old_debt = client.debt
        old_cash = client.cash_balance
        old_card = client.card_balance
        
        # Рассчитываем реальные значения
        real_debt = self.calculate_real_debt(client)
        real_cash_balance = self.calculate_real_cash_balance(client)
        real_card_balance = self.calculate_real_card_balance(client)
        
        self.stdout.write(f'  Текущий долг в БД: {old_debt}')
        self.stdout.write(f'  Реальный долг: {real_debt}')
        self.stdout.write(f'  Текущие наличные в БД: {old_cash}')
        self.stdout.write(f'  Реальные наличные: {real_cash_balance}')
        self.stdout.write(f'  Текущие безналичные в БД: {old_card}')
        self.stdout.write(f'  Реальные безналичные: {real_card_balance}')
        
        # Проверяем, нужны ли изменения
        needs_update = (
            old_debt != real_debt or 
            old_cash != real_cash_balance or 
            old_card != real_card_balance
        )
        
        if needs_update:
            self.stdout.write(self.style.WARNING('  ТРЕБУЕТСЯ ОБНОВЛЕНИЕ'))
            
            if not dry_run:
                with transaction.atomic():
                    client.debt = real_debt
                    client.cash_balance = real_cash_balance
                    client.card_balance = real_card_balance
                    client.save()
                    self.stdout.write(self.style.SUCCESS('  ✓ Баланс обновлен'))
        else:
            self.stdout.write(self.style.SUCCESS('  ✓ Баланс корректен'))

    def calculate_real_debt(self, client):
        """Рассчитывает реальный долг клиента"""
        # Сумма всех входящих инвойсов (не исходящих)
        total_invoiced = Invoice.objects.filter(
            client=client, 
            is_outgoing=False
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Сумма всех платежей (кроме списаний с баланса)
        total_paid = Payment.objects.filter(
            payer=client,
            from_balance=False
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Долг = инвойсы - платежи
        debt = total_invoiced - total_paid
        
        return debt

    def calculate_real_cash_balance(self, client):
        """Рассчитывает реальный наличный баланс клиента"""
        # Наличные платежи (кроме списаний с баланса)
        cash_payments = Payment.objects.filter(
            payer=client,
            payment_type='CASH',
            from_balance=False
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Списания с наличного баланса
        cash_debits = Payment.objects.filter(
            payer=client,
            from_balance=True,
            from_cash_balance=True
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Накопительный наличный баланс
        cash_balance = cash_payments - cash_debits
        
        return cash_balance

    def calculate_real_card_balance(self, client):
        """Рассчитывает реальный безналичный баланс клиента"""
        # Безналичные платежи (кроме списаний с баланса)
        card_payments = Payment.objects.filter(
            payer=client,
            payment_type='CARD',
            from_balance=False
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Списания с безналичного баланса
        card_debits = Payment.objects.filter(
            payer=client,
            from_balance=True,
            from_cash_balance=False
        ).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Накопительный безналичный баланс
        card_balance = card_payments - card_debits
        
        return card_balance
