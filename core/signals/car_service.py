"""Сигналы для ``CarService``:

1. Пересчёт ``Car.total_price`` при изменении состава услуг машины.
2. Регенерация ``NewInvoice.items`` для инвойсов, в которых участвует
   эта машина.

Оба пути дедуплицируются внутри транзакции: пересчёт цены — через
единый ``_enqueue_recalc_cars_total_price`` (P2, дедуп по on_commit-очереди),
регенерация — через thread-local ``_regen_local.cars``. При сохранении
карточки авто из админки приходит 5-15 ``post_save`` от ``CarService``
подряд (по одной услуге на каждый ``service.save()``). Раньше каждый
сигнал запускал собственный ``calculate_total_price`` + ``UPDATE Car``,
что давало N+1 даже при включённом ``_bulk_updating``. Теперь
пересчёт/регенерация происходят ровно один раз на коммит транзакции.
"""

import logging
import threading

from django.db import OperationalError, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models import CarService
from core.models_billing import NewInvoice

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Car.total_price recalculation
# ---------------------------------------------------------------------------

# Оставлен для обратной совместимости (на него ссылается фикстура
# ``_clear_pricing_thread_locals`` в тестах); сам дедуп теперь живёт в
# ``_enqueue_recalc_cars_total_price`` и опирается на on_commit-очередь.
_pricing_local = threading.local()


def _schedule_car_price_recalc(car_id):
    """Пересчёт ``Car.total_price`` после изменения состава услуг.

    P2: раньше здесь был собственный синхронный путь (``calculate_total_price``
    + ``UPDATE`` в ``on_commit``), а ``signals.car`` параллельно ставил Celery
    ``recalculate_cars_total_price_task`` — при сохранении карточки авто цена
    считалась дважды. Теперь оба триггера идут через один
    :func:`~core.signals.service_catalog._enqueue_recalc_cars_total_price`
    (Celery, inline-fallback при недоступном брокере, дедуп внутри транзакции).
    В тестах и dev ``CELERY_TASK_ALWAYS_EAGER`` выполняет задачу синхронно в
    ``on_commit`` — результат тот же, что у старого пути.
    """
    if not car_id:
        return
    from core.signals.service_catalog import _enqueue_recalc_cars_total_price

    _enqueue_recalc_cars_total_price([car_id])


@receiver(post_save, sender=CarService)
def recalculate_car_price_on_service_save(sender, instance, **kwargs):
    if getattr(instance.car, "_creating_services", False):
        return
    # Админ car.py во время _save_model_inner делает много CarService.save()
    # подряд — включает флаг _bulk_updating, чтобы дублирующих пересчётов
    # Car не было; в конце _save_model_inner делает один пересчёт сам.
    if getattr(instance.car, "_bulk_updating", False):
        return
    _schedule_car_price_recalc(instance.car_id)


@receiver(post_delete, sender=CarService)
def recalculate_car_price_on_service_delete(sender, instance, **kwargs):
    # При удалении car может быть уже отвязан от instance, но car_id в FK
    # всё ещё валиден.
    car = getattr(instance, "car", None)
    if car is not None:
        if getattr(car, "_creating_services", False):
            return
        if getattr(car, "_bulk_updating", False):
            return
    _schedule_car_price_recalc(instance.car_id)


# ---------------------------------------------------------------------------
# Invoice regeneration
# ---------------------------------------------------------------------------

_regen_local = threading.local()


def _get_pending_regen_cars():
    """Thread-local множество car_id, для которых пересчёт уже запланирован
    в рамках текущей транзакции. Очищается в ``on_commit``.
    """
    bucket = getattr(_regen_local, "cars", None)
    if bucket is None:
        bucket = set()
        _regen_local.cars = bucket
    return bucket


def _deferred_invoice_regeneration(car_id):
    """Планирует пересчёт инвойсов для ``car_id`` после коммита.

    Пересчёт делается в Celery (``regenerate_invoices_for_car_task``),
    HTTP-запрос не блокируется. В dev-окружении
    (``CELERY_TASK_ALWAYS_EAGER=True``) Celery выполняет задачу
    синхронно — поведение совпадает со старым ``on_commit(_do_regenerate)``.

    Дедуплицирует: если для этого car уже запланирован пересчёт в рамках
    текущей транзакции (например, пришло 10 ``post_save`` от ``CarService``
    подряд при сохранении карточки авто), планируется только один общий
    вызов.

    Если broker лежит — fallback на синхронный пересчёт в ``on_commit``
    (старое поведение), чтобы не терять регенерацию.
    """
    pending = _get_pending_regen_cars()
    if car_id in pending:
        return
    pending.add(car_id)

    def _dispatch():
        try:
            from core.tasks import regenerate_invoices_for_car_task

            try:
                regenerate_invoices_for_car_task.delay(car_id)
            except Exception:
                logger.exception(
                    "Celery enqueue failed for regenerate_invoices_for_car(%s) — running inline",
                    car_id,
                )
                _regenerate_invoices_for_car_inline(car_id)
        finally:
            _get_pending_regen_cars().discard(car_id)

    transaction.on_commit(_dispatch)


def _regenerate_invoices_for_car_inline(car_id):
    """Синхронный fallback: дублирует логику ``regenerate_invoices_for_car_task``.

    Используется только если Celery broker недоступен; в обычной работе
    путь идёт через Celery.
    """
    try:
        from core.mixins import AUTO_REGENERATABLE_INVOICE_STATUSES

        invoice_ids = list(
            NewInvoice.objects.filter(
                cars__id=car_id,
                status__in=AUTO_REGENERATABLE_INVOICE_STATUSES,
            ).values_list("id", flat=True)
        )
        for invoice_id in invoice_ids:
            try:
                with transaction.atomic():
                    invoice = NewInvoice.objects.select_for_update(nowait=True).get(id=invoice_id)
                    invoice.regenerate_items_from_cars()
            except OperationalError:
                logger.warning("Skipping invoice %s - locked", invoice_id)
            except NewInvoice.DoesNotExist:
                pass
    except Exception as e:
        logger.error("Error in inline invoice regeneration for car %s: %s", car_id, e)


@receiver(post_save, sender=CarService)
def recalculate_invoices_on_car_service_save(sender, instance, **kwargs):
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)


@receiver(post_delete, sender=CarService)
def recalculate_invoices_on_car_service_delete(sender, instance, **kwargs):
    if instance.car_id:
        _deferred_invoice_regeneration(instance.car_id)
