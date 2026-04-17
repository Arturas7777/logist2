"""
Оптимизированные менеджеры для моделей с улучшенными запросами
"""

from datetime import timedelta

from django.db import models
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone


class OptimizedCarManager(models.Manager):
    """Оптимизированный менеджер для модели Car"""

    def with_related(self):
        """Получить автомобили с предзагруженными связанными объектами"""
        return self.select_related(
            'client', 'warehouse', 'container', 'container__line'
        )

    def by_client(self, client_id):
        """Автомобили клиента с оптимизацией"""
        return self.with_related().filter(client_id=client_id)

    def by_warehouse(self, warehouse_id):
        """Автомобили склада с оптимизацией"""
        return self.with_related().filter(warehouse_id=warehouse_id)

    def by_status(self, status):
        """Автомобили по статусу с оптимизацией"""
        return self.with_related().filter(status=status)

    def by_date_range(self, start_date, end_date):
        """Автомобили за период с оптимизацией"""
        return self.with_related().filter(
            unload_date__gte=start_date,
            unload_date__lte=end_date
        )

    def active_cars(self):
        """Активные автомобили (не переданные)"""
        return self.with_related().exclude(status='TRANSFERRED')

    def recent_cars(self, days=30):
        """Недавние автомобили"""
        cutoff_date = timezone.now().date() - timedelta(days=days)
        return self.with_related().filter(unload_date__gte=cutoff_date)

    def search_cars(self, query):
        """Поиск автомобилей по VIN, марке, году"""
        return self.with_related().filter(
            Q(vin__icontains=query) |
            Q(brand__icontains=query) |
            Q(year__icontains=query)
        )

    def update_related(self, instance):
        """Заглушка для совместимости — для Car не делает ничего"""
        pass


class OptimizedContainerManager(models.Manager):
    """Оптимизированный менеджер для модели Container"""

    def with_related(self):
        """Получить контейнеры с предзагруженными связанными объектами"""
        return self.select_related(
            'client', 'warehouse', 'line'
        ).prefetch_related('container_cars')

    def by_client(self, client_id):
        return self.with_related().filter(client_id=client_id)

    def by_warehouse(self, warehouse_id):
        return self.with_related().filter(warehouse_id=warehouse_id)

    def by_status(self, status):
        return self.with_related().filter(status=status)

    def by_date_range(self, start_date, end_date):
        return self.with_related().filter(
            eta__gte=start_date,
            eta__lte=end_date
        )

    def with_car_stats(self):
        return self.with_related().annotate(
            cars_count=Count('container_cars'),
            total_car_value=Sum('container_cars__total_price'),
            avg_car_value=Avg('container_cars__total_price')
        )

    def update_related(self, instance):
        """Обновить связанные объекты контейнера"""
        if not instance.pk:
            return

        cars = instance.container_cars.all()
        if not cars:
            return
        ths_per_car = (instance.ths or 0) / cars.count()
        for car in cars:
            car.sync_with_container(instance, ths_per_car)
            car.save()


class OptimizedClientManager(models.Manager):
    """Оптимизированный менеджер для модели Client"""

    def with_balance_info(self):
        """Клиенты с информацией о балансах — использует единое поле balance"""
        return self.annotate(
            cars_count=Count('car'),
            active_cars_count=Count(
                'car',
                filter=Q(car__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            unpaid_invoices_count=Count(
                'received_invoices_new',
                filter=Q(received_invoices_new__status__in=['ISSUED', 'PARTIALLY_PAID', 'OVERDUE'])
            )
        )

    def with_recent_activity(self, days=30):
        cutoff_date = timezone.now().date() - timedelta(days=days)
        return self.with_balance_info().filter(
            Q(car__unload_date__gte=cutoff_date) |
            Q(received_invoices_new__date__gte=cutoff_date) |
            Q(transactions_sent_new__date__gte=cutoff_date)
        ).distinct()

    def search_clients(self, query):
        return self.with_balance_info().filter(name__icontains=query)


class OptimizedWarehouseManager(models.Manager):
    """Оптимизированный менеджер для модели Warehouse"""

    def with_activity_info(self):
        """Склады с информацией об активности"""
        return self.annotate(
            cars_count=Count('car'),
            active_cars_count=Count(
                'car',
                filter=Q(car__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            containers_count=Count('container'),
            active_containers_count=Count(
                'container',
                filter=Q(container__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            total_cars_value=Sum('car__total_price')
        )

    def active_warehouses(self):
        return self.with_activity_info().filter(
            Q(cars_count__gt=0) | Q(containers_count__gt=0)
        )


class OptimizedCompanyManager(models.Manager):
    """Оптимизированный менеджер для модели Company"""

    def with_financial_info(self):
        """Компании с финансовой информацией через новую систему"""
        return self.annotate(
            outgoing_invoices_total=Sum(
                'issued_invoices_new__total',
            ),
            incoming_invoices_total=Sum(
                'received_invoices_new__total',
            ),
            received_payments_total=Sum(
                'transactions_received_new__amount',
                filter=Q(transactions_received_new__status='COMPLETED')
            ),
            sent_payments_total=Sum(
                'transactions_sent_new__amount',
                filter=Q(transactions_sent_new__status='COMPLETED')
            ),
        )

    def default_company(self):
        """Получить компанию по умолчанию (Caromoto Lithuania)"""
        return self.filter(name__icontains='Caromoto').first()
