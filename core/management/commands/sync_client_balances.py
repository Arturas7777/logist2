from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Client
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Быстро синхронизирует поля баланса клиентов с реальными данными'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=int,
            help='ID конкретного клиента для синхронизации (если не указан - все клиенты)'
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
                self.sync_client_balance(client, dry_run)
            except Client.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Клиент с ID {client_id} не найден'))
        else:
            clients = Client.objects.all()
            self.stdout.write(f'Синхронизирую балансы для {clients.count()} клиентов...')
            
            for client in clients:
                self.sync_client_balance(client, dry_run)
        
        if not dry_run:
            self.stdout.write(self.style.SUCCESS('Балансы клиентов успешно синхронизированы'))
        else:
            self.stdout.write(self.style.SUCCESS('Предпросмотр завершен'))

    def sync_client_balance(self, client, dry_run=False):
        """Синхронизирует баланс конкретного клиента"""
        self.stdout.write(f'\nКлиент: {client.name} (ID: {client.id})')
        
        # Текущие значения в базе
        old_debt = client.debt
        old_cash = client.cash_balance
        old_card = client.card_balance
        
        # Рассчитываем реальные значения
        real_debt = client.real_balance
        real_cash_balance = client.cash_balance  # будет пересчитан в sync_balance_fields
        real_card_balance = client.card_balance  # будет пересчитан в sync_balance_fields
        
        self.stdout.write(f'  Текущий долг в БД: {old_debt}')
        self.stdout.write(f'  Реальный долг: {real_debt}')
        self.stdout.write(f'  Текущие наличные в БД: {old_cash}')
        self.stdout.write(f'  Текущие безналичные в БД: {old_card}')
        
        # Проверяем, нужны ли изменения
        needs_update = abs(old_debt - real_debt) >= 0.01
        
        if needs_update:
            self.stdout.write(self.style.WARNING('  ТРЕБУЕТСЯ СИНХРОНИЗАЦИЯ'))
            
            if not dry_run:
                try:
                    with transaction.atomic():
                        client.sync_balance_fields()
                        self.stdout.write(self.style.SUCCESS('  ✓ Баланс синхронизирован'))
                        self.stdout.write(f'  Новый долг: {client.debt}')
                        self.stdout.write(f'  Новые наличные: {client.cash_balance}')
                        self.stdout.write(f'  Новые безналичные: {client.card_balance}')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ Ошибка синхронизации: {e}'))
        else:
            self.stdout.write(self.style.SUCCESS('  ✓ Баланс уже синхронизирован'))

