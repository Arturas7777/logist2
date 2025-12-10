"""
Утилиты для кэширования часто запрашиваемых данных
"""

from django.core.cache import cache
from django.db.models import Sum, Count, Avg
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# Время кэширования (в секундах)
CACHE_TIMEOUTS = {
    'short': 300,      # 5 минут
    'medium': 1800,    # 30 минут
    'long': 3600,      # 1 час
    'very_long': 7200, # 2 часа
}

def get_cache_key(prefix, *args):
    """Генерирует ключ кэша из префикса и аргументов"""
    return f"{prefix}:{':'.join(str(arg) for arg in args)}"

def cache_company_stats():
    """Кэширует статистику компании"""
    cache_key = get_cache_key('company_stats')
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    from .models import Company, Car
    from .models_billing import NewInvoice as Invoice, Transaction as Payment
    
    try:
        # Получаем компанию по умолчанию
        company = Company.objects.filter(name__icontains='Caromoto').first()
        if not company:
            return {}
        
        # Общая статистика
        total_cars = Car.objects.count()
        active_cars = Car.objects.exclude(status='TRANSFERRED').count()
        
        # Статистика за последний месяц
        month_ago = timezone.now().date() - timedelta(days=30)
        
        monthly_invoices = Invoice.objects.filter(
            issue_date__gte=month_ago
        ).aggregate(
            total_amount=Sum('total_amount'),
            count=Count('id')
        )
        
        monthly_payments = Payment.objects.filter(
            date__gte=month_ago
        ).aggregate(
            total_amount=Sum('amount'),
            count=Count('id')
        )
        
        monthly_cars = Car.objects.filter(
            unload_date__gte=month_ago
        ).aggregate(
            total_value=Sum('total_price'),
            count=Count('id')
        )
        
        stats = {
            'company': {
                'name': company.name,
                'invoice_balance': float(company.invoice_balance),
                'cash_balance': float(company.cash_balance),
                'card_balance': float(company.card_balance),
                'total_balance': float(company.invoice_balance + company.cash_balance + company.card_balance),
            },
            'cars': {
                'total': total_cars,
                'active': active_cars,
                'transferred': total_cars - active_cars,
            },
            'monthly': {
                'invoices': {
                    'total_amount': float(monthly_invoices['total_amount'] or 0),
                    'count': monthly_invoices['count'] or 0,
                },
                'payments': {
                    'total_amount': float(monthly_payments['total_amount'] or 0),
                    'count': monthly_payments['count'] or 0,
                },
                'cars': {
                    'total_value': float(monthly_cars['total_value'] or 0),
                    'count': monthly_cars['count'] or 0,
                },
            },
            'cached_at': timezone.now().isoformat(),
        }
        
        cache.set(cache_key, stats, CACHE_TIMEOUTS['medium'])
        return stats
        
    except Exception as e:
        logger.error(f"Error caching company stats: {e}")
        return {}

def cache_client_stats(client_id):
    """Кэширует статистику клиента"""
    cache_key = get_cache_key('client_stats', client_id)
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    from .models import Client, Car
    from .models_billing import NewInvoice as Invoice, Transaction as Payment
    
    try:
        client = Client.objects.get(id=client_id)
        
        # Статистика автомобилей
        cars_stats = Car.objects.filter(client=client).aggregate(
            total=Count('id'),
            active=Count('id', filter=models.Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])),
            total_value=Sum('total_price'),
            avg_value=Avg('total_price')
        )
        
        # Статистика инвойсов
        invoices_stats = Invoice.objects.filter(
            Q(from_entity_type='CLIENT', from_entity_id=client_id) |
            Q(to_entity_type='CLIENT', to_entity_id=client_id)
        ).aggregate(
            total_amount=Sum('total_amount'),
            count=Count('id'),
            paid_count=Count('id', filter=models.Q(paid=True))
        )
        
        # Статистика платежей
        payments_stats = Payment.objects.filter(
            Q(from_client=client) | Q(to_client=client)
        ).aggregate(
            total_amount=Sum('amount'),
            count=Count('id')
        )
        
        stats = {
            'client': {
                'id': client.id,
                'name': client.name,
                'invoice_balance': float(client.invoice_balance),
                'cash_balance': float(client.cash_balance),
                'card_balance': float(client.card_balance),
                'total_balance': float(client.invoice_balance + client.cash_balance + client.card_balance),
            },
            'cars': {
                'total': cars_stats['total'] or 0,
                'active': cars_stats['active'] or 0,
                'total_value': float(cars_stats['total_value'] or 0),
                'avg_value': float(cars_stats['avg_value'] or 0),
            },
            'invoices': {
                'total_amount': float(invoices_stats['total_amount'] or 0),
                'count': invoices_stats['count'] or 0,
                'paid_count': invoices_stats['paid_count'] or 0,
                'unpaid_count': (invoices_stats['count'] or 0) - (invoices_stats['paid_count'] or 0),
            },
            'payments': {
                'total_amount': float(payments_stats['total_amount'] or 0),
                'count': payments_stats['count'] or 0,
            },
            'cached_at': timezone.now().isoformat(),
        }
        
        cache.set(cache_key, stats, CACHE_TIMEOUTS['medium'])
        return stats
        
    except Client.DoesNotExist:
        return {}
    except Exception as e:
        logger.error(f"Error caching client stats for {client_id}: {e}")
        return {}

def cache_warehouse_stats(warehouse_id):
    """Кэширует статистику склада"""
    cache_key = get_cache_key('warehouse_stats', warehouse_id)
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    from .models import Warehouse, Car, Container
    from .models_billing import NewInvoice as Invoice, Transaction as Payment
    
    try:
        warehouse = Warehouse.objects.get(id=warehouse_id)
        
        # Статистика автомобилей
        cars_stats = Car.objects.filter(warehouse=warehouse).aggregate(
            total=Count('id'),
            active=Count('id', filter=models.Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])),
            total_value=Sum('total_price')
        )
        
        # Статистика контейнеров
        containers_stats = Container.objects.filter(warehouse=warehouse).aggregate(
            total=Count('id'),
            active=Count('id', filter=models.Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED']))
        )
        
        # Статистика инвойсов
        invoices_stats = Invoice.objects.filter(
            Q(from_entity_type='WAREHOUSE', from_entity_id=warehouse_id) |
            Q(to_entity_type='WAREHOUSE', to_entity_id=warehouse_id)
        ).aggregate(
            total_amount=Sum('total_amount'),
            count=Count('id')
        )
        
        # Статистика платежей
        payments_stats = Payment.objects.filter(
            Q(from_warehouse=warehouse) | Q(to_warehouse=warehouse)
        ).aggregate(
            total_amount=Sum('amount'),
            count=Count('id')
        )
        
        stats = {
            'warehouse': {
                'id': warehouse.id,
                'name': warehouse.name,
                'invoice_balance': float(warehouse.invoice_balance),
                'cash_balance': float(warehouse.cash_balance),
                'card_balance': float(warehouse.card_balance),
                'total_balance': float(warehouse.invoice_balance + warehouse.cash_balance + warehouse.card_balance),
            },
            'cars': {
                'total': cars_stats['total'] or 0,
                'active': cars_stats['active'] or 0,
                'total_value': float(cars_stats['total_value'] or 0),
            },
            'containers': {
                'total': containers_stats['total'] or 0,
                'active': containers_stats['active'] or 0,
            },
            'invoices': {
                'total_amount': float(invoices_stats['total_amount'] or 0),
                'count': invoices_stats['count'] or 0,
            },
            'payments': {
                'total_amount': float(payments_stats['total_amount'] or 0),
                'count': payments_stats['count'] or 0,
            },
            'cached_at': timezone.now().isoformat(),
        }
        
        cache.set(cache_key, stats, CACHE_TIMEOUTS['medium'])
        return stats
        
    except Warehouse.DoesNotExist:
        return {}
    except Exception as e:
        logger.error(f"Error caching warehouse stats for {warehouse_id}: {e}")
        return {}

def cache_comparison_data(start_date, end_date):
    """Кэширует данные для системы сравнения"""
    cache_key = get_cache_key('comparison_data', start_date, end_date)
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    from .services.comparison_service import ComparisonService
    
    try:
        comparison_service = ComparisonService()
        
        # Общий отчет
        report = comparison_service.get_comparison_report(start_date, end_date)
        
        # Расхождения
        discrepancies = comparison_service.find_discrepancies(start_date, end_date)
        
        data = {
            'report': report,
            'discrepancies': discrepancies,
            'cached_at': timezone.now().isoformat(),
        }
        
        cache.set(cache_key, data, CACHE_TIMEOUTS['short'])
        return data
        
    except Exception as e:
        logger.error(f"Error caching comparison data: {e}")
        return {}

def invalidate_cache(pattern):
    """Инвалидирует кэш по паттерну"""
    try:
        cache.delete_many(cache.keys(pattern))
        logger.info(f"Cache invalidated for pattern: {pattern}")
    except Exception as e:
        logger.error(f"Error invalidating cache for pattern {pattern}: {e}")

def invalidate_related_cache(model_name, instance_id):
    """Инвалидирует связанный кэш при изменении объекта"""
    patterns = [
        f"company_stats:*",
        f"client_stats:*",
        f"warehouse_stats:*",
        f"comparison_data:*",
    ]
    
    for pattern in patterns:
        invalidate_cache(pattern)
    
    logger.info(f"Related cache invalidated for {model_name} #{instance_id}")

# Декораторы для кэширования
def cache_method_result(timeout='medium'):
    """Декоратор для кэширования результатов методов"""
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            cache_key = get_cache_key(
                f"{func.__name__}_{self.__class__.__name__}",
                self.id if hasattr(self, 'id') else str(self),
                *args
            )
            
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            result = func(self, *args, **kwargs)
            cache.set(cache_key, result, CACHE_TIMEOUTS[timeout])
            return result
        
        return wrapper
    return decorator
