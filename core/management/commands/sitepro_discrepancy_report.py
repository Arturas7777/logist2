"""
Отчёт расхождений между данными Django, site.pro и Revolut.

Сравнивает:
1. Инвойсы Django <-> Продажи site.pro (суммы, статусы оплаты)
2. Банковские транзакции Revolut <-> site.pro (входящие платежи)
3. Клиенты site.pro без совпадений в Django

Использование:
    python manage.py sitepro_discrepancy_report
"""

import sys
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Client
from core.models_billing import NewInvoice
from core.models_accounting import SiteProConnection, SiteProInvoiceSync
from core.models_banking import BankTransaction
from core.services.sitepro_service import SiteProService


class Command(BaseCommand):
    help = 'Отчёт расхождений между Django, site.pro и Revolut'

    def handle(self, *args, **options):
        sys.stdout.reconfigure(encoding='utf-8')

        conn = SiteProConnection.objects.filter(is_active=True).first()
        if not conn:
            self.stderr.write(self.style.ERROR('Нет активного подключения SiteProConnection'))
            return

        svc = SiteProService(conn)

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n  ═══════════════════════════════════════════════════════'
            '\n  ОТЧЁТ РАСХОЖДЕНИЙ: Django / site.pro / Revolut'
            '\n  ═══════════════════════════════════════════════════════'
        ))

        # ── 1. ИНВОЙСЫ: Django vs site.pro ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  1. Инвойсы Django vs Продажи site.pro'))

        sp_sales = svc.list_all_sales()
        sp_real_sales = {str(s['id']): s for s in sp_sales if s.get('isSale')}

        syncs = SiteProInvoiceSync.objects.filter(connection=conn).select_related('invoice')
        synced_map = {s.external_id: s for s in syncs}

        django_invoices = {inv.number: inv for inv in NewInvoice.objects.all()}

        amount_mismatches = []
        status_mismatches = []
        only_in_sitepro = []
        only_in_django = []

        for sp_id, sp_sale in sp_real_sales.items():
            sync = synced_map.get(sp_id)
            if not sync:
                only_in_sitepro.append(sp_sale)
                continue

            inv = sync.invoice
            sp_amount = Decimal(str(sp_sale.get('sumWithVat', 0) or 0))
            sp_balance = Decimal(str(sp_sale.get('currencyBalance', 0) or 0))
            sp_paid = sp_amount - sp_balance

            if abs(inv.total - sp_amount) > Decimal('0.01'):
                amount_mismatches.append({
                    'number': inv.number,
                    'django_total': inv.total,
                    'sitepro_total': sp_amount,
                    'diff': inv.total - sp_amount,
                })

            if abs(inv.paid_amount - sp_paid) > Decimal('0.01'):
                status_mismatches.append({
                    'number': inv.number,
                    'django_paid': inv.paid_amount,
                    'sitepro_paid': sp_paid,
                    'django_status': inv.get_status_display(),
                    'sitepro_balance': sp_balance,
                })

        for number, inv in django_invoices.items():
            if number.startswith('PARDP-') or number.startswith('INV-'):
                if not SiteProInvoiceSync.objects.filter(invoice=inv, connection=conn).exists():
                    if number.startswith('PARDP-'):
                        only_in_django.append(inv)

        self.stdout.write(f'  Продаж в site.pro (реальных): {len(sp_real_sales)}')
        self.stdout.write(f'  Синхронизированных: {len(synced_map)}')
        self.stdout.write(f'  Инвойсов в Django: {len(django_invoices)}')

        if amount_mismatches:
            self.stdout.write(self.style.WARNING(f'\n  Расхождения в суммах: {len(amount_mismatches)}'))
            for m in amount_mismatches:
                self.stdout.write(
                    f'    {m["number"]}: Django={m["django_total"]}, '
                    f'site.pro={m["sitepro_total"]}, разница={m["diff"]}'
                )
        else:
            self.stdout.write(self.style.SUCCESS('  Расхождений в суммах: 0'))

        if status_mismatches:
            self.stdout.write(self.style.WARNING(f'\n  Расхождения в оплате: {len(status_mismatches)}'))
            for m in status_mismatches:
                self.stdout.write(
                    f'    {m["number"]}: Django оплачено={m["django_paid"]} ({m["django_status"]}), '
                    f'site.pro оплачено={m["sitepro_paid"]} (баланс: {m["sitepro_balance"]})'
                )
        else:
            self.stdout.write(self.style.SUCCESS('  Расхождений в оплате: 0'))

        if only_in_sitepro:
            self.stdout.write(self.style.WARNING(f'\n  Только в site.pro (нет в Django): {len(only_in_sitepro)}'))
            for s in only_in_sitepro[:20]:
                self.stdout.write(
                    f'    [{s["id"]}] {s.get("series","")}-{s.get("number","")} '
                    f'{s.get("saleDate","")} {s.get("clientName","")} {s.get("sumWithVat",0)} EUR'
                )

        if only_in_django:
            self.stdout.write(self.style.WARNING(f'\n  Только в Django (нет в site.pro): {len(only_in_django)}'))
            for inv in only_in_django[:20]:
                self.stdout.write(f'    {inv.number} {inv.date} {inv.total} EUR ({inv.get_status_display()})')

        # ── 2. БАНКОВСКИЕ ТРАНЗАКЦИИ: Revolut vs site.pro ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  2. Банковские транзакции: Revolut vs site.pro'))

        revolut_txns = BankTransaction.objects.all().order_by('created_at')
        sp_bank_txns = svc.list_bank_transactions()

        self.stdout.write(f'  Revolut транзакций в Django: {revolut_txns.count()}')
        self.stdout.write(f'  Банковских транзакций в site.pro: {len(sp_bank_txns)}')

        revolut_incoming = revolut_txns.filter(amount__gt=0)
        revolut_outgoing = revolut_txns.filter(amount__lt=0)
        revolut_in_sum = sum(t.amount for t in revolut_incoming)
        revolut_out_sum = sum(abs(t.amount) for t in revolut_outgoing)

        sp_debit = [t for t in sp_bank_txns if t.get('creditDebitIndicator') == 'D']
        sp_credit = [t for t in sp_bank_txns if t.get('creditDebitIndicator') == 'K']
        sp_d_sum = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sp_debit)
        sp_k_sum = sum(Decimal(str(t.get('amount', 0) or 0)) for t in sp_credit)

        self.stdout.write(f'\n  Revolut (Django):')
        self.stdout.write(f'    Входящих: {revolut_incoming.count()}, сумма: {revolut_in_sum:.2f} EUR')
        self.stdout.write(f'    Исходящих: {revolut_outgoing.count()}, сумма: {revolut_out_sum:.2f} EUR')
        self.stdout.write(f'\n  site.pro:')
        self.stdout.write(f'    Дебет (D): {len(sp_debit)}, сумма: {sp_d_sum} EUR')
        self.stdout.write(f'    Кредит (K): {len(sp_credit)}, сумма: {sp_k_sum} EUR')

        diff_in = revolut_in_sum - sp_d_sum
        diff_out = revolut_out_sum - sp_k_sum
        if abs(diff_in) > 1:
            self.stdout.write(self.style.WARNING(f'\n  Расхождение входящих: {diff_in:.2f} EUR'))
        if abs(diff_out) > 1:
            self.stdout.write(self.style.WARNING(f'  Расхождение исходящих: {diff_out:.2f} EUR'))

        # ── 3. НЕОПЛАЧЕННЫЕ ИНВОЙСЫ ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n  3. Неоплаченные инвойсы'))

        unpaid = NewInvoice.objects.filter(
            status__in=['ISSUED', 'PARTIALLY_PAID', 'OVERDUE']
        ).order_by('date')

        total_unpaid = Decimal('0')
        self.stdout.write(f'  Неоплаченных инвойсов: {unpaid.count()}')
        for inv in unpaid:
            remaining = inv.total - inv.paid_amount
            total_unpaid += remaining
            recipient = inv.recipient_client or inv.recipient
            self.stdout.write(
                f'    {inv.number} {inv.date} {recipient} — '
                f'итого: {inv.total}, оплачено: {inv.paid_amount}, '
                f'остаток: {remaining} EUR ({inv.get_status_display()})'
            )

        self.stdout.write(self.style.WARNING(f'  ИТОГО к получению: {total_unpaid} EUR'))

        # ── 4. СВОДКА ──
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n  ═══════════════════════════════════════════════════════'
            '\n  ИТОГ'
            '\n  ═══════════════════════════════════════════════════════'
        ))
        issues = len(amount_mismatches) + len(status_mismatches) + len(only_in_sitepro) + len(only_in_django)
        if issues:
            self.stdout.write(self.style.WARNING(f'  Обнаружено расхождений: {issues}'))
        else:
            self.stdout.write(self.style.SUCCESS('  Расхождений не обнаружено!'))
        self.stdout.write('')
