"""Ставит галочку Hazzard по электрификации NHTSA.

Новые машины получают её сами при расшифровке VIN. Эта команда — для уже
заведённых: спрашивает NHTSA пачками и отмечает электромобили и гибриды.
Уже стоящую галочку не снимает.
"""

from django.core.management.base import BaseCommand

from core.models import Car, VinCheck
from core.services.vin_gate import nhtsa_marks_hazard, normalize_vin_input
from core.services.vin_validator import decode_powertrain_batch


class Command(BaseCommand):
    help = "Отметить Hazzard по электрификации NHTSA (электромобили и гибриды)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Только посчитать, без записи")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cars = list(Car.objects.filter(is_hazardous=False).only("pk", "vin"))
        by_vin: dict[str, list[int]] = {}
        for car in cars:
            vin = normalize_vin_input(car.vin)
            if len(vin) == 17:
                by_vin.setdefault(vin, []).append(car.pk)

        power = decode_powertrain_batch(list(by_vin))
        self.stdout.write(f"NHTSA ответил по {len(power)} VIN из {len(by_vin)}")

        checks = list(VinCheck.objects.filter(vin__in=list(power)))
        dirty = []
        for check in checks:
            row = power.get(check.vin)
            if row is None:
                continue
            electrification = row["electrification"][:100]
            fuel = row["fuel_primary"][:50]
            if check.nhtsa_electrification == electrification and check.nhtsa_fuel_primary == fuel:
                continue
            check.nhtsa_electrification = electrification
            check.nhtsa_fuel_primary = fuel
            dirty.append(check)
        if dirty and not dry_run:
            VinCheck.objects.bulk_update(dirty, ["nhtsa_electrification", "nhtsa_fuel_primary"])

        marked: list[int] = []
        for vin, row in power.items():
            if nhtsa_marks_hazard(row["electrification"], row["fuel_primary"]):
                marked.extend(by_vin.get(vin, []))
        if marked and not dry_run:
            Car.objects.filter(pk__in=marked, is_hazardous=False).update(is_hazardous=True)

        verb = "Будет отмечено" if dry_run else "Отмечено"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {len(marked)}. Снимков VIN обновлено: {len(dirty)}"))
