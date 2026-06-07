"""Сигналы для модели ``Car``:

* ``pre_save`` — снимок старых значений по нужным полям, чтобы
  ``post_save`` знал, что именно поменялось (статус, контрактники,
  ``unload_date``, ``container_id``, ``is_important``).
* ``post_save`` — единый обработчик :func:`car_post_save`, который
  выполняет в одном месте семь связанных задач (см. docstring внутри).

Ранее пункты «пересчёт ``total_price``» и «WS-нотификация» жили в
``car_lifecycle_service.after_car_save``, который вызывался из
``Car.save()``. Это создавало два параллельных пост-сейв пути и удваивало
работу. После рефакторинга всё консолидировано здесь.

Импортируем :func:`_enqueue_recalc_cars_total_price` из
:mod:`core.signals.service_catalog` и
:func:`_update_container_status_if_all_transferred` из
:mod:`core.signals.autotransport`, чтобы избежать дублирования логики.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import (
    Car,
    CarService,
    DeletedCarService,
)
from core.service_codes import ServiceCode, is_storage_service

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Car)
def save_old_car_values(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None:
        tracked = {"warehouse_id", "line_id", "carrier_id", "unload_date", "container_id", "status", "is_important"}
        if not tracked.intersection(update_fields):
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
            instance._pre_save_status = None
            instance._pre_save_is_important = None
            return

    if instance.pk:
        try:
            old = (
                Car.objects.filter(pk=instance.pk)
                .values(
                    "warehouse_id", "line_id", "carrier_id", "unload_date", "container_id", "status", "is_important"
                )
                .first()
            )
            if old:
                instance._pre_save_contractors = {
                    "warehouse_id": old["warehouse_id"],
                    "line_id": old["line_id"],
                    "carrier_id": old["carrier_id"],
                }
                instance._pre_save_car_notification = {
                    "unload_date": old["unload_date"],
                    "container_id": old["container_id"],
                }
                instance._pre_save_status = old["status"]
                instance._pre_save_is_important = old["is_important"]
            else:
                instance._pre_save_contractors = None
                instance._pre_save_car_notification = None
                instance._pre_save_status = None
                instance._pre_save_is_important = None
        except Exception:
            instance._pre_save_contractors = None
            instance._pre_save_car_notification = None
            instance._pre_save_status = None
            instance._pre_save_is_important = None
    else:
        instance._pre_save_contractors = None
        instance._pre_save_car_notification = None
        instance._pre_save_status = None
        instance._pre_save_is_important = None


def _car_pricing_relevant_changed(instance, created):
    """True, если у машины изменились поля, влияющие на позиции инвойса.

    Ценообразующими считаем контрактников (``warehouse``/``line``/``carrier``,
    определяют набор и цены услуг) и ``unload_date`` (влияет на дни хранения).
    Снимок старых значений делает :func:`save_old_car_values` в ``pre_save``.

    Логика:
    * новая машина (``created``) → всегда True;
    * ``pre_save`` не снял старые значения (``save(update_fields=...)`` без
      ценообразующих полей) → False, эти поля точно не менялись;
    * иначе сравниваем старые значения с текущими.
    """
    if created:
        return True

    old_contractors = getattr(instance, "_pre_save_contractors", None)
    old_notification = getattr(instance, "_pre_save_car_notification", None)

    if old_contractors is None and old_notification is None:
        return False

    if old_contractors and (
        old_contractors.get("warehouse_id") != instance.warehouse_id
        or old_contractors.get("line_id") != instance.line_id
        or old_contractors.get("carrier_id") != instance.carrier_id
    ):
        return True

    if old_notification and old_notification.get("unload_date") != instance.unload_date:
        return True

    return False


@receiver(post_save, sender=Car)
def car_post_save(sender, instance, **kwargs):
    """Consolidated post_save handler for Car.

    Responsibilities (in order):

    1. Create/update CarService records when contractors change.
    2. Deferred invoice regeneration.
    3. Email notifications (standalone cars).
    4. Container status auto-update on transfer.
    5. Task auto-create on is_important transition.
    6. Recalculate car total_price/days/storage_cost (deferred to Celery).
    7. WebSocket update notification.

    Ранее пункты 6 и 7 жили в ``car_lifecycle_service.after_car_save``,
    который вызывался из ``Car.save()``. Это создавало два параллельных
    пост-сейв пути и удваивало работу. Теперь всё в одном месте.
    """
    # Import in-function чтобы не ловить цикл при загрузке пакета
    # (autotransport.py импортирует из этого же пакета и грузится позже).
    from core.signals.autotransport import _update_container_status_if_all_transferred
    from core.signals.service_catalog import _enqueue_recalc_cars_total_price

    if not instance.pk:
        return

    created = kwargs.get("created", False)

    # --- 1. Invoice regeneration ---
    # Единый путь регенерации: делегируем в `car_service._deferred_invoice_regeneration`,
    # который ставит Celery-задачу `regenerate_invoices_for_car_task` (с inline-fallback
    # при недоступности брокера) и фильтрует инвойсы по `REGENERATABLE_INVOICE_STATUSES`.
    # Раньше здесь был отдельный СИНХРОННЫЙ путь (`_deferred_invoice_regeneration_for_car`)
    # — он дублировал логику и не дедуплицировался с путём от CarService. Теперь оба
    # триггера (Car.save и CarService.save) делят один thread-local bucket и одну задачу.
    #
    # Регенерируем только когда изменились ценообразующие поля машины
    # (контрактники / unload_date) либо машина только что создана — иначе любое
    # сохранение Car (комментарий, is_important) лишний раз пересчитывало бы инвойсы.
    if _car_pricing_relevant_changed(instance, created):
        from core.signals.car_service import _deferred_invoice_regeneration

        _deferred_invoice_regeneration(instance.pk)

    # --- 2. CarService creation [COMMAND/orchestration] ---
    _create_car_services_if_needed(instance, created=created, kwargs=kwargs)

    # --- 3. Email notification for standalone cars [EVENT] ---
    _maybe_send_car_unload_notification(instance, created=created)

    # --- 4. Container status auto-update [COMMAND/denorm] ---
    old_status = getattr(instance, "_pre_save_status", None)
    instance._pre_save_status = None
    if instance.status == "TRANSFERRED" and old_status != "TRANSFERRED" and instance.container_id:
        _update_container_status_if_all_transferred(instance.container_id)

    # --- 5. Авто-создание/закрытие "Дела" по флагу is_important [COMMAND/orchestration] ---
    _handle_car_important_transition(instance, created=created)

    # --- 6. Recalculate total_price / days / storage_cost (deferred to Celery) [COMMAND/denorm] ---
    # Раньше делалось синхронно в `Car.save()` через `after_car_save()`.
    # Теперь — в фоне через ту же таску что и для catalog-changes, с
    # graceful inline-fallback если broker лежит.
    if not getattr(instance, "_bulk_updating", False) and not getattr(instance, "_creating_services", False):
        _enqueue_recalc_cars_total_price([instance.pk])

    # --- 7. WebSocket data_update notification [EVENT] ---
    # Фаза 3: единый источник EVENT-нотификации — сервис; сигнал делегирует.
    from core.services.car_lifecycle_service import send_car_ws_notification

    send_car_ws_notification(instance)


def _handle_car_important_transition(car, *, created: bool):
    """Синхронизирует ``Task`` с состоянием ``Car.is_important``.

    * False → True: создаём авто-дело (если ещё не открыто).
    * True → False: закрываем все открытые авто-дела этой машины.

    Старое значение берём из ``_pre_save_is_important``, выставленного в
    pre_save; для новых записей считаем «старое» = False.
    """
    from core.models import Task

    old_is_important = getattr(car, "_pre_save_is_important", None)
    car._pre_save_is_important = None
    if created:
        old_is_important = False

    new_is_important = bool(car.is_important)

    # Нет перехода — ничего не делаем.
    if old_is_important == new_is_important:
        return

    if new_is_important:
        # False → True: открываем дело, если ещё нет открытого авто-дела.
        existing = Task.objects.filter(car=car, auto_created=True, is_completed=False).first()
        if existing:
            return
        title = f"Важное: {car.brand} ({car.vin})"
        description = car.notes or ""
        Task.objects.create(
            car=car,
            title=title[:200],
            description=description,
            auto_created=True,
            priority="HIGH",
        )
        logger.info("Task auto-created for car %s (is_important set)", car.vin)
    else:
        # True → False: пользователь снял галочку = «действие выполнено».
        open_tasks = Task.objects.filter(car=car, auto_created=True, is_completed=False)
        now = timezone.now()
        updated = open_tasks.update(
            is_completed=True,
            completed_at=now,
            updated_at=now,
        )
        if updated:
            logger.info("Auto-completed %d task(s) for car %s (is_important unset)", updated, car.vin)


def _maybe_send_car_unload_notification(instance, *, created):
    """Send unload notification for standalone (non-container) cars."""
    if instance.container_id:
        return
    old_values = getattr(instance, "_pre_save_car_notification", None) or {}
    instance._pre_save_car_notification = None
    if old_values.get("container_id"):
        return
    old_unload_date = old_values.get("unload_date")
    if instance.unload_date and (created or old_unload_date is None):

        def _enqueue():
            try:
                from core.tasks import send_car_unload_notification_task

                send_car_unload_notification_task.delay(instance.pk)
            except Exception:
                from core.services.email_service import CarNotificationService
                from core.services.telegram_service import TelegramNotificationService

                if not TelegramNotificationService.was_car_unload_notification_sent(instance):
                    TelegramNotificationService.send_car_unload_notification(instance)
                if not CarNotificationService.was_car_unload_notification_sent(instance):
                    CarNotificationService.send_car_unload_notification(instance)

        transaction.on_commit(_enqueue)


def _create_car_services_if_needed(instance, *, created, kwargs):
    """Create ``CarService`` records when a car's contractors change.

    Only recreates services for the contractor type that actually changed,
    preserving services (and manual edits) of unaffected types.
    """
    from core.models import LineService
    from core.services.car_service_manager import (
        find_carrier_services_for_car,
        find_company_services_for_car,
        find_line_services_for_car,
        find_warehouse_services_for_car,
        get_main_company,
    )

    if not instance.pk:
        return
    if getattr(instance, "_creating_services", False):
        return

    warehouse_changed = False
    line_changed = False
    carrier_changed = False

    if not created:
        old_contractors = getattr(instance, "_pre_save_contractors", None)
        instance._pre_save_contractors = None
        if old_contractors:
            warehouse_changed = old_contractors.get("warehouse_id") != instance.warehouse_id
            line_changed = old_contractors.get("line_id") != instance.line_id
            carrier_changed = old_contractors.get("carrier_id") != instance.carrier_id
            if not (warehouse_changed or line_changed or carrier_changed):
                return
        else:
            return
    else:
        warehouse_changed = True
        line_changed = True
        carrier_changed = True

    instance._creating_services = True
    try:
        deleted_by_type = {}
        for stype in ("WAREHOUSE", "LINE", "CARRIER", "COMPANY"):
            deleted_by_type[stype] = set(
                DeletedCarService.objects.filter(car=instance, service_type=stype).values_list("service_id", flat=True)
            )

        # WAREHOUSE — only when warehouse actually changed
        if warehouse_changed:
            instance.car_services.filter(service_type="WAREHOUSE").delete()
            if instance.warehouse:
                for service in find_warehouse_services_for_car(instance.warehouse):
                    if service.id in deleted_by_type["WAREHOUSE"]:
                        continue
                    if is_storage_service(service):
                        days = Decimal(str(instance.days or 0))
                        custom_price = days * Decimal(str(service.default_price or 0))
                        default_markup = days * Decimal(str(getattr(service, "default_markup", 0) or 0))
                    else:
                        custom_price = service.default_price
                        default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type="WAREHOUSE",
                        service_id=service.id,
                        defaults={"custom_price": custom_price, "markup_amount": default_markup},
                    )

        # LINE (non-THS only; THS managed by create_ths_services_for_container)
        # Only when line actually changed
        if line_changed:
            from django.db.models import Q

            ths_line_ids = LineService.objects.filter(Q(code=ServiceCode.THS) | Q(name__icontains="THS")).values_list(
                "id", flat=True
            )
            instance.car_services.filter(service_type="LINE").exclude(service_id__in=ths_line_ids).delete()
            if instance.line:
                for service in find_line_services_for_car(instance.line):
                    if service.id in deleted_by_type["LINE"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type="LINE",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )

        # CARRIER — only when carrier actually changed
        if carrier_changed:
            instance.car_services.filter(service_type="CARRIER").delete()
            if instance.carrier:
                for service in find_carrier_services_for_car(instance.carrier):
                    if service.id in deleted_by_type["CARRIER"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type="CARRIER",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )

        # COMPANY (only for newly created cars)
        if created:
            main_company = get_main_company()
            if main_company:
                for service in find_company_services_for_car(main_company):
                    if service.id in deleted_by_type["COMPANY"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=instance,
                        service_type="COMPANY",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )
    except Exception as e:
        logger.error("Error creating car services: %s", e)
    finally:
        instance._creating_services = False
