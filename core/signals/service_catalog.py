"""Изменения каталогов услуг (Warehouse/Line/Carrier/Company) → массовые
обновления существующих ``CarService`` + пересчёт ``Car.total_price``.

Дополнительно — каскадное удаление ``CarService`` при удалении услуги
каталога (раньше FK был с PROTECT, сейчас удаляем явно).

Хелпер :func:`_enqueue_recalc_cars_total_price` живёт здесь, потому что
именно отсюда им пользуются все 4 receiver'а. Он же реэкспортируется
дальше — :mod:`core.signals.car` и :mod:`core.signals.container` тоже
вызывают его в своих обработчиках.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.models import (
    Car,
    CarrierService,
    CarService,
    CompanyService,
    LineService,
    WarehouseService,
)
from core.service_codes import is_storage_service

logger = logging.getLogger(__name__)


def _enqueue_recalc_cars_total_price(car_ids):
    """Поставить пересчёт Car.total_price в Celery; fallback inline.

    Раньше пересчёт делался синхронно в HTTP-потоке (N+1 SELECT + N UPDATE),
    при импорте 100+ машин это блокировало запрос. Теперь HTTP отдаёт ответ
    сразу, тяжёлая работа идёт в фоне с graceful inline-fallback при
    недоступности брокера.
    """
    if not car_ids:
        return
    car_ids = list({int(cid) for cid in car_ids if cid})

    def _dispatch():
        try:
            from core.tasks import recalculate_cars_total_price_task

            try:
                recalculate_cars_total_price_task.delay(car_ids)
            except Exception:
                logger.exception(
                    "Celery enqueue failed for recalculate_cars_total_price(%s ids) — running inline",
                    len(car_ids),
                )
                _recalc_cars_total_price_inline(car_ids)
        except Exception:
            logger.exception(
                "Failed to dispatch cars total_price recalc for %s ids",
                len(car_ids),
            )

    transaction.on_commit(_dispatch)


def _recalc_cars_total_price_inline(car_ids):
    """Синхронный fallback для ``recalculate_cars_total_price_task``.

    Обновляет те же три поля, что и Celery-таска: ``calculate_total_price()``
    через ``update_days_and_storage()`` меняет days/storage_cost, поэтому
    bulk_update тянет все три (раньше fallback сохранял только total_price,
    из-за чего при недоступном брокере days/storage_cost расходились с БД).
    """
    cars_to_update = []
    for car in Car.objects.filter(pk__in=car_ids).prefetch_related("car_services").select_related("warehouse"):
        car.calculate_total_price()
        cars_to_update.append(car)
    if cars_to_update:
        Car.objects.bulk_update(cars_to_update, ["total_price", "days", "storage_cost"], batch_size=200)


# ---------------------------------------------------------------------------
# Catalog change → bulk update of related CarService rows
# ---------------------------------------------------------------------------
#
# ``default_markup`` в каталоге — только для НОВЫХ CarService (при создании
# авто / добавлении услуги). Сохранение карточки склада/линии раньше
# массово перезаписывало ``markup_amount`` у всех существующих машин
# (включая переданные и с тарифной наценкой). Админский formset при этом
# save()'ит КАЖДУЮ строку, даже нетронутую — поэтому правка наценки у
# одной услуги склада обнуляла наценки остальных.
#
# Теперь: существующие машины трогаем только если изменилась цена
# (``custom_price``) или услуга выключена. ``markup_amount`` не трогаем.


_CATALOG_TRACK_FIELDS = ("default_price", "is_active")


def _stash_catalog_previous(sender, instance):
    """Снимок цены/активности до save — чтобы post_save знал, что реально изменилось."""
    instance._catalog_previous = None
    if not instance.pk:
        return
    instance._catalog_previous = sender.objects.filter(pk=instance.pk).values(*_CATALOG_TRACK_FIELDS).first()


def _price_actually_changed(instance) -> bool:
    prev = getattr(instance, "_catalog_previous", None)
    if prev is None:
        return False
    return Decimal(str(prev["default_price"] or 0)) != Decimal(str(instance.default_price or 0))


def _clear_catalog_previous(instance):
    instance._catalog_previous = None


@receiver(pre_save, sender=WarehouseService)
def warehouse_service_pre_save(sender, instance, **kwargs):
    _stash_catalog_previous(sender, instance)


@receiver(pre_save, sender=LineService)
def line_service_pre_save(sender, instance, **kwargs):
    _stash_catalog_previous(sender, instance)


@receiver(pre_save, sender=CarrierService)
def carrier_service_pre_save(sender, instance, **kwargs):
    _stash_catalog_previous(sender, instance)


@receiver(pre_save, sender=CompanyService)
def company_service_pre_save(sender, instance, **kwargs):
    _stash_catalog_previous(sender, instance)


def _delete_related_carservices(service_type, service_id):
    affected_car_ids = list(
        CarService.objects.filter(service_type=service_type, service_id=service_id).values_list("car_id", flat=True)
    )
    deleted, _ = CarService.objects.filter(service_type=service_type, service_id=service_id).delete()
    if deleted:
        _enqueue_recalc_cars_total_price(affected_car_ids)


@receiver(post_save, sender=WarehouseService)
def update_cars_on_warehouse_service_change(sender, instance, created, **kwargs):
    try:
        if not (instance.is_active and instance.default_price > 0):
            _delete_related_carservices("WAREHOUSE", instance.id)
            return
        if created or not _price_actually_changed(instance):
            return
        car_services = list(
            CarService.objects.filter(
                service_type="WAREHOUSE", service_id=instance.id, car__warehouse=instance.warehouse
            ).select_related("car")
        )
        if not car_services:
            return
        for cs in car_services:
            if is_storage_service(instance):
                days = Decimal(str(cs.car.days or 0))
                cs.custom_price = days * Decimal(str(instance.default_price or 0))
            else:
                cs.custom_price = instance.default_price
        CarService.objects.bulk_update(car_services, ["custom_price"], batch_size=100)
        _enqueue_recalc_cars_total_price([cs.car_id for cs in car_services])
    except Exception as e:
        logger.error("Error updating cars on warehouse service change: %s", e)
    finally:
        _clear_catalog_previous(instance)


@receiver(post_save, sender=LineService)
def update_cars_on_line_service_change(sender, instance, created, **kwargs):
    """При изменении ``default_price`` активной услуги линии — обновить
    ``custom_price`` связанных ``CarService`` (наценку не трогаем).
    Если услуга стала неактивной — удалить связанные ``CarService``.
    """
    try:
        if not (instance.is_active and instance.default_price > 0):
            _delete_related_carservices("LINE", instance.id)
            return
        if created or not _price_actually_changed(instance):
            return
        qs = CarService.objects.filter(service_type="LINE", service_id=instance.id, car__line=instance.line)
        affected_car_ids = list(qs.values_list("car_id", flat=True))
        qs.update(custom_price=instance.default_price)
        _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on line service change: %s", e)
    finally:
        _clear_catalog_previous(instance)


@receiver(post_save, sender=CarrierService)
def update_cars_on_carrier_service_change(sender, instance, created, **kwargs):
    try:
        if not (instance.is_active and instance.default_price > 0):
            _delete_related_carservices("CARRIER", instance.id)
            return
        if created or not _price_actually_changed(instance):
            return
        qs = CarService.objects.filter(service_type="CARRIER", service_id=instance.id, car__carrier=instance.carrier)
        affected_car_ids = list(qs.values_list("car_id", flat=True))
        qs.update(custom_price=instance.default_price)
        _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on carrier service change: %s", e)
    finally:
        _clear_catalog_previous(instance)


@receiver(post_save, sender=CompanyService)
def update_cars_on_company_service_change(sender, instance, created, **kwargs):
    try:
        qs = CarService.objects.filter(service_type="COMPANY", service_id=instance.id)
        if not (instance.is_active and instance.default_price > 0):
            affected_car_ids = list(qs.values_list("car_id", flat=True).distinct())
            deleted, _ = qs.delete()
            if deleted:
                _enqueue_recalc_cars_total_price(affected_car_ids)
            return
        if created or not _price_actually_changed(instance):
            return
        affected_car_ids = list(qs.values_list("car_id", flat=True).distinct())
        qs.update(custom_price=instance.default_price)
        _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on company service change: %s", e)
    finally:
        _clear_catalog_previous(instance)


# ---------------------------------------------------------------------------
# Cascade delete of CarService when catalog service is deleted
# ---------------------------------------------------------------------------


@receiver(pre_delete, sender=LineService)
def delete_car_services_on_line_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type="LINE", service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on LineService delete: %s", e)


@receiver(pre_delete, sender=WarehouseService)
def delete_car_services_on_warehouse_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type="WAREHOUSE", service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on WarehouseService delete: %s", e)


@receiver(pre_delete, sender=CarrierService)
def delete_car_services_on_carrier_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type="CARRIER", service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on CarrierService delete: %s", e)


@receiver(pre_delete, sender=CompanyService)
def delete_car_services_on_company_service_delete(sender, instance, **kwargs):
    try:
        CarService.objects.filter(service_type="COMPANY", service_id=instance.id).delete()
    except Exception as e:
        logger.error("Error deleting CarService on CompanyService delete: %s", e)
