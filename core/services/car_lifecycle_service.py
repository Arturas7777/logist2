"""
Car lifecycle service — orchestrates side-effects that were previously
embedded in ``Car.save()``.

Call ``after_car_save()`` from model ``save()`` or admin ``save_model()``
instead of scattering logic across signals and the model itself.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)


def recalculate_car_price(car) -> None:
    """Recalculate total_price, days, storage_cost and persist via UPDATE."""
    from core.models import Car

    try:
        car.calculate_total_price()
        Car.objects.filter(pk=car.pk).update(
            total_price=car.total_price,
            days=car.days,
            storage_cost=car.storage_cost,
        )
    except Exception as e:
        logger.error("Failed to calculate total price for car %s: %s", car.vin, e)


def check_container_status(car) -> None:
    """If all cars in the container are TRANSFERRED, update container status."""
    if not car.container_id:
        return
    try:
        car.container.check_and_update_status_from_cars()
    except Exception as e:
        logger.error("Failed to check container status for car %s: %s", car.pk, e)


# Денормализованные поля Car: их пишут фоновые пересчёты
# (recalculate_cars_total_price_task, refresh_unloaded_storage_daily,
# apply_car_service_edits). Сохранение ТОЛЬКО этих полей — не событие для
# UI-рассылки: иначе ежедневный пересчёт 40+ машин = 40+ group_send подряд (Q8).
CAR_WS_DENORM_FIELDS = frozenset({"total_price", "days", "storage_cost", "current_price", "updated_at"})

# Окно дебаунса рассылки по одной машине, сек. Сохранение карточки из админки
# даёт несколько Car.save подряд (форма → тариф → финальный пересчёт) — в
# WebSocket уходит одно сообщение.
CAR_WS_DEBOUNCE_SECONDS = 2


def should_send_car_ws(car, *, update_fields=None, raw=False) -> bool:
    """Нужно ли слать WS-уведомление для этого сохранения Car."""
    if raw or getattr(car, "_bulk_updating", False) or getattr(car, "_creating_services", False):
        return False
    if update_fields is not None and set(update_fields) <= CAR_WS_DENORM_FIELDS:
        return False
    return True


def send_car_ws_notification(car, *, update_fields=None, raw=False) -> None:
    """Enqueue a WebSocket notification after commit.

    Q8: пропускаем «тихие» сохранения (только денормализованные поля,
    ``_bulk_updating``, ``raw``) и дебаунсим по ``car_id`` через
    ``cache.add`` — повторное сохранение той же машины в течение
    :data:`CAR_WS_DEBOUNCE_SECONDS` сообщение не шлёт.
    """
    if not should_send_car_ws(car, update_fields=update_fields, raw=raw):
        return

    car_id = car.pk
    payload = {
        "type": "data_update",
        "data": {
            "model": "Car",
            "id": car_id,
            "status": car.status,
            "storage_cost": str(car.storage_cost),
            "days": car.days,
            "price": str(car.total_price),
        },
    }

    def _notify():
        try:
            # add() атомарен: True только у первого за окно дебаунса.
            if not cache.add(f"ws:car:{car_id}", 1, CAR_WS_DEBOUNCE_SECONDS):
                return
        except Exception:
            # Кэш недоступен — лучше лишнее сообщение, чем потерянное.
            logger.debug("WS debounce cache unavailable for car %s", car_id, exc_info=True)
        try:
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)("updates", payload)
        except Exception as e:
            logger.error("Failed to send WebSocket notification for car %s: %s", car_id, e)

    transaction.on_commit(_notify)


def transition_cars(
    car_ids,
    target: str,
    *,
    transfer_date=None,
    source: str = "ADMIN",
    note: str = "",
    finalize: bool = True,
    skip_transferred: bool = False,
    require_unload_info: bool = False,
) -> dict:
    """Единственная массовая смена статуса авто (bulk action, контейнер, автовоз).

    Правила те же, что у одиночного ``Car.save``:

    * переходы только по ``ALLOWED_STATUS_TRANSITIONS``, «Важное» не трогаем;
    * ``require_unload_info`` — UNLOADED только со складом и датой разгрузки
      (каскад контейнера проставляет их авто следом, ему проверка не нужна);
    * в TRANSFERRED — ``transfer_date`` (явная → уже стоящая → сегодня, не
      раньше разгрузки); откат из TRANSFERRED очищает ``transfer_date``;
    * запись в ``CarStatusHistory``, свёртка статуса контейнеров;
    * после commit: пересчёт цен (+ реген DRAFT-счетов при передаче/откате,
      если ``finalize``) и уведомление клиенту «авто передано».

    ``skip_transferred`` — каскад контейнера: переданные авто не откатываются
    побочным эффектом статуса контейнера.

    Возвращает ``{"updated": [pk], "skipped_fsm" / "skipped_important" /
    "skipped_missing" / "skipped_transferred": [VIN]}``.
    """
    from django.utils import timezone

    from core.models import Car, Container
    from core.models.containers import ALLOWED_STATUS_TRANSITIONS
    from core.models.status_history import CarStatusHistory

    result = {
        "updated": [],
        "skipped_fsm": [],
        "skipped_important": [],
        "skipped_missing": [],
        "skipped_transferred": [],
    }
    car_ids = list(car_ids)
    if not car_ids:
        return result

    today = timezone.localdate()
    by_date: dict = {}
    history = []
    transferred_with_client = []
    returned_container_ids = set()
    transferred_container_ids = set()
    touched_transfer = False

    with transaction.atomic():
        rows = (
            Car.objects.select_for_update()
            .filter(pk__in=car_ids)
            .order_by("pk")
            .values(
                "pk",
                "vin",
                "status",
                "is_important",
                "warehouse_id",
                "unload_date",
                "transfer_date",
                "container_id",
                "client_id",
            )
        )
        for row in rows:
            current = row["status"]
            if current == target:
                continue
            if skip_transferred and current == "TRANSFERRED":
                result["skipped_transferred"].append(row["vin"])
                continue
            if row["is_important"]:
                result["skipped_important"].append(row["vin"])
                continue
            if target not in ALLOWED_STATUS_TRANSITIONS.get(current, set()):
                result["skipped_fsm"].append(row["vin"])
                continue
            if require_unload_info and target == "UNLOADED" and not (row["warehouse_id"] and row["unload_date"]):
                result["skipped_missing"].append(row["vin"])
                continue

            new_transfer_date = row["transfer_date"]
            if target == "TRANSFERRED":
                new_transfer_date = transfer_date or row["transfer_date"] or today
                if row["unload_date"] and new_transfer_date < row["unload_date"]:
                    new_transfer_date = row["unload_date"]
                touched_transfer = True
                if row["container_id"]:
                    transferred_container_ids.add(row["container_id"])
                if row["client_id"]:
                    transferred_with_client.append(row["pk"])
            elif current == "TRANSFERRED":
                new_transfer_date = None
                touched_transfer = True
                if row["container_id"]:
                    returned_container_ids.add(row["container_id"])

            by_date.setdefault(new_transfer_date, []).append(row["pk"])
            result["updated"].append(row["pk"])
            history.append(
                CarStatusHistory(car_id=row["pk"], status=target, previous_status=current, source=source, note=note)
            )

        for new_transfer_date, pks in by_date.items():
            Car.objects.filter(pk__in=pks).update(status=target, transfer_date=new_transfer_date)
        if history:
            CarStatusHistory.objects.bulk_create(history)

        for container in Container.objects.filter(pk__in=transferred_container_ids):
            container.check_and_update_status_from_cars()
        for container in Container.objects.filter(pk__in=returned_container_ids):
            container.reopen_if_not_all_transferred()

    updated = result["updated"]
    if updated:
        logger.info(
            "transition_cars → %s: updated=%d skipped(fsm=%d important=%d missing=%d transferred=%d) %s",
            target,
            len(updated),
            len(result["skipped_fsm"]),
            len(result["skipped_important"]),
            len(result["skipped_missing"]),
            len(result["skipped_transferred"]),
            note,
        )
        _after_transition(updated, finalize=finalize and touched_transfer, recalc=finalize)
        _notify_transferred(transferred_with_client)
    return result


def _after_transition(car_ids, *, finalize: bool, recalc: bool) -> None:
    """Пересчёт после bulk-перехода: при передаче/откате — finalize-задача
    (хранение по transfer_date, затем реген DRAFT-счетов), иначе пересчёт цен."""
    if finalize:
        from core.tasks import finalize_cars_transfer_task

        def _run():
            try:
                finalize_cars_transfer_task.delay(car_ids)
            except Exception as exc:
                logger.warning("Celery unavailable, finalizing transfer inline: %s", exc)
                finalize_cars_transfer_task(car_ids)

        transaction.on_commit(_run)
    elif recalc:
        from core.signals.service_catalog import _enqueue_recalc_cars_total_price

        _enqueue_recalc_cars_total_price(car_ids)


def _notify_transferred(car_ids) -> None:
    if not car_ids:
        return

    def _queue():
        from core.tasks import send_car_transferred_notification_task

        for car_id in car_ids:
            try:
                send_car_transferred_notification_task.delay(car_id)
            except Exception as exc:
                logger.warning("[client_notify] Celery недоступен для авто %s: %s", car_id, exc)

    transaction.on_commit(_queue)


def after_car_save(car, *, is_new: bool = False) -> None:
    """
    Central entry-point for post-save side-effects.

    Called from ``Car.save()`` after ``super().save()``.
    """
    if not car.pk:
        return

    recalculate_car_price(car)
    check_container_status(car)
    send_car_ws_notification(car)
