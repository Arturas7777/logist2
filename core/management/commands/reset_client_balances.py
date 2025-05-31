from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from core.models import Client, Payment
import logging

logger = logging.getLogger('django')

class Command(BaseCommand):
    help = 'Resets client balances to zero by creating correcting payments or direct reset'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=int,
            help='Reset balance for a specific client by ID',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Reset balances for all clients',
        )

    def handle(self, *args, **options):
        if not (options['client_id'] or options['all']):
            self.stdout.write(self.style.ERROR('Specify --client-id or --all'))
            return

        clients = []
        if options['client_id']:
            try:
                client = Client.objects.get(id=options['client_id'])
                clients = [client]
            except Client.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Client with ID {options["client_id"]} not found'))
                return
        elif options['all']:
            clients = Client.objects.all()

        if not clients:
            self.stdout.write(self.style.WARNING('No clients found to reset'))
            return

        with transaction.atomic():
            for client in clients:
                self.stdout.write(f'Processing client: {client.name}')
                logger.info(f'Resetting balances for client {client.name}: debt={client.debt}, cash_balance={client.cash_balance}, card_balance={client.card_balance}')

                # Корректируем долг
                if client.debt != 0:
                    amount = client.debt  # Уменьшаем долг
                    payment = Payment(
                        amount=abs(amount),
                        payment_type='BALANCE',
                        payer=client,
                        recipient='System Correction',
                        from_balance=False,
                        from_cash_balance=False,
                        description=f'Correction to reset debt from {client.debt} to 0'
                    )
                    payment.save()
                    self.stdout.write(self.style.SUCCESS(f'Created correction payment for debt: {amount}'))

                # Корректируем наличный баланс
                if client.cash_balance != 0:
                    amount = -client.cash_balance
                    if amount > 0 and client.cash_balance >= amount:
                        payment = Payment(
                            amount=abs(amount),
                            payment_type='CASH',
                            payer=client,
                            recipient='System Correction',
                            from_balance=True,
                            from_cash_balance=True,
                            description=f'Correction to reset cash balance from {client.cash_balance} to 0'
                        )
                        payment.save()
                        self.stdout.write(self.style.SUCCESS(f'Created correction payment for cash balance: {amount}'))
                    else:
                        client.cash_balance = 0
                        client.save()
                        self.stdout.write(self.style.WARNING(f'Insufficient cash_balance, directly reset cash balance to 0'))

                # Корректируем безналичный баланс
                if client.card_balance != 0:
                    amount = -client.card_balance
                    if amount > 0 and client.card_balance >= amount:
                        payment = Payment(
                            amount=abs(amount),
                            payment_type='CARD',
                            payer=client,
                            recipient='System Correction',
                            from_balance=True,
                            from_cash_balance=False,
                            description=f'Correction to reset card balance from {client.card_balance} to 0'
                        )
                        payment.save()
                        self.stdout.write(self.style.SUCCESS(f'Created correction payment for card balance: {amount}'))
                    else:
                        client.card_balance = 0
                        client.save()
                        self.stdout.write(self.style.WARNING(f'Insufficient card_balance, directly reset card balance to 0'))

                # Проверяем, что балансы обнулены
                client.refresh_from_db()
                logger.info(f'Client {client.name} after reset: debt={client.debt}, cash_balance={client.cash_balance}, card_balance={client.card_balance}')
                if client.debt == 0 and client.cash_balance == 0 and client.card_balance == 0:
                    self.stdout.write(self.style.SUCCESS(f'Client {client.name} balances reset to zero'))
                else:
                    self.stdout.write(self.style.ERROR(f'Failed to reset balances for {client.name}: debt={client.debt}, cash_balance={client.cash_balance}, card_balance={client.card_balance}'))

        self.stdout.write(self.style.SUCCESS('Balance reset completed'))