"""
Management command to check data integrity across the system.

Checks:
- CarService orphans (service_id pointing to non-existent catalog service)
- Balance consistency (stored balance vs. calculated from transactions)
- Invoice paid_amount consistency
- Invoice issuer/recipient integrity (exactly one set)

Usage:
    python manage.py check_data_integrity
    python manage.py check_data_integrity --fix
"""
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check data integrity: orphan services, balance consistency, invoice integrity'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix', action='store_true',
            help='Automatically fix found issues where possible',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        total_issues = 0

        total_issues += self._check_car_service_orphans(fix)
        total_issues += self._check_balance_consistency(fix)
        total_issues += self._check_invoice_paid_amounts(fix)
        total_issues += self._check_invoice_parties()

        if total_issues == 0:
            self.stdout.write(self.style.SUCCESS('All integrity checks passed.'))
        else:
            self.stdout.write(self.style.WARNING(f'Found {total_issues} issue(s).'))

    def _check_car_service_orphans(self, fix):
        from core.models import CarrierService, CarService, CompanyService, LineService, WarehouseService

        self.stdout.write('\n--- CarService orphan check ---')
        model_map = {
            'LINE': LineService,
            'CARRIER': CarrierService,
            'WAREHOUSE': WarehouseService,
            'COMPANY': CompanyService,
        }
        orphans = 0
        for stype, model_cls in model_map.items():
            valid_ids = set(model_cls.objects.values_list('id', flat=True))
            car_services = CarService.objects.filter(service_type=stype)
            for cs in car_services.iterator():
                if cs.service_id not in valid_ids:
                    orphans += 1
                    self.stdout.write(self.style.WARNING(
                        f'  Orphan: CarService id={cs.id} car={cs.car_id} '
                        f'type={stype} service_id={cs.service_id}'
                    ))
                    if fix:
                        cs.delete()
                        self.stdout.write(f'    -> Deleted')

        if orphans == 0:
            self.stdout.write(self.style.SUCCESS('  No orphan CarService records found.'))
        return orphans

    def _check_balance_consistency(self, fix):
        from core.models import Carrier, Client, Company, Line, Warehouse
        from core.models_billing import Transaction

        self.stdout.write('\n--- Balance consistency check ---')
        issues = 0
        for model in [Client, Warehouse, Line, Company, Carrier]:
            model_name = model.__name__.lower()
            for entity in model.objects.all():
                incoming = Transaction.objects.filter(
                    status='COMPLETED', **{f'to_{model_name}': entity}
                ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
                outgoing = Transaction.objects.filter(
                    status='COMPLETED', **{f'from_{model_name}': entity}
                ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
                expected = incoming - outgoing
                if entity.balance != expected:
                    issues += 1
                    self.stdout.write(self.style.WARNING(
                        f'  {model.__name__} "{entity}" id={entity.pk}: '
                        f'stored={entity.balance}, expected={expected} '
                        f'(diff={entity.balance - expected})'
                    ))
                    if fix:
                        entity.balance = expected
                        entity.save(update_fields=['balance', 'balance_updated_at'])
                        self.stdout.write(f'    -> Fixed to {expected}')

        if issues == 0:
            self.stdout.write(self.style.SUCCESS('  All balances are consistent.'))
        return issues

    def _check_invoice_paid_amounts(self, fix):
        from core.models_billing import NewInvoice

        self.stdout.write('\n--- Invoice paid_amount check ---')
        issues = 0
        invoices = NewInvoice.objects.exclude(status='CANCELLED')
        for inv in invoices.iterator():
            payments = inv.transactions.filter(
                type='PAYMENT', status='COMPLETED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            refunds = inv.transactions.filter(
                type='REFUND', status='COMPLETED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            expected = max(Decimal('0.00'), payments - refunds)
            if inv.paid_amount != expected:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f'  Invoice {inv.number}: stored paid_amount={inv.paid_amount}, '
                    f'expected={expected} (diff={inv.paid_amount - expected})'
                ))
                if fix:
                    inv.paid_amount = expected
                    inv.update_status()
                    inv.save(update_fields=['paid_amount', 'status', 'updated_at'])
                    self.stdout.write(f'    -> Fixed to {expected}, status={inv.status}')

        if issues == 0:
            self.stdout.write(self.style.SUCCESS('  All invoice paid_amounts are consistent.'))
        return issues

    def _check_invoice_parties(self):
        from core.models_billing import NewInvoice

        self.stdout.write('\n--- Invoice issuer/recipient check ---')
        issues = 0
        for inv in NewInvoice.objects.exclude(status='CANCELLED').iterator():
            issuers = sum(1 for f in [
                inv.issuer_company_id, inv.issuer_warehouse_id,
                inv.issuer_line_id, inv.issuer_carrier_id,
            ] if f)
            recipients = sum(1 for f in [
                inv.recipient_client_id, inv.recipient_warehouse_id,
                inv.recipient_line_id, inv.recipient_carrier_id,
                inv.recipient_company_id,
            ] if f)
            if issuers != 1:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f'  Invoice {inv.number}: has {issuers} issuers (expected 1)'
                ))
            if recipients != 1:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f'  Invoice {inv.number}: has {recipients} recipients (expected 1)'
                ))

        if issues == 0:
            self.stdout.write(self.style.SUCCESS('  All invoices have exactly one issuer and one recipient.'))
        return issues
