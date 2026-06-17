"""Пересчёт скрытой наценки для авто FIXED-клиентов под исправленную логику.

Контекст: для тарифа FIXED складской пакет должен быть РОВНО равен тарифу.
Раньше дефолтные наценки услуг склада задирали пакет выше тарифа (см.
`_distribute_markup_for_car`). После фикса наценка пересчитывается заново.
Эта команда переприменяет тариф к существующим авто.

По умолчанию обрабатываются только АКТИВНЫЕ авто (не TRANSFERRED), чтобы не
трогать историю и уже выставленные инвойсы. `--all` снимает это ограничение
(использовать осознанно — изменит и переданные авто).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Car
from core.services.car_service_manager import apply_client_tariff_for_car


class Command(BaseCommand):
    help = "Переприменяет тариф (скрытую наценку) к авто FIXED-клиентов под новую логику"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Включая переданные (TRANSFERRED) авто — затрагивает историю!",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что изменится, без записи в БД",
        )

    def handle(self, *args, **options):
        include_all = options.get("all", False)
        dry_run = options.get("dry_run", False)

        cars_qs = Car.objects.filter(client__tariff_type="FIXED").select_related("client", "warehouse")
        if not include_all:
            cars_qs = cars_qs.exclude(status="TRANSFERRED")

        total = cars_qs.count()
        changed = 0
        self.stdout.write(f"Кандидатов (FIXED, {'все' if include_all else 'активные'}): {total}")

        for car in cars_qs.iterator():
            old_total = car.total_price

            with transaction.atomic():
                sid = transaction.savepoint()
                apply_client_tariff_for_car(car)
                car.calculate_total_price()
                new_total = car.total_price

                if new_total != old_total:
                    if dry_run:
                        transaction.savepoint_rollback(sid)
                    else:
                        Car.objects.filter(pk=car.pk).update(
                            days=car.days,
                            storage_cost=car.storage_cost,
                            total_price=car.total_price,
                        )
                        transaction.savepoint_commit(sid)
                    changed += 1
                    self.stdout.write(
                        f"  #{car.id} {car.vin} {car.client.name}: {old_total} -> {new_total}"
                    )
                else:
                    transaction.savepoint_rollback(sid)

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}Изменено авто: {changed} из {total}"))
