"""
Утилиты для кэширования часто запрашиваемых данных
"""

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_TIMEOUTS = {
    'short': 300,
    'medium': 1800,
    'long': 3600,
    'very_long': 7200,
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

    from .models import Car, Company
    from .models_billing import NewInvoice, Transaction

    try:
        company = Company.get_default()
        if not company:
            return {}

        total_cars = Car.objects.count()
        active_cars = Car.objects.exclude(status='TRANSFERRED').count()

        month_ago = timezone.now().date() - timedelta(days=30)

        monthly_invoices = NewInvoice.objects.filter(
            date__gte=month_ago
        ).aggregate(
            total_amount=Sum('total'),
            count=Count('id')
        )

        monthly_payments = Transaction.objects.filter(
            date__gte=month_ago,
            status='COMPLETED'
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
                'balance': float(company.balance),
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

    from .models import Car, Client
    from .models_billing import NewInvoice, Transaction

    try:
        client = Client.objects.get(id=client_id)

        cars_stats = Car.objects.filter(client=client).aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])),
            total_value=Sum('total_price'),
            avg_value=Avg('total_price')
        )

        invoices_stats = NewInvoice.objects.filter(
            recipient_client=client
        ).aggregate(
            total_amount=Sum('total'),
            count=Count('id'),
            paid_count=Count('id', filter=Q(status='PAID'))
        )

        payments_stats = Transaction.objects.filter(
            Q(from_client=client) | Q(to_client=client),
            status='COMPLETED'
        ).aggregate(
            total_amount=Sum('amount'),
            count=Count('id')
        )

        stats = {
            'client': {
                'id': client.id,
                'name': client.name,
                'balance': float(client.balance),
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

    from .models import Car, Container, Warehouse
    from .models_billing import NewInvoice, Transaction

    try:
        warehouse = Warehouse.objects.get(id=warehouse_id)

        cars_stats = Car.objects.filter(warehouse=warehouse).aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED'])),
            total_value=Sum('total_price')
        )

        containers_stats = Container.objects.filter(warehouse=warehouse).aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status__in=['FLOATING', 'IN_PORT', 'UNLOADED']))
        )

        invoices_stats = NewInvoice.objects.filter(
            Q(issuer_warehouse=warehouse) | Q(recipient_warehouse=warehouse)
        ).aggregate(
            total_amount=Sum('total'),
            count=Count('id')
        )

        payments_stats = Transaction.objects.filter(
            Q(from_warehouse=warehouse) | Q(to_warehouse=warehouse),
            status='COMPLETED'
        ).aggregate(
            total_amount=Sum('amount'),
            count=Count('id')
        )

        stats = {
            'warehouse': {
                'id': warehouse.id,
                'name': warehouse.name,
                'balance': float(warehouse.balance),
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
        report = comparison_service.get_comparison_report(start_date, end_date)
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
    """Инвалидирует кэш по паттерну.

    Поддерживает Redis (через django-redis) и FileBasedCache / LocMem.
    Для Redis использует SCAN вместо KEYS для production-safety.
    Все ошибки глушатся — инвалидация кэша не должна ронять основной flow.
    """
    try:
        # django-redis
        try:
            from django_redis.cache import RedisCache as DjangoRedisCache
            if isinstance(cache, DjangoRedisCache):
                cache.delete_pattern(pattern)
                logger.debug(f"Cache invalidated (django-redis): {pattern}")
                return
        except Exception:
            pass

        # Native Django Redis backend (Django 4.1+)
        try:
            from django.core.cache.backends.redis import RedisCache as NativeRedisCache
            if isinstance(cache, NativeRedisCache):
                client = cache._cache.get_client()
                prefix = getattr(cache, 'key_prefix', '') or ''
                full_pattern = f"{prefix}:{pattern}" if prefix else pattern
                cursor = 0
                keys_to_delete = []
                while True:
                    cursor, keys = client.scan(cursor, match=full_pattern, count=100)
                    keys_to_delete.extend(keys)
                    if cursor == 0:
                        break
                if keys_to_delete:
                    client.delete(*keys_to_delete)
                logger.debug(f"Cache invalidated (native redis): {pattern}, keys={len(keys_to_delete)}")
                return
        except Exception:
            pass

        # Fallback для LocMem / FileBasedCache — точечная очистка известных префиксов.
        # get_cache_key всегда ставит ':' в конце, поэтому пустые ключи совпадают с паттерном.
        base_keys = [
            'company_stats:',
            'client_stats:',
            'warehouse_stats:',
            'comparison_data:',
        ]
        pattern_stripped = pattern.rstrip('*').rstrip(':')
        matching = [k for k in base_keys if pattern_stripped in k]
        if matching:
            cache.delete_many(matching)
        logger.debug(f"Cache invalidated (fallback): {pattern}")
    except Exception as e:
        logger.debug(f"Error invalidating cache for pattern {pattern}: {e}")

def invalidate_dashboard_cache():
    """Удаляет все ключи дашборда (`dashboard:*`) явным списком.

    Явный `delete_many` вместо pattern-инвалидации: работает на любом
    cache backend (LocMem/File/Redis) и не требует SCAN. Список ключей —
    единый источник правды `DashboardService._DASHBOARD_CACHE_KEYS`.
    """
    try:
        from .services.dashboard_service import DashboardService

        keys = [
            get_cache_key('dashboard', name, *args)
            for name, args in DashboardService._DASHBOARD_CACHE_KEYS
        ]
        cache.delete_many(keys)
        logger.debug("Dashboard cache invalidated (%d keys)", len(keys))
    except Exception as e:
        logger.debug("Error invalidating dashboard cache: %s", e)


def invalidate_related_cache(model_name, instance_id):
    """Точечно инвалидирует связанный кэш при изменении объекта.

    Раньше любой post_save на 9 ключевых моделей выжигал ВСЕ паттерны
    (`company_stats:*`, `client_stats:*`, `warehouse_stats:*`,
    `comparison_data:*`). При активной работе кэш фактически никогда
    не доживал до TTL, плюс каждый раз дёргался Redis SCAN. Теперь
    инвалидация привязана к модели и к конкретному ID, где это возможно.
    """
    model_lower = (model_name or '').lower()

    # 1. company_stats — единственная компания (Caromoto), её статистика
    #    меняется только когда движутся транзакции/инвойсы/машины/контейнеры.
    if model_lower in ('newinvoice', 'transaction', 'car', 'container', 'company'):
        cache.delete(get_cache_key('company_stats'))

    # 2. client_stats:<id> — если знаем id, чистим только его. Если изменилась
    #    транзакция/инвойс — нужно вычислить, какого клиента она касается;
    #    делаем точечную чистку, иначе fallback на pattern.
    if model_lower == 'client' and instance_id:
        cache.delete(get_cache_key('client_stats', instance_id))
    elif model_lower in ('newinvoice', 'transaction', 'car') and instance_id:
        # Tx/Invoice/Car могут касаться нескольких клиентов; чтобы не
        # тащить лишний SQL, инвалидируем по pattern только в этих случаях.
        invalidate_cache("client_stats:*")

    # 3. warehouse_stats:<id> — точечно при изменении warehouse, либо по pattern
    #    если транзакция/инвойс могли касаться неизвестного склада.
    if model_lower == 'warehouse' and instance_id:
        cache.delete(get_cache_key('warehouse_stats', instance_id))
    elif model_lower in ('newinvoice', 'transaction', 'car', 'container'):
        # Любой Car/Container/Tx/Invoice потенциально влияет на склад.
        invalidate_cache("warehouse_stats:*")

    # 4. comparison_data — зависит от CarService/InvoiceItem/SupplierCost и от
    #    Client/Warehouse при наличии. Меняется реже — pattern допустим, но
    #    только при «настоящих» сущностях расчёта.
    if model_lower in ('newinvoice', 'transaction', 'car', 'client', 'warehouse'):
        invalidate_cache("comparison_data:*")

    # 5. Дашборд компании: KPI, aging, recent-списки, cash wallet. Все эти
    #    ключи зависят от транзакций/инвойсов/машин/контейнеров — без явной
    #    инвалидации пользователь видел устаревшие цифры до конца TTL (5 мин).
    if model_lower in ('newinvoice', 'transaction', 'car', 'container', 'company'):
        invalidate_dashboard_cache()

    # 6. Маленькие справочные ключи.
    if model_lower in ('client', 'warehouse', 'line', 'carrier', 'company'):
        cache.delete(f'payment_objects:{model_lower}')
    if model_lower == 'warehouse':
        cache.delete('ref:warehouses_list')
    if model_lower == 'company':
        cache.delete('ref:companies_list')

    logger.debug("Related cache invalidated for %s #%s", model_name, instance_id)


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
