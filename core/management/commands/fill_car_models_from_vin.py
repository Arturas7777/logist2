"""Дописывает модель в марку по VIN (NHTSA), без обращения к языковой модели.

По умолчанию только плывущие авто, у которых в марке нет пробела
(«TESLA», «HONDA»). Уже записанная «HONDA CR-V» не трогается.
"""

from django.core.management.base import BaseCommand

from core.models import Car
from core.services.vin_gate import identity_from_vin


class Command(BaseCommand):
    help = "Дописать модель автомобиля по VIN из NHTSA."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Все авто с маркой из одного слова, не только плывущие",
        )

    def handle(self, *args, **options):
        cars = Car.objects.exclude(brand__contains=" ")
        if not options["all"]:
            cars = cars.filter(status="FLOATING")
        updated = 0
        for car in cars.iterator():
            brand, year = identity_from_vin(car.vin, brand=car.brand, year=car.year or 0)
            fields = []
            if brand and brand != car.brand:
                car.brand = brand
                fields.append("brand")
            if not car.year and year:
                car.year = year
                fields.append("year")
            if not fields:
                continue
            car.save(update_fields=fields)
            updated += 1
            self.stdout.write(f"{car.vin} -> {car.brand}")
        self.stdout.write(self.style.SUCCESS(f"Обновлено: {updated}"))
