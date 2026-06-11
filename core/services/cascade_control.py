"""
Единая точка управления каскадными сигналами при массовых правках авто.

Раньше контекст-менеджер ``signals_disabled`` и списки сигналов
``CAR_SIGNALS`` / ``INVOICE_SIGNALS`` были объявлены прямо в
``core/admin/container.py``. Это «знание о каскадах» нужно в нескольких
местах (админка контейнера, сервис жизненного цикла контейнера, потенциально
management-команды), поэтому вынесено сюда — чтобы отключение/включение
каскадов жило в одном месте.

Использование::

    from core.services.cascade_control import signals_disabled, CAR_SIGNALS

    with signals_disabled(*CAR_SIGNALS):
        Car.objects.bulk_update(...)
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db.models.signals import post_delete, post_save

from core.models import Car, CarService
from core.signals import (
    car_post_save,
    recalculate_car_price_on_service_delete,
    recalculate_car_price_on_service_save,
    recalculate_invoices_on_car_service_delete,
    recalculate_invoices_on_car_service_save,
)


@contextmanager
def signals_disabled(*signal_pairs):
    """Временно отключает сигналы, гарантируя обратное подключение даже при
    исключении.

    :param signal_pairs: кортежи ``(signal, handler, sender)``.
    """
    for signal, handler, sender in signal_pairs:
        signal.disconnect(handler, sender=sender)
    try:
        yield
    finally:
        for signal, handler, sender in signal_pairs:
            signal.connect(handler, sender=sender)


# Каскад пересчёта цены авто (Car.post_save + CarService save/delete).
CAR_SIGNALS = [
    (post_save, car_post_save, Car),
    (post_save, recalculate_car_price_on_service_save, CarService),
    (post_delete, recalculate_car_price_on_service_delete, CarService),
]

# Каскад регенерации инвойсов от изменений услуг авто.
INVOICE_SIGNALS = [
    (post_save, recalculate_invoices_on_car_service_save, CarService),
    (post_delete, recalculate_invoices_on_car_service_delete, CarService),
]
