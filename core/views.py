from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from datetime import timedelta
from typing import Optional
from .models import Car, Invoice, Container, Payment, Client
import logging

logger = logging.getLogger('django')

def car_list_api(request):
    """Возвращает список автомобилей для клиента, отфильтрованный по статусу."""
    client_id: Optional[str] = request.GET.get('client_id')
    search_query: str = request.GET.get('search', '').strip().lower()
    logger.info(f"car_list_api called with GET: {request.GET}")
    logger.info(f"Extracted client_id: {client_id}, search: '{search_query}'")

    if client_id and client_id != 'undefined' and client_id.isdigit():
        last_month = timezone.now().date() - timedelta(days=30)
        filters = Q(client_id=client_id) & (
            Q(status='UNLOADED') | Q(status='TRANSFERRED', transfer_date__gte=last_month)
        )
        all_cars = Car.objects.filter(filters)
        logger.info(f"All cars for client {client_id}: {all_cars.count()} (UNLOADED or TRANSFERRED within last 30 days)")
        if all_cars.exists():
            for car in all_cars:
                logger.debug(f"Car {car.pk}: VIN={car.vin}, Brand={car.brand}, Year={car.year}, Status={car.status}, Transfer Date={car.transfer_date}")
        else:
            logger.warning(f"No cars found for client {client_id}")

        if search_query:
            all_cars = all_cars.filter(
                Q(vin__icontains=search_query) |
                Q(brand__icontains=search_query) |
                Q(year__icontains=search_query)
            )
            logger.info(f"Filtered cars with search '{search_query}': {all_cars.count()}")
            if all_cars.exists():
                for car in all_cars:
                    logger.debug(f"Filtered car: {car.pk} - VIN: {car.vin}, Brand: {car.brand}, Year: {car.year}, Status: {car.status}, Transfer Date={car.transfer_date}")

        html = render_to_string('admin/car_options.html', context={'cars': all_cars}, request=request)
        logger.debug(f"Returning HTML: {html[:100]}...")
        return HttpResponse(html, content_type='text/html')
    logger.warning("Invalid or missing client_id, returning no client selected")
    return HttpResponse('<option class="no-results">No client selected</option>', content_type='text/html')

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
        cars = Car.objects.filter(id__in=car_ids)
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

    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    invoice = Invoice(number=f"temp_{timestamp}", issue_date=timezone.now().date())
    try:
        invoice.save()
        logger.info(f"Temporary invoice saved with number: {invoice.number}")
    except Exception as e:
        logger.error(f"Error saving temporary invoice: {e}")
        result['error'] = f"Failed to save temporary invoice: {e}"
        return JsonResponse(result, status=500)

    try:
        invoice.cars.set(cars)
        logger.info(f"Cars set to invoice: {list(invoice.cars.all())}")
    except Exception as e:
        logger.error(f"Error setting cars: {e}")
        invoice.delete()
        result['error'] = f"Failed to set cars: {e}"
        return JsonResponse(result, status=500)

    try:
        invoice.update_total_amount()
        logger.info(f"Calculated total_amount: {invoice.total_amount}")
        result['total_amount'] = str(invoice.total_amount or '0.00')
    except Exception as e:
        logger.error(f"Error in update_total_amount: {e}")
        for car in invoice.cars.all():
            logger.debug(f"Car {car.pk}: total_price={car.total_price}, storage_cost={car.storage_cost}, is_outgoing={invoice.is_outgoing}")
        result['error'] = str(e)
    finally:
        try:
            invoice.delete()
            logger.info("Temporary invoice deleted")
        except Exception as e:
            logger.error(f"Error deleting temporary invoice: {e}")
            result['error'] = str(e)

    return JsonResponse(result)

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
            client = Client.objects.get(id=client_id)
            data = client.balance_details()
            logger.info(f"Client balance for {client_id}: {data}")
            return JsonResponse(data)
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
            payer=payer,
            recipient=recipient,
            from_balance=from_balance,
            from_cash_balance=from_cash_balance
        )
        payment.save()

        logger.info(f"Payment saved: id={payment.pk}, client_id={payer.pk if payer else 'N/A'}, cash_balance={payer.cash_balance if payer else 'N/A'}, card_balance={payer.card_balance if payer else 'N/A'}")

        return JsonResponse({
            'status': 'success',
            'message': f'Платеж на сумму {amount} зарегистрирован',
            'client_balance': str(payer.balance) if payer else None,
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