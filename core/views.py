from django.http import HttpResponse, JsonResponse
import re
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from datetime import timedelta, datetime
from typing import Optional
from .models import Car, InvoiceOLD as Invoice, Container, PaymentOLD as Payment, Client, Warehouse, Line, Company, Carrier, CarService, WarehouseService, LineService, CarrierService
from .services.comparison_service import ComparisonService
from .pagination import paginate_queryset, paginated_json_response, PaginationHelper
from .cache_utils import cache_company_stats, cache_client_stats, cache_warehouse_stats, cache_comparison_data
from decimal import Decimal
import logging
from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.apps import apps

logger = logging.getLogger('django')

def car_list_api(request):
    """Возвращает список автомобилей для клиента, отфильтрованный по статусу."""
    raw_client = (request.GET.get('client_id') or request.GET.get('client') or '').strip()
    # Нормализуем client_id: берём только цифры
    m = re.search(r"\d+", raw_client)
    raw_client = m.group(0) if m else ''
    search_query: str = request.GET.get('search', '').strip().lower()
    logger.info(f"car_list_api called with GET: {request.GET}")
    logger.info(f"Extracted client: '{raw_client}', search: '{search_query}'")

    try:
        client_id_int = int(raw_client)
    except (TypeError, ValueError):
        client_id_int = None

    if client_id_int:
        # Используем оптимизированный менеджер с prefetch
        allowed_statuses = ['UNLOADED', 'IN_PORT', 'FLOATING', 'TRANSFERRED']
        all_cars = Car.objects.by_client(client_id_int).filter(
            status__in=allowed_statuses
        ).select_related('client', 'warehouse', 'container', 'line', 'carrier')
        logger.info(f"All cars for client {client_id_int}: {all_cars.count()}")
        if all_cars.exists():
            for car in all_cars:
                logger.debug(f"Car {car.pk}: VIN={car.vin}, Brand={car.brand}, Year={car.year}, Status={car.status}, Transfer Date={car.transfer_date}")
        else:
            logger.warning(f"No cars found for client {client_id_int}")

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
            logger.info(f"Filtered cars with search '{search_query}': {all_cars.count()}")
            if all_cars.exists():
                for car in all_cars:
                    logger.debug(f"Filtered car: {car.pk} - VIN: {car.vin}, Brand: {car.brand}, Year: {car.year}, Status: {car.status}, Transfer Date={car.transfer_date}")

        html = render_to_string('admin/car_options.html', context={'cars': all_cars}, request=request)
        logger.debug(f"Returning HTML: {html[:100]}...")
        return HttpResponse(html, content_type='text/html')
    logger.warning("Invalid or missing client id, returning no client selected")
    return HttpResponse('<option class="no-results">Клиент не выбран</option>', content_type='text/html')

@require_GET
def get_invoice_total(request):
    """Вычисляет общую сумму для выбранных автомобилей."""
    car_ids = request.GET.get('car_ids', '').split(',')
    car_ids = [int(cid) for cid in car_ids if cid.strip().isdigit()]
    logger.info(f"get_invoice_total called with car_ids: {car_ids}")

    result = {'total_amount': '0.00'}
    if not car_ids:
        logger.warning("No valid car IDs provided, returning 0.00")
        return JsonResponse(result)

    try:
        cars = Car.objects.filter(id__in=car_ids).select_related(
            'client', 'warehouse', 'container', 'line', 'carrier'
        )
        if not cars.exists():
            logger.warning(f"No cars found for IDs: {car_ids}")
            result['error'] = 'No cars found for the provided IDs'
            return JsonResponse(result)
        logger.info(f"Cars found: {list(cars)}")
        for car in cars:
            logger.debug(f"Car {car.pk}: total_price={car.total_price}, storage_cost={car.storage_cost}")
    except Exception as e:
        logger.error(f"Error querying cars: {e}")
        result['error'] = f"Error querying cars: {e}"
        return JsonResponse(result, status=500)

    try:
        total = Decimal('0.00')
        for car in cars:
            current_price, total_price = car.calculate_total_price()
            add = total_price if (total_price and total_price > 0) else (current_price or Decimal('0.00'))
            total += Decimal(str(add))
        result['total_amount'] = str(total)
        logger.info(f"Calculated total_amount (in-memory): {result['total_amount']}")
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Error calculating total in-memory: {e}")
        result['error'] = str(e)
        return JsonResponse(result, status=500)

@require_GET
def get_container_data(request, container_id: int):
    """Возвращает данные контейнера по ID."""
    logger.info(f"get_container_data called with container_id: {container_id}")
    try:
        container = Container.objects.get(id=container_id)
        container.refresh_from_db()
        data = {
            'free_days': container.free_days,
            'storage_cost': str(container.storage_cost),
            'status': container.status,
        }
        logger.info(f"get_container_data: ID={container_id}, free_days={container.free_days}, storage_cost={container.storage_cost}")
        return JsonResponse(data)
    except Container.DoesNotExist:
        logger.error(f"Container not found: ID={container_id}")
        return JsonResponse({'error': 'Container not found'}, status=404)

@require_GET
def get_client_balance(request):
    """Возвращает детализированный баланс клиента по ID."""
    client_id: Optional[str] = request.GET.get('client_id')
    logger.info(f"get_client_balance called with client_id: {client_id}")
    if client_id and client_id.isdigit():
        try:
            from decimal import Decimal
            client = Client.objects.get(id=client_id)
            details = client.balance_details()

            # Совместимость с фронтендом: total_balance и status
            real_balance = client.real_balance
            total_balance = real_balance  # используем реальный баланс
            status = 'Переплата' if total_balance < 0 else ('Задолженность' if total_balance > 0 else 'Ноль')

            response = {
                **details,
                'total_balance': str(total_balance),
                'status': status,
            }
            logger.info(f"Client balance for {client_id}: {response}")
            return JsonResponse(response)
        except Client.DoesNotExist:
            logger.error(f"Client not found: ID={client_id}")
            return JsonResponse({'error': 'Client not found'}, status=404)
    logger.warning("Invalid client ID")
    return JsonResponse({'error': 'Invalid client ID'}, status=400)

@login_required
def register_payment(request):
    """Регистрирует платеж для инвойса."""
    if request.method != 'POST':
        logger.warning("Invalid request method for register_payment")
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

    invoice_id: Optional[str] = request.POST.get('invoice_id')
    amount: float = float(request.POST.get('amount', 0))
    payment_type: Optional[str] = request.POST.get('payment_type')
    from_balance: bool = request.POST.get('from_balance') == 'on'
    from_cash_balance: bool = request.POST.get('from_cash_balance') == 'on'
    description: str = request.POST.get('description', '')
    payer_id: Optional[str] = request.POST.get('payer_id')
    recipient: str = request.POST.get('recipient', '')

    logger.info(f"Registering payment: invoice_id={invoice_id}, amount={amount}, payment_type={payment_type}, from_balance={from_balance}, from_cash_balance={from_cash_balance}, payer_id={payer_id}")

    try:
        invoice = Invoice.objects.get(id=invoice_id) if invoice_id else None
        payer = Client.objects.get(id=payer_id) if payer_id else None

        if from_balance and not payer:
            logger.error("Payer required for balance payment")
            return JsonResponse({'status': 'error', 'message': 'Плательщик обязателен для оплаты с баланса'}, status=400)

        # Проверка достаточности баланса для платежа с баланса
        if from_balance and payer:
            if not payer.can_pay_from_balance(amount, payment_type, from_cash_balance):
                logger.error(f"Insufficient funds for client {payer.name}: amount={amount}, from_cash_balance={from_cash_balance}")
                return JsonResponse({'status': 'error', 'message': f"Недостаточно средств на {'наличном' if from_cash_balance else 'безналичном'} балансе"}, status=400)

        payment = Payment(
            invoice=invoice,
            amount=amount,
            payment_type=payment_type,
            description=description,
            from_client=payer,
            to_client=recipient if hasattr(recipient, 'name') else None,
            to_warehouse=recipient if hasattr(recipient, 'name') and 'warehouse' in str(type(recipient)).lower() else None,
            to_line=recipient if hasattr(recipient, 'name') and 'line' in str(type(recipient)).lower() else None,
            to_company=recipient if hasattr(recipient, 'name') and 'company' in str(type(recipient)).lower() else None
        )
        payment.save()

        logger.info(f"Payment saved: id={payment.pk}, client_id={payer.pk if payer else 'N/A'}, cash_balance={payer.cash_balance if payer else 'N/A'}, card_balance={payer.card_balance if payer else 'N/A'}")

        return JsonResponse({
            'status': 'success',
            'message': f'Платеж на сумму {amount} зарегистрирован',
            'client_balance': str(payer.invoice_balance) if payer else None,
            'cash_balance': str(payer.cash_balance) if payer else None,
            'card_balance': str(payer.card_balance) if payer else None
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
    """Дашборд для Caromoto Lithuania с кэшированием"""
    
    # Получаем кэшированную статистику компании
    stats = cache_company_stats()
    
    if not stats:
        # Fallback к прямому запросу если кэш недоступен
        company = get_object_or_404(Company, name="Caromoto Lithuania")
        
        # Получаем текущий месяц
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Общий баланс компании
        company_total_balance = company.invoice_balance + company.cash_balance + company.card_balance
        
        # Прибыль за месяц (входящие платежи)
        monthly_income = Payment.objects.filter(
            to_company=company,
            date__gte=start_of_month
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Расходы за месяц (исходящие платежи)
        monthly_expenses = Payment.objects.filter(
            from_company=company,
            date__gte=start_of_month
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Прибыль за месяц
        monthly_profit = monthly_income - monthly_expenses
        
        # Количество активных инвойсов (неоплаченные и частично оплаченные)
        active_invoices_count = Invoice.objects.filter(
            paid=False
        ).count()
        
        # Последние транзакции (последние 20)
        recent_transactions = Payment.objects.filter(
            Q(from_company=company) | Q(to_company=company)
        ).order_by('-date')[:20]
        
        # Активные инвойсы (неоплаченные и частично оплаченные)
        active_invoices = Invoice.objects.filter(
            paid=False
        ).order_by('-issue_date')[:10]
    else:
        # Используем кэшированные данные
        company_total_balance = stats['company']['total_balance']
        monthly_profit = stats['monthly']['payments']['total_amount'] - stats['monthly']['invoices']['total_amount']
        monthly_expenses = stats['monthly']['invoices']['total_amount']
        active_invoices_count = stats['monthly']['invoices']['count']
        
        # Получаем компанию для дополнительных данных
        company = get_object_or_404(Company, name="Caromoto Lithuania")
        
        # Последние транзакции (последние 20) - не кэшируем для актуальности
        recent_transactions = Payment.objects.filter(
            Q(from_company=company) | Q(to_company=company)
        ).order_by('-date')[:20]
        
        # Активные инвойсы (неоплаченные и частично оплаченные) - не кэшируем для актуальности
        active_invoices = Invoice.objects.filter(
            paid=False
        ).order_by('-issue_date')[:10]
    
    # Последние действия (имитация)
    recent_activities = [
        {
            'icon': '💰',
            'title': 'Платеж получен',
            'description': f'От клиента на сумму {monthly_income:.2f} €',
            'time': now
        },
        {
            'icon': '📄',
            'title': 'Инвойс создан',
            'description': f'Новый инвойс #{active_invoices_count + 1}',
            'time': now - timedelta(hours=2)
        },
        {
            'icon': '💳',
            'title': 'Транзакция баланса',
            'description': 'Обновление баланса компании',
            'time': now - timedelta(hours=4)
        }
    ]
    
    context = {
        'company': company,
        'company_total_balance': company_total_balance,
        'monthly_profit': monthly_profit,
        'monthly_expenses': monthly_expenses,
        'active_invoices_count': active_invoices_count,
        'recent_transactions': recent_transactions,
        'active_invoices': active_invoices,
        'recent_activities': recent_activities,
    }
    
    return render(request, 'admin/company_dashboard.html', context)

@staff_member_required
def get_payment_objects(request):
    """AJAX view для получения списка объектов определенного типа для формы платежа"""
    object_type = request.GET.get('type')
    logger.info(f"get_payment_objects called with type: {object_type}")
    
    if not object_type:
        logger.warning("No type parameter provided")
        return JsonResponse({'error': 'Type parameter is required'}, status=400)
    
    try:
        # Получаем модель по типу
        logger.info(f"Getting model for type: {object_type}")
        model = apps.get_model('core', object_type.title())
        logger.info(f"Model class: {model}")
        
        # Получаем все объекты модели, отсортированные по имени
        objects = model.objects.all().order_by('name' if hasattr(model, 'name') else 'id')
        logger.info(f"Found {objects.count()} objects")
        
        # Формируем список объектов для JSON
        objects_list = []
        for obj in objects:
            display_name = getattr(obj, 'name', str(obj))
            objects_list.append({
                'id': obj.id,
                'name': display_name
            })
        
        logger.info(f"Returning {len(objects_list)} objects")
        return JsonResponse({
            'type': object_type,
            'objects': objects_list
        })
        
    except Exception as e:
        logger.error(f"Error getting objects for type {object_type}: {e}")
        return JsonResponse({'error': str(e)}, status=500)

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

@require_GET
def get_invoice_cars_api(request):
    """API для получения автомобилей для инвойса - показываем автомобили, связанные с отправителем"""
    from_entity_type = request.GET.get('from_entity_type')
    from_entity_id = request.GET.get('from_entity_id')
    to_entity_type = request.GET.get('to_entity_type')
    to_entity_id = request.GET.get('to_entity_id')
    search_query = request.GET.get('search', '').strip()
    
    logger.info(f"get_invoice_cars_api called with: from_entity_type={from_entity_type}, from_entity_id={from_entity_id}, to_entity_type={to_entity_type}, to_entity_id={to_entity_id}, search_query={search_query}")
    
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
        
        # Формируем данные для каждого автомобиля
        cars_data = []
        logger.info(f"Found {cars.count()} cars for entity type {to_entity_type} with ID {to_entity_id}")
        
        for car in cars:
            # Всегда показываем полную стоимость автомобиля
            total_cost = car.total_price or car.current_price or Decimal('0.00')
            
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
        
        logger.info(f"Returning {len(cars_data)} cars")
        response = JsonResponse({'cars': cars_data})
        response['Content-Type'] = 'application/json'
        return response
        
    except Exception as e:
        logger.error(f"Error getting invoice cars: {e}")
        response = JsonResponse({'error': str(e)}, status=500)
        response['Content-Type'] = 'application/json'
        return response

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
    """Дашборд для сравнения сумм между расчетами и счетами склада"""
    
    # Получаем параметры фильтрации
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if start_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    if end_date:
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    # Создаем сервис сравнения
    comparison_service = ComparisonService()
    
    # Получаем общий отчет
    report = comparison_service.get_comparison_report(start_date, end_date)
    
    # Находим расхождения
    discrepancies = comparison_service.find_discrepancies(start_date, end_date)
    
    # Получаем статистику по клиентам
    clients = Client.objects.all()
    client_comparisons = []
    for client in clients:
        comparison = comparison_service.compare_client_costs_with_warehouse_invoices(
            client, start_date, end_date
        )
        if comparison['status'] != 'no_data':
            client_comparisons.append(comparison)
    
    # Получаем статистику по складам
    warehouses = Warehouse.objects.all()
    warehouse_comparisons = []
    for warehouse in warehouses:
        comparison = comparison_service.compare_warehouse_costs_with_payments(
            warehouse, start_date, end_date
        )
        if comparison['status'] != 'no_data':
            warehouse_comparisons.append(comparison)
    
    context = {
        'report': report,
        'discrepancies': discrepancies,
        'client_comparisons': client_comparisons,
        'warehouse_comparisons': warehouse_comparisons,
        'start_date': start_date,
        'end_date': end_date,
    }
    
    return render(request, 'admin/comparison_dashboard.html', context)

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
@csrf_exempt
def get_available_services(request, car_id):
    """Получает доступные услуги для добавления к автомобилю"""
    logger.info(f"get_available_services called: car_id={car_id}, method={request.method}")
    
    service_type = request.GET.get('type')
    
    logger.info(f"get_available_services called: car_id={car_id}, service_type={service_type}")
    
    if not service_type:
        logger.error("Service type is required")
        return JsonResponse({'error': 'Service type is required'}, status=400)
    
    try:
        car = Car.objects.get(id=car_id)
        logger.info(f"Found car: {car.vin}, warehouse={car.warehouse}, line={car.line}, carrier={car.carrier}")
        services = []
        
        if service_type == 'warehouse' and car.warehouse:
            logger.info(f"Processing warehouse services for warehouse: {car.warehouse}")
            # Получаем услуги склада, которые еще не добавлены к автомобилю
            existing_service_ids = CarService.objects.filter(
                car=car, 
                service_type='WAREHOUSE'
            ).values_list('service_id', flat=True)
            
            logger.info(f"Existing warehouse service IDs: {list(existing_service_ids)}")
            
            available_services = WarehouseService.objects.filter(
                warehouse=car.warehouse
            ).exclude(id__in=existing_service_ids)
            
            logger.info(f"Available warehouse services count: {available_services.count()}")
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in available_services]
            
        elif service_type == 'line' and car.line:
            logger.info(f"Processing line services for line: {car.line}")
            # Получаем услуги линии, которые еще не добавлены к автомобилю
            existing_service_ids = CarService.objects.filter(
                car=car, 
                service_type='LINE'
            ).values_list('service_id', flat=True)
            
            logger.info(f"Existing line service IDs: {list(existing_service_ids)}")
            
            available_services = LineService.objects.filter(
                line=car.line
            ).exclude(id__in=existing_service_ids)
            
            logger.info(f"Available line services count: {available_services.count()}")
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in available_services]
            
        elif service_type == 'carrier' and car.carrier:
            logger.info(f"Processing carrier services for carrier: {car.carrier}")
            # Получаем услуги перевозчика, которые еще не добавлены к автомобилю
            existing_service_ids = CarService.objects.filter(
                car=car, 
                service_type='CARRIER'
            ).values_list('service_id', flat=True)
            
            logger.info(f"Existing carrier service IDs: {list(existing_service_ids)}")
            
            available_services = CarrierService.objects.filter(
                carrier=car.carrier
            ).exclude(id__in=existing_service_ids)
            
            logger.info(f"Available carrier services count: {available_services.count()}")
            
            services = [{
                'id': service.id,
                'name': service.name,
                'price': float(service.default_price)
            } for service in available_services]
        else:
            logger.warning(f"No {service_type} associated with car {car_id}")
            services = []
        
        logger.info(f"Returning {len(services)} services")
        return JsonResponse({'services': services})
        
    except Car.DoesNotExist:
        logger.error(f"Car not found: {car_id}")
        return JsonResponse({'error': 'Car not found'}, status=404)
    except Exception as e:
        logger.error(f"Error getting available services: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

@staff_member_required
@csrf_exempt
def add_services(request, car_id):
    """Добавляет выбранные услуги к автомобилю"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        service_type = data.get('service_type')
        service_ids = data.get('service_ids', [])
        
        if not service_type or not service_ids:
            return JsonResponse({'error': 'Service type and IDs are required'}, status=400)
        
        car = Car.objects.get(id=car_id)
        added_count = 0
        
        for service_id in service_ids:
            # Создаем CarService для каждой выбранной услуги
            car_service = CarService.objects.create(
                car=car,
                service_type=service_type.upper(),
                service_id=service_id
            )
            added_count += 1
        
        return JsonResponse({
            'success': True,
            'message': f'Добавлено {added_count} услуг',
            'added_count': added_count
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def sync_container_photos_from_gdrive(request, container_id):
    """Синхронизирует фотографии контейнера с Google Drive"""
    try:
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'Only POST method allowed'}, status=405)
        
        from .google_drive_sync import GoogleDriveSync
        from .models import Container
        import threading
        
        container = Container.objects.get(id=container_id)
        
        # Проверяем, есть ли ссылка на Google Drive
        if not container.google_drive_folder_url:
            return JsonResponse({
                'success': False,
                'error': 'Не указана ссылка на папку Google Drive. Добавьте ссылку в поле "Google Drive папка"'
            })
        
        # Запускаем загрузку в отдельном потоке чтобы не блокировать worker
        def download_in_background():
            try:
                GoogleDriveSync.download_folder_photos(
                    container.google_drive_folder_url,
                    container
                )
            except Exception as e:
                logger.error(f"Background download error: {e}", exc_info=True)
        
        thread = threading.Thread(target=download_in_background, daemon=True)
        thread.start()
        
        # Сразу возвращаем ответ - загрузка продолжится в фоне
        return JsonResponse({
            'success': True,
            'message': 'Загрузка фотографий начата. Обновите страницу через 1-2 минуты чтобы увидеть результат.',
            'photos_count': 0
        })
        
    except Container.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Контейнер не найден'}, status=404)
    except Exception as e:
        logger.error(f"Error syncing Google Drive photos: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)