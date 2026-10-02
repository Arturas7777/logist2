"""Q8 / P2 — WS-рассылка по Car и единый путь пересчёта цены.

- сохранение только денормализованных полей (``update_fields=["total_price"]``)
  не шлёт ``group_send``;
- полное сохранение шлёт ровно один раз, повтор в окне дебаунса — ноль;
- ``_bulk_updating`` / ``raw`` — ноль;
- ``Car.save()`` + ``CarService.save()`` в одной транзакции → ОДИН
  ``recalculate_cars_total_price_task.delay`` с id машины (а не sync+async);
- откат транзакции не «залипает» в дедупе — следующий save снова ставит задачу;
- частичный save без ``is_important`` не трогает ``core_task``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from core.models import Car, CarService, Container, Warehouse, WarehouseService
from core.services.car_lifecycle_service import CAR_WS_DENORM_FIELDS, should_send_car_ws

pytestmark = pytest.mark.django_db


@pytest.fixture
def car(db):
    wh = Warehouse.objects.create(name="WH-WS", free_days=0)
    container = Container.objects.create(number="WSCASE00001", status="FLOATING")
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin="WSPERF00000000001",
        status="FLOATING",
        container=container,
        warehouse=wh,
    )


@pytest.fixture
def ws_layer():
    layer = MagicMock()
    layer.group_send = AsyncMock()
    with patch("core.services.car_lifecycle_service.get_channel_layer", return_value=layer):
        yield layer


# ---------------------------------------------------------------------------
# WS
# ---------------------------------------------------------------------------


def test_should_send_rules(car):
    assert should_send_car_ws(car) is True
    assert should_send_car_ws(car, update_fields=["total_price"]) is False
    assert should_send_car_ws(car, update_fields=list(CAR_WS_DENORM_FIELDS)) is False
    assert should_send_car_ws(car, update_fields=["status", "total_price"]) is True
    assert should_send_car_ws(car, raw=True) is False
    car._bulk_updating = True
    assert should_send_car_ws(car) is False


def test_denorm_only_save_does_not_group_send(car, ws_layer, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        car.total_price = Decimal("10.00")
        car.save(update_fields=["total_price"])
    ws_layer.group_send.assert_not_called()


def test_full_save_sends_once_and_debounces(car, ws_layer, django_capture_on_commit_callbacks, monkeypatch):
    # Окно дебаунса делаем заведомо большим: первый on_commit тянет за собой
    # импорт urlconf (сверка контейнера) и на холодном процессе может
    # занять больше 2 секунд.
    monkeypatch.setattr("core.services.car_lifecycle_service.CAR_WS_DEBOUNCE_SECONDS", 60)
    with django_capture_on_commit_callbacks(execute=True):
        car.notes = "first"
        car.save()
    assert ws_layer.group_send.await_count == 1
    group, payload = ws_layer.group_send.await_args.args
    assert group == "updates"
    assert payload["type"] == "data_update"
    assert payload["data"]["id"] == car.pk

    # повтор в окне дебаунса (2 с) — молчим
    with django_capture_on_commit_callbacks(execute=True):
        car.notes = "second"
        car.save()
    assert ws_layer.group_send.await_count == 1


def test_bulk_updating_flag_silences_ws(car, ws_layer, django_capture_on_commit_callbacks):
    car._bulk_updating = True
    with django_capture_on_commit_callbacks(execute=True):
        car.save()
    ws_layer.group_send.assert_not_called()


# ---------------------------------------------------------------------------
# Цена: один путь
# ---------------------------------------------------------------------------


def _wh_service(warehouse, name, price):
    return WarehouseService.objects.create(
        warehouse=warehouse,
        name=name,
        code="",
        default_price=Decimal(str(price)),
        is_active=True,
    )


def _make_car(vin, number):
    wh = Warehouse.objects.create(name=f"WH-{vin[-4:]}", free_days=0)
    container = Container.objects.create(number=number, status="FLOATING")
    return Car.objects.create(
        year=2023,
        brand="Toyota",
        vin=vin,
        status="FLOATING",
        container=container,
        warehouse=wh,
    )


# ``transaction=True``: дедуп пересчёта смотрит на on_commit-очередь текущей
# транзакции. В обычном @django_db весь тест — одна незакоммиченная
# транзакция, и коллбэк от создания машины в фикстуре «висел» бы в очереди,
# глуша последующие enqueue. С реальными коммитами поведение как в проде.


@pytest.mark.django_db(transaction=True)
def test_car_and_service_save_enqueue_single_recalc():
    car = _make_car("WSPERF00000000002", "WSCASE00002")
    svc1 = _wh_service(car.warehouse, "Разгрузка", 50)
    svc2 = _wh_service(car.warehouse, "Погрузка", 30)
    with patch("core.tasks.recalculate_cars_total_price_task.delay") as mock_delay:
        with transaction.atomic():
            car.notes = "edit"
            car.save()
            CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc1.id, custom_price=Decimal("50"))
            CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc2.id, custom_price=Decimal("30"))

    assert mock_delay.call_count == 1
    (ids,), _ = mock_delay.call_args
    assert ids == [car.pk]


@pytest.mark.django_db(transaction=True)
def test_service_save_alone_recalculates_via_task():
    """Старый sync-путь удалён: цена всё равно должна попасть в БД (eager Celery)."""
    car = _make_car("WSPERF00000000003", "WSCASE00003")
    svc = _wh_service(car.warehouse, "Разгрузка", 50)
    CarService.objects.create(car=car, service_type="WAREHOUSE", service_id=svc.id, custom_price=Decimal("50"))
    car.refresh_from_db()
    assert car.total_price == Decimal("50.00")


@pytest.mark.django_db(transaction=True)
def test_rollback_does_not_leak_dedup():
    car = _make_car("WSPERF00000000004", "WSCASE00004")
    with patch("core.tasks.recalculate_cars_total_price_task.delay") as mock_delay:
        try:
            with transaction.atomic():
                car.notes = "will rollback"
                car.save()
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert mock_delay.call_count == 0

        car.notes = "after rollback"
        car.save()
    assert mock_delay.call_count == 1
    (ids,), _ = mock_delay.call_args
    assert ids == [car.pk]


def test_partial_save_does_not_touch_tasks(car):
    with CaptureQueriesContext(connection) as ctx:
        car.save(update_fields=["total_price"])
    assert not any("core_task" in q["sql"] for q in ctx.captured_queries)
