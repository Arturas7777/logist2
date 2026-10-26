"""Приводит марку и модель авто к расшифровке VIN (NHTSA), без языковой модели.

По умолчанию — активные авто: в пути, в порту, разгруженные. Текст, с которым
машину прислали, сохраняется в «Исходное описание», пометка «(Hazzard)» —
в галочку. Для VIN без верной контрольной цифры (корейский внутренний рынок,
Европа) марка остаётся как была, только без пометки.
"""

from django.core.management.base import BaseCommand

from core.models import Car
from core.services.vin_gate import normalize_car_identity

ACTIVE_STATUSES = ("FLOATING", "IN_PORT", "UNLOADED")


class Command(BaseCommand):
    help = "Привести марку и модель авто к расшифровке VIN (NHTSA)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Включая переданные авто")
        parser.add_argument("--dry-run", action="store_true", help="Только показать изменения")
        parser.add_argument("--offline", action="store_true", help="Не ходить в NHTSA, только кэш проверок")

    def handle(self, *args, **options):
        cars = Car.objects.all() if options["all"] else Car.objects.filter(status__in=ACTIVE_STATUSES)
        dry_run = options["dry_run"]
        updated = 0
        for car in cars.order_by("pk").iterator():
            old_brand = car.brand
            fields = normalize_car_identity(car, allow_network=not options["offline"])
            if not fields:
                continue
            updated += 1
            hazard = " [Hazzard]" if "is_hazardous" in fields else ""
            self.stdout.write(f"{car.vin}: {old_brand} -> {car.brand}{hazard}")
            if not dry_run:
                car.save(update_fields=fields)
        verb = "Будет обновлено" if dry_run else "Обновлено"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {updated}"))
