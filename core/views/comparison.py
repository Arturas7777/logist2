"""Comparison dashboard and cost-comparison API endpoints."""
import logging
from datetime import datetime

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.core.cache import cache
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required

from core.models import Car, Client, Warehouse
from core.services.comparison_service import ComparisonService
from core.cache_utils import CACHE_TIMEOUTS

logger = logging.getLogger(__name__)


@staff_member_required
def comparison_dashboard(request):
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
    car_id = request.GET.get('car_id')
    if not car_id:
        return JsonResponse({'error': 'Car ID is required'}, status=400)

    try:
        car = Car.objects.get(id=car_id)
        result = ComparisonService().compare_car_costs_with_warehouse_invoices(car)
        return JsonResponse(result)
    except Car.DoesNotExist:
        return JsonResponse({'error': 'Car not found'}, status=404)
    except Exception as e:
        logger.error("Error in compare_car_costs_api: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def compare_client_costs_api(request):
    client_id = request.GET.get('client_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if not client_id:
        return JsonResponse({'error': 'Client ID is required'}, status=400)

    try:
        client = Client.objects.get(id=client_id)
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        result = ComparisonService().compare_client_costs_with_warehouse_invoices(
            client, start_date, end_date
        )
        return JsonResponse(result)
    except Client.DoesNotExist:
        return JsonResponse({'error': 'Client not found'}, status=404)
    except Exception as e:
        logger.error("Error in compare_client_costs_api: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def compare_warehouse_costs_api(request):
    warehouse_id = request.GET.get('warehouse_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if not warehouse_id:
        return JsonResponse({'error': 'Warehouse ID is required'}, status=400)

    try:
        warehouse = Warehouse.objects.get(id=warehouse_id)
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        result = ComparisonService().compare_warehouse_costs_with_payments(
            warehouse, start_date, end_date
        )
        return JsonResponse(result)
    except Warehouse.DoesNotExist:
        return JsonResponse({'error': 'Warehouse not found'}, status=404)
    except Exception as e:
        logger.error("Error in compare_warehouse_costs_api: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
@require_GET
def get_discrepancies_api(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    try:
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        discrepancies = ComparisonService().find_discrepancies(start_date, end_date)
        return JsonResponse({'discrepancies': discrepancies})
    except Exception as e:
        logger.error("Error in get_discrepancies_api: %s", e, exc_info=True)
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)
