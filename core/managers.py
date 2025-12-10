"""
Оптимизированные менеджеры для моделей с улучшенными запросами
"""

from django.db import models
from django.db.models import Prefetch, Q, Sum, Count, Avg
from django.utils import timezone
from datetime import timedelta


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


class OptimizedInvoiceManager(models.Manager):
    """Оптимизированный менеджер для модели Invoice"""
    
    def with_related(self):
        """Получить инвойсы с предзагруженными связанными объектами"""
        return self.select_related('client').prefetch_related('cars', 'payment_set')  # cars здесь - это ManyToMany в Invoice
    
    def by_entity(self, entity_type, entity_id):
        """Инвойсы по сущности (от кого или кому)"""
        return self.with_related().filter(
            Q(from_entity_type=entity_type, from_entity_id=entity_id) |
            Q(to_entity_type=entity_type, to_entity_id=entity_id)
        )
    
    def by_client(self, client_id):
        """Инвойсы клиента"""
        return self.with_related().filter(client_id=client_id)
    
    def by_date_range(self, start_date, end_date):
        """Инвойсы за период"""
        return self.with_related().filter(
            issue_date__gte=start_date,
            issue_date__lte=end_date
        )
    
    def unpaid(self):
        """Неоплаченные инвойсы"""
        return self.with_related().filter(paid=False)
    
    def paid(self):
        """Оплаченные инвойсы"""
        return self.with_related().filter(paid=True)
    
    def overdue(self):
        """Просроченные инвойсы"""
        cutoff_date = timezone.now().date() - timedelta(days=30)
        return self.with_related().filter(
            issue_date__lt=cutoff_date,
            paid=False
        )
    
    def with_payment_info(self):
        """Инвойсы с информацией о платежах"""
        return self.with_related().annotate(
            total_payments=Sum('payment__amount'),
            remaining_amount=models.F('total_amount') - models.F('total_payments')
        )


class OptimizedPaymentManager(models.Manager):
    """Оптимизированный менеджер для модели Payment"""
    
    def with_related(self):
        """Получить платежи с предзагруженными связанными объектами"""
        return self.select_related(
            'sender_content_type',
            'recipient_content_type',
            'invoice'
        )
    
    def by_sender(self, sender_type, sender_id):
        """Платежи отправителя"""
        return self.with_related().filter(
            sender_content_type__model=sender_type,
            sender_object_id=sender_id
        )
    
    def by_recipient(self, recipient_type, recipient_id):
        """Платежи получателя"""
        return self.with_related().filter(
            recipient_content_type__model=recipient_type,
            recipient_object_id=recipient_id
        )
    
    def by_date_range(self, start_date, end_date):
        """Платежи за период"""
        return self.with_related().filter(
            date__gte=start_date,
            date__lte=end_date
        )
    
    def by_payment_type(self, payment_type):
        """Платежи по типу"""
        return self.with_related().filter(payment_type=payment_type)
    
    def recent_payments(self, days=30):
        """Недавние платежи"""
        cutoff_date = timezone.now().date() - timedelta(days=days)
        return self.with_related().filter(date__gte=cutoff_date)
    
    def with_balance_effects(self):
        """Платежи с информацией о влиянии на баланс"""
        return self.with_related().annotate(
            balance_effect=models.Case(
                models.When(from_balance=True, then=models.F('amount') * -1),
                default=models.F('amount'),
                output_field=models.DecimalField()
            )
        )


class OptimizedContainerManager(models.Manager):
    """Оптимизированный менеджер для модели Container"""
    
    def with_related(self):
        """Получить контейнеры с предзагруженными связанными объектами"""
        return self.select_related(
            'client', 'warehouse', 'line'
        ).prefetch_related('container_cars')
    
    def by_client(self, client_id):
        """Контейнеры клиента"""
        return self.with_related().filter(client_id=client_id)
    
    def by_warehouse(self, warehouse_id):
        """Контейнеры склада"""
        return self.with_related().filter(warehouse_id=warehouse_id)
    
    def by_status(self, status):
        """Контейнеры по статусу"""
        return self.with_related().filter(status=status)
    
    def by_date_range(self, start_date, end_date):
        """Контейнеры за период"""
        return self.with_related().filter(
            eta__gte=start_date,
            eta__lte=end_date
        )
    
    def with_car_stats(self):
        """Контейнеры со статистикой по автомобилям"""
        return self.with_related().annotate(
            cars_count=Count('cars'),
            total_car_value=Sum('cars__total_price'),
            avg_car_value=Avg('cars__total_price')
        )
    
    def update_related(self, instance):
        """Обновить связанные объекты контейнера"""
        # Проверяем, что у экземпляра есть первичный ключ
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
        """Клиенты с информацией о балансах - использует единое поле balance"""
        return self.annotate(
            # Количество автомобилей
            cars_count=Count('car'),
            # Активные автомобили
            active_cars_count=Count(
                'car', 
                filter=Q(car__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            # Неоплаченные инвойсы (новая система)
            unpaid_invoices_count=Count(
                'received_invoices_new',
                filter=Q(received_invoices_new__status__in=['ISSUED', 'PARTIALLY_PAID', 'OVERDUE'])
            )
        )
    
    def with_recent_activity(self, days=30):
        """Клиенты с недавней активностью"""
        cutoff_date = timezone.now().date() - timedelta(days=days)
        return self.with_balance_info().filter(
            Q(car__unload_date__gte=cutoff_date) |
            Q(received_invoices_new__date__gte=cutoff_date) |
            Q(transactions_sent_new__date__gte=cutoff_date)
        ).distinct()
    
    def search_clients(self, query):
        """Поиск клиентов по имени"""
        return self.with_balance_info().filter(name__icontains=query)


class OptimizedWarehouseManager(models.Manager):
    """Оптимизированный менеджер для модели Warehouse"""
    
    def with_activity_info(self):
        """Склады с информацией об активности - БЫСТРАЯ ВЕРСИЯ"""
        return self.annotate(
            # Количество автомобилей
            cars_count=Count('car'),
            # Активные автомобили
            active_cars_count=Count(
                'car',
                filter=Q(car__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            # Количество контейнеров
            containers_count=Count('container'),
            # Активные контейнеры
            active_containers_count=Count(
                'container',
                filter=Q(container__status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])
            ),
            # Сумма инвойсов
            total_invoices=Sum('payments_received__invoice__total_amount'),
            # Сумма платежей
            total_payments=Sum('payments_received__amount'),
            # Общая стоимость автомобилей на складе
            total_cars_value=Sum('car__total_price')
        )
    
    def active_warehouses(self):
        """Активные склады (с автомобилями или контейнерами)"""
        return self.with_activity_info().filter(
            Q(cars_count__gt=0) | Q(containers_count__gt=0)
        )


class OptimizedCompanyManager(models.Manager):
    """Оптимизированный менеджер для модели Company"""
    
    def with_financial_info(self):
        """Компании с финансовой информацией - БЫСТРАЯ ВЕРСИЯ"""
        return self.annotate(
            # Исходящие инвойсы (мы выставляем)
            outgoing_invoices_total=Sum(
                'payments_sent__invoice__total_amount',
                filter=Q(payments_sent__invoice__from_entity_type='COMPANY')
            ),
            # Входящие инвойсы (нам выставляют)
            incoming_invoices_total=Sum(
                'payments_received__invoice__total_amount',
                filter=Q(payments_received__invoice__to_entity_type='COMPANY')
            ),
            # Полученные платежи
            received_payments_total=Sum('payments_received__amount'),
            # Отправленные платежи
            sent_payments_total=Sum('payments_sent__amount'),
            # Баланс
            balance_difference=models.F('received_payments_total') - models.F('sent_payments_total')
        )
    
    def default_company(self):
        """Получить компанию по умолчанию (Caromoto Lithuania)"""
        return self.with_financial_info().filter(name__icontains='Caromoto').first()
