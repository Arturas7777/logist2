from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Car, Container


class Command(BaseCommand):
    help = "Recalculate paid days and storage cost for active (non-transferred) cars and containers"

    def add_arguments(self, parser):
        parser.add_argument('--only', choices=['cars', 'containers'], help='Limit recalculation scope')
        parser.add_argument('--all', action='store_true', help='Include transferred cars/containers too')
        parser.add_argument('--verbose', action='store_true', help='Show details per car')

    def handle(self, *args, **options):
        scope = options.get('only')
        include_all = options.get('all', False)
        verbose = options.get('verbose', False)

        updated_cars = 0
        updated_containers = 0

        if scope in (None, 'cars'):
            cars_qs = Car.objects.select_related('warehouse', 'container')
            if not include_all:
                cars_qs = cars_qs.exclude(status='TRANSFERRED')

            for car in cars_qs.iterator():
                old_days = car.days
                old_storage = car.storage_cost

                car.update_days_and_storage()
                car.calculate_total_price()

                if car.days != old_days or car.storage_cost != old_storage:
                    Car.objects.filter(pk=car.pk).update(
                        days=car.days,
                        storage_cost=car.storage_cost,
                        total_price=car.total_price,
                    )
                    updated_cars += 1
                    if verbose:
                        self.stdout.write(
                            f"  {car.vin}: days {old_days} -> {car.days}, "
                            f"storage {old_storage} -> {car.storage_cost}"
                        )

            self.stdout.write(f"Cars: {updated_cars} updated")

        if scope in (None, 'containers'):
            cont_qs = Container.objects.all()
            if not include_all:
                cont_qs = cont_qs.exclude(status='TRANSFERRED')

            for cont in cont_qs.iterator():
                old_days = cont.days
                old_storage = cont.storage_cost

                cont.update_days_and_storage()

                if cont.days != old_days or cont.storage_cost != old_storage:
                    Container.objects.filter(pk=cont.pk).update(
                        days=cont.days,
                        storage_cost=cont.storage_cost,
                    )
                    updated_containers += 1
                    if verbose:
                        self.stdout.write(
                            f"  {cont.number}: days {old_days} -> {cont.days}, "
                            f"storage {old_storage} -> {cont.storage_cost}"
                        )

            self.stdout.write(f"Containers: {updated_containers} updated")

        now = timezone.now().strftime('%Y-%m-%d %H:%M')
        self.stdout.write(self.style.SUCCESS(
            f"[{now}] Recalculation completed: {updated_cars} cars, {updated_containers} containers updated"
        ))
