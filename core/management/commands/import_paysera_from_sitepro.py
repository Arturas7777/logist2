"""
Импорт транзакций Paysera из site.pro в Django BankTransaction.

Site.pro уже содержит данные из выписок Paysera, импортированных бухгалтером.
Эта команда перетягивает их в Django для единого обзора.

Использование:
    python manage.py import_paysera_from_sitepro
    python manage.py import_paysera_from_sitepro --dry-run
"""

import sys
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Company
from core.models_accounting import SiteProConnection
from core.models_banking import BankConnection, BankTransaction
from core.services.sitepro_service import SiteProService


class Command(BaseCommand):
    help = 'Импорт транзакций Paysera из site.pro в Django'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать что будет импортировано без записи в БД',
        )

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('  === DRY RUN ===\n'))

        sp_conn = SiteProConnection.objects.filter(is_active=True).first()
        if not sp_conn:
            self.stderr.write(self.style.ERROR('Нет активного подключения SiteProConnection'))
            return

        svc = SiteProService(sp_conn)
        company = Company.get_default()

        self.stdout.write(self.style.MIGRATE_HEADING('\n  Загрузка Paysera транзакций из site.pro'))

        all_txns = svc.list_bank_transactions()
        paysera_txns = [
            t for t in all_txns
            if 'Paysera' in (t.get('accountingBankInternalName', '') or '')
        ]

        self.stdout.write(f'  Всего банковских транзакций в site.pro: {len(all_txns)}')
        self.stdout.write(f'  Из них Paysera: {len(paysera_txns)}')

        accounts = {}
        for t in paysera_txns:
            bank_name = t.get('accountingBankInternalName', 'Paysera')
            if bank_name not in accounts:
                accounts[bank_name] = []
            accounts[bank_name].append(t)

        self.stdout.write(f'  Счетов Paysera: {len(accounts)}')
        for name, txns in accounts.items():
            self.stdout.write(f'    {name}: {len(txns)} транзакций')

        conn_map = {}
        for bank_name in accounts:
            if dry_run:
                self.stdout.write(f'  [dry] Создание BankConnection: {bank_name}')
                conn_map[bank_name] = None
            else:
                conn, created = BankConnection.objects.get_or_create(
                    bank_type='PAYSERA',
                    company=company,
                    name=bank_name,
                    defaults={'is_active': True},
                )
                conn_map[bank_name] = conn
                action = 'создано' if created else 'уже есть'
                self.stdout.write(f'  BankConnection "{bank_name}": {action} (id={conn.pk})')

        self.stdout.write(self.style.MIGRATE_HEADING('\n  Импорт транзакций'))

        total_created = 0
        total_updated = 0

        for t in paysera_txns:
            bank_name = t.get('accountingBankInternalName', 'Paysera')
            conn = conn_map.get(bank_name)

            sp_id = str(t.get('id', ''))
            ext_id = f'sitepro-{sp_id}'

            d = t.get('date', '')
            client_name = t.get('clientName', '') or ''
            amount_raw = Decimal(str(t.get('amount', 0) or 0))
            cd = t.get('creditDebitIndicator', '')
            details = (t.get('details', '') or '')
            note = (t.get('note', '') or '')
            currency = t.get('currencyCode', 'EUR') or 'EUR'

            if cd == 'K':
                amount = -amount_raw
            else:
                amount = amount_raw

            description = details or note
            if details and note and details != note:
                description = f'{details} | {note}'

            try:
                created_at = datetime.strptime(d, '%Y-%m-%d')
                created_at = timezone.make_aware(
                    created_at, timezone.get_current_timezone()
                )
            except (ValueError, TypeError):
                created_at = timezone.now()

            tx_type = 'transfer'
            if 'комиссия' in description.lower() or 'обслуживание' in description.lower():
                tx_type = 'fee'
            elif 'плата за' in description.lower() or 'mokestis' in description.lower():
                tx_type = 'fee'
            elif 'налог' in description.lower() or 'mokest' in description.lower():
                tx_type = 'tax'

            if dry_run:
                arrow = '+' if amount >= 0 else ''
                self.stdout.write(
                    f'    [dry] {d} {arrow}{amount} {currency}  '
                    f'{client_name[:30]}  ({tx_type})  {description[:50]}'
                )
                total_created += 1
                continue

            tx, created = BankTransaction.objects.update_or_create(
                connection=conn,
                external_id=ext_id,
                defaults={
                    'transaction_type': tx_type,
                    'amount': amount,
                    'currency': currency,
                    'description': description[:500],
                    'counterparty_name': client_name[:200],
                    'state': 'completed',
                    'created_at': created_at,
                },
            )

            if created:
                total_created += 1
                if tx_type in ('fee', 'tax'):
                    tx.reconciliation_skipped = True
                    tx.reconciliation_note = f'Авто-пропуск: {tx_type}'
                    tx.save(update_fields=['reconciliation_skipped', 'reconciliation_note'])
            else:
                total_updated += 1

        self.stdout.write(self.style.MIGRATE_HEADING('\n  Итог'))
        self.stdout.write(f'  Новых: {total_created}')
        self.stdout.write(f'  Обновлённых: {total_updated}')

        total_bank = BankTransaction.objects.count()
        self.stdout.write(f'  Всего транзакций в Django: {total_bank}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\n  DRY RUN. Запустите без --dry-run.\n'))
        else:
            self.stdout.write(self.style.SUCCESS('\n  Импорт Paysera завершён.\n'))
