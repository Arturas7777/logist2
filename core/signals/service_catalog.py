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
from django.db.models.signals import post_save, pre_delete
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


@receiver(post_save, sender=WarehouseService)
def update_cars_on_warehouse_service_change(sender, instance, **kwargs):
    try:
        if instance.is_active and instance.default_price > 0:
            car_services = list(
                CarService.objects.filter(
                    service_type="WAREHOUSE", service_id=instance.id, car__warehouse=instance.warehouse
                ).select_related("car")
            )
            if not car_services:
                return
            default_markup_val = getattr(instance, "default_markup", None) or Decimal("0")
            for cs in car_services:
                if is_storage_service(instance):
                    days = Decimal(str(cs.car.days or 0))
                    cs.custom_price = days * Decimal(str(instance.default_price or 0))
                    cs.markup_amount = days * Decimal(str(default_markup_val))
                else:
                    cs.custom_price = instance.default_price
                    cs.markup_amount = default_markup_val
            CarService.objects.bulk_update(car_services, ["custom_price", "markup_amount"], batch_size=100)
            _enqueue_recalc_cars_total_price([cs.car_id for cs in car_services])
        else:
            affected_car_ids = list(
                CarService.objects.filter(service_type="WAREHOUSE", service_id=instance.id).values_list(
                    "car_id", flat=True
                )
            )
            CarService.objects.filter(service_type="WAREHOUSE", service_id=instance.id).delete()
            _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on warehouse service change: %s", e)


@receiver(post_save, sender=LineService)
def update_cars_on_line_service_change(sender, instance, **kwargs):
    """Симметрично с Warehouse/Carrier/CompanyService:
    при изменении ``default_price/markup`` активной услуги линии — обновить
    цены всех ``CarService`` этого типа на машинах нужной линии и
    пересчитать ``Car.total_price``. Если услуга стала неактивной —
    удалить связанные ``CarService``.

    Раньше LineService обновлял только при ``is_active=False``, и при
    ручной правке прайса в каталоге цены машин «зависали» на старой
    ``default_price`` до явного перерасчёта в админке Car — это и
    фиксирует пункт #14 плана.
    """
    try:
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, "default_markup", None) or Decimal("0")
            affected_car_ids = list(
                CarService.objects.filter(
                    service_type="LINE", service_id=instance.id, car__line=instance.line
                ).values_list("car_id", flat=True)
            )
            CarService.objects.filter(service_type="LINE", service_id=instance.id, car__line=instance.line).update(
                custom_price=instance.default_price, markup_amount=default_markup
            )
            _enqueue_recalc_cars_total_price(affected_car_ids)
        else:
            affected_car_ids = list(
                CarService.objects.filter(service_type="LINE", service_id=instance.id).values_list("car_id", flat=True)
            )
            deleted = CarService.objects.filter(service_type="LINE", service_id=instance.id).delete()
            if deleted[0] > 0:
                _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on line service change: %s", e)


@receiver(post_save, sender=CarrierService)
def update_cars_on_carrier_service_change(sender, instance, **kwargs):
    try:
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, "default_markup", None) or Decimal("0")
            affected_car_ids = list(
                CarService.objects.filter(
                    service_type="CARRIER", service_id=instance.id, car__carrier=instance.carrier
                ).values_list("car_id", flat=True)
            )
            CarService.objects.filter(
                service_type="CARRIER", service_id=instance.id, car__carrier=instance.carrier
            ).update(custom_price=instance.default_price, markup_amount=default_markup)
            _enqueue_recalc_cars_total_price(affected_car_ids)
        else:
            affected_car_ids = list(
                CarService.objects.filter(service_type="CARRIER", service_id=instance.id).values_list(
                    "car_id", flat=True
                )
            )
            CarService.objects.filter(service_type="CARRIER", service_id=instance.id).delete()
            _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on carrier service change: %s", e)


@receiver(post_save, sender=CompanyService)
def update_cars_on_company_service_change(sender, instance, **kwargs):
    try:
        car_services = CarService.objects.filter(service_type="COMPANY", service_id=instance.id)
        affected_car_ids = list(car_services.values_list("car_id", flat=True).distinct())
        if instance.is_active and instance.default_price > 0:
            default_markup = getattr(instance, "default_markup", None) or Decimal("0")
            car_services.update(custom_price=instance.default_price, markup_amount=default_markup)
        else:
            car_services.delete()
        _enqueue_recalc_cars_total_price(affected_car_ids)
    except Exception as e:
        logger.error("Error updating cars on company service change: %s", e)


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
