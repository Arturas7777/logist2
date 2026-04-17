"""JSON API endpoints consumed by admin JS and templates."""
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.cache_utils import CACHE_TIMEOUTS
from core.models import (
    Car,
    Carrier,
    CarrierService,
    CarService,
    Client,
    Company,
    CompanyService,
    Container,
    Line,
    LineService,
    Warehouse,
    WarehouseService,
)

logger = logging.getLogger(__name__)


@staff_member_required
def car_list_api(request):
    raw_client = (request.GET.get('client_id') or request.GET.get('client') or '').strip()
    m = re.search(r"\d+", raw_client)
    raw_client = m.group(0) if m else ''
    search_query: str = request.GET.get('search', '').strip().lower()
    logger.debug("car_list_api called with GET: %s", request.GET)

    try:
        client_id_int = int(raw_client)
    except (TypeError, ValueError):
        client_id_int = None

    if client_id_int:
        allowed_statuses = ['UNLOADED', 'IN_PORT', 'FLOATING', 'TRANSFERRED']
        all_cars = Car.objects.by_client(client_id_int).filter(
            status__in=allowed_statuses
        ).select_related('client', 'warehouse', 'container', 'line', 'carrier')

        if search_query:
            year_q = Q()
            if search_query.isdigit():
                try:
                    year_q = Q(year=int(search_query))
                except Exception:
                    year_q = Q()
            all_cars = all_cars.filter(
                Q(vin__icontains=search_query) |
                Q(brand__icontains=search_query) |
                year_q
            )

        html = render_to_string('admin/car_options.html', context={'cars': all_cars}, request=request)
        return HttpResponse(html, content_type='text/html')
    return HttpResponse('<option class="no-results">Клиент не выбран</option>', content_type='text/html')


@staff_member_required
@require_GET
def get_invoice_total(request):
    car_ids = request.GET.get('car_ids', '').split(',')
    car_ids = [int(cid) for cid in car_ids if cid.strip().isdigit()]

    result = {'total_amount': '0.00'}
    if not car_ids:
        return JsonResponse(result)

    try:
        agg = Car.objects.filter(id__in=car_ids).aggregate(total=Sum('total_price'))
        total = agg['total'] or Decimal('0.00')
        result['total_amount'] = str(total)
        return JsonResponse(result)
    except Exception as e:
        logger.error("Error calculating invoice total: %s", e, exc_info=True)
        result['error'] = 'Внутренняя ошибка сервера'
        return JsonResponse(result, status=500)


@staff_member_required
@require_GET
def get_container_data(request, container_id: int):
    try:
        container = Container.objects.get(id=container_id)
        data = {
            'free_days': container.free_days,
            'storage_cost': str(container.storage_cost),
            'status': container.status,
        }
        return JsonResponse(data)
    except Container.DoesNotExist:
        return JsonResponse({'error': 'Container not found'}, status=404)


@staff_member_required
@require_GET
def get_client_balance(request):
    client_id: Optional[str] = request.GET.get('client_id')
    if client_id and client_id.isdigit():
        try:
            client = Client.objects.get(id=client_id)
            balance = client.balance

            if balance > 0:
                status = 'Переплата'
            elif balance < 0:
                status = 'Задолженность'
            else:
                status = 'Ноль'

            return JsonResponse({
                'balance': str(balance),
                'total_balance': str(balance),
                'status': status,
                'balance_status': client.balance_status,
                'balance_color': client.balance_color,
            })
        except Client.DoesNotExist:
            return JsonResponse({'error': 'Client not found'}, status=404)
    return JsonResponse({'error': 'Invalid client ID'}, status=400)


@staff_member_required
def get_payment_objects(request):
    object_type = request.GET.get('type', '').strip().lower()
    logger.debug("get_payment_objects called with type: %s", object_type)

    if not object_type:
        return JsonResponse({'error': 'Type parameter is required'}, status=400)

    ALLOWED_MODELS = {
        'client': Client,
        'warehouse': Warehouse,
        'line': Line,
        'carrier': Carrier,
        'company': Company,
    }

    model = ALLOWED_MODELS.get(object_type)
    if not model:
        logger.warning("Disallowed model type requested: %s", object_type)
        return JsonResponse({'error': f'Invalid type: {object_type}'}, status=400)

    try:
        cache_key = f'payment_objects:{object_type}'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        objects = model.objects.all().order_by('name' if hasattr(model, 'name') else 'id')
        objects_list = [{'id': obj.id, 'name': getattr(obj, 'name', str(obj))} for obj in objects]
        result = {'type': object_type, 'objects': objects_list}
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
    except Exception as e:
        logger.error("Error getting objects for type %s: %s", object_type, e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def search_partners_api(request):
    entity_type = request.GET.get('entity_type', '').strip().upper()
    search_query = request.GET.get('search', '').strip()

    if not entity_type:
        return JsonResponse({'error': 'Entity type is required'}, status=400)

    model_map = {
        'CLIENT': Client,
        'WAREHOUSE': Warehouse,
        'LINE': Line,
        'CARRIER': Carrier,
        'COMPANY': Company,
    }

    if entity_type not in model_map:
        return JsonResponse({'error': f'Invalid entity type: {entity_type}'}, status=400)

    try:
        model = model_map[entity_type]

        if search_query and len(search_query) >= 2:
            name_filter = Q(name__icontains=search_query)
            if hasattr(model, 'short_name'):
                name_filter |= Q(short_name__icontains=search_query)
            objects = model.objects.filter(name_filter).order_by('name' if hasattr(model, 'name') else 'id')[:10]
        else:
            objects = model.objects.none()

        objects_list = [
            {'id': obj.id, 'name': getattr(obj, 'name', str(obj)), 'type': entity_type}
            for obj in objects
        ]
        return JsonResponse({'type': entity_type, 'objects': objects_list})
    except Exception as e:
        logger.error("Error searching partners for type %s: %s", entity_type, e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def get_invoice_cars_api(request):
    from_entity_type = request.GET.get('from_entity_type')
    from_entity_id = request.GET.get('from_entity_id')
    to_entity_type = request.GET.get('to_entity_type')
    to_entity_id = request.GET.get('to_entity_id')
    search_query = request.GET.get('search', '').strip()

    if not all([from_entity_type, from_entity_id]):
        return JsonResponse({'error': 'From entity parameters are required'}, status=400)

    try:
        six_months_ago = timezone.now().date() - timedelta(days=180)
        cars = Car.objects.none()

        if from_entity_type == 'CLIENT':
            cars = Car.objects.filter(
                Q(client_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED', 'FLOATING']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
        elif from_entity_type == 'WAREHOUSE':
            cars = Car.objects.filter(
                Q(warehouse_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
        elif from_entity_type == 'LINE':
            cars = Car.objects.filter(
                Q(line_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
        elif from_entity_type == 'COMPANY':
            cars = Car.objects.filter(
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')

        if to_entity_type and to_entity_id:
            if to_entity_type == 'WAREHOUSE':
                cars = cars.filter(warehouse_id=to_entity_id)
            elif to_entity_type == 'CLIENT':
                cars = cars.filter(client_id=to_entity_id)

        if search_query:
            year_q = Q()
            if search_query.isdigit():
                try:
                    year_q = Q(year=int(search_query))
                except Exception:
                    year_q = Q()
            cars = cars.filter(
                Q(vin__icontains=search_query) |
                Q(brand__icontains=search_query) |
                year_q
            )

        limit = int(request.GET.get('limit', 200))
        cars = cars[:limit]

        cars_data = []
        for car in cars:
            total_cost = car.total_price or Decimal('0.00')
            cars_data.append({
                'id': car.id,
                'vin': car.vin,
                'brand': car.brand,
                'year': car.year,
                'status': car.status,
                'client_name': car.client.name if car.client else 'Не указан',
                'warehouse_name': car.warehouse.name if car.warehouse else 'Не указан',
                'unload_date': car.unload_date.strftime('%d.%m.%Y') if car.unload_date else 'Не указана',
                'transfer_date': car.transfer_date.strftime('%d.%m.%Y') if car.transfer_date else 'Не указана',
                'total_cost': f"{total_cost:.2f}",
                'storage_cost': float(car.storage_cost or 0),
                'ocean_freight': float(car.ocean_freight or 0),
                'ths': float(car.ths or 0),
                'delivery_fee': float(car.delivery_fee or 0),
                'transport_kz': float(car.transport_kz or 0),
            })

        return JsonResponse({'cars': cars_data})
    except Exception as e:
        logger.error("Error getting invoice cars: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def get_warehouse_cars_api(request):
    warehouse_id = request.GET.get('warehouse_id')
    search_query = request.GET.get('search', '').strip()

    if not warehouse_id:
        return JsonResponse({'error': 'Warehouse ID is required'}, status=400)

    try:
        month_ago = timezone.now().date() - timedelta(days=30)
        cars = Car.objects.filter(
            Q(warehouse_id=warehouse_id) &
            Q(status__in=['UNLOADED', 'TRANSFERRED']) &
            Q(unload_date__gte=month_ago)
        ).select_related('client', 'warehouse', 'container', 'line', 'carrier')

        if search_query:
            year_q = Q()
            if search_query.isdigit():
                try:
                    year_q = Q(year=int(search_query))
                except Exception:
                    year_q = Q()
            cars = cars.filter(
                Q(vin__icontains=search_query) |
                Q(brand__icontains=search_query) |
                year_q
            )

        limit = int(request.GET.get('limit', 200))
        cars = cars[:limit]

        cars_data = []
        for car in cars:
            warehouse_services = (
                (car.unload_fee or Decimal('0.00')) +
                (car.delivery_fee or Decimal('0.00')) +
                (car.loading_fee or Decimal('0.00')) +
                (car.docs_fee or Decimal('0.00')) +
                (car.transfer_fee or Decimal('0.00')) +
                (car.transit_declaration or Decimal('0.00')) +
                (car.export_declaration or Decimal('0.00')) +
                (car.extra_costs or Decimal('0.00')) +
                (car.complex_fee or Decimal('0.00')) +
                (car.storage_cost or Decimal('0.00'))
            )
            cars_data.append({
                'id': car.id,
                'vin': car.vin,
                'brand': car.brand,
                'year': car.year,
                'status': car.status,
                'client_name': car.client.name if car.client else 'Не указан',
                'unload_date': car.unload_date.strftime('%d.%m.%Y') if car.unload_date else 'Не указана',
                'transfer_date': car.transfer_date.strftime('%d.%m.%Y') if car.transfer_date else 'Не указана',
                'warehouse_services_cost': f"{warehouse_services:.2f}",
                'storage_cost': f"{car.storage_cost or 0:.2f}",
                'total_warehouse_cost': f"{warehouse_services:.2f}",
            })

        return JsonResponse({'cars': cars_data})
    except Exception as e:
        logger.error("Error getting warehouse cars: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def get_warehouses(request):
    try:
        cache_key = 'ref:warehouses_list'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        warehouses = Warehouse.objects.all().order_by('name')
        warehouses_data = [{'id': w.id, 'name': w.name} for w in warehouses]
        result = {'warehouses': warehouses_data}
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
    except Exception as e:
        logger.error("Error loading warehouses: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def get_companies(request):
    try:
        cache_key = 'ref:companies_list'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        companies = Company.objects.all().order_by('name')
        companies_data = [{'id': c.id, 'name': c.name} for c in companies]
        result = {'companies': companies_data}
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
    except Exception as e:
        logger.error("Error loading companies: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def get_available_services(request, car_id):
    service_type = request.GET.get('type')
    if not service_type:
        return JsonResponse({'error': 'Service type is required'}, status=400)

    try:
        car = Car.objects.select_related('warehouse', 'line', 'carrier').get(id=car_id)
        services = []

        if service_type == 'warehouse':
            warehouse_id = request.GET.get('warehouse_id')
            if warehouse_id:
                warehouse = Warehouse.objects.get(id=warehouse_id)
            elif car.warehouse:
                warehouse = car.warehouse
            else:
                return JsonResponse({'services': []})

            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='WAREHOUSE'
            ).values_list('service_id', flat=True))
            services = [
                {'id': s.id, 'name': s.name, 'price': float(s.default_price)}
                for s in WarehouseService.objects.filter(warehouse=warehouse).exclude(id__in=existing_service_ids)
            ]

        elif service_type == 'line' and car.line:
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='LINE'
            ).values_list('service_id', flat=True))
            services = [
                {'id': s.id, 'name': s.name, 'price': float(s.default_price)}
                for s in LineService.objects.filter(line=car.line).exclude(id__in=existing_service_ids)
            ]

        elif service_type == 'carrier' and car.carrier:
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='CARRIER'
            ).values_list('service_id', flat=True))
            services = [
                {'id': s.id, 'name': s.name, 'price': float(s.default_price)}
                for s in CarrierService.objects.filter(carrier=car.carrier).exclude(id__in=existing_service_ids)
            ]

        elif service_type == 'company':
            company_id = request.GET.get('company_id')
            if not company_id:
                return JsonResponse({'services': []})
            company = Company.objects.get(id=company_id)
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='COMPANY'
            ).values_list('service_id', flat=True))
            services = [
                {'id': s.id, 'name': s.name, 'price': float(s.default_price)}
                for s in CompanyService.objects.filter(company=company, is_active=True).exclude(id__in=existing_service_ids)
            ]

        return JsonResponse({'services': services})
    except Car.DoesNotExist:
        return JsonResponse({'error': 'Car not found'}, status=404)
    except Exception as e:
        logger.error("Error getting available services: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def add_services(request, car_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)

    try:
        data = json.loads(request.body)
        service_type = data.get('service_type')
        service_ids = [int(sid) for sid in data.get('service_ids', [])]

        if not service_type or not service_ids:
            return JsonResponse({'error': 'Service type and IDs are required'}, status=400)

        service_type_upper = service_type.upper()
        car = Car.objects.get(id=car_id)

        MODEL_MAP = {
            'WAREHOUSE': WarehouseService,
            'LINE': LineService,
            'CARRIER': CarrierService,
            'COMPANY': CompanyService,
        }
        service_model = MODEL_MAP.get(service_type_upper)
        if not service_model:
            return JsonResponse({'error': f'Invalid service type: {service_type}'}, status=400)

        services_by_id = {s.id: s for s in service_model.objects.filter(id__in=service_ids)}

        existing_ids = set(CarService.objects.filter(
            car=car, service_type=service_type_upper, service_id__in=service_ids
        ).values_list('service_id', flat=True))

        to_create = []
        skipped_count = 0
        errors = []

        for service_id in service_ids:
            if service_id in existing_ids:
                skipped_count += 1
                continue

            service = services_by_id.get(service_id)
            if not service:
                errors.append(f'Service {service_id} not found')
                continue

            custom_price = service.default_price
            markup_amount = getattr(service, 'default_markup', 0) or 0

            from core.service_codes import is_storage_service
            if service_type_upper == 'WAREHOUSE' and is_storage_service(service):
                days = Decimal(str(car.days or 0))
                custom_price = days * Decimal(str(service.default_price or 0))
                markup_amount = days * Decimal(str(getattr(service, 'default_markup', 0) or 0))

            to_create.append(CarService(
                car=car,
                service_type=service_type_upper,
                service_id=service_id,
                custom_price=custom_price,
                markup_amount=markup_amount,
            ))

        added_count = 0
        if to_create:
            CarService.objects.bulk_create(to_create, ignore_conflicts=True)
            added_count = CarService.objects.filter(
                car=car, service_type=service_type_upper,
                service_id__in=[s.service_id for s in to_create]
            ).count() - len(existing_ids)

            try:
                if car.client and car.client.tariff_type in ('FIXED', 'FLEXIBLE') and car.status != 'TRANSFERRED':
                    from core.services.car_service_manager import apply_client_tariff_for_car
                    apply_client_tariff_for_car(car)
                    car.calculate_total_price()
                    Car.objects.filter(pk=car.pk).update(total_price=car.total_price)
            except Exception as e:
                logger.error("Error re-applying client tariff after add_services: %s", e)

        if added_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Добавлено {added_count} услуг',
                'added_count': added_count,
                'skipped_count': skipped_count,
            })
        elif skipped_count > 0:
            return JsonResponse({
                'success': False,
                'already_exists': True,
                'message': f'Все выбранные услуги ({skipped_count}) уже добавлены к этому авто',
                'added_count': 0,
                'skipped_count': skipped_count,
            })
        else:
            return JsonResponse({
                'success': False,
                'message': 'Не удалось добавить услуги' + (f': {"; ".join(errors)}' if errors else ''),
                'added_count': 0,
            })
    except Car.DoesNotExist:
        return JsonResponse({'error': f'Автомобиль с ID {car_id} не найден'}, status=404)
    except Exception as e:
        logger.error("Error in add_services for car %s: %s", car_id, e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def search_counterparties(request):
    query = request.GET.get('q', '').strip()
    if len(query) < 1:
        return JsonResponse({'results': []})

    cache_key = f'search_counterparties:{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    results = []
    search_config = [
        (Company, Q(name__icontains=query), 'company', '\U0001f3e2'),
        (Client, Q(name__icontains=query), 'client', '\U0001f464'),
        (Warehouse, Q(name__icontains=query), 'warehouse', '\U0001f3ed'),
        (Line, Q(name__icontains=query), 'line', '\U0001f6a2'),
        (Carrier, Q(name__icontains=query) | Q(contact_person__icontains=query), 'carrier', '\U0001f69a'),
    ]
    for model, q_filter, type_name, icon in search_config:
        for pk, name in model.objects.filter(q_filter).values_list('pk', 'name')[:5]:
            results.append({
                'id': f'{type_name}_{pk}',
                'text': f'{icon} {name}',
                'type': type_name,
                'type_id': pk,
            })

    result = {'results': results}
    cache.set(cache_key, result, 60)
    return JsonResponse(result)


@staff_member_required
@require_GET
def search_cars(request):
    query = request.GET.get('q', '').strip()
    selected = request.GET.getlist('selected', [])

    if not query:
        cars = Car.objects.exclude(pk__in=selected).select_related('client')[:15]
    else:
        cars = Car.objects.filter(
            Q(vin__icontains=query) | Q(brand__icontains=query)
        ).exclude(pk__in=selected).select_related('client')[:15]

    results = []
    for car in cars:
        client_name = car.client.name if car.client else 'Без клиента'
        results.append({
            'id': car.pk,
            'text': f'{car.brand} {car.year} ({car.vin})',
            'vin': car.vin,
            'brand': car.brand,
            'year': car.year,
            'client': client_name,
            'client_name': client_name,
            'status': car.status,
        })

    return JsonResponse({'results': results})


@staff_member_required
@require_GET
def search_invoices(request):
    """AJAX autocomplete for invoices — search by number, external_number, or counterparty name."""
    from core.models_billing import NewInvoice

    query = request.GET.get('q', '').strip()
    exclude_id = request.GET.get('exclude', '')

    if len(query) < 2:
        return JsonResponse({'results': []})

    qs = NewInvoice.objects.filter(
        Q(number__icontains=query)
        | Q(external_number__icontains=query)
        | Q(issuer_company__name__icontains=query)
        | Q(issuer_warehouse__name__icontains=query)
        | Q(issuer_line__name__icontains=query)
        | Q(issuer_carrier__name__icontains=query)
        | Q(recipient_client__name__icontains=query)
        | Q(recipient_company__name__icontains=query)
    )

    if exclude_id and exclude_id.isdigit():
        qs = qs.exclude(pk=int(exclude_id))

    qs = qs.select_related(
        'issuer_company', 'issuer_warehouse', 'issuer_line', 'issuer_carrier',
        'recipient_client', 'recipient_company',
    ).order_by('-date')[:15]

    results = []
    for inv in qs:
        issuer = inv.issuer
        recipient = inv.recipient
        label = inv.number
        if inv.external_number:
            label += f' ({inv.external_number})'
        parts = [label]
        if issuer:
            parts.append(str(issuer))
        if recipient:
            parts.append(f'→ {recipient}')
        parts.append(f'{inv.total:.2f} €')

        results.append({
            'id': inv.number,
            'text': ' · '.join(parts),
            'pk': inv.pk,
            'number': inv.number,
            'total': str(inv.total),
            'status': inv.status,
        })

    return JsonResponse({'results': results})
