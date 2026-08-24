"""Разовое заполнение кэша проверок VIN по уже заведённым машинам.

Форма ввода и applier сканов наполняют :class:`core.models.VinCheck` сами,
но машины, заведённые до появления проверки, в кэше отсутствуют — а без
него сверка контейнера не может сказать, сходится ли VIN с расшифровкой.

NHTSA держит примерно пять запросов в секунду, поэтому между вызовами
пауза. Команда идемпотентна: уже проверенные VIN пропускаются, если не
передан ``--force``.

Примеры:
    python manage.py backfill_vin_checks
    python manage.py backfill_vin_checks --limit 100 --containers-only
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from core.models import Car, VinCheck
from core.services.vin_gate import refresh_vin_check


class Command(BaseCommand):
    help = "Заполняет кэш VinCheck (контрольная цифра + NHTSA) по существующим машинам."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Не больше N VIN за запуск")
        parser.add_argument("--force", action="store_true", help="Перепроверить даже уже известные VIN")
        parser.add_argument("--containers-only", action="store_true", help="Только машины, привязанные к контейнеру")
        parser.add_argument("--delay", type=float, default=0.25, help="Пауза между запросами, сек")

    def handle(self, *args, **opts):
        cars = Car.objects.exclude(vin="")
        if opts["containers_only"]:
            cars = cars.filter(container__isnull=False)

        vins = sorted({vin for vin in cars.values_list("vin", flat=True) if vin and len(vin) == 17})
        if not opts["force"]:
            known = set(VinCheck.objects.filter(vin__in=vins).values_list("vin", flat=True))
            vins = [vin for vin in vins if vin not in known]
        if opts["limit"]:
            vins = vins[: opts["limit"]]

        if not vins:
            self.stdout.write(self.style.SUCCESS("Все VIN уже проверены — делать нечего."))
            return

        self.stdout.write(f"Проверяем VIN: {len(vins)}")
        confirmed = suspicious = failed = 0
        for index, vin in enumerate(vins, start=1):
            try:
                check = refresh_vin_check(vin)
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f"  {vin}: ошибка проверки ({exc})"))
                continue
            if check is None:
                failed += 1
                continue
            if check.nhtsa_ok:
                confirmed += 1
            else:
                suspicious += 1
                self.stdout.write(f"  {vin}: не подтверждён ({check.error_text or 'без пояснения'})")
            if index % 50 == 0:
                self.stdout.write(f"  … обработано {index} из {len(vins)}")
            time.sleep(opts["delay"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Подтверждено: {confirmed}, под вопросом: {suspicious}, не удалось проверить: {failed}."
            )
        )
