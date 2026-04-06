"""
Авто-пропуск исходящих банковских транзакций, не связанных с клиентскими инвойсами.

Категории:
- Банковские комиссии (Revolut plan fees, Expenses app charges)
- Внутренние переводы между счетами (TARP SASKAITU)
- Социальные налоги и государственные платежи (Sodra, Registrų centras, PLAIS)
- Оплаты поставщикам логистики (Neto terminalas LTKLJP/LTKLJPX, FAAS, TTG, OTR)
- Топливо (Alauša, Orlen, Circle K, VIADA, Baltic Petroleum)
- Курьерские (DHL)
- Подписки (Cursor, EMSI, site.pro B1S)
- Комиссии Paysera

Использование:
    python manage.py skip_outgoing --dry-run
    python manage.py skip_outgoing
"""

import re
import sys

from django.core.management.base import BaseCommand

from core.models_banking import BankTransaction


SKIP_RULES = [
    # (category_label, matching_function)
    ('Комиссия Revolut', lambda bt: bool(re.search(
        r'Company (Pro|Free) plan fee|Expenses app charges', bt.description or '', re.IGNORECASE
    ))),
    ('Внутренний перевод', lambda bt: bool(re.search(
        r'TARP SASKAITU|tarp saskaitu', bt.description or '', re.IGNORECASE
    ))),
    ('Соц. налоги / госплатежи', lambda bt: bool(re.search(
        r'Soc\.\s*dr\.\s*[iį]mok|socialinio draudimo|Registr[uų] centr|PLAIS palaikymo|'
        r'[IĮ]mokos kodas',
        bt.description or '', re.IGNORECASE
    ))),
    ('Терминал Клайпеда (Neto)', lambda bt: bool(re.search(
        r'LTKLJP[X]?\d', bt.description or ''
    ))),
    ('FAAS (контейнерный терминал)', lambda bt: bool(re.search(
        r'FAAS\s+Nr\.', bt.description or '', re.IGNORECASE
    ))),
    ('TTG (логистика)', lambda bt: bool(re.search(
        r'TTG\s*-\s*L\d', bt.description or ''
    ))),
    ('OTR (логистика)', lambda bt: bool(re.search(
        r'\d{2}OTR-\d', bt.description or ''
    ))),
    ('Терминал TERM', lambda bt: bool(re.search(
        r'^TERM\d{4}$', (bt.description or '').strip()
    ))),
    ('Автовозы ATC', lambda bt: bool(re.search(
        r'^ATC\d{5,6}$', (bt.description or '').strip()
    ))),
    ('Автовозы AVL', lambda bt: bool(re.search(
        r'^AVL-\d{7}$', (bt.description or '').strip()
    ))),
    ('Топливо', lambda bt: bool(re.search(
        r'Alau[sš]a|Orlen|Circle K|VIADA|Baltic Petroleum|Degalin[eė]',
        (bt.counterparty_name or '') + ' ' + (bt.description or ''), re.IGNORECASE
    ))),
    ('DHL', lambda bt: 'DHL' in (bt.counterparty_name or '').upper()),
    ('Подписка Cursor', lambda bt: (bt.counterparty_name or '').strip().lower() == 'cursor'),
    ('Подписка EMSI', lambda bt: (bt.counterparty_name or '').strip().upper() == 'EMSI'),
    ('Подписка site.pro', lambda bt: bool(re.search(
        r'^B1S\d{7}$', (bt.description or '').strip()
    ))),
    ('Бухгалтерия / поставщики (номерные инвойсы)', lambda bt: bool(re.search(
        r'^\d{2}/\d{4,5}$', (bt.description or '').strip()
    ))),
    ('IMP INV (Maersk / импорт)', lambda bt: bool(re.search(
        r'IMP INV \d', bt.description or ''
    ))),
    ('MAI (поставщик)', lambda bt: bool(re.search(
        r'^MAI M\d{6}$', (bt.description or '').strip()
    ))),
    ('Комиссия Paysera', lambda bt: (
        'paysera' in (bt.counterparty_name or '').lower()
    )),
    ('INVOICE поставщику', lambda bt: bool(re.search(
        r'^INVOICE\s+\d+/\d+TR$', (bt.description or '').strip()
    ))),
    ('INVOICE - L (поставщик)', lambda bt: bool(re.search(
        r'^INVOICE\s*-\s*L\d', (bt.description or '').strip()
    ))),
    ('WE (складские)', lambda bt: bool(re.search(
        r'^WE\s+\d{2}-\d{4}$', (bt.description or '').strip()
    ))),
]


class Command(BaseCommand):
    help = 'Авто-пропуск исходящих транзакций, не связанных с клиентскими инвойсами'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('  === DRY RUN ===\n'))

        outgoing = BankTransaction.objects.filter(
            amount__lt=0,
            matched_invoice__isnull=True,
            matched_transaction__isnull=True,
            reconciliation_skipped=False,
        ).select_related('connection')

        total_count = outgoing.count()
        self.stdout.write(f'  Несопоставленных исходящих: {total_count}\n')

        category_counts = {}
        skipped_ids = []
        unmatched = []

        for bt in outgoing.order_by('-created_at'):
            matched_category = None
            for cat_label, matcher in SKIP_RULES:
                if matcher(bt):
                    matched_category = cat_label
                    break

            if matched_category:
                category_counts[matched_category] = category_counts.get(matched_category, 0) + 1
                skipped_ids.append((bt.pk, matched_category))
                cp = (bt.counterparty_name or '')[:25]
                desc = (bt.description or '')[:50]
                self.stdout.write(
                    f'    [{matched_category:30}] '
                    f'{bt.created_at.strftime("%Y-%m-%d")} {bt.amount:>10} EUR  '
                    f'{cp:25} | {desc}'
                )
            else:
                unmatched.append(bt)

        self.stdout.write(self.style.MIGRATE_HEADING('\n  Категории'))
        for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
            self.stdout.write(f'    {cat:40} {cnt:>3}')

        self.stdout.write(f'\n  Итого к пропуску: {len(skipped_ids)} из {total_count}')

        if unmatched:
            self.stdout.write(self.style.WARNING(f'\n  Без категории ({len(unmatched)}):'))
            for bt in unmatched:
                cp = (bt.counterparty_name or '')[:30]
                desc = (bt.description or '')[:60]
                self.stdout.write(
                    f'    {bt.created_at.strftime("%Y-%m-%d")} {bt.amount:>10} EUR  '
                    f'{cp:30} | {desc}'
                )

        if dry_run:
            self.stdout.write(self.style.WARNING('\n  DRY RUN. Ничего не сохранено.\n'))
            return

        updated = 0
        for pk, category in skipped_ids:
            BankTransaction.objects.filter(pk=pk).update(
                reconciliation_skipped=True,
                reconciliation_note=f'Авто-пропуск: {category}',
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f'\n  Помечено как не требующие привязки: {updated}\n'))
