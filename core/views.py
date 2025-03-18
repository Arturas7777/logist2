from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from .models import Car, Invoice

def car_list_api(request):
    client_id = request.GET.get('client_id')
    print(f"API called with GET: {request.GET}")
    print(f"Extracted client_id: {client_id}")

    if client_id and client_id.isdigit():
        try:
            cars = Car.objects.filter(client_id=client_id, status='UNLOADED')
            print(f"All cars for client {client_id}: {cars.count()}")
            print(f"Cars: {list(cars)}")
            if cars.exists():
                html = ''.join([f'<option value="{car.id}">{car}</option>' for car in cars])
            else:
                html = '<option class="no-results">No results found</option>'
            return HttpResponse(html, content_type='text/html')
        except Exception as e:
            print(f"Error in car_list_api: {e}")
            return HttpResponse('<option class="no-results">Error loading cars</option>', content_type='text/html')
    return HttpResponse('<option class="no-results">No client selected</option>', content_type='text/html')

@require_GET
def get_invoice_total(request):
    car_ids = request.GET.get('car_ids', '').split(',')
    car_ids = [int(cid) for cid in car_ids if cid.strip().isdigit()]
    print(f"Received car_ids in get_invoice_total: {car_ids}")

    # Создаем временный объект Invoice с уникальным номером
    timestamp = timezone.now().strftime('%H%M%S')  # Берем только время (например, 080616)
    invoice = Invoice(number=f"temp_{timestamp}", issue_date=timezone.now().date())
    try:
        invoice.save()
    except Exception as e:
        print(f"Error saving temporary invoice: {e}")
        return JsonResponse({'total_amount': '0.00', 'error': f"Failed to save temporary invoice: {e}"}, status=500)

    if car_ids:
        try:
            cars = Car.objects.filter(id__in=car_ids)
            print(f"Cars found: {list(cars)}")
            invoice.cars.set(cars)
        except Exception as e:
            print(f"Error setting cars: {e}")
            invoice.delete()
            return JsonResponse({'total_amount': '0.00', 'error': f"Failed to set cars: {e}"}, status=500)
    else:
        print("No car IDs provided, clearing cars")
        invoice.cars.clear()

    try:
        # Вызываем метод для пересчета суммы с отключением рекурсии
        invoice.update_total_amount()
        print(f"Calculated total_amount: {invoice.total_amount}")
        result = {'total_amount': str(invoice.total_amount or '0.00')}
    except Exception as e:
        print(f"Error in update_total_amount: {e}")
        result = {'total_amount': '0.00', 'error': str(e)}
    finally:
        invoice.delete()  # Удаляем временный объект после расчета

    return JsonResponse(result)