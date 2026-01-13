from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Car, Container


class Command(BaseCommand):
    help = "Recalculate paid days and storage cost for cars and containers"

    def add_arguments(self, parser):
        parser.add_argument('--only', choices=['cars', 'containers'], help='Limit recalculation scope')

    def handle(self, *args, **options):
        scope = options.get('only')
        with transaction.atomic():
            if scope in (None, 'cars'):
                for car in Car.objects.select_related('warehouse', 'container').all():
                    car.update_days_and_storage()
                    car.calculate_total_price()
                    car.save(update_fields=['days', 'storage_cost', 'current_price', 'total_price'])
            if scope in (None, 'containers'):
                for cont in Container.objects.all():
                    cont.update_days_and_storage()
                    cont.save(update_fields=['days', 'storage_cost'])
        self.stdout.write(self.style.SUCCESS('Recalculation completed'))


