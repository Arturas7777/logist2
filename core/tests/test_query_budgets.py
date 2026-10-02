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
- T10 (план 2026-10): страницы целиком через test client — changelist
  контейнеров, changelist заявок на автовоз, ``/admin/reconciliation/``,
  change-форма инвойса. Число запросов не должно расти с числом строк.

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
            year=2023,
            brand="Toyota",
            vin=f"QBUDGETCAR{_vin_counter[0]:07d}",
            status="UNLOADED",
            container=container,
            warehouse=warehouse,
            unload_date=unload,
        )


@pytest.mark.django_db
class TestCarAdminStorageBudget:
    """storage_cost_display не должен делать запрос на каждую строку."""

    def _count_for(self, n):
        from django.contrib import admin as django_admin

        wh = Warehouse.objects.create(name=f"WH-QB-{n}", free_days=0)
        WarehouseService.objects.create(
            warehouse=wh,
            name="Хранение",
            code="STORAGE",
            default_price=Decimal("5"),
            is_active=True,
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
            f"Число запросов растёт с числом машин ({q2} → {q5}); вероятно вернулся N+1 в storage_cost_display"
        )

    def test_storage_value_still_correct(self):
        from django.contrib import admin as django_admin

        wh = Warehouse.objects.create(name="WH-QB-val", free_days=0)
        WarehouseService.objects.create(
            warehouse=wh,
            name="Хранение",
            code="STORAGE",
            default_price=Decimal("5"),
            is_active=True,
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
            containers = list(Container.objects.filter(pk=container.pk).prefetch_related("container_cars"))
            for c in containers:
                # len() по префетченному related — без доп. запроса на контейнер
                _ = len(c.container_cars.all())
        return len(ctx.captured_queries)

    def test_container_cars_count_constant_budget(self):
        assert self._count_for(2) == self._count_for(5)


@pytest.mark.django_db
class TestContainerStorageAggregatesBudget:
    """Container.storage_cost / days в списках — через with_storage_aggregates
    (P1, AUDIT_ROUND3): один annotate-запрос вместо aggregate на контейнер."""

    def _seed(self, n_containers, cars_per_container=2):
        wh = Warehouse.objects.create(name=f"WH-SA-{n_containers}", free_days=0)
        pks = []
        for i in range(n_containers):
            container = Container.objects.create(
                number=f"SA-{n_containers}-{i}",
                status="FLOATING",
            )
            _seed_cars(cars_per_container, warehouse=wh, container=container)
            pks.append(container.pk)
        return pks

    def _count_for(self, n):
        pks = self._seed(n)
        with CaptureQueriesContext(connection) as ctx:
            containers = list(Container.objects.with_storage_aggregates().filter(pk__in=pks))
            for c in containers:
                _ = c.storage_cost
                _ = c.days
        return len(ctx.captured_queries)

    def test_storage_aggregates_constant_budget(self):
        q1 = self._count_for(1)
        q4 = self._count_for(4)
        assert q1 == q4 == 1, (
            f"with_storage_aggregates должен давать ровно 1 запрос независимо от числа контейнеров ({q1} → {q4})"
        )

    def test_storage_aggregates_values_match_properties(self):
        from django.db.models import Sum

        pks = self._seed(1)
        Car.objects.filter(container_id__in=pks).update(
            storage_cost=Decimal("12.50"),
            days=3,
        )

        annotated = Container.objects.with_storage_aggregates().get(pk__in=pks)
        plain = Container.objects.get(pk=pks[0])

        expected_sum = Car.objects.filter(container_id=pks[0]).aggregate(s=Sum("storage_cost"))["s"]
        assert annotated.storage_cost == plain.storage_cost == expected_sum
        assert annotated.days == plain.days == 3


@pytest.mark.django_db
class TestServiceCatalogBatchResolve:
    """P2 (AUDIT_ROUND3): prefetch_service_objects прогревает каталог батчем —
    invoice_price дальше считается без запросов к БД."""

    def _seed(self):
        from core.models import CarService

        wh = Warehouse.objects.create(name="WH-P2", free_days=0)
        svc_a = WarehouseService.objects.create(
            warehouse=wh,
            name="Разгрузка",
            default_price=Decimal("100"),
            is_active=True,
        )
        svc_b = WarehouseService.objects.create(
            warehouse=wh,
            name="Погрузка",
            default_price=Decimal("50"),
            is_active=True,
        )
        container = Container.objects.create(number="P2-1", status="FLOATING")
        _seed_cars(1, warehouse=wh, container=container)
        car = Car.objects.get(container=container)
        # Без custom_price — invoice_price вынужден резолвить каталог.
        css = [
            CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc_a.pk),
            CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc_b.pk),
        ]
        return css

    def test_invoice_price_after_prefetch_makes_no_queries(self):
        from django.core.cache import cache

        from core.models.services import prefetch_service_objects

        css = self._seed()
        cache.clear()

        warmed = prefetch_service_objects(css)
        assert warmed == 2

        with CaptureQueriesContext(connection) as ctx:
            total = sum(svc.invoice_price for svc in css)
        assert total == Decimal("150")
        assert len(ctx.captured_queries) == 0, (
            f"После prefetch_service_objects резолвинг каталога не должен "
            f"ходить в БД, выполнено запросов: {len(ctx.captured_queries)}"
        )

    def test_missing_catalog_entry_cached_as_none(self):
        from django.core.cache import cache

        from core.models import CarService
        from core.models.services import prefetch_service_objects

        css = self._seed()
        ghost = CarService.objects.create(
            car=css[0].car,
            service_type="WAREHOUSE",
            service_id=999999,
        )
        cache.clear()
        prefetch_service_objects([ghost])

        with CaptureQueriesContext(connection) as ctx:
            assert ghost.get_default_price() == 0
        assert len(ctx.captured_queries) == 0


# ---------------------------------------------------------------------------
# T10: бюджеты страниц целиком (Django test client)
# ---------------------------------------------------------------------------


@pytest.fixture
def staff_client(client):
    User = get_user_model()
    user = User.objects.create_user(username="qb-staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


def _page_queries(client, url):
    """Число SQL-запросов на GET страницы.

    Прогревочный запрос снимает одноразовые эффекты (ContentType-кэш,
    сессия); Django-кэш перед замером чистим, чтобы считать холодный путь.
    """
    from django.core.cache import cache

    client.get(url)
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, f"{url} → {response.status_code}"
    return len(ctx.captured_queries)


@pytest.mark.django_db
class TestContainerChangelistBudget:
    """Q2/V2: changelist контейнеров — счётчики фото/писем подзапросами,
    без prefetch машин; число запросов не зависит от числа контейнеров."""

    def _seed(self, n):
        from core.models.website import ContainerPhoto

        wh = Warehouse.objects.create(name=f"WH-CL-{n}", free_days=0)
        for i in range(n):
            container = Container.objects.create(
                number=f"CL{n}{i:03d}",
                status="IN_PORT",
                eta=timezone.now().date() - timezone.timedelta(days=i),
            )
            _seed_cars(2, warehouse=wh, container=container)
            ContainerPhoto.objects.create(container=container, photo=f"photos/cl-{n}-{i}.jpg")

    def test_constant_budget(self, staff_client):
        self._seed(2)
        q2 = _page_queries(staff_client, "/admin/core/container/")
        self._seed(5)
        q7 = _page_queries(staff_client, "/admin/core/container/")
        assert q2 == q7, f"Запросы changelist контейнеров растут с числом строк: {q2} → {q7}"

    def test_eta_filter_works(self, staff_client):
        self._seed(1)
        resp = staff_client.get("/admin/core/container/?status_multi=IN_PORT&eta_state=overdue")
        assert resp.status_code == 200
        # Контейнер с ETA «сегодня» не просрочен — список пуст, фильтр отработал без ошибок.
        assert "просрочен на" not in resp.content.decode()


@pytest.mark.django_db
class TestTransportRequestChangelistBudget:
    """Q3: число машин заявки — аннотация, а не ``cars.count()`` на строку."""

    def _seed(self, n):
        from core.models import Client as ClientModel
        from core.models.website import TransportRequest

        wh = Warehouse.objects.create(name=f"WH-TR-{n}", free_days=0)
        owner = ClientModel.objects.create(name=f"TR client {n}")
        for i in range(n):
            tr = TransportRequest.objects.create(
                client=owner,
                carrier_name="Carrier",
                truck_number=f"TR{n}{i}",
                driver_name="Driver",
                status="SUBMITTED",
            )
            container = Container.objects.create(number=f"TR{n}{i:03d}", status="FLOATING")
            _seed_cars(2, warehouse=wh, container=container)
            tr.cars.set(Car.objects.filter(container=container))

    def test_constant_budget(self, staff_client):
        self._seed(2)
        q2 = _page_queries(staff_client, "/admin/core/transportrequest/")
        self._seed(5)
        q7 = _page_queries(staff_client, "/admin/core/transportrequest/")
        assert q2 == q7, f"Запросы changelist заявок растут с числом строк: {q2} → {q7}"


@pytest.mark.django_db
class TestReconciliationDashboardBudget:
    """Q5: сверка — SupplierCost/CarService батчами, не по одному на услугу."""

    def _seed(self, n):
        from core.models import CarService
        from core.models_invoice_audit import SupplierCost

        wh = Warehouse.objects.create(name=f"WH-RC-{n}", free_days=0)
        svc = WarehouseService.objects.create(
            warehouse=wh, name="Разгрузка", code="UNLOADING", default_price=Decimal("100"), is_active=True
        )
        container = Container.objects.create(number=f"RC{n:03d}", status="FLOATING")
        _seed_cars(n, warehouse=wh, container=container)
        for car in Car.objects.filter(container=container):
            cs = CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc.pk)
            SupplierCost.objects.create(
                car=car,
                car_service=cs,
                counterparty="Terminal",
                service_type="THS",
                amount=Decimal("80"),
                vin=car.vin,
            )

    def test_constant_budget(self, staff_client):
        self._seed(2)
        q2 = _page_queries(staff_client, "/admin/reconciliation/")
        self._seed(5)
        q7 = _page_queries(staff_client, "/admin/reconciliation/")
        assert q2 == q7, f"Запросы сверки растут с числом услуг: {q2} → {q7}"
        assert q2 <= 30, f"Бюджет сверки ≤ 30 запросов, сейчас {q2}"


@pytest.mark.django_db
class TestInvoiceChangeFormBudget:
    """Q4: категории расходов в форме инвойса — один запрос, не по одному на <option>."""

    def _seed_categories(self, n):
        from core.models.billing import ExpenseCategory

        for i in range(n):
            ExpenseCategory.objects.create(name=f"Категория {n}-{i}", category_type="OPERATIONAL")

    def _make_invoice(self):
        from core.models import Client as ClientModel
        from core.models import Company
        from core.models.billing import NewInvoice

        company = Company.objects.create(name="Caromoto Lithuania")
        owner = ClientModel.objects.create(name="Invoice owner")
        return NewInvoice.objects.create(
            issuer_company=company,
            recipient_client=owner,
            date=timezone.now().date(),
            status="DRAFT",
        )

    def test_constant_budget(self, staff_client):
        invoice = self._make_invoice()
        url = f"/admin/core/newinvoice/{invoice.pk}/change/"
        self._seed_categories(2)
        q2 = _page_queries(staff_client, url)
        self._seed_categories(6)
        q8 = _page_queries(staff_client, url)
        assert q2 == q8, f"Запросы формы инвойса растут с числом категорий: {q2} → {q8}"
