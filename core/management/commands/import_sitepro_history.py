"""
Импорт исторических данных из site.pro в Django.

Этапы:
1. Клиенты — сопоставление/создание Client
2. Продажи (PARDP) — создание NewInvoice + SiteProInvoiceSync
3. Коммерческие предложения (AV) — только логирование

Использование:
    python manage.py import_sitepro_history --dry-run     # только показать что будет импортировано
    python manage.py import_sitepro_history               # реальный импорт
    python manage.py import_sitepro_history --force        # повторный импорт (перезаписать)
"""

import sys
import re
from collections import defaultdict
from datetime import date as date_type, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone


def parse_date(value) -> date_type | None:
    """Parse a date string (YYYY-MM-DD) or return as-is if already a date."""
    if value is None:
        return None
    if isinstance(value, date_type):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

from core.models import Client, Company
from core.models_billing import NewInvoice
from core.models_accounting import SiteProConnection, SiteProInvoiceSync
from core.services.sitepro_service import SiteProService


def normalize_name(name: str) -> str:
    """Normalize name for fuzzy matching."""
    name = name.strip().upper()
    name = re.sub(r'["\'\u201c\u201d\u201e\u201f]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def match_client(sp_name: str, django_clients: dict) -> Client | None:
    """Try to match a site.pro client name to a Django Client."""
    norm = normalize_name(sp_name)
    if norm in django_clients:
        return django_clients[norm]
    for key, client in django_clients.items():
        if key in norm or norm in key:
            return client
    return None


class Command(BaseCommand):
    help = 'Импорт исторических данных из site.pro (клиенты, продажи)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать что будет импортировано, без изменений в БД',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Перезаписать уже импортированные записи',
        )
        parser.add_argument(
            '--skip-clients', action='store_true',
            help='Пропустить импорт клиентов',
        )

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')
        dry_run = options['dry_run']
        force = options['force']
        skip_clients = options['skip_clients']

        if dry_run:
            self.stdout.write(self.style.WARNING('\n  === DRY RUN — изменения не будут сохранены ===\n'))

        conn = SiteProConnection.objects.filter(is_active=True).first()
        if not conn:
            self.stderr.write(self.style.ERROR('Нет активного подключения SiteProConnection'))
            return

        svc = SiteProService(conn)
        company = Company.get_default()

        # ── 1. КЛИЕНТЫ ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Этап 1: Клиенты'))

        sp_clients = svc.list_all_clients()
        sp_sales = svc.list_all_sales()

        client_ids_with_sales = {s.get('clientId') for s in sp_sales if s.get('isSale')}
        relevant_sp_clients = [c for c in sp_clients if c.get('id') in client_ids_with_sales]

        self.stdout.write(f'  Клиентов в site.pro: {len(sp_clients)}')
        self.stdout.write(f'  Клиентов с реальными продажами: {len(relevant_sp_clients)}')

        django_clients_by_norm = {}
        for c in Client.objects.all():
            django_clients_by_norm[normalize_name(c.name)] = c

        self.stdout.write(f'  Клиентов в Django: {len(django_clients_by_norm)}')

        client_map = {}
        created_clients = 0
        matched_clients = 0
        unmatched_clients = []

        for sp_c in relevant_sp_clients:
            sp_name = sp_c.get('name', '').strip()
            sp_id = sp_c.get('id')
            if not sp_name:
                continue

            existing = match_client(sp_name, django_clients_by_norm)
            if existing:
                client_map[sp_id] = existing
                matched_clients += 1
                self.stdout.write(f'    MATCH: {sp_name} -> {existing.name} (Django id={existing.pk})')
            elif not skip_clients:
                if dry_run:
                    self.stdout.write(f'    CREATE (dry): {sp_name}')
                    created_clients += 1
                else:
                    new_client = Client.objects.create(name=sp_name)
                    client_map[sp_id] = new_client
                    django_clients_by_norm[normalize_name(sp_name)] = new_client
                    created_clients += 1
                    self.stdout.write(f'    CREATE: {sp_name} -> Django id={new_client.pk}')
            else:
                unmatched_clients.append(sp_name)

        self.stdout.write(f'\n  Итого: совпадений {matched_clients}, создано {created_clients}')
        if unmatched_clients:
            self.stdout.write(f'  Без совпадения (пропущены): {len(unmatched_clients)}')
            for name in unmatched_clients:
                self.stdout.write(f'    - {name}')

        # ── 2. ПРОДАЖИ (PARDP — реальные инвойсы) ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Этап 2: Импорт реальных продаж (PARDP)'))

        real_sales = [s for s in sp_sales if s.get('isSale')]
        self.stdout.write(f'  Реальных продаж в site.pro: {len(real_sales)}')

        already_synced_ids = set()
        if not force:
            already_synced_ids = set(
                SiteProInvoiceSync.objects.filter(
                    connection=conn
                ).values_list('external_id', flat=True)
            )
            self.stdout.write(f'  Уже синхронизировано (пропуск): {len(already_synced_ids)}')

        imported = 0
        skipped = 0
        errors = []

        for sale in real_sales:
            sp_sale_id = str(sale.get('id', ''))
            sp_number = sale.get('number', '')
            sp_series = sale.get('series', '')
            sp_date = parse_date(sale.get('saleDate'))
            sp_pay_until = parse_date(sale.get('payUntil'))
            sp_amount = Decimal(str(sale.get('sumWithVat', 0) or 0))
            sp_vat = Decimal(str(sale.get('vat', 0) or 0))
            sp_subtotal = Decimal(str(sale.get('sumWithoutVat', 0) or 0))
            sp_client_id = sale.get('clientId')
            sp_client_name = sale.get('clientName', '')
            sp_currency = sale.get('currencyCode', 'EUR')
            sp_balance = Decimal(str(sale.get('currencyBalance', 0) or 0))
            sp_notes = sale.get('notes', '') or ''

            invoice_number = f'{sp_series}-{sp_number}'

            if sp_sale_id in already_synced_ids:
                skipped += 1
                continue

            django_client = client_map.get(sp_client_id)
            if not django_client and not dry_run:
                existing_match = match_client(sp_client_name, django_clients_by_norm)
                if existing_match:
                    django_client = existing_match
                    client_map[sp_client_id] = existing_match

            if not django_client and not dry_run and not skip_clients:
                django_client = Client.objects.create(name=sp_client_name)
                django_clients_by_norm[normalize_name(sp_client_name)] = django_client
                client_map[sp_client_id] = django_client

            paid_amount = max(sp_amount - sp_balance, Decimal('0'))

            if sp_balance <= 0:
                status = 'PAID'
            elif paid_amount > 0:
                status = 'PARTIALLY_PAID'
            else:
                status = 'ISSUED'

            if dry_run:
                self.stdout.write(
                    f'    IMPORT (dry): {invoice_number} {sp_date} '
                    f'{sp_client_name} {sp_amount} {sp_currency} '
                    f'(оплачено: {paid_amount}, статус: {status})'
                )
                imported += 1
                continue

            try:
                with db_transaction.atomic():
                    existing_invoice = NewInvoice.objects.filter(number=invoice_number).first()
                    if existing_invoice and not force:
                        skipped += 1
                        continue

                    if existing_invoice and force:
                        invoice = existing_invoice
                        invoice.date = sp_date
                        invoice.subtotal = sp_subtotal
                        invoice.tax = sp_vat
                        invoice.total = sp_amount
                        invoice.paid_amount = paid_amount
                        invoice.status = status
                        invoice.currency = sp_currency
                        invoice.notes = f'[site.pro import] {sp_notes}'.strip()
                        if sp_pay_until:
                            invoice.due_date = sp_pay_until
                        if django_client:
                            invoice.recipient_client = django_client
                        invoice.save()
                    else:
                        invoice_data = {
                            'number': invoice_number,
                            'date': sp_date,
                            'issuer_company': company,
                            'subtotal': sp_subtotal,
                            'tax': sp_vat,
                            'total': sp_amount,
                            'paid_amount': paid_amount,
                            'status': status,
                            'currency': sp_currency,
                            'notes': f'[site.pro import] {sp_notes}'.strip(),
                        }
                        if sp_pay_until:
                            invoice_data['due_date'] = sp_pay_until
                        if django_client:
                            invoice_data['recipient_client'] = django_client

                        invoice = NewInvoice(**invoice_data)
                        invoice._balance_updated = True
                        invoice.save()

                    SiteProInvoiceSync.objects.update_or_create(
                        connection=conn,
                        invoice=invoice,
                        defaults={
                            'external_id': sp_sale_id,
                            'external_number': f'{sp_series}-{sp_number}',
                            'sync_status': 'SENT',
                            'last_synced_at': timezone.now(),
                        },
                    )
                    imported += 1

            except Exception as e:
                errors.append(f'{invoice_number}: {str(e)[:200]}')

        self.stdout.write(f'\n  Итого: импортировано {imported}, пропущено {skipped}, ошибок {len(errors)}')
        for err in errors:
            self.stdout.write(self.style.ERROR(f'    {err}'))

        # ── 3. КОММЕРЧЕСКИЕ ПРЕДЛОЖЕНИЯ (AV) ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Этап 3: Коммерческие предложения (AV)'))
        av_sales = [s for s in sp_sales if not s.get('isSale') and s.get('series') == 'AV']
        self.stdout.write(f'  Коммерческих предложений: {len(av_sales)}')
        for s in av_sales:
            num = s.get('number', '?')
            d = s.get('saleDate', '?')
            client = s.get('clientName', '?')
            amt = s.get('sumWithVat', 0)
            self.stdout.write(f'    AV-{num} {d}  {client}  {amt} EUR')

        # ── 4. СВОДКА ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Сводка'))
        self.stdout.write(f'  Клиентов совпало: {matched_clients}')
        self.stdout.write(f'  Клиентов создано: {created_clients}')
        self.stdout.write(f'  Инвойсов импортировано: {imported}')
        self.stdout.write(f'  Инвойсов пропущено: {skipped}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  Ошибок: {len(errors)}'))

        if dry_run:
            self.stdout.write(self.style.WARNING('\n  Это был DRY RUN. Запустите без --dry-run для реального импорта.\n'))
        else:
            self.stdout.write(self.style.SUCCESS('\n  Импорт завершён.\n'))
