"""
Сверка бухгалтерского баланса Caromoto Lithuania с фактическими
балансами банковских счетов и объяснение разницы.

Отвечает на вопрос менеджера: «Почему наш баланс по бухгалтерии
и баланс на банковском счету не одинаковы?»

Использование:
    python manage.py bookkeep_vs_bank
    python manage.py bookkeep_vs_bank --json  # машинно-читаемый вывод
    python manage.py bookkeep_vs_bank --currency EUR  # только EUR-счета
"""

import json
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Q, Sum


def _q(value: Any) -> Decimal:
    """Аккуратно приводит число к Decimal с 2 знаками."""
    if value is None:
        return Decimal('0.00')
    return Decimal(str(value)).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = (
        "Объясняет разницу между Caromoto.balance (бухгалтерская касса) "
        "и фактической суммой денег на банковских счетах."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--json', action='store_true', dest='as_json',
            help='Машинно-читаемый JSON-вывод.',
        )
        parser.add_argument(
            '--currency', default='EUR',
            help='Валюта для сверки банковских счетов (default: EUR).',
        )

    def handle(self, *args, **options):
        from core.models import Carrier, Company, Line, Warehouse
        from core.models_banking import BankAccount, BankTransaction
        from core.models_billing import NewInvoice

        currency = options['currency'].upper()
        as_json = options['as_json']

        caromoto = Company.get_default()
        if not caromoto:
            self.stderr.write(self.style.ERROR(
                "Не найдена компания по умолчанию (settings.COMPANY_NAME)."
            ))
            return

        # 1. Бухгалтерский баланс (касса в Django).
        bookkeep_balance = _q(caromoto.balance)

        # 2. Сумма по банковским счетам в указанной валюте.
        bank_accounts = list(
            BankAccount.objects.filter(
                connection__is_active=True,
                currency=currency,
            ).select_related('connection')
        )
        bank_total = sum((_q(a.balance) for a in bank_accounts), Decimal('0.00'))

        # 3. Залоги контрагентам — наши деньги, лежащие у складов / линий /
        #    перевозчиков / других компаний (balance > 0 у них = они нам должны
        #    или мы у них держим депозит).
        warehouses_deposits = _q(
            Warehouse.objects.aggregate(s=Sum('balance'))['s']
        )
        lines_deposits = _q(Line.objects.aggregate(s=Sum('balance'))['s'])
        carriers_deposits = _q(Carrier.objects.aggregate(s=Sum('balance'))['s'])
        # Балансы других компаний (не наша) — внешние депозиты / задолженности.
        other_companies_deposits = _q(
            Company.objects.exclude(pk=caromoto.pk)
            .aggregate(s=Sum('balance'))['s']
        )
        counterparty_deposits = (
            warehouses_deposits + lines_deposits +
            carriers_deposits + other_companies_deposits
        )

        # 4. Открытые FACT (мы должны контрагентам) — деньги уйдут с банка,
        #    но в bookkeep уже учтены косвенно.
        from core.models_billing import NewInvoice
        # direction вычисляется в Python — фильтруем по issuer-полям.
        fact_qs = NewInvoice.objects.filter(
            document_type='INVOICE_FACT',
            status__in=['ISSUED', 'OVERDUE', 'PARTIALLY_PAID'],
        )
        open_fact_total = Decimal('0.00')
        for inv in fact_qs.only('id', 'total', 'paid_amount'):
            open_fact_total += _q(inv.total) - _q(inv.paid_amount)

        # 5. Открытые PARDP — клиенты ещё не заплатили; в банке этих денег нет,
        #    в bookkeep тоже нет (PARDP даёт +balance Caromoto только при оплате).
        pardp_qs = NewInvoice.objects.filter(
            document_type='INVOICE',
            status__in=['ISSUED', 'OVERDUE', 'PARTIALLY_PAID'],
        )
        open_pardp_total = Decimal('0.00')
        for inv in pardp_qs.only('id', 'total', 'paid_amount'):
            open_pardp_total += _q(inv.total) - _q(inv.paid_amount)

        # 6. Платёж-в-пути: банковские транзакции в нужной валюте, ещё не
        #    сопоставленные ни с инвойсом, ни помеченные «не требует».
        in_flight_in = _q(
            BankTransaction.objects.filter(
                currency=currency,
                amount__gt=0,
                matched_transaction__isnull=True,
                reconciliation_skipped=False,
            ).aggregate(s=Sum('amount'))['s']
        )
        in_flight_out = _q(
            BankTransaction.objects.filter(
                currency=currency,
                amount__lt=0,
                matched_transaction__isnull=True,
                reconciliation_skipped=False,
            ).aggregate(s=Sum('amount'))['s']
        )

        diff = bookkeep_balance - bank_total

        data = {
            'company': caromoto.name,
            'currency': currency,
            'bookkeep_balance': str(bookkeep_balance),
            'bank_total': str(bank_total),
            'difference_bookkeep_minus_bank': str(diff),
            'breakdown': {
                'counterparty_deposits_total': str(counterparty_deposits),
                'counterparty_deposits_warehouses': str(warehouses_deposits),
                'counterparty_deposits_lines': str(lines_deposits),
                'counterparty_deposits_carriers': str(carriers_deposits),
                'counterparty_deposits_other_companies': str(other_companies_deposits),
                'open_fact_we_owe': str(open_fact_total),
                'open_pardp_clients_owe_us': str(open_pardp_total),
                'in_flight_incoming_unmatched': str(in_flight_in),
                'in_flight_outgoing_unmatched': str(in_flight_out),
            },
            'bank_accounts': [
                {
                    'name': a.name,
                    'connection': a.connection.name,
                    'currency': a.currency,
                    'balance': str(_q(a.balance)),
                }
                for a in bank_accounts
            ],
        }

        if as_json:
            self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
            return

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Сверка бухгалтерии и банков для «{caromoto.name}» ({currency})"
        ))
        self.stdout.write('')
        self.stdout.write(f"  Бухгалтерский баланс (Caromoto.balance): {bookkeep_balance:>12} {currency}")
        self.stdout.write(f"  Сумма по банковским счетам ({len(bank_accounts)} шт.):     {bank_total:>12} {currency}")
        self.stdout.write(self.style.WARNING(
            f"  Разница (бух - банк):                    {diff:>12} {currency}"
        ))
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING("Объяснение разницы:"))
        self.stdout.write(
            f"  Залоги контрагентам (наши деньги у них): {counterparty_deposits:>12}\n"
            f"    ├ склады:     {warehouses_deposits:>10}\n"
            f"    ├ линии:      {lines_deposits:>10}\n"
            f"    ├ перевозчики:{carriers_deposits:>10}\n"
            f"    └ др. компании:{other_companies_deposits:>10}"
        )
        self.stdout.write(
            f"  Открытые FACT (мы должны):               {open_fact_total:>12}"
        )
        self.stdout.write(
            f"  Открытые PARDP (нам должны клиенты):     {open_pardp_total:>12}"
        )
        self.stdout.write(
            f"  Несопоставленные входящие в банке:       {in_flight_in:>12}"
        )
        self.stdout.write(
            f"  Несопоставленные исходящие в банке:      {in_flight_out:>12}"
        )
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING("Банковские счета:"))
        for a in bank_accounts:
            self.stdout.write(
                f"  • {a.connection.name:25} {a.name:30} {_q(a.balance):>10} {a.currency}"
            )
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            "Подсказка: расхождение ~= "
            "(счёт = бух + залоги - открытые FACT + неучтённые входящие - неучтённые исходящие)"
        ))
