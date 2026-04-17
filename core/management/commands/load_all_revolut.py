"""
Загрузка полной истории транзакций из Revolut Business API.

Revolut API возвращает максимум 1000 записей за запрос, поэтому
загружаем помесячно, начиная с заданной даты.

Использование:
    python manage.py load_all_revolut                     # с 2024-07-01
    python manage.py load_all_revolut --since 2024-01-01
    python manage.py load_all_revolut --dry-run
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models_banking import BankConnection, BankTransaction

REVOLUT_TYPE_MAP = {
    'card_payment': 'card_payment',
    'card_refund': 'card_refund',
    'transfer': 'transfer',
    'exchange': 'exchange',
    'topup': 'topup',
    'fee': 'fee',
    'atm': 'atm',
    'refund': 'refund',
    'tax': 'tax',
    'tax_refund': 'tax',
    'topup_return': 'topup',
    'card_chargeback': 'card_refund',
    'card_credit': 'card_payment',
}


class Command(BaseCommand):
    help = 'Загрузить полную историю Revolut-транзакций (помесячно)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since', type=str, default='2024-07-01',
            help='Дата начала (YYYY-MM-DD), по умолчанию 2024-07-01',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Только показать что будет загружено, без записи в БД',
        )

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')
        import re

        dry_run = options['dry_run']
        since_str = options['since']
        since = datetime.strptime(since_str, '%Y-%m-%d').date()

        conn = BankConnection.objects.filter(bank_type='REVOLUT', is_active=True).first()
        if not conn:
            self.stderr.write(self.style.ERROR('Нет активного подключения Revolut'))
            return

        from core.services.revolut_service import RevolutService
        svc = RevolutService(conn)

        svc._get_valid_token()
        self.stdout.write(self.style.SUCCESS('  Токен Revolut валиден'))

        existing_before = BankTransaction.objects.filter(connection=conn).count()
        self.stdout.write(f'  Транзакций до загрузки: {existing_before}')

        if dry_run:
            self.stdout.write(self.style.WARNING('  === DRY RUN ===\n'))

        total_fetched = 0
        total_created = 0
        total_updated = 0
        current = since

        today = date.today()

        while current <= today:
            month_end = date(
                current.year + (current.month // 12),
                (current.month % 12) + 1,
                1,
            ) - timedelta(days=1)
            if month_end > today:
                month_end = today

            from_iso = f'{current.isoformat()}T00:00:00Z'
            to_iso = f'{month_end.isoformat()}T23:59:59Z'

            self.stdout.write(f'  {current.strftime("%Y-%m")} ({from_iso[:10]} .. {to_iso[:10]}): ', ending='')

            try:
                params = {
                    'from': from_iso,
                    'to': to_iso,
                    'count': 1000,
                }
                data = svc._api_get(svc.TRANSACTIONS_ENDPOINT, params=params)

                month_created = 0
                month_updated = 0

                for item in data:
                    ext_id = item.get('id', '')
                    raw_type = item.get('type', 'other').lower()
                    tx_type = REVOLUT_TYPE_MAP.get(raw_type, 'other')

                    legs = item.get('legs', [])
                    amount = Decimal('0')
                    currency = ''
                    counterparty = ''
                    description = item.get('reference', '') or item.get('description', '')

                    if legs:
                        leg = legs[0]
                        amount = Decimal(str(leg.get('amount', 0)))
                        currency = leg.get('currency', '')
                        cp = leg.get('counterparty', {})
                        if isinstance(cp, dict):
                            counterparty = (
                                cp.get('account_name', '')
                                or cp.get('name', '')
                                or cp.get('company_name', '')
                            )
                        leg_desc = leg.get('description', '')
                        if not description and leg_desc:
                            description = leg_desc
                        if not counterparty and leg_desc:
                            pf_match = re.match(
                                r'(?:Payment from|Transfer from)\s+(.+)',
                                leg_desc, re.IGNORECASE,
                            )
                            if pf_match:
                                counterparty = pf_match.group(1).strip()

                    if not counterparty:
                        top_cp = item.get('counterparty', {})
                        if isinstance(top_cp, dict):
                            counterparty = (
                                top_cp.get('name', '')
                                or top_cp.get('account_name', '')
                                or top_cp.get('company_name', '')
                            )

                    if not counterparty:
                        merchant = item.get('merchant', {})
                        if isinstance(merchant, dict):
                            counterparty = merchant.get('name', '')

                    created_at_str = item.get('created_at', '')
                    try:
                        created_at = parse_datetime(created_at_str)
                        if created_at is None:
                            created_at = timezone.now()
                    except Exception:
                        created_at = timezone.now()

                    state = item.get('state', 'completed').lower()

                    if dry_run:
                        exists = BankTransaction.objects.filter(
                            connection=conn, external_id=ext_id,
                        ).exists()
                        if exists:
                            month_updated += 1
                        else:
                            month_created += 1
                    else:
                        tx, created = BankTransaction.objects.update_or_create(
                            connection=conn,
                            external_id=ext_id,
                            defaults={
                                'transaction_type': tx_type,
                                'amount': amount,
                                'currency': currency,
                                'description': description[:500] if description else '',
                                'counterparty_name': counterparty[:200] if counterparty else '',
                                'state': state,
                                'created_at': created_at,
                            },
                        )
                        if created:
                            month_created += 1
                            if tx_type in ('fee', 'exchange', 'tax'):
                                type_labels = {
                                    'fee': 'Комиссия банка',
                                    'exchange': 'Обмен валют',
                                    'tax': 'Налог',
                                }
                                tx.reconciliation_skipped = True
                                tx.reconciliation_note = (
                                    f'Авто-пропуск: {type_labels.get(tx_type, tx_type)}'
                                )
                                tx.save(update_fields=[
                                    'reconciliation_skipped', 'reconciliation_note',
                                ])
                        else:
                            month_updated += 1

                total_fetched += len(data)
                total_created += month_created
                total_updated += month_updated

                self.stdout.write(
                    f'{len(data)} txns (new: {month_created}, updated: {month_updated})'
                )

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'ОШИБКА: {e}'))

            current = month_end + timedelta(days=1)

        existing_after = BankTransaction.objects.filter(connection=conn).count()

        self.stdout.write(self.style.MIGRATE_HEADING('\n  Итог'))
        self.stdout.write(f'  Загружено из API: {total_fetched}')
        self.stdout.write(f'  Новых: {total_created}')
        self.stdout.write(f'  Обновлённых: {total_updated}')
        self.stdout.write(f'  Транзакций в БД: {existing_before} -> {existing_after}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n  DRY RUN. Запустите без --dry-run для записи.\n'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\n  Загрузка завершена.\n'))
