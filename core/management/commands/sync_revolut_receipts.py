"""
Management-команда для загрузки чеков из Revolut Expenses API.

Использование:
    python manage.py sync_revolut_receipts                    # все активные Revolut-подключения
    python manage.py sync_revolut_receipts --id 1             # конкретное подключение
    python manage.py sync_revolut_receipts --days 90          # глубина поиска expenses
    python manage.py sync_revolut_receipts --missing-only     # только для BT без файла чека

Для cron (раз в час):
    0 * * * * cd /var/www/logist2 && .venv/bin/python manage.py sync_revolut_receipts >> /var/log/logist2/revolut_receipts.log 2>&1
"""

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Загрузка чеков из Revolut Expenses API в BankTransaction.receipt_file'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='ID конкретного BankConnection')
        parser.add_argument('--days', type=int, default=30,
                            help='За сколько дней тянуть expenses (по умолчанию 30)')
        parser.add_argument('--missing-only', action='store_true',
                            help='Только догрузить чеки для BT без receipt_file (игнорирует --days)')

    def handle(self, *args, **options):
        from core.models_banking import BankConnection
        from core.services.revolut_service import RevolutService

        connection_id = options.get('id')
        days = options.get('days', 30)
        missing_only = options.get('missing_only', False)

        qs = BankConnection.objects.filter(bank_type='REVOLUT', is_active=True)
        if connection_id:
            qs = qs.filter(id=connection_id)

        if not qs.exists():
            self.stdout.write(self.style.WARNING('Нет активных Revolut-подключений.'))
            return

        total_updated = 0
        total_downloaded = 0
        errors = 0

        for conn in qs:
            self.stdout.write(f'-> {conn}')
            service = RevolutService(conn)

            try:
                if missing_only:
                    downloaded = service.fetch_receipts_for_existing()
                    total_downloaded += downloaded
                    self.stdout.write(self.style.SUCCESS(
                        f'  Догружено чеков: {downloaded}'
                    ))
                else:
                    updated = service.fetch_expenses(days=days)
                    total_updated += updated
                    self.stdout.write(self.style.SUCCESS(
                        f'  Обновлено expenses: {updated}'
                    ))
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(f'  Ошибка: {e}'))
                logger.exception('[sync_revolut_receipts] connection=%s', conn.pk)

        self.stdout.write('')
        if missing_only:
            self.stdout.write(self.style.SUCCESS(
                f'Итого догружено чеков: {total_downloaded}'
                f'{f", {errors} ошибок" if errors else ""}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Итого обновлено: {total_updated}'
                f'{f", {errors} ошибок" if errors else ""}'
            ))
