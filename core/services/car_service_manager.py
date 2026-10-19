"""
Business logic for managing CarService records, THS distribution, and client tariffs.

Extracted from signals.py to keep signal handlers thin and testable.
"""

import logging
from decimal import Decimal

from django.db import models as db_models
from django.db import transaction
from django.db.models import Q

from core.service_codes import ServiceCode, is_storage_service, is_ths_service

logger = logging.getLogger(__name__)


def calculate_ths_for_container(container):
    """
    Proportional THS distribution across cars in container based on vehicle-type coefficients.

    Returns dict: {car_id: ths_amount}.
    """
    from core.models import LineTHSCoefficient

    if not container or not container.line or not container.ths:
        return {}

    total_ths = Decimal(str(container.ths))
    if total_ths <= 0:
        return {}

    cars = list(container.container_cars.all())
    if not cars:
        return {}

    ths_coefficients = {
        tc.vehicle_type: Decimal(str(tc.coefficient)) for tc in LineTHSCoefficient.objects.filter(line=container.line)
    }

    total_coefficient = Decimal("0.00")
    car_coefficients = {}

    for car in cars:
        coeff = ths_coefficients.get(car.vehicle_type, Decimal("1.00"))
        car_coefficients[car.id] = coeff
        total_coefficient += coeff

    from core.utils import round_up_to_5

    if total_coefficient == 0:
        equal_share = total_ths / len(cars)
        return {car.id: round_up_to_5(equal_share) for car in cars}

    result = {}
    for car in cars:
        car_share = car_coefficients[car.id] / total_coefficient
        car_ths = total_ths * car_share
        result[car.id] = round_up_to_5(car_ths)

    logger.info(
        "THS distribution for container %s: total=%s, coefficients=%s, result=%s",
        container.number,
        total_ths,
        car_coefficients,
        result,
    )
    return result


def create_ths_services_for_container(container, *, include_transferred=True):
    """
    Create THS CarService records for all cars in container using proportional distribution.

    Service provider type (LINE or WAREHOUSE) is determined by container.ths_payer.
    Returns the number of created services.

    B11: ``include_transferred=False`` — распределение считается по ВСЕМ авто
    контейнера (доли не меняются), но запись услуг для TRANSFERRED авто
    пропускается: их THS уже выставлен в счёт. Используется каскадом
    контейнера; «заполнение пробела» (``ensure_ths_for_car_container``)
    по-прежнему пишет всем.
    """
    from core.models import (
        Car,
        CarService,
        LineService,
        WarehouseService,
    )

    if not container or not container.line:
        return 0

    ths_distribution = calculate_ths_for_container(container)
    if not ths_distribution:
        return 0

    service_type = container.ths_payer if hasattr(container, "ths_payer") else "LINE"

    if service_type == "WAREHOUSE" and not container.warehouse:
        logger.warning(
            "Container %s: ths_payer=WAREHOUSE but no warehouse set. Falling back to LINE.",
            container.number,
        )
        service_type = "LINE"

    line_service = None
    if service_type == "LINE":
        line_service = (
            LineService.objects.filter(
                line=container.line,
                is_active=True,
            )
            .filter(
                Q(code=ServiceCode.THS) | Q(name__icontains="THS"),
            )
            .first()
        )
        if not line_service:
            line_service, _ = LineService.objects.get_or_create(
                line=container.line,
                name=f"THS {container.line.name}",
                defaults={
                    "code": ServiceCode.THS,
                    "description": "Услуга THS (рассчитывается пропорционально)",
                    "default_price": 0,
                    "is_active": True,
                },
            )

    warehouse_service = None
    if service_type == "WAREHOUSE" and container.warehouse:
        warehouse_service = (
            WarehouseService.objects.filter(
                warehouse=container.warehouse,
                is_active=True,
            )
            .filter(
                Q(code=ServiceCode.THS) | Q(name__icontains="THS"),
            )
            .first()
        )
        if not warehouse_service:
            warehouse_service, _ = WarehouseService.objects.get_or_create(
                warehouse=container.warehouse,
                name=f"THS {container.warehouse.name}",
                defaults={
                    "code": ServiceCode.THS,
                    "description": "Услуга THS (рассчитывается пропорционально)",
                    "default_price": 0,
                    "is_active": True,
                    "add_by_default": False,
                },
            )

    created_count = 0
    car_ids = list(ths_distribution.keys())
    cars_by_id = {c.id: c for c in Car.objects.filter(id__in=car_ids)}

    # Весь пересчёт THS контейнера — одна транзакция. Раньше delete старых
    # THS и create новых шли по машинам без atomic, а исключение только
    # логировалось: сбой посередине оставлял часть машин контейнера ВООБЩЕ
    # без THS-услуги (старая удалена, новая не создана) — тихо неправильные
    # инвойсы. Теперь при любой ошибке транзакция откатывается целиком и
    # исключение пробрасывается наверх (админка покажет ошибку, Sentry —
    # алерт).
    with transaction.atomic():
        for car_id, ths_amount in ths_distribution.items():
            car = cars_by_id.get(car_id)
            if not car:
                logger.warning("Car %s not found when creating THS service", car_id)
                continue
            if not include_transferred and car.status == "TRANSFERRED":
                continue

            CarService.objects.filter(car=car, service_type="LINE").filter(
                service_id__in=LineService.objects.filter(
                    Q(code=ServiceCode.THS) | Q(name__icontains="THS")
                ).values_list("id", flat=True)
            ).delete()

            CarService.objects.filter(car=car, service_type="WAREHOUSE").filter(
                service_id__in=WarehouseService.objects.filter(
                    Q(code=ServiceCode.THS) | Q(name__icontains="THS")
                ).values_list("id", flat=True)
            ).delete()

            if service_type == "LINE" and line_service:
                CarService.objects.create(
                    car=car,
                    service_type="LINE",
                    service_id=line_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}",
                )
                created_count += 1
            elif service_type == "WAREHOUSE" and warehouse_service:
                CarService.objects.create(
                    car=car,
                    service_type="WAREHOUSE",
                    service_id=warehouse_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}",
                )
                created_count += 1

    return created_count


def _car_missing_ths_service(car) -> bool:
    return not any(is_ths_service(cs) for cs in car.car_services.all())


def ensure_ths_for_car_container(car) -> bool:
    """Создать пропорциональный THS по контейнеру, если у любой машины его нет.

    Назначение клиента и оформление автовоза постфактум не запускали
    ``create_ths_services_for_container`` — машины оставались без услуги THS,
    и она не попадала в инвойс. Возвращает True, если THS пересобран.
    """
    if not car or not car.container_id:
        return False

    container = getattr(car, "container", None)
    if container is None or container.pk != car.container_id:
        from core.models import Container

        try:
            container = Container.objects.select_related("line", "warehouse").get(pk=car.container_id)
        except Container.DoesNotExist:
            return False

    if not container.line_id or not container.ths:
        return False

    from core.models import Car

    siblings = list(Car.objects.filter(container_id=container.pk).prefetch_related("car_services"))
    if not siblings or not any(_car_missing_ths_service(c) for c in siblings):
        return False

    return create_ths_services_for_container(container) > 0


def ensure_ths_and_tariffs_for_car(car) -> bool:
    """Пересобрать THS контейнера при пробеле и заново разложить тарифы.

    После ``create_ths_services_for_container`` старые THS-услуги удалены,
    наценки на них сброшены — тариф нужно применить ко всем авто контейнера.
    """
    if not ensure_ths_for_car_container(car):
        return False

    container = car.container
    apply_client_tariffs_for_container(container)

    from core.models import Car

    for sibling in Car.objects.filter(container_id=container.pk):
        sibling.calculate_total_price()
        Car.objects.filter(pk=sibling.pk).update(
            total_price=sibling.total_price,
            days=sibling.days,
            storage_cost=sibling.storage_cost,
        )
    car.refresh_from_db(fields=["total_price", "days", "storage_cost"])
    return True


# Типы ТС, которые считаются мотоциклами. Мотоциклы НЕ участвуют в подсчёте
# количества легковых (и прочих) авто в контейнере при подборе тарифа — и
# наоборот. Считаем по категориям, чтобы мотоцикл не влиял на тариф/THS авто.
MOTORCYCLE_TYPES = {"MOTO", "BIG_MOTO"}


def _count_vehicles_for_tariff(cars, vehicle_type):
    """Кол-во ТС в контейнере, релевантное для подбора тарифа данного ТС.

    Мотоциклы и не-мотоциклы считаются раздельно: для легкового авто
    мотоциклы не учитываются (и наоборот), поэтому мотоцикл в контейнере не
    сдвигает гибкий тариф/THS легковых авто в другую ценовую группу.
    """
    target_is_moto = vehicle_type in MOTORCYCLE_TYPES
    return sum(1 for c in cars if (c.vehicle_type in MOTORCYCLE_TYPES) == target_is_moto)


TARIFF_TYPES = ("FIXED", "FLEXIBLE")

# Типы ТС, которые без собственной ставки тарифа берут ставку легкового.
SEDAN_RATE_FALLBACK_TYPES = {"CROSSOVER", "SUV", "PICKUP"}


def _tariff_line_id(car, container=None):
    container = container or (car.container if car.container_id else None)
    return car.line_id or (container.line_id if container else None)


def apply_client_tariffs_for_container(container, *, include_transferred=True):
    """
    Apply client tariff markups to warehouse services after THS is calculated.

    agreed_total_price — согласованная с клиентом цена за складской пакет
    (все услуги WAREHOUSE кроме хранения) вместе с THS линии. Услуги
    перевозчика (CARRIER) и отдельные услуги компаний (COMPANY) в тариф НЕ
    входят — они добавляются сверху как есть.

    Количество ТС для подбора тарифа считается по категориям: мотоциклы не
    учитываются при подсчёте легковых авто (и наоборот).

    B11: ``include_transferred=False`` — число авто для подбора тарифа
    считается по всему контейнеру, но наценки переданных авто не
    перезаписываются.
    """
    if not container:
        return

    cars = list(container.container_cars.select_related("client").all())
    if not cars:
        return

    for car in cars:
        if not include_transferred and car.status == "TRANSFERRED":
            continue
        if not car.client or car.client.tariff_type == "NONE":
            continue

        client = car.client
        count = _count_vehicles_for_tariff(cars, car.vehicle_type)
        agreed_total = _get_agreed_total(client, car.vehicle_type, count, line_id=_tariff_line_id(car, container))

        if agreed_total is None:
            logger.debug(
                "Нет тарифа для %s (%s), тип ТС: %s, кол-во ТС (без учёта др. категории): %s",
                client.name,
                client.tariff_type,
                car.vehicle_type,
                count,
            )
            continue

        _distribute_markup_for_car(car, agreed_total, count)


def apply_client_tariff_for_car(car):
    """
    Apply client tariff to a single car (e.g. when client changes).

    Resets markup to defaults if new client has tariff_type NONE.
    """

    if not car or not car.pk:
        return

    client = car.client
    if not client or client.tariff_type == "NONE":
        _reset_markup_to_defaults(car)
        return

    from core.models import Car

    count = 1
    if car.container_id:
        cars = list(Car.objects.filter(container_id=car.container_id).only("id", "vehicle_type"))
        count = _count_vehicles_for_tariff(cars, car.vehicle_type)

    agreed_total = _get_agreed_total(client, car.vehicle_type, count, line_id=_tariff_line_id(car))
    if agreed_total is None:
        return

    _distribute_markup_for_car(car, agreed_total, count)


def apply_tariff_if_applicable(car) -> bool:
    """Применить тариф клиента к активному авто; переданные авто не трогаем.

    В отличие от :func:`apply_client_tariff_for_car`, для клиента без тарифа
    ничего не делает — ручные наценки таких авто сохраняются.
    """
    if not car or not car.pk or car.status == "TRANSFERRED":
        return False
    client = car.client
    if not client or client.tariff_type not in TARIFF_TYPES:
        return False
    apply_client_tariff_for_car(car)
    return True


def _find_tariff_rate(client, vehicle_type, total_cars_in_container, line_id=None):
    """Ставка тарифа: сначала свой тип ТС, потом SEDAN (для кроссовера/джипа/пикапа);
    внутри типа — ставка этой линии, потом ставка без линии."""
    from core.models import ClientTariffRate

    if client.tariff_type not in TARIFF_TYPES:
        return None

    vehicle_types = [vehicle_type]
    if vehicle_type in SEDAN_RATE_FALLBACK_TYPES:
        vehicle_types.append("SEDAN")

    line_q = Q(line__isnull=True)
    if line_id:
        line_q |= Q(line_id=line_id)
    rates = ClientTariffRate.objects.filter(line_q, client=client, vehicle_type__in=vehicle_types)

    if client.tariff_type == "FLEXIBLE":
        rates = rates.filter(min_cars__lte=total_cars_in_container).filter(
            db_models.Q(max_cars__gte=total_cars_in_container) | db_models.Q(max_cars__isnull=True)
        )

    return min(
        rates,
        key=lambda r: (vehicle_types.index(r.vehicle_type), r.line_id is None, r.min_cars, r.pk),
        default=None,
    )


def _get_agreed_total(client, vehicle_type, total_cars_in_container, line_id=None):
    rate = _find_tariff_rate(client, vehicle_type, total_cars_in_container, line_id=line_id)
    return rate.agreed_total_price if rate else None


def _distribute_markup_for_car(car, agreed_total, total_cars_in_container):
    """Приводит сумму «услуги склада (кроме хранения) + THS линии» РОВНО к тарифу.

    Тариф (``FIXED`` или подобранная по числу ТС ставка ``FLEXIBLE``) — это
    точная цена пакета, выше которой клиент платить не может. THS входит в
    тариф при любом плательщике: при оплате складу это услуга склада, при
    оплате линии — услуга линии, которая учитывается в базе как есть.
    Скрытая наценка услуг склада всегда перезаписывается так, чтобы итог был
    равен тарифу:
      - база ниже тарифа  → наценка положительная (прибыль);
      - база выше тарифа  → наценка отрицательная (убыток), т.к. с клиента
        всё равно берётся только фиксированная сумма.

    Хранение в тариф не входит и добавляется сверху; его наценку задают
    условия хранения клиента (``Car._update_storage_service_price``). Услуги
    перевозчика (CARRIER) и отдельные услуги компаний (COMPANY) тоже не
    затрагиваются.

    Пересчёт ``total_price`` — забота вызывающего кода: сигналы пересчёта
    цены от сохранения наценок здесь подавлены флагом ``_bulk_updating``.
    """
    getattr(car, "_prefetched_objects_cache", {}).pop("car_services", None)
    all_services = list(car.car_services.all())

    warehouse_non_storage = [
        svc for svc in all_services if svc.service_type == "WAREHOUSE" and not is_storage_service(svc)
    ]
    line_ths_services = [svc for svc in all_services if svc.service_type == "LINE" and is_ths_service(svc)]

    was_bulk_updating = getattr(car, "_bulk_updating", False)
    car._bulk_updating = True
    try:
        if not warehouse_non_storage:
            logger.info(
                "Tariff for %s (%s): agreed=%s, но нет услуг склада (кроме хранения) — наценка не распределена",
                car.vin,
                car.client.name if car.client else "?",
                agreed_total,
            )
            return

        tariff_type = car.client.tariff_type if car.client else "?"

        # Склад — себестоимость БЕЗ скрытой наценки (её и раскладываем),
        # THS линии — как его видит клиент.
        warehouse_base = sum(Decimal(str(svc.final_price)) for svc in warehouse_non_storage)
        line_ths_total = sum(Decimal(str(svc.invoice_price)) for svc in line_ths_services)
        base_total = warehouse_base + line_ths_total

        # Цена пакета не может быть выше тарифа: приводим ровно к тарифу.
        # diff > 0 → прибыль, diff < 0 → убыток (отрицательная наценка).
        diff = agreed_total - base_total

        share = (diff / len(warehouse_non_storage)).quantize(Decimal("0.01"))
        remainder = diff - share * len(warehouse_non_storage)

        for i, svc in enumerate(warehouse_non_storage):
            new_markup = share
            if i == len(warehouse_non_storage) - 1:
                new_markup = share + remainder
            if svc.markup_amount != new_markup:
                svc.markup_amount = new_markup
                svc.save(update_fields=["markup_amount"])
    finally:
        car._bulk_updating = was_bulk_updating

    logger.info(
        "Tariff for %s (%s): type=%s, agreed=%s, base=%s (THS линии %s), diff=%s, cars_count=%s, "
        "распределено по %d услугам склада",
        car.vin,
        car.client.name if car.client else "?",
        tariff_type,
        agreed_total,
        base_total,
        line_ths_total,
        diff,
        total_cars_in_container,
        len(warehouse_non_storage),
    )


def _reset_markup_to_defaults(car):
    from core.models import CarService

    for svc in CarService.objects.filter(car=car):
        default_markup = Decimal("0")
        try:
            service_obj = svc._get_service_obj()
            if service_obj:
                default_markup = Decimal(str(getattr(service_obj, "default_markup", 0) or 0))
        except Exception:
            pass

        if svc.markup_amount != default_markup:
            svc.markup_amount = default_markup
            svc.save(update_fields=["markup_amount"])


# ---------------------------------------------------------------------------
# Service lookup helpers (used by signals to create CarService on contractor change)
# ---------------------------------------------------------------------------


def find_warehouse_services_for_car(warehouse):
    from core.models import WarehouseService

    if not warehouse:
        return []
    return list(WarehouseService.objects.filter(warehouse=warehouse, is_active=True, add_by_default=True))


def find_line_services_for_car(line):
    from core.models import LineService

    if not line:
        return []
    return list(
        LineService.objects.filter(line=line, is_active=True, add_by_default=True).exclude(name__icontains="THS")
    )


def find_carrier_services_for_car(carrier):
    from core.models import CarrierService

    if not carrier:
        return []
    return list(CarrierService.objects.filter(carrier=carrier, is_active=True, add_by_default=True))


def get_main_company():
    from core.models import Company

    return Company.get_default()


def find_company_services_for_car(company):
    from core.models import CompanyService

    if not company:
        return []
    return list(CompanyService.objects.filter(company=company, is_active=True, add_by_default=True))


# ---------------------------------------------------------------------------
# Пересоздание ценообразующих CarService при смене контрактников
# ---------------------------------------------------------------------------


def sync_car_services_for_car(
    car,
    *,
    created: bool,
    warehouse_changed: bool,
    line_changed: bool,
    carrier_changed: bool,
):
    """Пересоздать ``CarService`` для изменившихся типов контрактников.

    A4 (AUDIT_ROUND3): раньше эта логика жила в post_save-сигнале
    ``car_post_save`` → ``_create_car_services_if_needed`` и глотала
    исключения (``except Exception: logger.error`` без reraise) — сбой
    означал неправильные суммы в будущих инвойсах, молча. Теперь это
    явная команда, вызываемая из save-пути (``Car.save``); исключения
    пробрасываются (Sentry-алерт, транзакция откатывается).

    Пересоздаются только услуги изменившегося типа — ручные правки
    остальных типов сохраняются. Услуги, удалённые пользователем
    (``DeletedCarService``), не восстанавливаются.
    """
    from core.models import CarService, DeletedCarService, LineService

    if not car.pk:
        return
    if getattr(car, "_creating_services", False):
        return

    if not (created or warehouse_changed or line_changed or carrier_changed):
        return

    car._creating_services = True
    try:
        deleted_by_type = {}
        for stype in ("WAREHOUSE", "LINE", "CARRIER", "COMPANY"):
            deleted_by_type[stype] = set(
                DeletedCarService.objects.filter(car=car, service_type=stype).values_list("service_id", flat=True)
            )

        # WAREHOUSE — только если склад действительно сменился
        if warehouse_changed:
            car.car_services.filter(service_type="WAREHOUSE").delete()
            if car.warehouse:
                for service in find_warehouse_services_for_car(car.warehouse):
                    if service.id in deleted_by_type["WAREHOUSE"]:
                        continue
                    if is_storage_service(service):
                        days = Decimal(str(car.days or 0))
                        custom_price = days * Decimal(str(service.default_price or 0))
                        default_markup = days * Decimal(str(getattr(service, "default_markup", 0) or 0))
                    else:
                        custom_price = service.default_price
                        default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=car,
                        service_type="WAREHOUSE",
                        service_id=service.id,
                        defaults={"custom_price": custom_price, "markup_amount": default_markup},
                    )

        # LINE (без THS: THS управляется create_ths_services_for_container)
        if line_changed:
            ths_line_ids = LineService.objects.filter(Q(code=ServiceCode.THS) | Q(name__icontains="THS")).values_list(
                "id", flat=True
            )
            car.car_services.filter(service_type="LINE").exclude(service_id__in=ths_line_ids).delete()
            if car.line:
                for service in find_line_services_for_car(car.line):
                    if service.id in deleted_by_type["LINE"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=car,
                        service_type="LINE",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )

        # CARRIER — только если перевозчик действительно сменился
        if carrier_changed:
            car.car_services.filter(service_type="CARRIER").delete()
            if car.carrier:
                for service in find_carrier_services_for_car(car.carrier):
                    if service.id in deleted_by_type["CARRIER"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=car,
                        service_type="CARRIER",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )

        # COMPANY (только для новых машин)
        if created:
            main_company = get_main_company()
            if main_company:
                for service in find_company_services_for_car(main_company):
                    if service.id in deleted_by_type["COMPANY"]:
                        continue
                    default_markup = getattr(service, "default_markup", None) or Decimal("0")
                    CarService.objects.get_or_create(
                        car=car,
                        service_type="COMPANY",
                        service_id=service.id,
                        defaults={"custom_price": service.default_price, "markup_amount": default_markup},
                    )
    finally:
        car._creating_services = False

    # Пересчёт из post_save машины мог отработать ДО создания услуг: вне
    # транзакции (скрипты, бот) on_commit выполняется сразу, и авто получало
    # total_price=0. Ставим пересчёт заново — уже по созданным услугам
    # (внутри транзакции дубль схлопнется). Админка с _bulk_updating
    # пересчитывает цену сама.
    if not getattr(car, "_bulk_updating", False):
        from core.signals.service_catalog import _enqueue_recalc_cars_total_price

        _enqueue_recalc_cars_total_price([car.pk])
