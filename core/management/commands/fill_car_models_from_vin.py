"""Дописывает модель и тип ТС по VIN (NHTSA), без обращения к языковой модели.

По умолчанию только плывущие авто. Марка из одного слова дополняется моделью.
Тип «Легковой» меняется на мотоцикл, квадроцикл или кроссовер, если это
следует из уже сохранённой расшифровки. Выбранный вручную тип не трогается.
"""

from django.core.management.base import BaseCommand

from core.models import Car
from core.services.vin_gate import cached_vehicle_type, identity_from_vin


class Command(BaseCommand):
    help = "Дописать модель автомобиля по VIN из NHTSA."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Все авто с маркой из одного слова, не только плывущие",
        )

    def handle(self, *args, **options):
        cars = Car.objects.all() if options["all"] else Car.objects.filter(status="FLOATING")
        updated = 0
        for car in cars.iterator():
            fields = []
            if " " not in (car.brand or "").strip():
                brand, year = identity_from_vin(car.vin, brand=car.brand, year=car.year or 0)
                if brand and brand != car.brand:
                    car.brand = brand
                    fields.append("brand")
                if not car.year and year:
                    car.year = year
                    fields.append("year")
            if (car.vehicle_type or "SEDAN") == "SEDAN":
                mapped = cached_vehicle_type(car.vin)
                if mapped:
                    car.vehicle_type = mapped
                    fields.append("vehicle_type")
            if not fields:
                continue
            car.save(update_fields=fields)
            updated += 1
            label = car.get_vehicle_type_display()
            self.stdout.write(f"{car.vin} -> {car.brand} ({label})")
        self.stdout.write(self.style.SUCCESS(f"Обновлено: {updated}"))
