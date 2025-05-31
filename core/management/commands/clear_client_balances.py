from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Client, Payment, Invoice
import logging

logger = logging.getLogger('django')

class Command(BaseCommand):
    help = 'Clears client balances, invoices, and payments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--client-id',
            type=int,
            help='Clear balances and data for a specific client by ID',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Clear balances and data for all clients',
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
            self.stdout.write(self.style.WARNING('No clients found to clear'))
            return

        with transaction.atomic():
            for client in clients:
                self.stdout.write(f'Clearing data for client: {client.name}')
                logger.info(f'Clearing balances for client {client.name}: debt={client.debt}, cash_balance={client.cash_balance}, card_balance={client.card_balance}')

                # Удаляем связанные платежи
                Payment.objects.filter(payer=client).delete()
                self.stdout.write(self.style.SUCCESS(f'Deleted payments for {client.name}'))

                # Удаляем связанные инвойсы
                Invoice.objects.filter(client=client).delete()
                self.stdout.write(self.style.SUCCESS(f'Deleted invoices for {client.name}'))

                # Обнуляем балансы
                client.debt = 0
                client.cash_balance = 0
                client.card_balance = 0
                client.save()
                self.stdout.write(self.style.SUCCESS(f'Client {client.name} balances cleared to zero'))

        self.stdout.write(self.style.SUCCESS('Balance clearing completed'))