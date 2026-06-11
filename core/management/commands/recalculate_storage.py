from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Car


class Command(BaseCommand):
    help = "Recalculate paid days and storage cost for active (non-transferred) cars"

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Include transferred cars too')
        parser.add_argument('--verbose', action='store_true', help='Show details per car')

    def handle(self, *args, **options):
        include_all = options.get('all', False)
        verbose = options.get('verbose', False)

        updated_cars = 0

        # NOTE: контейнеры больше не пересчитываются — у Container нет
        # собственных полей days/storage_cost, это read-only properties
        # (агрегат по машинам, см. Container.storage_cost / days и
        # OptimizedContainerManager.with_storage_aggregates).
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

        now = timezone.now().strftime('%Y-%m-%d %H:%M')
        self.stdout.write(self.style.SUCCESS(
            f"[{now}] Recalculation completed: {updated_cars} cars updated"
        ))
