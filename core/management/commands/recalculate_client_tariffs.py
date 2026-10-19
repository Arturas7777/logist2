"""Пересчёт скрытой наценки для авто клиентов с тарифом (FIXED/FLEXIBLE).

Контекст: пакет «услуги склада (кроме хранения) + THS линии» приводится РОВНО
к тарифу клиента (для FLEXIBLE ставка подбирается по числу ТС в контейнере,
без учёта мотоциклов; ставка может зависеть от линии). Команда переприменяет
тариф к существующим авто под актуальную логику.

По умолчанию обрабатываются только АКТИВНЫЕ авто (не TRANSFERRED), чтобы не
трогать историю и уже выставленные инвойсы. `--all` снимает это ограничение
(использовать осознанно — изменит и переданные авто).

`--with-totals` дополнительно пересчитывает total_price активных авто
клиентов без тарифа (например, если он разошёлся с суммой услуг).

У изменившихся авто регенерируются черновики инвойсов (выставленные счета
не трогаются — см. AUTO_REGENERATABLE_INVOICE_STATUSES).
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.models import Car
from core.services.car_service_manager import TARIFF_TYPES, apply_client_tariff_for_car
from core.signals.car_service import _deferred_invoice_regeneration


class Command(BaseCommand):
    help = "Переприменяет тариф (скрытую наценку) к авто клиентов FIXED/FLEXIBLE под актуальную логику"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Включая переданные (TRANSFERRED) авто — затрагивает историю!",
        )
        parser.add_argument(
            "--with-totals",
            action="store_true",
            help="Также пересчитать total_price активных авто клиентов без тарифа",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что изменится, без записи в БД",
        )

    def handle(self, *args, **options):
        include_all = options.get("all", False)
        with_totals = options.get("with_totals", False)
        dry_run = options.get("dry_run", False)

        tariff_q = Q(client__tariff_type__in=TARIFF_TYPES)
        cars_qs = Car.objects.filter(tariff_q) if not with_totals else Car.objects.all()
        cars_qs = cars_qs.select_related("client", "warehouse").order_by("pk")
        if not include_all:
            cars_qs = cars_qs.exclude(status="TRANSFERRED")

        total = cars_qs.count()
        changed = 0
        self.stdout.write(f"Кандидатов ({'все' if include_all else 'активные'}): {total}")

        for car in cars_qs.iterator():
            old_total = car.total_price
            has_tariff = bool(car.client and car.client.tariff_type in TARIFF_TYPES)

            with transaction.atomic():
                sid = transaction.savepoint()
                if has_tariff:
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
                        _deferred_invoice_regeneration(car.pk)
                        transaction.savepoint_commit(sid)
                    changed += 1
                    client_label = f"{car.client.name} [{car.client.tariff_type}]" if car.client else "без клиента"
                    self.stdout.write(f"  #{car.id} {car.vin} {client_label}: {old_total} -> {new_total}")
                elif dry_run:
                    transaction.savepoint_rollback(sid)
                else:
                    transaction.savepoint_commit(sid)

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}Изменено авто: {changed} из {total}"))
