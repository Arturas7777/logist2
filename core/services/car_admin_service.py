"""
Сервис оркестрации услуг автомобиля при сохранении карточки в админке.

Бизнес-логика, которая раньше жила прямо в ``CarAdmin._save_model_inner``
(и трёх приватных helper-методах админки), вынесена сюда и
**декаплена от ``request``**: на вход подаётся обычный mapping POST-данных
(``request.POST`` или простой ``dict`` в тестах), а не объект запроса.

Это:
- делает логифицируемой/тестируемой без HTTP-слоя;
- собирает управление каскадом цены/инвойсов в одном месте
  (см. ``apply_car_service_edits`` и контекст ``bulk_car_pricing``);
- убирает дублирование шагов обработки услуг.

Поведение полностью повторяет прежний код админки (характеризующие тесты
фазы 0 + unit-тесты сервиса гарантируют отсутствие регрессий).
"""
from __future__ import annotations

import logging

from core.models import (
    Car,
    CarrierService,
    CarService,
    DeletedCarService,
    LineService,
    WarehouseService,
)

logger = logging.getLogger(__name__)

# prefix формы → service_type в БД
PREFIX_TO_TYPE = {
    "warehouse": "WAREHOUSE",
    "line": "LINE",
    "carrier": "CARRIER",
    "company": "COMPANY",
}

# Префиксы POST-полей, наличие которых означает «услуги правились вручную».
_SERVICE_FIELD_PREFIXES = (
    "warehouse_service_", "line_service_",
    "carrier_service_", "company_service_",
    "markup_warehouse_service_", "markup_line_service_",
    "markup_carrier_service_", "markup_company_service_",
    "remove_warehouse_service_", "remove_line_service_",
    "remove_carrier_service_", "remove_company_service_",
)


def services_touched(post) -> bool:
    """True, если в POST есть хоть одно поле правки услуг."""
    return any(
        key.startswith(prefix)
        for key in post.keys()
        for prefix in _SERVICE_FIELD_PREFIXES
    )


def process_removed_services(car, post) -> set[str]:
    """Сканирует POST на пометки ``remove_<prefix>_service_<id>=1``,
    удаляет соответствующие CarService и регистрирует в blacklist
    ``DeletedCarService``. Возвращает множество ключей вида ``"<prefix>_<id>"``.
    """
    removed: set[str] = set()
    for key, value in post.items():
        if value != "1":
            continue
        for prefix, svc_type in PREFIX_TO_TYPE.items():
            marker = f"remove_{prefix}_service_"
            if not key.startswith(marker):
                continue
            service_id = key[len(marker):]
            removed.add(f"{prefix}_{service_id}")
            try:
                CarService.objects.filter(
                    car=car, service_type=svc_type, service_id=service_id
                ).delete()
                DeletedCarService.objects.get_or_create(
                    car=car, service_type=svc_type, service_id=service_id,
                )
            except Exception:
                logger.exception("Error deleting %s service %s", prefix, service_id)
            break
    return removed


def update_existing_carservices(car, post, *, prefix, service_type, removed_services):
    """Обновляет custom_price/markup_amount существующих CarService по полям
    ``<prefix>_service_<id>`` / ``markup_<prefix>_service_<id>`` из POST.
    Возвращает QuerySet существующих CarService этого типа.
    """
    existing_qs = CarService.objects.filter(car=car, service_type=service_type)
    for car_service in existing_qs:
        if f"{prefix}_{car_service.service_id}" in removed_services:
            continue
        field_name = f"{prefix}_service_{car_service.service_id}"
        if field_name not in post:
            continue
        value = post.get(field_name)
        if value:
            try:
                car_service.custom_price = float(value)
            except (ValueError, TypeError):
                pass
        markup_field = f"markup_{prefix}_service_{car_service.service_id}"
        markup_value = post.get(markup_field)
        if markup_value is not None:
            try:
                car_service.markup_amount = float(markup_value) if markup_value else 0
            except (ValueError, TypeError):
                car_service.markup_amount = 0
        car_service.save()
    return existing_qs


def auto_add_default_services(
    car, post, *, prefix, service_type, catalog_model,
    related_field, related_value, removed_services, existing_qs,
):
    """Автодобавление дефолтных услуг провайдера при создании авто или смене
    провайдера (warehouse/line/carrier)."""
    if related_value is None:
        return
    new_service_ids = set(
        catalog_model.objects.filter(**{related_field: related_value})
        .values_list("id", flat=True)
    )
    DeletedCarService.objects.filter(
        car=car, service_type=service_type
    ).exclude(service_id__in=new_service_ids).delete()
    services = catalog_model.objects.filter(
        **{related_field: related_value},
        is_active=True,
        add_by_default=True,
    ).only("id", "default_price", "default_markup")
    existing_ids = set(existing_qs.values_list("service_id", flat=True))
    blacklisted = set(
        DeletedCarService.objects.filter(
            car=car, service_type=service_type
        ).values_list("service_id", flat=True)
    )
    for service in services:
        if f"{prefix}_{service.id}" in removed_services:
            continue
        if service.id in blacklisted:
            continue
        if service.id in existing_ids:
            continue
        field_name = f"{prefix}_service_{service.id}"
        value = post.get(field_name) or service.default_price
        default_markup = getattr(service, "default_markup", 0) or 0
        CarService.objects.create(
            car=car,
            service_type=service_type,
            service_id=service.id,
            custom_price=float(value),
            markup_amount=float(default_markup),
        )


def apply_car_service_edits(car, *, post, changed_data, is_change) -> None:
    """Полная оркестрация правок услуг карточки авто после ``super().save_model``.

    Повторяет прежний хвост ``CarAdmin._save_model_inner``:
      1. удалить помеченные услуги;
      2. синхронизировать цену/наценку существующих;
      3. автодобавить дефолтные услуги при создании/смене провайдера;
      4. пересчитать хранение при смене склада;
      5. применить тариф клиента (FIXED/FLEXIBLE);
      6. финальный пересчёт ``total_price`` (флаг ``_bulk_updating`` снят).

    ``car._bulk_updating`` ДОЛЖЕН быть выставлен вызывающим до первого save
    (см. ``CarAdmin._save_model_inner``); снимается в конце этой функции.
    """
    removed_services = process_removed_services(car, post)
    logger.debug("Removed services: %s", removed_services)

    # WAREHOUSE
    existing_warehouse_qs = update_existing_carservices(
        car, post, prefix="warehouse", service_type="WAREHOUSE",
        removed_services=removed_services,
    )
    if (not is_change) or "warehouse" in changed_data:
        auto_add_default_services(
            car, post, prefix="warehouse", service_type="WAREHOUSE",
            catalog_model=WarehouseService,
            related_field="warehouse", related_value=car.warehouse,
            removed_services=removed_services, existing_qs=existing_warehouse_qs,
        )

    # LINE (включая THS)
    existing_line_qs = update_existing_carservices(
        car, post, prefix="line", service_type="LINE",
        removed_services=removed_services,
    )
    if (not is_change) or "line" in changed_data:
        auto_add_default_services(
            car, post, prefix="line", service_type="LINE",
            catalog_model=LineService,
            related_field="line", related_value=car.line,
            removed_services=removed_services, existing_qs=existing_line_qs,
        )

    # CARRIER
    existing_carrier_qs = update_existing_carservices(
        car, post, prefix="carrier", service_type="CARRIER",
        removed_services=removed_services,
    )
    if (not is_change) or "carrier" in changed_data:
        auto_add_default_services(
            car, post, prefix="carrier", service_type="CARRIER",
            catalog_model=CarrierService,
            related_field="carrier", related_value=car.carrier,
            removed_services=removed_services, existing_qs=existing_carrier_qs,
        )

    # COMPANY: auto-add отсутствует (компания не привязана к Car).
    update_existing_carservices(
        car, post, prefix="company", service_type="COMPANY",
        removed_services=removed_services,
    )

    # Пересчёт хранения и дней при смене склада.
    if is_change and "warehouse" in changed_data:
        logger.debug("Склад изменился для авто %s, пересчитываем хранение", car.vin)
        try:
            car.update_days_and_storage()
            car.calculate_total_price()
            car.save(update_fields=["storage_cost", "days", "total_price"])
        except Exception as e:
            logger.error("Ошибка при пересчете стоимости хранения: %s", e)

    # Тариф клиента (FIXED / FLEXIBLE) — только когда реально изменилось что-то,
    # влияющее на распределение, или правились сами услуги.
    client_cleared = is_change and "client" in changed_data and not car.client
    deps_touched = (
        not is_change
        or any(f in changed_data for f in ("client", "warehouse", "line", "carrier"))
        or services_touched(post)
        or client_cleared
    )
    if car.status != "TRANSFERRED" and deps_touched:
        client = car.client
        try:
            from core.services.car_service_manager import apply_client_tariff_for_car
            if (client and client.tariff_type in ("FIXED", "FLEXIBLE")) or client_cleared:
                apply_client_tariff_for_car(car)
                car.calculate_total_price()
                Car.objects.filter(pk=car.pk).update(total_price=car.total_price)
        except Exception:
            logger.exception("Ошибка при пересчете тарифа клиента для car=%s", car.pk)

    # Финальный пересчёт цены авто после всех манипуляций с CarService.
    car._bulk_updating = False
    try:
        car.calculate_total_price()
        Car.objects.filter(pk=car.pk).update(
            total_price=car.total_price,
            days=car.days,
            storage_cost=car.storage_cost,
        )
    except Exception:
        logger.exception("Ошибка финального пересчёта цены авто %s", car.pk)
