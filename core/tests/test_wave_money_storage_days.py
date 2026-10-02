"""T3 (B4): дни хранения по политике склада — календарные / рабочие /
рабочие без праздников Литвы.

Запуск: pytest core/tests/test_wave_money_storage_days.py
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from core.models import Car, Container, Warehouse, WarehouseService
from core.utils import (
    STORAGE_DAY_POLICY_BUSINESS,
    STORAGE_DAY_POLICY_BUSINESS_LT,
    STORAGE_DAY_POLICY_CALENDAR,
    count_storage_days,
    is_storage_day,
    lithuanian_holidays,
)

# Понедельник 2026-03-02 … воскресенье 2026-03-15: ровно две недели.
MON = datetime.date(2026, 3, 2)
SUN_2W = datetime.date(2026, 3, 15)


class TestCountStorageDaysPure:
    def test_calendar_counts_every_day_inclusive(self):
        assert count_storage_days(MON, SUN_2W, STORAGE_DAY_POLICY_CALENDAR) == 14
        assert count_storage_days(MON, MON, STORAGE_DAY_POLICY_CALENDAR) == 1

    def test_business_skips_weekends(self):
        assert count_storage_days(MON, SUN_2W, STORAGE_DAY_POLICY_BUSINESS) == 10
        # Суббота–воскресенье — 0 дней хранения.
        sat = datetime.date(2026, 3, 7)
        assert count_storage_days(sat, sat + datetime.timedelta(days=1), STORAGE_DAY_POLICY_BUSINESS) == 0

    def test_business_lt_skips_lithuanian_holidays(self):
        # 11 марта (День восстановления независимости) — среда в 2026.
        assert datetime.date(2026, 3, 11).weekday() == 2
        assert datetime.date(2026, 3, 11) in lithuanian_holidays(2026)
        assert count_storage_days(MON, SUN_2W, STORAGE_DAY_POLICY_BUSINESS_LT) == 9

    def test_business_lt_knows_easter(self):
        # Пасхальный понедельник 2026 — 6 апреля (переходящий праздник).
        assert datetime.date(2026, 4, 6) in lithuanian_holidays(2026)
        assert is_storage_day(datetime.date(2026, 4, 6), STORAGE_DAY_POLICY_BUSINESS_LT) is False
        assert is_storage_day(datetime.date(2026, 4, 6), STORAGE_DAY_POLICY_BUSINESS) is True

    def test_end_before_start_is_zero(self):
        assert count_storage_days(SUN_2W, MON, STORAGE_DAY_POLICY_CALENDAR) == 0

    def test_unknown_or_empty_policy_falls_back_to_calendar(self):
        assert count_storage_days(MON, SUN_2W, "") == 14
        assert count_storage_days(MON, SUN_2W, None) == 14


@pytest.fixture
def storage_rate():
    return Decimal("5")


def _warehouse(name, policy, free_days=0, rate=Decimal("5")):
    wh = Warehouse.objects.create(name=name, free_days=free_days, storage_day_policy=policy)
    WarehouseService.objects.create(warehouse=wh, name="Хранение", code="STORAGE", default_price=rate, is_active=True)
    return wh


def _transferred_car(warehouse, vin, start=MON, end=SUN_2W):
    container = Container.objects.create(number=f"SD-{vin[-4:]}", status="UNLOADED", warehouse=warehouse, unload_date=start)
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status="TRANSFERRED",
        container=container,
        warehouse=warehouse,
        unload_date=start,
        transfer_date=end,
    )


@pytest.mark.django_db
class TestCarStorageDaysByPolicy:
    def test_default_policy_is_calendar_and_matches_old_formula(self):
        wh = _warehouse("WH-SD-CAL", STORAGE_DAY_POLICY_CALENDAR)
        assert Warehouse.objects.get(pk=wh.pk).storage_day_policy == "CALENDAR"
        car = _transferred_car(wh, "STORAGEDAYSCAL001")
        assert car.get_storage_days() == (14, 14)
        car.update_days_and_storage()
        assert car.days == 14
        assert car.storage_cost == Decimal("70.00")

    def test_business_policy_excludes_weekends(self):
        wh = _warehouse("WH-SD-BUS", STORAGE_DAY_POLICY_BUSINESS)
        car = _transferred_car(wh, "STORAGEDAYSBUS001")
        assert car.get_storage_days() == (10, 10)
        car.update_days_and_storage()
        assert car.storage_cost == Decimal("50.00")

    def test_business_lt_policy_excludes_holidays(self):
        wh = _warehouse("WH-SD-LT", STORAGE_DAY_POLICY_BUSINESS_LT)
        car = _transferred_car(wh, "STORAGEDAYSLT0001")
        assert car.get_storage_days() == (9, 9)

    def test_free_days_subtracted_from_policy_days(self):
        wh = _warehouse("WH-SD-FREE", STORAGE_DAY_POLICY_BUSINESS, free_days=3)
        car = _transferred_car(wh, "STORAGEDAYSFREE01")
        # 10 рабочих дней − 3 бесплатных = 7 платных; «всего» — 10 по политике.
        assert car.get_storage_days() == (7, 10)

    def test_free_days_exceeding_period_gives_zero(self):
        wh = _warehouse("WH-SD-FREE2", STORAGE_DAY_POLICY_BUSINESS, free_days=30)
        car = _transferred_car(wh, "STORAGEDAYSFREE02")
        assert car.get_storage_days() == (0, 10)
        car.update_days_and_storage()
        assert car.storage_cost == Decimal("0.00")

    def test_car_without_warehouse_has_no_storage(self):
        container = Container.objects.create(number="SD-NOWH", status="FLOATING")
        car = Car.objects.create(
            year=2023, brand="Toyota", vin="STORAGEDAYSNOWH01", status="UNLOADED", container=container, unload_date=MON
        )
        assert car.get_storage_days() == (0, 0)
        car.update_days_and_storage()
        assert car.days == 0
        assert car.storage_cost == Decimal("0.00")

    def test_car_without_container_uses_own_warehouse_policy(self):
        wh = _warehouse("WH-SD-LOOSE", STORAGE_DAY_POLICY_BUSINESS)
        car = Car.objects.create(
            year=2023,
            brand="Toyota",
            vin="STORAGEDAYSLOOSE1",
            status="TRANSFERRED",
            warehouse=wh,
            unload_date=MON,
            transfer_date=SUN_2W,
        )
        assert car.get_storage_days() == (10, 10)

    def test_warehouse_change_mid_storage_applies_new_policy_to_whole_period(self):
        """Истории складов нет: политика берётся у ТЕКУЩЕГО склада на весь период."""
        calendar_wh = _warehouse("WH-SD-MID-CAL", STORAGE_DAY_POLICY_CALENDAR)
        business_wh = _warehouse("WH-SD-MID-BUS", STORAGE_DAY_POLICY_BUSINESS)
        car = _transferred_car(calendar_wh, "STORAGEDAYSMID001")
        assert car.get_storage_days() == (14, 14)

        car.warehouse = business_wh
        assert car.get_storage_days() == (10, 10)
