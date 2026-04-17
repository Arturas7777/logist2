"""
Сервис для автоматического сравнения сумм между расчетами и счетами склада
"""

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List

from django.db import models
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..models import Car, Client, Warehouse
from ..models_billing import NewInvoice as Invoice
from ..models_billing import Transaction as Payment

logger = logging.getLogger(__name__)


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
        car_total_cost = car.total_price or Decimal('0.00')

        warehouse_invoices = Invoice.objects.filter(
            cars=car,
            issuer_warehouse=car.warehouse,
        )

        warehouse_invoices_total = warehouse_invoices.aggregate(
            total=Sum('total')
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
            'car_total_cost': str(car_total_cost),
            'warehouse_invoices_total': str(warehouse_invoices_total),
            'difference': str(difference),
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

        agg = cars_query.aggregate(
            total_cost=Sum('total_price'),
            cars_count=models.Count('id')
        )
        cars_total_cost = agg['total_cost'] or Decimal('0.00')
        cars_count = agg['cars_count']

        warehouse_invoices = Invoice.objects.filter(
            cars__client=client,
            issuer_warehouse__isnull=False,
        )
        if start_date:
            warehouse_invoices = warehouse_invoices.filter(
                Q(date__gte=start_date) | Q(cars__unload_date__gte=start_date)
            )
        if end_date:
            warehouse_invoices = warehouse_invoices.filter(
                Q(date__lte=end_date) | Q(cars__unload_date__lte=end_date)
            )
        warehouse_invoices = warehouse_invoices.distinct()

        wh_agg = warehouse_invoices.aggregate(
            total=Sum('total'),
            inv_count=models.Count('id')
        )
        warehouse_invoices_total = wh_agg['total'] or Decimal('0.00')

        difference = cars_total_cost - warehouse_invoices_total

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
            'cars_count': cars_count,
            'cars_total_cost': str(cars_total_cost),
            'warehouse_invoices_total': str(warehouse_invoices_total),
            'difference': str(difference),
            'invoices_count': wh_agg['inv_count'],
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
        invoices_query = Invoice.objects.filter(
            issuer_warehouse=warehouse,
        )

        if start_date:
            invoices_query = invoices_query.filter(date__gte=start_date)
        if end_date:
            invoices_query = invoices_query.filter(date__lte=end_date)

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
            total=Sum('total')
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
            'invoices_total': str(invoices_total),
            'payments_count': payments_query.count(),
            'payments_total': str(payments_total),
            'difference': str(difference),
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

        cars_agg = Car.objects.filter(
            unload_date__gte=start_date, unload_date__lte=end_date
        ).aggregate(
            total=Sum('total_price'),
            cnt=Count('id')
        )
        cars_total = cars_agg['total'] or Decimal('0.00')

        invoices_agg = Invoice.objects.filter(
            date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('total'),
            cnt=Count('id')
        )
        invoices_total = invoices_agg['total'] or Decimal('0.00')

        payments_agg = Payment.objects.filter(
            date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('amount'),
            cnt=Count('id')
        )
        payments_total = payments_agg['total'] or Decimal('0.00')

        cars_vs_invoices_diff = cars_total - invoices_total
        invoices_vs_payments_diff = invoices_total - payments_total

        return {
            'period': {
                'start_date': start_date,
                'end_date': end_date
            },
            'summary': {
                'cars_count': cars_agg['cnt'],
                'cars_total': str(cars_total),
                'invoices_count': invoices_agg['cnt'],
                'invoices_total': str(invoices_total),
                'payments_count': payments_agg['cnt'],
                'payments_total': str(payments_total),
                'cars_vs_invoices_difference': str(cars_vs_invoices_diff),
                'invoices_vs_payments_difference': str(invoices_vs_payments_diff)
            },
            'status': 'success'
        }

    def batch_compare_clients(self, start_date=None, end_date=None) -> List[Dict[str, Any]]:
        """Batch comparison for all clients with cars — 2-3 SQL queries instead of N+1."""
        car_filters = Q(unload_date__isnull=False)
        if start_date:
            car_filters &= Q(unload_date__gte=start_date)
        if end_date:
            car_filters &= Q(unload_date__lte=end_date)

        from django.db.models import Count
        client_agg = (
            Car.objects.filter(car_filters)
            .values('client_id', 'client__name')
            .annotate(total_cost=Sum('total_price'), cars_count=Count('id'))
            .filter(client_id__isnull=False)
            .order_by('client__name')
        )

        inv_filters = Q(cars__client__isnull=False, issuer_warehouse__isnull=False)
        if start_date:
            inv_filters &= (Q(date__gte=start_date) | Q(cars__unload_date__gte=start_date))
        if end_date:
            inv_filters &= (Q(date__lte=end_date) | Q(cars__unload_date__lte=end_date))

        inv_agg = dict(
            Invoice.objects.filter(inv_filters)
            .values('cars__client_id')
            .annotate(total=Sum('total'), cnt=Count('id', distinct=True))
            .values_list('cars__client_id', 'total')
        )

        results = []
        for row in client_agg:
            client_name = row['client__name']
            cars_total = row['total_cost'] or Decimal('0.00')
            wh_total = inv_agg.get(row['client_id']) or Decimal('0.00')
            diff = cars_total - wh_total

            if abs(diff) <= self.tolerance:
                status, message = 'match', 'Суммы совпадают'
            elif diff > 0:
                status, message = 'cars_higher', f'Стоимость автомобилей выше на {diff:.2f} €'
            else:
                status, message = 'warehouse_higher', f'Стоимость склада выше на {abs(diff):.2f} €'

            results.append({
                'status': status, 'message': message,
                'client_name': client_name,
                'cars_count': row['cars_count'],
                'cars_total_cost': str(cars_total),
                'warehouse_invoices_total': str(wh_total),
                'difference': str(diff),
                'invoices_count': 0,
                'period': {'start_date': start_date, 'end_date': end_date},
            })
        return results

    def batch_compare_warehouses(self, start_date=None, end_date=None) -> List[Dict[str, Any]]:
        """Batch comparison for all warehouses — 2 SQL queries instead of N+1."""
        inv_filters = Q(issuer_warehouse__isnull=False)
        if start_date:
            inv_filters &= Q(date__gte=start_date)
        if end_date:
            inv_filters &= Q(date__lte=end_date)

        inv_agg = (
            Invoice.objects.filter(inv_filters)
            .values('issuer_warehouse_id')
            .annotate(total=Sum('total'), cnt=Count('id'))
        )
        inv_by_wh = {r['issuer_warehouse_id']: r for r in inv_agg}

        pay_filters = Q(to_warehouse__isnull=False)
        if start_date:
            pay_filters &= Q(date__gte=start_date)
        if end_date:
            pay_filters &= Q(date__lte=end_date)

        pay_agg = dict(
            Payment.objects.filter(pay_filters)
            .values('to_warehouse_id')
            .annotate(total=Sum('amount'))
            .values_list('to_warehouse_id', 'total')
        )

        wh_ids = set(inv_by_wh.keys()) | set(pay_agg.keys())
        if not wh_ids:
            return []

        warehouses = {w.id: w.name for w in Warehouse.objects.filter(id__in=wh_ids)}

        results = []
        for wh_id in sorted(wh_ids):
            wh_name = warehouses.get(wh_id, f'Warehouse #{wh_id}')
            inv_data = inv_by_wh.get(wh_id, {})
            inv_total = inv_data.get('total') or Decimal('0.00')
            pay_total = pay_agg.get(wh_id) or Decimal('0.00')
            diff = inv_total - pay_total

            if abs(diff) <= self.tolerance:
                status, message = 'match', 'Суммы совпадают'
            elif diff > 0:
                status, message = 'invoices_higher', f'Стоимость инвойсов выше на {diff:.2f} €'
            else:
                status, message = 'payments_higher', f'Сумма платежей выше на {abs(diff):.2f} €'

            results.append({
                'status': status, 'message': message,
                'warehouse_name': wh_name,
                'invoices_count': inv_data.get('cnt', 0),
                'invoices_total': str(inv_total),
                'payments_count': 0,
                'payments_total': str(pay_total),
                'difference': str(diff),
                'period': {'start_date': start_date, 'end_date': end_date},
            })
        return results

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

        clients = Client.objects.filter(
            car__unload_date__gte=start_date,
            car__unload_date__lte=end_date
        ).distinct()

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

        warehouses = Warehouse.objects.filter(
            Q(car__unload_date__gte=start_date, car__unload_date__lte=end_date)
        ).distinct()

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


