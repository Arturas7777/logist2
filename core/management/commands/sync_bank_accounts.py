"""
Management command для синхронизации банковских счетов через API.

Использование:
    python manage.py sync_bank_accounts          # синхронизирует все активные подключения
    python manage.py sync_bank_accounts --id 1   # только конкретное подключение

Для cron (каждые 15 минут):
    */15 * * * * cd /var/www/logist2 && .venv/bin/python manage.py sync_bank_accounts >> /var/log/logist2/bank_sync.log 2>&1
"""

from django.core.management.base import BaseCommand
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Синхронизация банковских счетов и транзакций через API (Revolut и др.)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--id',
            type=int,
            help='ID конкретного BankConnection для синхронизации',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='За сколько дней загружать транзакции (по умолчанию: 30)',
        )

    def handle(self, *args, **options):
        from core.models_banking import BankConnection
        from core.services.revolut_service import RevolutService

        connection_id = options.get('id')
        days = options.get('days', 30)

        if connection_id:
            connections = BankConnection.objects.filter(id=connection_id, is_active=True)
        else:
            connections = BankConnection.objects.filter(is_active=True)

        if not connections.exists():
            self.stdout.write(self.style.WARNING('Нет активных банковских подключений.'))
            return

        total_accounts = 0
        total_transactions = 0
        errors = 0

        for conn in connections:
            self.stdout.write(f'Синхронизация: {conn} ...')

            if conn.bank_type == 'REVOLUT':
                service = RevolutService(conn)
            else:
                self.stdout.write(self.style.WARNING(
                    f'  Тип банка {conn.bank_type} пока не поддерживается, пропускаем.'
                ))
                continue

            result = service.sync_all()

            if result['error']:
                errors += 1
                self.stdout.write(self.style.ERROR(f'  Ошибка: {result["error"]}'))
            else:
                n_accounts = len(result['accounts'])
                n_transactions = len(result['transactions'])
                total_accounts += n_accounts
                total_transactions += n_transactions
                self.stdout.write(self.style.SUCCESS(
                    f'  OK: {n_accounts} счетов, {n_transactions} транзакций'
                ))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Итого: {total_accounts} счетов, {total_transactions} транзакций'
            f'{f", {errors} ошибок" if errors else ""}'
        ))
