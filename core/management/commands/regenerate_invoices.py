"""
Regenerate all invoices in simplified format.

New format (06.02.2026):
- All services (except storage) -> one line: "Brand, VIN (THS, Unloading, ...)"
- Storage -> separate line: "Brand, VIN (Storage XX days)"
- Car total shown in storage line description

Usage:
    python manage.py regenerate_invoices          # all invoices with cars
    python manage.py regenerate_invoices --dry-run # show what will be regenerated
"""

import logging

from django.core.management.base import BaseCommand
from core.models_billing import NewInvoice


class Command(BaseCommand):
    help = 'Regenerate all invoices in simplified format'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what will be regenerated without making changes',
        )

    def handle(self, *args, **options):
        # Suppress SQL debug logging during regeneration
        logging.getLogger('django.db.backends').setLevel(logging.WARNING)
        logging.getLogger('core').setLevel(logging.WARNING)
        
        dry_run = options['dry_run']
        
        invoices = NewInvoice.objects.filter(cars__isnull=False).distinct()
        total = invoices.count()
        
        if total == 0:
            self.stdout.write('No invoices with cars found')
            return
        
        mode = "DRY RUN" if dry_run else "REGENERATE"
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  {mode}: {total} invoices")
        self.stdout.write(f"{'='*60}\n")
        
        success = 0
        errors = 0
        
        for i, invoice in enumerate(invoices, 1):
            cars_count = invoice.cars.count()
            old_total = invoice.total
            
            if dry_run:
                self.stdout.write(f"  [{i}/{total}] {invoice.number} -- {cars_count} cars, total: {old_total}")
            else:
                try:
                    invoice.regenerate_items_from_cars()
                    new_total = invoice.total
                    
                    if old_total != new_total:
                        self.stdout.write(
                            f"  [{i}/{total}] {invoice.number} -- OK ({cars_count} cars, "
                            f"total: {old_total} -> {new_total})"
                        )
                    else:
                        self.stdout.write(
                            f"  [{i}/{total}] {invoice.number} -- OK ({cars_count} cars, total: {new_total})"
                        )
                    success += 1
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  [{i}/{total}] {invoice.number} -- ERROR: {e}")
                    )
                    errors += 1
        
        self.stdout.write(f"\n{'='*60}")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"  DRY RUN complete. Run without --dry-run to apply."))
        else:
            self.stdout.write(self.style.SUCCESS(f"  Done! Success: {success}, errors: {errors}"))
        self.stdout.write(f"{'='*60}\n")
