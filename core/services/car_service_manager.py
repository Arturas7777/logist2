"""
Business logic for managing CarService records, THS distribution, and client tariffs.

Extracted from signals.py to keep signal handlers thin and testable.
"""
import logging
from decimal import Decimal

from django.db import models as db_models

logger = logging.getLogger('django')


def find_line_service_by_container_count(line, container, vehicle_type):
    """
    Legacy THS selection based on car count in container and vehicle type.

    For new proportional THS logic use calculate_ths_for_container().
    """
    from core.models import LineService

    if not line or not container:
        return None

    line_name_upper = line.name.upper()

    moto_types = ['MOTO', 'BIG_MOTO', 'ATV']
    car_count = container.container_cars.exclude(vehicle_type__in=moto_types).count()

    services = LineService.objects.filter(line=line, is_active=True)

    if vehicle_type in moto_types:
        for service in services:
            service_name_upper = service.name.upper()
            if 'MOTO' in service_name_upper:
                if line_name_upper in service_name_upper or 'THS' in service_name_upper:
                    return service
        for service in services:
            if 'MOTO' in service.name.upper():
                return service
    else:
        search_patterns = [
            f'{car_count} АВТО',
            f'{car_count} AUTO',
            f'{car_count}АВТО',
            f'{car_count}AUTO',
        ]
        for service in services:
            service_name_upper = service.name.upper()
            for pattern in search_patterns:
                if pattern in service_name_upper:
                    return service

    return None


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
        tc.vehicle_type: Decimal(str(tc.coefficient))
        for tc in LineTHSCoefficient.objects.filter(line=container.line)
    }

    total_coefficient = Decimal('0.00')
    car_coefficients = {}

    for car in cars:
        coeff = ths_coefficients.get(car.vehicle_type, Decimal('1.00'))
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
        container.number, total_ths, car_coefficients, result,
    )
    return result


def create_ths_services_for_container(container):
    """
    Create THS CarService records for all cars in container using proportional distribution.

    Service provider type (LINE or WAREHOUSE) is determined by container.ths_payer.
    Returns the number of created services.
    """
    from core.models import (
        Car, CarService, LineService, WarehouseService,
    )

    if not container or not container.line:
        return 0

    ths_distribution = calculate_ths_for_container(container)
    if not ths_distribution:
        return 0

    service_type = container.ths_payer if hasattr(container, 'ths_payer') else 'LINE'

    line_service = None
    if service_type == 'LINE':
        line_service = LineService.objects.filter(
            line=container.line, is_active=True, name__icontains='THS'
        ).first()
        if not line_service:
            line_service, _ = LineService.objects.get_or_create(
                line=container.line,
                name=f"THS {container.line.name}",
                defaults={
                    'description': 'Услуга THS (рассчитывается пропорционально)',
                    'default_price': 0,
                    'is_active': True,
                },
            )

    warehouse_service = None
    if service_type == 'WAREHOUSE' and container.warehouse:
        warehouse_service = WarehouseService.objects.filter(
            warehouse=container.warehouse, is_active=True, name__icontains='THS'
        ).first()
        if not warehouse_service:
            warehouse_service, _ = WarehouseService.objects.get_or_create(
                warehouse=container.warehouse,
                name=f"THS {container.warehouse.name}",
                defaults={
                    'description': 'Услуга THS (рассчитывается пропорционально)',
                    'default_price': 0,
                    'is_active': True,
                    'add_by_default': False,
                },
            )

    created_count = 0
    car_ids = list(ths_distribution.keys())
    cars_by_id = {c.id: c for c in Car.objects.filter(id__in=car_ids)}

    for car_id, ths_amount in ths_distribution.items():
        try:
            car = cars_by_id.get(car_id)
            if not car:
                logger.warning("Car %s not found when creating THS service", car_id)
                continue

            CarService.objects.filter(
                car=car, service_type='LINE'
            ).filter(
                service_id__in=LineService.objects.filter(name__icontains='THS').values_list('id', flat=True)
            ).delete()

            CarService.objects.filter(
                car=car, service_type='WAREHOUSE'
            ).filter(
                service_id__in=WarehouseService.objects.filter(name__icontains='THS').values_list('id', flat=True)
            ).delete()

            if service_type == 'LINE' and line_service:
                CarService.objects.create(
                    car=car,
                    service_type='LINE',
                    service_id=line_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}",
                )
                created_count += 1
            elif service_type == 'WAREHOUSE' and warehouse_service:
                CarService.objects.create(
                    car=car,
                    service_type='WAREHOUSE',
                    service_id=warehouse_service.id,
                    custom_price=ths_amount,
                    quantity=1,
                    notes=f"THS рассчитан пропорционально. Тип ТС: {car.get_vehicle_type_display()}",
                )
                created_count += 1
        except Exception as e:
            logger.error("Error creating THS service for car %s: %s", car_id, e)

    return created_count


def apply_client_tariffs_for_container(container):
    """
    Apply client tariff markups to non-storage services after THS is calculated.

    agreed_total_price is the total price per car (all services EXCEPT storage).
    """
    if not container:
        return

    from core.models import CarService, ClientTariffRate

    cars = list(container.container_cars.select_related('client').all())
    if not cars:
        return

    total_cars_in_container = len(cars)

    for car in cars:
        if not car.client or car.client.tariff_type == 'NONE':
            continue

        client = car.client
        agreed_total = _get_agreed_total(client, car.vehicle_type, total_cars_in_container)

        if agreed_total is None:
            logger.debug(
                "Нет тарифа для %s (%s), тип ТС: %s, кол-во авто: %s",
                client.name, client.tariff_type, car.vehicle_type, total_cars_in_container,
            )
            continue

        _distribute_markup_for_car(car, agreed_total, total_cars_in_container)


def apply_client_tariff_for_car(car):
    """
    Apply client tariff to a single car (e.g. when client changes).

    Resets markup to defaults if new client has tariff_type NONE.
    """
    from core.models import CarService

    if not car or not car.pk:
        return

    client = car.client
    if not client or client.tariff_type == 'NONE':
        _reset_markup_to_defaults(car)
        return

    from core.models import Car

    total_cars_in_container = 1
    if car.container_id:
        total_cars_in_container = Car.objects.filter(container_id=car.container_id).count()

    agreed_total = _get_agreed_total(client, car.vehicle_type, total_cars_in_container)
    if agreed_total is None:
        return

    _distribute_markup_for_car(car, agreed_total, total_cars_in_container)


def _get_agreed_total(client, vehicle_type, total_cars_in_container):
    from core.models import ClientTariffRate

    if client.tariff_type == 'FIXED':
        rate = ClientTariffRate.objects.filter(
            client=client, vehicle_type=vehicle_type
        ).first()
        return rate.agreed_total_price if rate else None

    if client.tariff_type == 'FLEXIBLE':
        rate = ClientTariffRate.objects.filter(
            client=client,
            vehicle_type=vehicle_type,
            min_cars__lte=total_cars_in_container,
        ).filter(
            db_models.Q(max_cars__gte=total_cars_in_container) | db_models.Q(max_cars__isnull=True)
        ).first()
        return rate.agreed_total_price if rate else None

    return None


def _distribute_markup_for_car(car, agreed_total, total_cars_in_container):
    from core.models import CarService

    all_services = list(CarService.objects.filter(car=car))
    non_storage = [
        svc for svc in all_services
        if svc.get_service_name() and 'Хранение' not in svc.get_service_name()
    ]
    if not non_storage:
        return

    actual_total = sum((svc.custom_price or Decimal('0')) for svc in non_storage)
    diff = agreed_total - actual_total

    share = (diff / len(non_storage)).quantize(Decimal('0.01'))
    remainder = diff - share * len(non_storage)

    for i, svc in enumerate(non_storage):
        svc.markup_amount = share
        if i == len(non_storage) - 1:
            svc.markup_amount = share + remainder
        svc.save(update_fields=['markup_amount'])

    logger.info(
        "Tariff for %s (%s): agreed=%s, actual_cost=%s, markup=%s, cars_count=%s, distributed over %d services",
        car.vin, car.client.name if car.client else '?',
        agreed_total, actual_total, diff, total_cars_in_container, len(non_storage),
    )


def _reset_markup_to_defaults(car):
    from core.models import CarService

    for svc in CarService.objects.filter(car=car):
        default_markup = Decimal('0')
        try:
            service_obj = svc._get_service_obj()
            if service_obj:
                default_markup = Decimal(str(getattr(service_obj, 'default_markup', 0) or 0))
        except Exception:
            pass

        if svc.markup_amount != default_markup:
            svc.markup_amount = default_markup
            svc.save(update_fields=['markup_amount'])


# ---------------------------------------------------------------------------
# Service lookup helpers (used by signals to create CarService on contractor change)
# ---------------------------------------------------------------------------

def find_warehouse_services_for_car(warehouse):
    from core.models import WarehouseService
    if not warehouse:
        return []
    return list(WarehouseService.objects.filter(
        warehouse=warehouse, is_active=True, add_by_default=True
    ))


def find_line_services_for_car(line):
    from core.models import LineService
    if not line:
        return []
    return list(LineService.objects.filter(
        line=line, is_active=True, add_by_default=True
    ).exclude(name__icontains='THS'))


def find_carrier_services_for_car(carrier):
    from core.models import CarrierService
    if not carrier:
        return []
    return list(CarrierService.objects.filter(
        carrier=carrier, is_active=True, add_by_default=True
    ))


def get_main_company():
    from core.models import Company
    return Company.get_default()


def find_company_services_for_car(company):
    from core.models import CompanyService
    if not company:
        return []
    return list(CompanyService.objects.filter(
        company=company, is_active=True, add_by_default=True
    ))
