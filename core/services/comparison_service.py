"""
Сервис для автоматического сравнения сумм между расчетами и счетами склада
"""

from decimal import Decimal
from django.db.models import Sum, Q
from django.utils import timezone
from datetime import timedelta
from typing import Dict, List, Any
import logging

from ..models import Car, Warehouse, Client, Company
from ..models_billing import NewInvoice as Invoice, Transaction as Payment

logger = logging.getLogger('django')


class ComparisonService:
    """Сервис для сравнения сумм между расчетами и счетами склада"""
    
    def __init__(self):
        self.tolerance = Decimal('0.01')  # Допустимая разница в 1 цент
    
    def compare_car_costs_with_warehouse_invoices(self, car: Car) -> Dict[str, Any]:
        """
        Сравнивает стоимость автомобиля с инвойсами склада
        
        Args:
            car: Автомобиль для сравнения
            
        Returns:
            Словарь с результатами сравнения
        """
        if not car.warehouse:
            return {
                'status': 'error',
                'message': 'У автомобиля не указан склад',
                'car_vin': car.vin,
                'car_total_cost': 0,
                'warehouse_invoices_total': 0,
                'difference': 0
            }
        
        # Получаем общую стоимость автомобиля
        car_total_cost = car.total_price or car.current_price or Decimal('0.00')
        
        # Получаем все инвойсы склада, связанные с этим автомобилем
        warehouse_invoices = Invoice.objects.filter(
            cars=car,
            to_entity_type='WAREHOUSE',
            to_entity_id=car.warehouse.id
        )
        
        warehouse_invoices_total = warehouse_invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Вычисляем разницу
        difference = car_total_cost - warehouse_invoices_total
        
        # Определяем статус
        if abs(difference) <= self.tolerance:
            status = 'match'
            message = 'Суммы совпадают'
        elif difference > 0:
            status = 'car_higher'
            message = f'Стоимость автомобиля выше на {difference:.2f} €'
        else:
            status = 'warehouse_higher'
            message = f'Стоимость склада выше на {abs(difference):.2f} €'
        
        return {
            'status': status,
            'message': message,
            'car_vin': car.vin,
            'car_brand': car.brand,
            'car_year': car.year,
            'car_total_cost': float(car_total_cost),
            'warehouse_invoices_total': float(warehouse_invoices_total),
            'difference': float(difference),
            'warehouse_name': car.warehouse.name,
            'invoices_count': warehouse_invoices.count()
        }
    
    def compare_client_costs_with_warehouse_invoices(self, client: Client, 
                                                   start_date=None, 
                                                   end_date=None) -> Dict[str, Any]:
        """
        Сравнивает общую стоимость автомобилей клиента с инвойсами склада
        
        Args:
            client: Клиент для сравнения
            start_date: Начальная дата (опционально)
            end_date: Конечная дата (опционально)
            
        Returns:
            Словарь с результатами сравнения
        """
        # Получаем автомобили клиента
        cars_query = Car.objects.filter(client=client)
        
        if start_date:
            cars_query = cars_query.filter(unload_date__gte=start_date)
        if end_date:
            cars_query = cars_query.filter(unload_date__lte=end_date)
        
        cars = cars_query.all()
        
        if not cars.exists():
            return {
                'status': 'no_data',
                'message': 'У клиента нет автомобилей в указанном периоде',
                'client_name': client.name,
                'cars_total_cost': 0,
                'warehouse_invoices_total': 0,
                'difference': 0
            }
        
        # Суммируем стоимость всех автомобилей клиента
        cars_total_cost = sum(
            (car.total_price or car.current_price or Decimal('0.00')) 
            for car in cars
        )
        
        # Получаем все инвойсы склада для автомобилей клиента
        warehouse_invoices = Invoice.objects.filter(
            cars__in=cars,
            to_entity_type='WAREHOUSE'
        )
        
        if start_date:
            warehouse_invoices = warehouse_invoices.filter(issue_date__gte=start_date)
        if end_date:
            warehouse_invoices = warehouse_invoices.filter(issue_date__lte=end_date)
        
        warehouse_invoices_total = warehouse_invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Вычисляем разницу
        difference = cars_total_cost - warehouse_invoices_total
        
        # Определяем статус
        if abs(difference) <= self.tolerance:
            status = 'match'
            message = 'Суммы совпадают'
        elif difference > 0:
            status = 'cars_higher'
            message = f'Стоимость автомобилей выше на {difference:.2f} €'
        else:
            status = 'warehouse_higher'
            message = f'Стоимость склада выше на {abs(difference):.2f} €'
        
        return {
            'status': status,
            'message': message,
            'client_name': client.name,
            'cars_count': cars.count(),
            'cars_total_cost': float(cars_total_cost),
            'warehouse_invoices_total': float(warehouse_invoices_total),
            'difference': float(difference),
            'invoices_count': warehouse_invoices.count(),
            'period': {
                'start_date': start_date,
                'end_date': end_date
            }
        }
    
    def compare_warehouse_costs_with_payments(self, warehouse: Warehouse, 
                                            start_date=None, 
                                            end_date=None) -> Dict[str, Any]:
        """
        Сравнивает стоимость услуг склада с фактическими платежами
        
        Args:
            warehouse: Склад для сравнения
            start_date: Начальная дата (опционально)
            end_date: Конечная дата (опционально)
            
        Returns:
            Словарь с результатами сравнения
        """
        # Получаем все инвойсы склада
        invoices_query = Invoice.objects.filter(
            to_entity_type='WAREHOUSE',
            to_entity_id=warehouse.id
        )
        
        if start_date:
            invoices_query = invoices_query.filter(issue_date__gte=start_date)
        if end_date:
            invoices_query = invoices_query.filter(issue_date__lte=end_date)
        
        invoices = invoices_query.all()
        
        if not invoices.exists():
            return {
                'status': 'no_data',
                'message': 'У склада нет инвойсов в указанном периоде',
                'warehouse_name': warehouse.name,
                'invoices_total': 0,
                'payments_total': 0,
                'difference': 0
            }
        
        # Суммируем стоимость всех инвойсов склада
        invoices_total = invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        # Получаем все платежи складу
        payments_query = Payment.objects.filter(
            to_warehouse=warehouse
        )
        
        if start_date:
            payments_query = payments_query.filter(date__gte=start_date)
        if end_date:
            payments_query = payments_query.filter(date__lte=end_date)
        
        payments_total = payments_query.aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Вычисляем разницу
        difference = invoices_total - payments_total
        
        # Определяем статус
        if abs(difference) <= self.tolerance:
            status = 'match'
            message = 'Суммы совпадают'
        elif difference > 0:
            status = 'invoices_higher'
            message = f'Стоимость инвойсов выше на {difference:.2f} €'
        else:
            status = 'payments_higher'
            message = f'Сумма платежей выше на {abs(difference):.2f} €'
        
        return {
            'status': status,
            'message': message,
            'warehouse_name': warehouse.name,
            'invoices_count': invoices.count(),
            'invoices_total': float(invoices_total),
            'payments_count': payments_query.count(),
            'payments_total': float(payments_total),
            'difference': float(difference),
            'period': {
                'start_date': start_date,
                'end_date': end_date
            }
        }
    
    def get_comparison_report(self, start_date=None, end_date=None) -> Dict[str, Any]:
        """
        Генерирует общий отчет по сравнению сумм
        
        Args:
            start_date: Начальная дата (опционально)
            end_date: Конечная дата (опционально)
            
        Returns:
            Словарь с общим отчетом
        """
        if not start_date:
            start_date = timezone.now().date() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now().date()
        
        # Получаем все автомобили в периоде
        cars_query = Car.objects.filter(unload_date__gte=start_date, unload_date__lte=end_date)
        cars = cars_query.all()
        
        # Получаем все инвойсы в периоде
        invoices_query = Invoice.objects.filter(issue_date__gte=start_date, issue_date__lte=end_date)
        invoices = invoices_query.all()
        
        # Получаем все платежи в периоде
        payments_query = Payment.objects.filter(date__gte=start_date, date__lte=end_date)
        payments = payments_query.all()
        
        # Суммируем общие суммы
        cars_total = sum(
            (car.total_price or car.current_price or Decimal('0.00')) 
            for car in cars
        )
        
        invoices_total = invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        payments_total = payments.aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        # Вычисляем разницы
        cars_vs_invoices_diff = cars_total - invoices_total
        invoices_vs_payments_diff = invoices_total - payments_total
        
        return {
            'period': {
                'start_date': start_date,
                'end_date': end_date
            },
            'summary': {
                'cars_count': cars.count(),
                'cars_total': float(cars_total),
                'invoices_count': invoices.count(),
                'invoices_total': float(invoices_total),
                'payments_count': payments.count(),
                'payments_total': float(payments_total),
                'cars_vs_invoices_difference': float(cars_vs_invoices_diff),
                'invoices_vs_payments_difference': float(invoices_vs_payments_diff)
            },
            'status': 'success'
        }
    
    def find_discrepancies(self, start_date=None, end_date=None) -> List[Dict[str, Any]]:
        """
        Находит расхождения в суммах
        
        Args:
            start_date: Начальная дата (опционально)
            end_date: Конечная дата (опционально)
            
        Returns:
            Список расхождений
        """
        discrepancies = []
        
        if not start_date:
            start_date = timezone.now().date() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now().date()
        
        # Проверяем каждого клиента
        clients = Client.objects.all()
        for client in clients:
            comparison = self.compare_client_costs_with_warehouse_invoices(
                client, start_date, end_date
            )
            if comparison['status'] not in ['match', 'no_data']:
                discrepancies.append({
                    'type': 'client_comparison',
                    'entity': client.name,
                    'comparison': comparison
                })
        
        # Проверяем каждый склад
        warehouses = Warehouse.objects.all()
        for warehouse in warehouses:
            comparison = self.compare_warehouse_costs_with_payments(
                warehouse, start_date, end_date
            )
            if comparison['status'] not in ['match', 'no_data']:
                discrepancies.append({
                    'type': 'warehouse_comparison',
                    'entity': warehouse.name,
                    'comparison': comparison
                })
        
        return discrepancies

