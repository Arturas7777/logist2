"""Смоук-тесты разделов «Наглядность» (V) и «UX админки» (U) плана 2026-10.

Проверяем через test client, что новые элементы рендерятся и не ломают
страницы: бейдж дней хранения (V1), ETA контейнеров (V2), сводная строка над
списком авто (V3), колонка «Сигналы» у инвойсов (V5), касса/несверено на
дашборде (V6), счётчики и подгруппы сайдбара (V9/U4), бейджи статусов через
CSS-классы (U7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from core.models import Car, Container, Warehouse, WarehouseService

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    User = get_user_model()
    user = User.objects.create_user(username="vis-staff", password="secret123", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def warehouse():
    wh = Warehouse.objects.create(name="WH-VIS", free_days=3)
    WarehouseService.objects.create(
        warehouse=wh, name="Хранение", code="STORAGE", default_price=Decimal("5"), is_active=True
    )
    return wh


def _car(warehouse, vin, *, days_ago, status="UNLOADED", **extra):
    container = Container.objects.create(number=f"C-{vin[-6:]}", status="FLOATING")
    car = Car.objects.create(
        year=2022,
        brand="Toyota",
        vin=vin,
        status=status,
        container=container,
        warehouse=warehouse,
        unload_date=timezone.now().date() - timezone.timedelta(days=days_ago),
        **extra,
    )
    return car


# ---------------------------------------------------------------------------
# V1: бейдж дней хранения
# ---------------------------------------------------------------------------


def test_days_badge_escalation(staff_client, warehouse):
    _car(warehouse, "VISFREE0000000001", days_ago=1)  # в бесплатном периоде
    _car(warehouse, "VISWARN0000000002", days_ago=6)  # 4 платных дня
    _car(warehouse, "VISOVER0000000003", days_ago=20)  # 18 платных дней
    Car.objects.filter(vin="VISFREE0000000001").update(days=0)
    Car.objects.filter(vin="VISWARN0000000002").update(days=4, storage_cost=Decimal("20"))
    Car.objects.filter(vin="VISOVER0000000003").update(days=18, storage_cost=Decimal("90"))

    resp = staff_client.get("/admin/core/car/?status_multi=UNLOADED")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "cm-days-badge--free" in html
    assert "cm-days-badge--warn" in html
    assert "cm-days-badge--over" in html
    assert "накоплено 90.00" in html
    # Статус — классом, а не inline-стилем (U7)
    assert "cm-badge--status-unloaded" in html


# ---------------------------------------------------------------------------
# V3: сводная строка над списком авто
# ---------------------------------------------------------------------------


def test_car_changelist_summary_bar(staff_client, warehouse):
    _car(warehouse, "VISSUM00000000001", days_ago=20, has_title=False)
    Car.objects.filter(vin="VISSUM00000000001").update(days=18)
    cache.clear()

    resp = staff_client.get("/admin/core/car/")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert 'id="cm-car-summary"' in html
    assert "?status_multi=UNLOADED&amp;days__gt=7" in html
    assert "?status_multi=UNLOADED&amp;has_title__exact=0" in html
    assert "?emails=unread" in html

    # Ссылки сводки открываются (lookup_allowed пропускает прямые поля)
    assert staff_client.get("/admin/core/car/?status_multi=UNLOADED&days__gt=7").status_code == 200
    assert staff_client.get("/admin/core/car/?status_multi=UNLOADED&has_title__exact=0").status_code == 200


def test_car_changelist_summary_is_cached(warehouse):
    from core.admin.car import CarAdmin

    cache.clear()
    first = CarAdmin.get_changelist_summary()
    _car(warehouse, "VISCACHE000000001", days_ago=1)
    second = CarAdmin.get_changelist_summary()
    assert first == second  # 60 с кэш — новая машина ещё не видна


# ---------------------------------------------------------------------------
# V2: ETA контейнеров и фильтр
# ---------------------------------------------------------------------------


def test_container_eta_countdown_and_filter(staff_client):
    today = timezone.localdate()
    Container.objects.create(number="ETAOVER001", status="FLOATING", eta=today - timezone.timedelta(days=2))
    Container.objects.create(number="ETASOON001", status="FLOATING", eta=today + timezone.timedelta(days=1))
    Container.objects.create(number="ETAFUT0001", status="IN_PORT", eta=today + timezone.timedelta(days=5))

    resp = staff_client.get("/admin/core/container/?status_multi=FLOATING&status_multi=IN_PORT")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "просрочен на 2 дн." in html
    assert "завтра" in html
    assert "через 5 дн." in html

    overdue = staff_client.get("/admin/core/container/?status_multi=FLOATING&eta_state=overdue").content.decode()
    assert "ETAOVER001" in overdue
    assert "ETASOON001" not in overdue

    week = staff_client.get(
        "/admin/core/container/?status_multi=FLOATING&status_multi=IN_PORT&eta_state=week"
    ).content.decode()
    assert "ETAFUT0001" in week
    assert "ETAOVER001" not in week


# ---------------------------------------------------------------------------
# V5: колонка «Сигналы» у инвойсов
# ---------------------------------------------------------------------------


def test_invoice_signals_column(staff_client):
    from core.models import Client as ClientModel
    from core.models import Company
    from core.models.billing import NewInvoice

    company = Company.objects.create(name="Caromoto Lithuania")
    owner = ClientModel.objects.create(name="Signals owner")
    inv = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=owner,
        date=timezone.now().date() - timezone.timedelta(days=40),
        status="ISSUED",
    )
    NewInvoice.objects.filter(pk=inv.pk).update(due_date=timezone.now().date() - timezone.timedelta(days=10))
    pair = NewInvoice.objects.create(
        issuer_company=company,
        recipient_client=owner,
        date=timezone.now().date(),
        status="DRAFT",
        linked_invoice=inv,
    )

    resp = staff_client.get("/admin/core/newinvoice/")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "cm-signals" in html
    assert "Просрочен на 10 дн." in html
    assert f"Связан с {pair.number}" in html
    assert "Вложения нет" in html


# ---------------------------------------------------------------------------
# V6: дашборд — касса, карты, несверено
# ---------------------------------------------------------------------------


def test_dashboard_treasury_and_unreconciled(staff_client):
    from core.models import Company
    from core.models.billing import PersonalCard

    Company.objects.create(name="Caromoto Lithuania")
    PersonalCard.objects.create(name="Revolut", last_four="1234", balance=Decimal("150.50"), is_active=True)
    PersonalCard.objects.create(name="Old", balance=Decimal("1"), is_active=False)
    cache.clear()

    resp = staff_client.get("/admin/dashboard/")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "Касса компании" in html
    assert "Revolut ·1234" in html
    assert "Old" not in html.split("Банк, касса и карты")[1].split("Аналитика")[0]
    assert "/admin/core/banktransaction/?reconciled=unmatched" in html
    assert "все операции сверены" in html


def test_unreconciled_summary_counts_and_sums():
    from core.models import Company
    from core.models.banking import BankConnection, BankTransaction
    from core.services.dashboard_service import DashboardService

    company = Company.objects.create(name="Caromoto Lithuania")
    conn = BankConnection.objects.create(bank_type="REVOLUT", company=company, name="Test Revolut")

    def _tx(ext, amount, **extra):
        return BankTransaction.objects.create(
            connection=conn,
            external_id=ext,
            amount=Decimal(amount),
            currency="EUR",
            created_at=timezone.now(),
            **extra,
        )

    _tx("t1", "100")
    _tx("t2", "-40")
    _tx("t3", "7", reconciliation_skipped=True)
    cache.clear()

    data = DashboardService().get_unreconciled_bank_summary()
    assert data["count"] == 2
    assert data["incoming"] == Decimal("100")
    assert data["outgoing"] == Decimal("40")
    assert data["total"] == Decimal("140")


# ---------------------------------------------------------------------------
# V9 / U4: сайдбар — счётчики и подгруппы
# ---------------------------------------------------------------------------


def test_sidebar_counters_and_subgroups(staff_client):
    from core.models import Client as ClientModel
    from core.models import Company
    from core.models.billing import NewInvoice

    company = Company.objects.create(name="Caromoto Lithuania")
    owner = ClientModel.objects.create(name="Sidebar owner")
    for _ in range(2):
        inv = NewInvoice.objects.create(
            issuer_company=company, recipient_client=owner, date=timezone.now().date(), status="ISSUED"
        )
        # save() может нормализовать статус — выставляем просрочку напрямую
        NewInvoice.objects.filter(pk=inv.pk).update(status="OVERDUE")
    cache.clear()

    resp = staff_client.get("/admin/core/task/")
    html = resp.content.decode()
    assert resp.status_code == 200
    # Подгруппы «Финансов»
    for title in ("Документы", "Банк и сверка", "Касса и карты", "Аналитика"):
        assert f'<li class="cm-nav-subhead">{title}</li>' in html
    # Прямая ссылка на список дел
    assert "Все дела (список)" in html
    # Счётчик OVERDUE у инвойсов
    assert 'href="/admin/core/newinvoice/?status__exact=OVERDUE"' in html
    assert ">2</a>" in html


def test_sidebar_counters_cached():
    from logist2.admin_site import LogistAdminSite

    cache.clear()
    first = LogistAdminSite.get_sidebar_counters()
    assert set(first) == {"car", "container", "autotransport", "banktransaction", "newinvoice"}
    assert cache.get(LogistAdminSite.SIDEBAR_COUNTERS_CACHE_KEY) == first


# ---------------------------------------------------------------------------
# U7: бейджи статусов заявок и дел — классами
# ---------------------------------------------------------------------------


def test_status_badges_use_css_classes(staff_client):
    from core.models import Client as ClientModel
    from core.models.tasks import Task
    from core.models.website import TransportRequest

    owner = ClientModel.objects.create(name="Badge owner")
    TransportRequest.objects.create(
        client=owner, carrier_name="Carrier", truck_number="B1", driver_name="D", status="SUBMITTED"
    )
    Task.objects.create(title="Проверить", priority="HIGH")

    tr_html = staff_client.get("/admin/core/transportrequest/").content.decode()
    assert "cm-badge--status-submitted" in tr_html

    task_html = staff_client.get("/admin/core/task/").content.decode()
    assert "cm-badge--priority-high" in task_html
    assert "cm-task-state--open" in task_html
