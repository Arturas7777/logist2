"""
Разведка данных site.pro: выгружает клиентов, продажи (инвойсы),
позиции и платежи — печатает сводку на экран.

Использование:
    python manage.py pull_sitepro_data
    python manage.py pull_sitepro_data --from 2024-01-01 --to 2026-04-06
    python manage.py pull_sitepro_data --json          # вывести сырой JSON
"""

import json
import sys
from collections import Counter
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models_accounting import SiteProConnection
from core.services.sitepro_service import SiteProService


class Command(BaseCommand):
    help = 'Разведка данных в site.pro — клиенты, продажи, позиции'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from', dest='date_from', type=str, default=None,
            help='Дата начала (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--to', dest='date_to', type=str, default=None,
            help='Дата конца (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--json', dest='dump_json', action='store_true',
            help='Вывести сырой JSON вместо сводки',
        )

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')

        conn = SiteProConnection.objects.filter(is_active=True).first()
        if not conn:
            self.stderr.write(self.style.ERROR('Нет активного подключения SiteProConnection'))
            return

        svc = SiteProService(conn)

        date_from = options['date_from']
        date_to = options['date_to']
        dump_json = options['dump_json']

        # ── Клиенты ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  1. Клиенты'))
        clients = svc.list_all_clients()
        self.stdout.write(f'  Всего клиентов: {len(clients)}')
        if dump_json:
            self.stdout.write(json.dumps(clients, ensure_ascii=False, indent=2, default=str))
        else:
            juridical = [c for c in clients if c.get('isJuridical')]
            physical = [c for c in clients if not c.get('isJuridical')]
            self.stdout.write(f'  Юридических лиц: {len(juridical)}, физических: {len(physical)}')
            for c in clients[:30]:
                name = c.get('name', '?')
                cid = c.get('id', '?')
                code = c.get('code', '')
                country = c.get('correspondenceCountry', '')
                jur = 'юр.' if c.get('isJuridical') else 'физ.'
                self.stdout.write(f'    [{cid}] {name}  ({jur}, код: {code}, {country})')
            if len(clients) > 30:
                self.stdout.write(f'    ... и ещё {len(clients) - 30}')

        # ── Продажи ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  2. Продажи (инвойсы)'))
        sales = svc.list_all_sales(date_from=date_from, date_to=date_to)
        self.stdout.write(f'  Всего продаж: {len(sales)}')

        if dump_json:
            self.stdout.write(json.dumps(sales, ensure_ascii=False, indent=2, default=str))
        else:
            total_sum = Decimal('0')
            real_sales_sum = Decimal('0')
            status_counter = Counter()
            currency_counter = Counter()
            year_counter = Counter()
            op_type_counter = Counter()
            series_counter = Counter()
            client_totals = Counter()
            real_sales_count = 0

            for s in sales:
                amount = Decimal(str(s.get('sumWithVat', 0) or 0))
                total_sum += amount
                is_sale = s.get('isSale', False)
                if is_sale:
                    real_sales_count += 1
                    real_sales_sum += amount
                status_counter[s.get('statusName') or 'без статуса'] += 1
                currency_counter[s.get('currencyCode', '?')] += 1
                sale_date = s.get('saleDate', '')
                if sale_date and len(sale_date) >= 4:
                    year_counter[sale_date[:4]] += 1
                op_type_counter[s.get('operationTypeName', '?')] += 1
                series_counter[s.get('series', '?')] += 1
                client_name = s.get('clientName', '?')
                if is_sale:
                    client_totals[client_name] += amount

            self.stdout.write(f'\n  Общая сумма (с НДС): {total_sum} EUR')
            self.stdout.write(f'  Реальных продаж (isSale=true): {real_sales_count}, на сумму: {real_sales_sum} EUR')
            self.stdout.write(f'\n  По статусам:     {dict(status_counter)}')
            self.stdout.write(f'  По валютам:      {dict(currency_counter)}')
            self.stdout.write(f'  По годам:        {dict(sorted(year_counter.items()))}')
            self.stdout.write(f'  По типам операций: {dict(op_type_counter)}')
            self.stdout.write(f'  По сериям:       {dict(series_counter)}')

            self.stdout.write(f'\n  Топ-10 клиентов по сумме реальных продаж:')
            for name, total in client_totals.most_common(10):
                self.stdout.write(f'    {name}: {total} EUR')

            # Неоплаченные (currencyBalance > 0)
            unpaid = [s for s in sales if s.get('isSale') and (s.get('currencyBalance') or 0) > 0]
            unpaid_total = sum(Decimal(str(s.get('currencyBalance', 0) or 0)) for s in unpaid)
            self.stdout.write(f'\n  Неоплаченных реальных продаж: {len(unpaid)}, баланс: {unpaid_total} EUR')

            self.stdout.write(f'\n  Последние 20 продаж:')
            for s in sales[:20]:
                sid = s.get('id', '?')
                series = s.get('series', '')
                num = s.get('number', '?')
                d = s.get('saleDate', '?')
                client = s.get('clientName', '?')
                amt = s.get('sumWithVat', 0)
                balance = s.get('currencyBalance')
                op_type = s.get('operationTypeName', '?')
                is_sale = '✓' if s.get('isSale') else '○'
                bal_str = f'  долг: {balance}' if balance else ''
                self.stdout.write(
                    f'    {is_sale} [{sid}] {series}-{num} {d}  {client}  '
                    f'{amt} EUR  ({op_type}){bal_str}'
                )

        # ── Позиции (для первых 5 продаж, для ознакомления со структурой) ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  3. Пример позиций (первые 5 продаж)'))
        sample_sales = sales[:5]
        for s in sample_sales:
            sid = s.get('id')
            series = s.get('series', '')
            num = s.get('number', '?')
            if not sid:
                continue
            items = svc.list_sale_items(sid)
            self.stdout.write(f'\n  Продажа [{sid}] {series}-{num}: {len(items)} позиций')
            if dump_json:
                self.stdout.write(json.dumps(items, ensure_ascii=False, indent=2, default=str))
            else:
                for item in items[:10]:
                    iname = item.get('itemName', '?')
                    qty = item.get('quantity', '?')
                    price = item.get('priceWithoutVat', '?')
                    total = item.get('sumWithVat', '?')
                    addition = item.get('addition', '')
                    add_str = f'  [{addition}]' if addition else ''
                    self.stdout.write(
                        f'    - {iname}  кол-во: {qty}  цена: {price}  итого: {total}{add_str}'
                    )

        # ── Банковские транзакции ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  4. Банковские транзакции'))
        bank_txns = svc.list_bank_transactions()
        self.stdout.write(f'  Всего банковских транзакций: {len(bank_txns)}')

        if dump_json:
            self.stdout.write(json.dumps(bank_txns[:10], ensure_ascii=False, indent=2, default=str))
        else:
            incoming = [t for t in bank_txns if t.get('creditDebitIndicator') == 'D']
            outgoing = [t for t in bank_txns if t.get('creditDebitIndicator') == 'K']
            incoming_sum = sum(Decimal(str(t.get('amount', 0) or 0)) for t in incoming)
            outgoing_sum = sum(Decimal(str(t.get('amount', 0) or 0)) for t in outgoing)

            bank_names = Counter(t.get('accountingBankInternalName', '?') for t in bank_txns)
            year_txn = Counter()
            for t in bank_txns:
                d = t.get('date', '')
                if d and len(d) >= 4:
                    year_txn[d[:4]] += 1

            self.stdout.write(f'  Дебет (D, входящие платежи): {len(incoming)}, сумма: {incoming_sum} EUR')
            self.stdout.write(f'  Кредит (K, исходящие платежи): {len(outgoing)}, сумма: {outgoing_sum} EUR')
            self.stdout.write(f'  По банкам:  {dict(bank_names)}')
            self.stdout.write(f'  По годам:   {dict(sorted(year_txn.items()))}')

            self.stdout.write(f'\n  Последние 15 транзакций:')
            for t in bank_txns[:15]:
                d = t.get('date', '?')
                client = t.get('clientName', '?')
                amount = t.get('amount', 0)
                cd = t.get('creditDebitIndicator', '?')
                details = (t.get('details', '') or '')[:80]
                bank = t.get('accountingBankInternalName', '?')
                arrow = '+' if cd == 'D' else '-'
                self.stdout.write(f'    {d} {arrow}{amount:>10} EUR  {client:30}  {bank}')
                if details:
                    self.stdout.write(f'          {details}')

        self.stdout.write(self.style.SUCCESS('\n  Разведка завершена.\n'))
