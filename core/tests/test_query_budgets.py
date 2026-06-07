"""
Бюджеты запросов на горячих выборках (assertNumQueries-style).

Эти тесты — сеть безопасности против регрессий N+1, устранённых в аудите
производительности (этапы A–C). Они фиксируют, что число SQL-запросов на
ключевых путях НЕ растёт линейно с числом строк.

Идея: измеряем запросы для N=2 и N=5 объектов и требуем РАВЕНСТВА
(константный бюджет), вместо хрупкого хардкода абсолютных чисел.

Покрывает:
- ``CarAdmin.get_queryset`` + ``storage_cost_display`` — ставка хранения
  берётся из аннотации одним Subquery, без запроса WarehouseService на
  каждую строку (см. Car._get_storage_daily_rate).
- ``Container`` список авто — ``container_cars`` префетчится, подсчёт
  числа машин не делает запрос на контейнер.

Запуск: pytest core/tests/test_query_budgets.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.admin.car import CarAdmin
from core.models import Car, Container, Warehouse, WarehouseService


def _make_request():
    rf = RequestFactory()
    request = rf.get("/admin/core/car/")
    User = get_user_model()
    request.user = User(is_staff=True, is_superuser=True, is_active=True)
    return request


_vin_counter = [0]


def _seed_cars(n, *, warehouse, container):
    unload = timezone.now().date() - timezone.timedelta(days=3)
    for _ in range(n):
        _vin_counter[0] += 1
        Car.objects.create(
            year=2023, brand="Toyota", vin=f"QBUDGETCAR{_vin_counter[0]:07d}",
            status="UNLOADED", container=container, warehouse=warehouse,
            unload_date=unload,
        )


@pytest.mark.django_db
class TestCarAdminStorageBudget:
    """storage_cost_display не должен делать запрос на каждую строку."""

    def _count_for(self, n):
        from django.contrib import admin as django_admin

        wh = Warehouse.objects.create(name=f"WH-QB-{n}", free_days=0)
        WarehouseService.objects.create(
            warehouse=wh, name="Хранение", code="STORAGE",
            default_price=Decimal("5"), is_active=True,
        )
        container = Container.objects.create(number=f"QB-{n}", status="FLOATING")
        _seed_cars(n, warehouse=wh, container=container)

        car_admin = CarAdmin(Car, django_admin.site)
        request = _make_request()

        with CaptureQueriesContext(connection) as ctx:
            qs = car_admin.get_queryset(request)
            cars = list(qs.filter(warehouse=wh))
            for car in cars:
                # имитируем рендер столбца «Хран» в списке админки
                car_admin.storage_cost_display(car)
        return len(ctx.captured_queries)

    def test_storage_display_is_constant_query_budget(self):
        q2 = self._count_for(2)
        q5 = self._count_for(5)
        assert q2 == q5, (
            f"Число запросов растёт с числом машин ({q2} → {q5}); "
            f"вероятно вернулся N+1 в storage_cost_display"
        )

    def test_storage_value_still_correct(self):
        from django.contrib import admin as django_admin

        wh = Warehouse.objects.create(name="WH-QB-val", free_days=0)
        WarehouseService.objects.create(
            warehouse=wh, name="Хранение", code="STORAGE",
            default_price=Decimal("5"), is_active=True,
        )
        container = Container.objects.create(number="QB-val", status="FLOATING")
        _seed_cars(1, warehouse=wh, container=container)

        car_admin = CarAdmin(Car, django_admin.site)
        qs = car_admin.get_queryset(_make_request())
        car = qs.get(warehouse=wh)
        # (3 + 1) дней * 5 = 20.00 — аннотация ставки подхватывается корректно
        assert car_admin.storage_cost_display(car) == "20.00"


@pytest.mark.django_db
class TestContainerCarsCountBudget:
    """Подсчёт числа машин контейнера через prefetch не должен расти с N."""

    def _count_for(self, n):
        wh = Warehouse.objects.create(name=f"WH-CC-{n}", free_days=0)
        container = Container.objects.create(number=f"CC-{n}", status="FLOATING")
        _seed_cars(n, warehouse=wh, container=container)

        with CaptureQueriesContext(connection) as ctx:
            containers = list(
                Container.objects.filter(pk=container.pk).prefetch_related("container_cars")
            )
            for c in containers:
                # len() по префетченному related — без доп. запроса на контейнер
                _ = len(c.container_cars.all())
        return len(ctx.captured_queries)

    def test_container_cars_count_constant_budget(self):
        assert self._count_for(2) == self._count_for(5)
