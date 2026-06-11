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


def sync_warehouse_to_cars(container) -> None:
    """Синхронизировать склад контейнера на все его авто."""
    try:
        logger.info("Warehouse changed for container %s, syncing cars...", container.id)
        container.sync_cars_after_warehouse_change()
        logger.info("Successfully synced warehouse for %s cars", container.container_cars.count())
    except Exception as e:
        logger.error("Failed to sync cars after warehouse change for container %s: %s", container.id, e)


def bulk_update_car_statuses(container) -> None:
    """Проставить статус контейнера всем его авто."""
    try:
        logger.info("Status changed for container %s to %s, bulk updating all cars...", container.id, container.status)
        updated_count = container.container_cars.update(status=container.status)
        logger.info(
            "Updated status to '%s' for %s cars in container %s", container.status, updated_count, container.number
        )
    except Exception as e:
        logger.error("Failed to update car statuses for container %s: %s", container.id, e)


def apply_unload_date_change(container) -> None:
    """Каскад при изменении даты разгрузки: обновить дату/дни/хранение/цену
    у всех авто контейнера и запланировать регенерацию инвойсов (Celery)."""
    try:
        logger.info(
            "Unload date changed for container %s to %s, bulk updating all cars...", container.id, container.unload_date
        )
        container.refresh_from_db()

        with signals_disabled(*CAR_SIGNALS):
            cars_to_update = []
            update_fields = ["unload_date", "days", "storage_cost", "total_price"]

            for car in container.container_cars.select_related("warehouse").all():
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


def apply_ths_change(container, changed_data) -> None:
    """Каскад при изменении THS-параметров (line/ths/ths_payer/warehouse):
    пересоздать/удалить THS-услуги, применить тарифы клиентов, пересчитать
    цены авто и регенерировать затронутые инвойсы."""
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

        with signals_disabled(*(CAR_SIGNALS + INVOICE_SIGNALS)):
            if "line" in changed_data:
                updated_count = container.container_cars.update(line=container.line)
                logger.info("[TIMING] Line updated for %s cars", updated_count)

            if container.line and container.ths:
                created_count = create_ths_services_for_container(container)
                logger.info("[TIMING] Created %s THS services with proportional distribution", created_count)
                apply_client_tariffs_for_container(container)
            else:
                car_ids = list(container.container_cars.values_list("id", flat=True))
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
            for car in container.container_cars.select_related("warehouse").all():
                car.update_days_and_storage()
                car.calculate_total_price()
                cars_to_update.append(car)
                from core.mixins import REGENERATABLE_INVOICE_STATUSES

                for invoice in NewInvoice.objects.filter(cars=car, status__in=REGENERATABLE_INVOICE_STATUSES):
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


def apply_post_save_cascades(container, *, changed_data, is_change, status_auto_changed) -> None:
    """Единая точка post-save каскадов контейнера (вызывается из админки
    после сохранения объекта в БД)."""
    if is_change and "warehouse" in changed_data:
        sync_warehouse_to_cars(container)

    status_changed_by_user = is_change and "status" in changed_data
    status_changed_auto = is_change and status_auto_changed
    if status_changed_by_user or status_changed_auto:
        bulk_update_car_statuses(container)

    if is_change and "unload_date" in changed_data:
        apply_unload_date_change(container)

    ths_related_changed = any(f in changed_data for f in ["line", "ths", "ths_payer", "warehouse"])
    should_create_ths = (not is_change and container.line and container.ths) or (is_change and ths_related_changed)
    if should_create_ths:
        apply_ths_change(container, changed_data)
