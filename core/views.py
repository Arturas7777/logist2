from django.http import HttpResponse, JsonResponse
import re
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.template.loader import render_to_string
from django.db.models import Q, Sum
from django.core.cache import cache
from datetime import timedelta, datetime
from typing import Optional
from .models import Car, Container, Client, Warehouse, Line, Company, Carrier, CarService, WarehouseService, LineService, CarrierService, CompanyService
from .models_billing import NewInvoice as Invoice, Transaction as Payment
from .services.comparison_service import ComparisonService
from .pagination import paginate_queryset, paginated_json_response, PaginationHelper
from .cache_utils import cache_company_stats, cache_client_stats, cache_warehouse_stats, cache_comparison_data, CACHE_TIMEOUTS
from decimal import Decimal
import logging
from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required

logger = logging.getLogger('django')

@staff_member_required
def car_list_api(request):
    """Возвращает список автомобилей для клиента, отфильтрованный по статусу."""
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
    """Вычисляет общую сумму для выбранных автомобилей (один SQL-запрос)."""
    car_ids = request.GET.get('car_ids', '').split(',')
    car_ids = [int(cid) for cid in car_ids if cid.strip().isdigit()]

    result = {'total_amount': '0.00'}
    if not car_ids:
        return JsonResponse(result)

    try:
        agg = Car.objects.filter(id__in=car_ids).aggregate(
            total=Sum('total_price')
        )
        total = agg['total'] or Decimal('0.00')
        result['total_amount'] = str(total)
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Error calculating invoice total: {e}")
        result['error'] = str(e)
        return JsonResponse(result, status=500)

@staff_member_required
@require_GET
def get_container_data(request, container_id: int):
    """Возвращает данные контейнера по ID."""
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
    """Возвращает баланс клиента по ID."""
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
def register_payment(request):
    """Регистрирует платеж для инвойса."""
    if request.method != 'POST':
        logger.warning("Invalid request method for register_payment")
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

    invoice_id: Optional[str] = request.POST.get('invoice_id')
    amount_raw = request.POST.get('amount', '0')
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Некорректная сумма'}, status=400)

    payment_method: Optional[str] = request.POST.get('payment_type', 'TRANSFER')
    from_balance: bool = request.POST.get('from_balance') == 'on'
    description: str = request.POST.get('description', '')
    payer_id: Optional[str] = request.POST.get('payer_id')

    logger.debug("Registering payment: invoice_id=%s, amount=%s, method=%s", invoice_id, amount, payment_method)

    try:
        invoice = Invoice.objects.get(id=invoice_id) if invoice_id else None
        payer = Client.objects.get(id=payer_id) if payer_id else None

        if from_balance and not payer:
            logger.error("Payer required for balance payment")
            return JsonResponse({'status': 'error', 'message': 'Плательщик обязателен для оплаты с баланса'}, status=400)

        if from_balance and payer and payer.balance < amount:
            return JsonResponse({'status': 'error', 'message': 'Недостаточно средств на балансе'}, status=400)

        method = 'BALANCE' if from_balance else (payment_method or 'TRANSFER')

        payment = Payment(
            type='PAYMENT',
            method=method,
            status='COMPLETED',
            invoice=invoice,
            amount=amount,
            description=description or f'Платёж на сумму {amount}',
            from_client=payer,
            to_company=Company.get_default(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        payment.save()

        if invoice:
            invoice.paid_amount += amount
            invoice.update_status()
            invoice.save(update_fields=['paid_amount', 'status', 'updated_at'])

        logger.debug("Payment saved: id=%s", payment.pk)

        return JsonResponse({
            'status': 'success',
            'message': f'Платеж на сумму {amount} зарегистрирован',
            'client_balance': str(payer.balance) if payer else None,
        })
    except (Invoice.DoesNotExist, Client.DoesNotExist) as e:
        logger.error(f"Error registering payment: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=404)
    except ValueError as e:
        logger.error(f"Error registering payment: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Unexpected error registering payment: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@staff_member_required
def company_dashboard(request):
    """Дашборд для Caromoto Lithuania"""
    from django.contrib import admin
    from .services.dashboard_service import DashboardService

    service = DashboardService()
    dashboard_data = service.get_full_dashboard_context()

    # Start with admin site context (sidebar_nav, user, site_header, etc.)
    context = admin.site.each_context(request)
    # Then overlay dashboard data (preserving admin keys like sidebar_nav)
    context.update(dashboard_data)

    # Pass raw Python dicts — json_script template tag handles serialization
    context['revenue_expenses_chart_json'] = context['revenue_expenses_chart']
    context['invoices_by_status_json'] = context['invoices_by_status']
    context['cars_by_status_json'] = {
        k: v for k, v in context['cars_by_status'].items() if k != 'total'
    }
    context['expenses_by_category_json'] = context.get('expenses_by_category', [])
    context['income_by_category_json'] = context.get('income_by_category', [])

    return render(request, 'admin/company_dashboard.html', context)

@staff_member_required
def get_payment_objects(request):
    """AJAX view для получения списка объектов определенного типа для формы платежа"""
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
        logger.warning(f"Disallowed model type requested: {object_type}")
        return JsonResponse({'error': f'Invalid type: {object_type}'}, status=400)
    
    try:
        cache_key = f'payment_objects:{object_type}'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        objects = model.objects.all().order_by('name' if hasattr(model, 'name') else 'id')
        
        objects_list = [{
            'id': obj.id,
            'name': getattr(obj, 'name', str(obj))
        } for obj in objects]
        
        result = {
            'type': object_type,
            'objects': objects_list
        }
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Error getting objects for type {object_type}: {e}")
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
@require_GET
def search_partners_api(request):
    """API для поиска партнеров по типу и названию"""
    entity_type = request.GET.get('entity_type', '').strip().upper()
    search_query = request.GET.get('search', '').strip()
    
    if not entity_type:
        response = JsonResponse({'error': 'Entity type is required'}, status=400)
        response['Content-Type'] = 'application/json'
        return response
    
    try:
        # Определяем модель на основе типа
        model_map = {
            'CLIENT': Client,
            'WAREHOUSE': Warehouse,
            'LINE': Line,
            'CARRIER': Carrier,
            'COMPANY': Company
        }
        
        if entity_type not in model_map:
            response = JsonResponse({'error': f'Invalid entity type: {entity_type}'}, status=400)
            response['Content-Type'] = 'application/json'
            return response
        
        model = model_map[entity_type]
        
        # Поиск по названию
        if search_query and len(search_query) >= 2:
            # Создаем базовый фильтр
            name_filter = Q(name__icontains=search_query)
            
            # Добавляем фильтр по short_name, если модель его поддерживает
            if hasattr(model, 'short_name'):
                name_filter |= Q(short_name__icontains=search_query)
            
            objects = model.objects.filter(name_filter).order_by('name' if hasattr(model, 'name') else 'id')[:10]
        else:
            # Если поисковый запрос пустой или слишком короткий, не возвращаем ничего
            objects = model.objects.none()
        
        # Формируем список объектов для JSON
        objects_list = []
        for obj in objects:
            display_name = getattr(obj, 'name', str(obj))
            objects_list.append({
                'id': obj.id,
                'name': display_name,
                'type': entity_type
            })
        
        response = JsonResponse({
            'type': entity_type,
            'objects': objects_list
        })
        response['Content-Type'] = 'application/json'
        return response
        
    except Exception as e:
        logger.error(f"Error searching partners for type {entity_type}: {e}")
        response = JsonResponse({'error': str(e)}, status=500)
        response['Content-Type'] = 'application/json'
        return response

@staff_member_required
@require_GET
def get_invoice_cars_api(request):
    """API для получения автомобилей для инвойса - показываем автомобили, связанные с отправителем"""
    from_entity_type = request.GET.get('from_entity_type')
    from_entity_id = request.GET.get('from_entity_id')
    to_entity_type = request.GET.get('to_entity_type')
    to_entity_id = request.GET.get('to_entity_id')
    search_query = request.GET.get('search', '').strip()
    
    logger.debug("get_invoice_cars_api: from=%s/%s, to=%s/%s", from_entity_type, from_entity_id, to_entity_type, to_entity_id)
    
    # Проверяем наличие отправителя (обязательно) и получателя (опционально)
    if not all([from_entity_type, from_entity_id]):
        logger.warning(f"Missing from_entity parameters: from_entity_type={from_entity_type}, from_entity_id={from_entity_id}")
        response = JsonResponse({'error': 'From entity parameters are required'}, status=400)
        response['Content-Type'] = 'application/json'
        return response
    
    try:
        # Получаем автомобили за последние 6 месяцев
        six_months_ago = timezone.now().date() - timedelta(days=180)
        cars = Car.objects.none()
        
        # Показываем автомобили, связанные с отправителем
        if from_entity_type == 'CLIENT':
            # Если отправитель - клиент, показываем автомобили этого клиента
            cars = Car.objects.filter(
                Q(client_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED', 'FLOATING']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
            
        elif from_entity_type == 'WAREHOUSE':
            # Если отправитель - склад, показываем автомобили на этом складе
            cars = Car.objects.filter(
                Q(warehouse_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
            
        elif from_entity_type == 'LINE':
            # Если отправитель - линия, показываем автомобили, связанные с этой линией
            cars = Car.objects.filter(
                Q(line_id=from_entity_id) &
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
            
        elif from_entity_type == 'COMPANY':
            # Если отправитель - компания, показываем все автомобили
            cars = Car.objects.filter(
                Q(status__in=['UNLOADED', 'IN_PORT', 'TRANSFERRED']) &
                Q(unload_date__gte=six_months_ago)
            ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
        
        # Дополнительная фильтрация по получателю, если указан
        if to_entity_type and to_entity_id:
            if to_entity_type == 'WAREHOUSE':
                # Если получатель - склад, фильтруем автомобили на этом складе
                cars = cars.filter(warehouse_id=to_entity_id)
            elif to_entity_type == 'CLIENT':
                # Если получатель - клиент, фильтруем автомобили этого клиента
                cars = cars.filter(client_id=to_entity_id)
        
        # Поиск по автомобилям
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
                # Добавляем поля для расчета стоимости услуг
                'storage_cost': float(car.storage_cost or 0),
                'ocean_freight': float(car.ocean_freight or 0),
                'ths': float(car.ths or 0),
                'delivery_fee': float(car.delivery_fee or 0),
                'transport_kz': float(car.transport_kz or 0)
            })
        
        response = JsonResponse({'cars': cars_data})
        response['Content-Type'] = 'application/json'
        return response
        
    except Exception as e:
        logger.error("Error getting invoice cars: %s", e)
        response = JsonResponse({'error': str(e)}, status=500)
        response['Content-Type'] = 'application/json'
        return response

@staff_member_required
@require_GET
def get_warehouse_cars_api(request):
    """API для получения доступных автомобилей для склада (Caromoto Lithuania)"""
    warehouse_id = request.GET.get('warehouse_id')
    search_query = request.GET.get('search', '').strip()
    
    if not warehouse_id:
        response = JsonResponse({'error': 'Warehouse ID is required'}, status=400)
        response['Content-Type'] = 'application/json'
        return response
    
    try:
        # Получаем автомобили за последний месяц для указанного склада
        month_ago = timezone.now().date() - timedelta(days=30)
        
        # Ищем автомобили, которые были разгружены или переданы на склад за последний месяц
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
        
        # Формируем данные для каждого автомобиля
        cars_data = []
        for car in cars:
            # Вычисляем стоимость складских услуг
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
                'total_warehouse_cost': f"{warehouse_services:.2f}"
            })
        
        response = JsonResponse({'cars': cars_data})
        response['Content-Type'] = 'application/json'
        return response
        
    except Exception as e:
        logger.error(f"Error getting warehouse cars: {e}")
        response = JsonResponse({'error': str(e)}, status=500)
        response['Content-Type'] = 'application/json'
        return response

@staff_member_required
def comparison_dashboard(request):
    """Дашборд для сравнения сумм между расчетами и счетами склада.
    Оптимизировано: batch-запросы вместо N+1, с кэшированием.
    """
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    try:
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Неверный формат даты. Используйте YYYY-MM-DD'}, status=400)
    
    cache_key = f'comparison_dashboard:{start_date}:{end_date}'
    cached_context = cache.get(cache_key)

    if cached_context is not None:
        cached_context['start_date'] = start_date
        cached_context['end_date'] = end_date
        return render(request, 'admin/comparison_dashboard.html', cached_context)

    comparison_service = ComparisonService()
    report = comparison_service.get_comparison_report(start_date, end_date)

    client_comparisons = comparison_service.batch_compare_clients(start_date, end_date)
    warehouse_comparisons = comparison_service.batch_compare_warehouses(start_date, end_date)

    discrepancies = [
        {'type': 'client_comparison', 'entity': c['client_name'], 'comparison': c}
        for c in client_comparisons if c['status'] not in ('match', 'no_data')
    ] + [
        {'type': 'warehouse_comparison', 'entity': w['warehouse_name'], 'comparison': w}
        for w in warehouse_comparisons if w['status'] not in ('match', 'no_data')
    ]

    context = {
        'report': report,
        'discrepancies': discrepancies,
        'client_comparisons': client_comparisons,
        'warehouse_comparisons': warehouse_comparisons,
        'start_date': start_date,
        'end_date': end_date,
    }
    
    cache.set(cache_key, context, CACHE_TIMEOUTS['short'])
    return render(request, 'admin/comparison_dashboard.html', context)

@staff_member_required
@require_GET
def compare_car_costs_api(request):
    """API для сравнения стоимости конкретного автомобиля"""
    car_id = request.GET.get('car_id')
    
    if not car_id:
        return JsonResponse({'error': 'Car ID is required'}, status=400)
    
    try:
        car = Car.objects.get(id=car_id)
        comparison_service = ComparisonService()
        result = comparison_service.compare_car_costs_with_warehouse_invoices(car)
        return JsonResponse(result)
    except Car.DoesNotExist:
        return JsonResponse({'error': 'Car not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
@require_GET
def compare_client_costs_api(request):
    """API для сравнения стоимости автомобилей клиента"""
    client_id = request.GET.get('client_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not client_id:
        return JsonResponse({'error': 'Client ID is required'}, status=400)
    
    try:
        client = Client.objects.get(id=client_id)
        comparison_service = ComparisonService()
        
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        result = comparison_service.compare_client_costs_with_warehouse_invoices(
            client, start_date, end_date
        )
        return JsonResponse(result)
    except Client.DoesNotExist:
        return JsonResponse({'error': 'Client not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
@require_GET
def compare_warehouse_costs_api(request):
    """API для сравнения стоимости услуг склада"""
    warehouse_id = request.GET.get('warehouse_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not warehouse_id:
        return JsonResponse({'error': 'Warehouse ID is required'}, status=400)
    
    try:
        warehouse = Warehouse.objects.get(id=warehouse_id)
        comparison_service = ComparisonService()
        
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        result = comparison_service.compare_warehouse_costs_with_payments(
            warehouse, start_date, end_date
        )
        return JsonResponse(result)
    except Warehouse.DoesNotExist:
        return JsonResponse({'error': 'Warehouse not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
@require_GET
def get_discrepancies_api(request):
    """API для получения расхождений"""
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    try:
        comparison_service = ComparisonService()
        
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        discrepancies = comparison_service.find_discrepancies(start_date, end_date)
        return JsonResponse({'discrepancies': discrepancies})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
def get_warehouses(request):
    """Получает список всех активных складов"""
    try:
        cache_key = 'ref:warehouses_list'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        warehouses = Warehouse.objects.all().order_by('name')
        warehouses_data = [{
            'id': warehouse.id,
            'name': warehouse.name
        } for warehouse in warehouses]
        
        result = {'warehouses': warehouses_data}
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Error loading warehouses: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_member_required
def get_companies(request):
    """Получает список всех компаний"""
    try:
        cache_key = 'ref:companies_list'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        companies = Company.objects.all().order_by('name')
        companies_data = [{
            'id': company.id,
            'name': company.name
        } for company in companies]
        
        result = {'companies': companies_data}
        cache.set(cache_key, result, CACHE_TIMEOUTS['medium'])
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Error loading companies: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_member_required
def get_available_services(request, car_id):
    """Получает доступные услуги для добавления к автомобилю"""
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
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in WarehouseService.objects.filter(
                warehouse=warehouse
            ).exclude(id__in=existing_service_ids)]
            
        elif service_type == 'line' and car.line:
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='LINE'
            ).values_list('service_id', flat=True))
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in LineService.objects.filter(
                line=car.line
            ).exclude(id__in=existing_service_ids)]
            
        elif service_type == 'carrier' and car.carrier:
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='CARRIER'
            ).values_list('service_id', flat=True))
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in CarrierService.objects.filter(
                carrier=car.carrier
            ).exclude(id__in=existing_service_ids)]
        
        elif service_type == 'company':
            company_id = request.GET.get('company_id')
            if not company_id:
                return JsonResponse({'services': []})
            
            company = Company.objects.get(id=company_id)
            existing_service_ids = set(CarService.objects.filter(
                car=car, service_type='COMPANY'
            ).values_list('service_id', flat=True))
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in CompanyService.objects.filter(
                company=company, is_active=True
            ).exclude(id__in=existing_service_ids)]
        
        return JsonResponse({'services': services})
        
    except Car.DoesNotExist:
        return JsonResponse({'error': 'Car not found'}, status=404)
    except Exception as e:
        logger.error("Error getting available services: %s", e, exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
def add_services(request, car_id):
    """Добавляет выбранные услуги к автомобилю (batch-оптимизация)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        service_type = data.get('service_type')
        service_ids = data.get('service_ids', [])
        
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

            if service_type_upper == 'WAREHOUSE' and service.name == 'Хранение':
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
            added_count = len(to_create)
        
        if added_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Добавлено {added_count} услуг',
                'added_count': added_count,
                'skipped_count': skipped_count
            })
        elif skipped_count > 0:
            return JsonResponse({
                'success': False,
                'already_exists': True,
                'message': f'Все выбранные услуги ({skipped_count}) уже добавлены к этому авто',
                'added_count': 0,
                'skipped_count': skipped_count
            })
        else:
            return JsonResponse({
                'success': False,
                'message': 'Не удалось добавить услуги' + (f': {"; ".join(errors)}' if errors else ''),
                'added_count': 0
            })
    except Car.DoesNotExist:
        return JsonResponse({'error': f'Автомобиль с ID {car_id} не найден'}, status=404)
    except Exception as e:
        logger.error(f"Error in add_services for car {car_id}: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@staff_member_required
def get_container_photos_json(request, container_id):
    """
    API endpoint для получения списка фотографий контейнера.
    Вызывается через AJAX при клике на раздел "Фотографии контейнера".
    """
    try:
        from .models import Container
        from .models_website import ContainerPhoto
        
        container = Container.objects.get(id=container_id)
        
        photos_data = []
        for photo in container.photos.only('id', 'photo', 'thumbnail', 'photo_type').all():
            # Ensure URLs have /media/ prefix
            photo_url = photo.photo.url if photo.photo else ''
            if photo_url and not photo_url.startswith('/media/') and not photo_url.startswith('http'):
                photo_url = '/media/' + photo_url.lstrip('/')
            
            thumb_url = photo.thumbnail.url if photo.thumbnail else photo_url
            if thumb_url and not thumb_url.startswith('/media/') and not thumb_url.startswith('http'):
                thumb_url = '/media/' + thumb_url.lstrip('/')
            
            photos_data.append({
                'id': photo.id,
                'url': photo_url,
                'thumbnail': thumb_url,
                'type': photo.photo_type or 'GENERAL'
            })
        
        return JsonResponse({
            'success': True,
            'photos': photos_data,
            'count': len(photos_data)
        })
    except Container.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Container not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
def sync_container_photos_from_gdrive(request, container_id):
    """
    Синхронизирует фотографии контейнера с Google Drive через Celery.
    """
    try:
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'Only POST method allowed'}, status=405)
        
        container = Container.objects.get(id=container_id)
        folder_url = container.google_drive_folder_url
        
        from .tasks import sync_container_photos_gdrive_task
        sync_container_photos_gdrive_task.delay(container_id, folder_url or None)
        
        message = 'Загрузка фотографий начата. '
        if folder_url:
            message += 'Используется указанная ссылка на папку.'
        else:
            message += 'Ищем папку по номеру контейнера.'
        message += ' Обновите страницу через 1-2 минуты.'
        
        return JsonResponse({
            'success': True,
            'message': message,
            'photos_count': 0
        })
        
    except Container.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Контейнер не найден'}, status=404)
    except Exception as e:
        logger.error(f"Error syncing Google Drive photos: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_GET
def search_counterparties(request):
    """
    API для поиска контрагентов (клиенты, склады, линии, перевозчики, компании)
    Используется для автокомплита в форме инвойса.
    """
    query = request.GET.get('q', '').strip()
    
    if len(query) < 1:
        return JsonResponse({'results': []})
    
    cache_key = f'search_counterparties:{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    results = []
    
    search_config = [
        (Company, Q(name__icontains=query), 'company', '🏢'),
        (Client, Q(name__icontains=query), 'client', '👤'),
        (Warehouse, Q(name__icontains=query), 'warehouse', '🏭'),
        (Line, Q(name__icontains=query), 'line', '🚢'),
        (Carrier, Q(name__icontains=query) | Q(contact_person__icontains=query), 'carrier', '🚚'),
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
    """
    API для поиска автомобилей по VIN, марке
    Используется для автокомплита в форме инвойса
    """
    query = request.GET.get('q', '').strip()
    selected = request.GET.getlist('selected', [])  # Уже выбранные ID
    
    # Если запрос пустой - показываем первые 15 авто
    if not query:
        cars = Car.objects.exclude(pk__in=selected).select_related('client')[:15]
    else:
        # Исключаем уже выбранные
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
    
    return JsonResponse({'results': results})# API endpoints for AutoTransport
