"""
Сервис жизненного цикла контейнера.

Бизнес-логика каскадов, которая раньше жила в
``ContainerAdmin._save_model_inner``, вынесена сюда и декаплена от
``request`` — на вход подаётся сам контейнер + ``changed_data`` формы и
флаги. Это собирает «что происходит при сохранении контейнера» в одном
тестируемом месте.

Поведение полностью повторяет прежний код админки (характеризующие тесты
+ полный прогон гарантируют отсутствие регрессий).
"""

from __future__ import annotations

import logging
import time

from core.models import Car, CarService, LineService, WarehouseService
from core.services.cascade_control import CAR_SIGNALS, INVOICE_SIGNALS, signals_disabled

logger = logging.getLogger(__name__)


def _cars_for_cascade(container, include_transferred: bool):
    """Авто контейнера, которых касается каскад (B11: без TRANSFERRED по умолчанию)."""
    qs = container.container_cars.select_related("warehouse")
    if not include_transferred:
        qs = qs.exclude(status="TRANSFERRED")
    return qs


def sync_warehouse_to_cars(container, *, include_transferred: bool = False) -> None:
    """Синхронизировать склад контейнера на его авто (переданные — только явно)."""
    try:
        logger.info("Warehouse changed for container %s, syncing cars...", container.id)
        container.sync_cars_after_warehouse_change(include_transferred=include_transferred)
        logger.info("Successfully synced warehouse for container %s", container.number)
    except Exception as e:
        logger.error("Failed to sync cars after warehouse change for container %s: %s", container.id, e)


def bulk_update_car_statuses(container) -> dict:
    """Каскад статуса контейнера на авто — через FSM (B3).

    Раньше ``container_cars.update(status=...)`` переписывал статус всем
    машинам: откат контейнера UNLOADED → IN_PORT возвращал уже TRANSFERRED
    авто «на склад» — снова шло хранение, ломались инвойсы. Теперь, как в
    ``CarAdmin._bulk_set_status``:

    * применяются только переходы из ``Car.ALLOWED_STATUS_TRANSITIONS``;
    * TRANSFERRED авто не трогаем вообще, кроме случая, когда и контейнер
      становится TRANSFERRED (откат передачи — осознанное действие по
      конкретному авто, а не побочный эффект статуса контейнера);
    * авто с пометкой «Важное» пропускаются (как в ``Car.save``);
    * при переходе в TRANSFERRED проставляется ``transfer_date`` (как
      делает ``Car._sync_status_and_dates``), иначе хранение не остановится.

    Возвращает счётчики ``{"updated", "skipped_fsm", "skipped_transferred",
    "skipped_important"}`` (списки VIN) и дублирует их в
    ``container._status_cascade_result`` — админка может показать
    пользователю, какие авто не переведены.
    """
    from django.utils import timezone

    from core.models.containers import ALLOWED_STATUS_TRANSITIONS

    target = container.status
    result = {"updated": 0, "skipped_fsm": [], "skipped_transferred": [], "skipped_important": []}
    try:
        logger.info("Status changed for container %s to %s, cascading to cars via FSM...", container.id, target)
        allowed_pks = []
        for pk, vin, cur_status, is_important in container.container_cars.values_list(
            "pk", "vin", "status", "is_important"
        ):
            if cur_status == target:
                continue
            if cur_status == "TRANSFERRED" and target != "TRANSFERRED":
                result["skipped_transferred"].append(vin)
                continue
            if is_important:
                result["skipped_important"].append(vin)
                continue
            if target not in ALLOWED_STATUS_TRANSITIONS.get(cur_status, set()):
                result["skipped_fsm"].append(vin)
                continue
            allowed_pks.append(pk)

        if allowed_pks:
            qs = Car.objects.filter(pk__in=allowed_pks)
            if target == "TRANSFERRED":
                today = timezone.now().date()
                qs.filter(transfer_date__isnull=True).update(status=target, transfer_date=today)
                qs.filter(transfer_date__isnull=False).update(status=target)
                result["updated"] = len(allowed_pks)
            else:
                result["updated"] = qs.update(status=target)

        logger.info(
            "Container %s → '%s': cars updated=%s, skipped(fsm=%s, transferred=%s, important=%s)",
            container.number,
            target,
            result["updated"],
            len(result["skipped_fsm"]),
            len(result["skipped_transferred"]),
            len(result["skipped_important"]),
        )
        if result["skipped_fsm"] or result["skipped_transferred"] or result["skipped_important"]:
            logger.warning(
                "Container %s → '%s': не переведены авто — FSM: %s; TRANSFERRED: %s; «Важное»: %s",
                container.number,
                target,
                ", ".join(result["skipped_fsm"]) or "—",
                ", ".join(result["skipped_transferred"]) or "—",
                ", ".join(result["skipped_important"]) or "—",
            )
    except Exception as e:
        logger.error("Failed to update car statuses for container %s: %s", container.id, e)
    container._status_cascade_result = result
    return result


def status_cascade_messages(result: dict) -> list[str]:
    """Человекочитаемые предупреждения по результату ``bulk_update_car_statuses``
    (для ``messages.warning`` в админке контейнера)."""
    msgs = []
    if result.get("skipped_transferred"):
        msgs.append(
            "Переданные авто не тронуты (откат передачи — только в карточке авто): "
            + ", ".join(result["skipped_transferred"])
        )
    if result.get("skipped_fsm"):
        msgs.append("Пропущены (недопустимый переход статуса): " + ", ".join(result["skipped_fsm"]))
    if result.get("skipped_important"):
        msgs.append("Пропущены авто с пометкой «Важное»: " + ", ".join(result["skipped_important"]))
    return msgs


def apply_unload_date_change(container, *, include_transferred: bool = False) -> None:
    """Каскад при изменении даты разгрузки: обновить дату/дни/хранение/цену
    у авто контейнера и запланировать регенерацию инвойсов (Celery).

    B11: переданные авто по умолчанию не трогаем — их период хранения
    закрыт transfer_date и уже в счёте."""
    try:
        logger.info(
            "Unload date changed for container %s to %s, bulk updating cars...", container.id, container.unload_date
        )
        container.refresh_from_db()

        with signals_disabled(*CAR_SIGNALS):
            cars_to_update = []
            update_fields = ["unload_date", "days", "storage_cost", "total_price"]

            for car in _cars_for_cascade(container, include_transferred):
                car.unload_date = container.unload_date
                if not container.unload_date and car.status == "UNLOADED":
                    car.status = container.status or "IN_PORT"
                    if "status" not in update_fields:
                        update_fields.append("status")
                car.update_days_and_storage()
                car.calculate_total_price()
                cars_to_update.append(car)

            if cars_to_update:
                Car.objects.bulk_update(cars_to_update, update_fields, batch_size=50)
                logger.info("Bulk updated %s cars in container %s", len(cars_to_update), container.number)

        # Регенерацию инвойсов выносим из HTTP в Celery (on_commit,
        # дедупликация по car_id).
        if cars_to_update:
            from core.signals.car_service import _deferred_invoice_regeneration

            for car in cars_to_update:
                _deferred_invoice_regeneration(car.id)

    except Exception as e:
        logger.error("Failed to update cars after unload_date change for container %s: %s", container.id, e)


def apply_ths_change(container, changed_data, *, include_transferred: bool = False) -> None:
    """Каскад при изменении THS-параметров (line/ths/ths_payer/warehouse):
    пересоздать/удалить THS-услуги, применить тарифы клиентов, пересчитать
    цены авто и регенерировать затронутые инвойсы.

    B11: TRANSFERRED авто заморожены — их услуги/наценки/цены не
    переписываются (доли THS по-прежнему считаются по всему контейнеру).
    ``include_transferred=True`` — явное «применить и к переданным»."""
    line_start = time.time()
    try:
        from core.models_billing import NewInvoice
        from core.services.car_service_manager import (
            apply_client_tariffs_for_container,
            create_ths_services_for_container,
        )

        logger.info(
            "[TIMING] THS-related change started for container %s, line: %s, ths: %s, ths_payer: %s",
            container.id,
            container.line,
            container.ths,
            container.ths_payer,
        )

        cascade_cars = _cars_for_cascade(container, include_transferred)

        with signals_disabled(*(CAR_SIGNALS + INVOICE_SIGNALS)):
            if "line" in changed_data:
                updated_count = cascade_cars.update(line=container.line)
                logger.info("[TIMING] Line updated for %s cars", updated_count)

            if container.line and container.ths:
                created_count = create_ths_services_for_container(container, include_transferred=include_transferred)
                logger.info("[TIMING] Created %s THS services with proportional distribution", created_count)
                apply_client_tariffs_for_container(container, include_transferred=include_transferred)
            else:
                car_ids = list(cascade_cars.values_list("id", flat=True))
                deleted_line = (
                    CarService.objects.filter(car_id__in=car_ids, service_type="LINE")
                    .filter(
                        service_id__in=LineService.objects.filter(name__icontains="THS").values_list("id", flat=True)
                    )
                    .delete()
                )
                deleted_wh = (
                    CarService.objects.filter(car_id__in=car_ids, service_type="WAREHOUSE")
                    .filter(
                        service_id__in=WarehouseService.objects.filter(name__icontains="THS").values_list(
                            "id", flat=True
                        )
                    )
                    .delete()
                )
                logger.info(
                    "[TIMING] Deleted %s line THS and %s warehouse THS services", deleted_line[0], deleted_wh[0]
                )

            cars_to_update = []
            affected_invoices = set()
            for car in cascade_cars.all():
                car.update_days_and_storage()
                car.calculate_total_price()
                cars_to_update.append(car)
                from core.mixins import AUTO_REGENERATABLE_INVOICE_STATUSES

                for invoice in NewInvoice.objects.filter(cars=car, status__in=AUTO_REGENERATABLE_INVOICE_STATUSES):
                    affected_invoices.add(invoice)

            if cars_to_update:
                Car.objects.bulk_update(cars_to_update, ["days", "storage_cost", "total_price"], batch_size=50)
                logger.info("[TIMING] Recalculated prices for %s cars", len(cars_to_update))

            if affected_invoices:
                logger.info("[TIMING] Updating %s affected invoices...", len(affected_invoices))
                for invoice in affected_invoices:
                    try:
                        invoice.regenerate_items_from_cars()
                    except Exception as e:
                        logger.error("Error updating invoice %s: %s", invoice.number, e)
                logger.info("[TIMING] Invoices updated")

            logger.info("[TIMING] THS-related change completed in %.2fs", time.time() - line_start)

    except Exception as e:
        logger.error("Failed to update cars after line change for container %s: %s", container.id, e, exc_info=True)


def apply_post_save_cascades(
    container, *, changed_data, is_change, status_auto_changed, include_transferred: bool = False
) -> dict | None:
    """Единая точка post-save каскадов контейнера (вызывается из админки
    после сохранения объекта в БД).

    ``include_transferred`` (B11) — применять каскады склада / даты
    разгрузки / THS и к уже переданным авто (явное действие оператора);
    по умолчанию переданные заморожены. Каскад статуса этим флагом не
    управляется — он всегда идёт через FSM (см. ``bulk_update_car_statuses``).

    Возвращает результат каскада статуса (см. ``bulk_update_car_statuses``)
    или ``None``, если статус не менялся — админка может показать
    предупреждения через ``status_cascade_messages``.
    """
    status_result = None
    if is_change and "warehouse" in changed_data:
        sync_warehouse_to_cars(container, include_transferred=include_transferred)

    status_changed_by_user = is_change and "status" in changed_data
    status_changed_auto = is_change and status_auto_changed
    if status_changed_by_user or status_changed_auto:
        status_result = bulk_update_car_statuses(container)

    if is_change and "unload_date" in changed_data:
        apply_unload_date_change(container, include_transferred=include_transferred)

    ths_related_changed = any(f in changed_data for f in ["line", "ths", "ths_payer", "warehouse"])
    should_create_ths = (not is_change and container.line and container.ths) or (is_change and ths_related_changed)
    if should_create_ths:
        apply_ths_change(container, changed_data, include_transferred=include_transferred)

    return status_result
