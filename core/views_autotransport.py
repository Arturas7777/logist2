"""
API endpoints для системы автовозов на загрузку
"""

from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET, require_POST
import json

from .models import Carrier, CarrierTruck, CarrierDriver, AutoTransport


@staff_member_required
@require_GET
def get_carrier_info(request, carrier_id):
    """API для получения информации о перевозчике (EORI код, автовозы, водители)"""
    try:
        carrier = Carrier.objects.get(pk=carrier_id)
        
        # Список автовозов
        trucks = []
        for truck in carrier.trucks.filter(is_active=True):
            trucks.append({
                'id': truck.pk,
                'text': truck.full_number,
                'truck_number': truck.truck_number,
                'trailer_number': truck.trailer_number,
            })
        
        # Список водителей
        drivers = []
        for driver in carrier.drivers.filter(is_active=True):
            drivers.append({
                'id': driver.pk,
                'text': driver.full_name,
                'first_name': driver.first_name,
                'last_name': driver.last_name,
                'phone': driver.phone,
            })
        
        return JsonResponse({
            'success': True,
            'eori_code': carrier.eori_code or '',
            'trucks': trucks,
            'drivers': drivers,
        })
    except Carrier.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Перевозчик не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_GET
def get_driver_phone(request, driver_id):
    """API для получения телефона водителя"""
    try:
        driver = CarrierDriver.objects.get(pk=driver_id)
        return JsonResponse({
            'success': True,
            'phone': driver.phone,
            'full_name': driver.full_name,
        })
    except CarrierDriver.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Водитель не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_POST
def update_driver_phone(request):
    """API для обновления телефона водителя"""
    try:
        data = json.loads(request.body)
        driver_id = data.get('driver_id')
        new_phone = data.get('phone', '').strip()
        
        if not driver_id or not new_phone:
            return JsonResponse({'success': False, 'error': 'Не указан ID водителя или телефон'}, status=400)
        
        driver = CarrierDriver.objects.get(pk=driver_id)
        driver.phone = new_phone
        driver.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Телефон водителя {driver.full_name} обновлен'
        })
    except CarrierDriver.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Водитель не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_GET
def get_border_crossings(request):
    """API для получения списка границ пересечения из существующих автовозов"""
    try:
        borders = AutoTransport.objects.exclude(
            border_crossing=''
        ).values_list('border_crossing', flat=True).distinct().order_by('border_crossing')
        
        results = [{'id': border, 'text': border} for border in borders]
        
        return JsonResponse({'success': True, 'results': results})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_POST
def create_carrier_truck(request):
    """API для создания нового автовоза у перевозчика"""
    try:
        data = json.loads(request.body)
        carrier_id = data.get('carrier_id')
        truck_number = data.get('truck_number', '').strip()
        trailer_number = data.get('trailer_number', '').strip()
        
        if not carrier_id or not truck_number:
            return JsonResponse({'success': False, 'error': 'Не указан перевозчик или номер тягача'}, status=400)
        
        carrier = Carrier.objects.get(pk=carrier_id)
        
        # Проверяем, нет ли уже такого автовоза
        existing = CarrierTruck.objects.filter(
            carrier=carrier,
            truck_number=truck_number,
            trailer_number=trailer_number
        ).first()
        
        if existing:
            return JsonResponse({
                'success': True,
                'truck': {'id': existing.pk, 'text': existing.full_number},
                'message': 'Такой автовоз уже существует'
            })
        
        # Создаем новый автовоз
        truck = CarrierTruck.objects.create(
            carrier=carrier,
            truck_number=truck_number,
            trailer_number=trailer_number
        )
        
        return JsonResponse({
            'success': True,
            'truck': {'id': truck.pk, 'text': truck.full_number},
            'message': f'Создан автовоз {truck.full_number}'
        })
    except Carrier.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Перевозчик не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@staff_member_required
@require_POST
def create_carrier_driver(request):
    """API для создания нового водителя у перевозчика"""
    try:
        data = json.loads(request.body)
        carrier_id = data.get('carrier_id')
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        phone = data.get('phone', '').strip()
        
        if not carrier_id or not first_name or not last_name:
            return JsonResponse({'success': False, 'error': 'Не указан перевозчик, имя или фамилия'}, status=400)
        
        carrier = Carrier.objects.get(pk=carrier_id)
        
        # Создаем нового водителя
        driver = CarrierDriver.objects.create(
            carrier=carrier,
            first_name=first_name,
            last_name=last_name,
            phone=phone
        )
        
        return JsonResponse({
            'success': True,
            'driver': {'id': driver.pk, 'text': driver.full_name, 'phone': driver.phone},
            'message': f'Создан водитель {driver.full_name}'
        })
    except Carrier.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Перевозчик не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
