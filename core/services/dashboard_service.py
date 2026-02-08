"""
Сервис агрегации данных для дашборда компании
"""

from django.core.cache import cache
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import logging

from ..cache_utils import CACHE_TIMEOUTS, get_cache_key

logger = logging.getLogger(__name__)


class DashboardService:
    """Агрегирует данные для дашборда Caromoto Lithuania"""

    def __init__(self):
        from ..models import Company
        self.company = Company.objects.filter(name__icontains='Caromoto').first()

    # ========================================================================
    # OPERATIONAL KPIs
    # ========================================================================

    def get_cars_by_status(self):
        cache_key = get_cache_key('dashboard', 'cars_by_status')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models import Car
        qs = Car.objects.values('status').annotate(count=Count('id'))
        result = {row['status']: row['count'] for row in qs}
        # Ensure all statuses present
        for s in ('FLOATING', 'IN_PORT', 'UNLOADED', 'TRANSFERRED'):
            result.setdefault(s, 0)
        result['total'] = sum(result.values())

        cache.set(cache_key, result, CACHE_TIMEOUTS['short'])
        return result

    def get_containers_by_status(self):
        cache_key = get_cache_key('dashboard', 'containers_by_status')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models import Container
        qs = Container.objects.values('status').annotate(count=Count('id'))
        result = {row['status']: row['count'] for row in qs}
        for s in ('FLOATING', 'IN_PORT', 'UNLOADED', 'TRANSFERRED'):
            result.setdefault(s, 0)
        result['total'] = sum(result.values())

        cache.set(cache_key, result, CACHE_TIMEOUTS['short'])
        return result

    def get_cars_on_storage(self):
        cache_key = get_cache_key('dashboard', 'cars_on_storage')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models import Car
        result = Car.objects.filter(status='UNLOADED').aggregate(
            count=Count('id'),
            total_storage_cost=Sum('storage_cost')
        )
        result['total_storage_cost'] = float(result['total_storage_cost'] or 0)

        cache.set(cache_key, result, CACHE_TIMEOUTS['short'])
        return result

    def get_active_auto_transports(self):
        cache_key = get_cache_key('dashboard', 'active_auto_transports')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models import AutoTransport
        count = AutoTransport.objects.exclude(
            status__in=['DELIVERED', 'CANCELLED']
        ).count()

        cache.set(cache_key, count, CACHE_TIMEOUTS['short'])
        return count

    # ========================================================================
    # FINANCIAL KPIs
    # ========================================================================

    def get_company_balance(self):
        """Баланс компании — всегда свежий, без кэша"""
        if not self.company:
            return Decimal('0.00')
        self.company.refresh_from_db()
        return self.company.balance

    def get_outstanding_invoices_total(self):
        cache_key = get_cache_key('dashboard', 'outstanding_invoices')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import NewInvoice
        result = NewInvoice.objects.filter(
            status__in=['ISSUED', 'PARTIALLY_PAID']
        ).aggregate(
            total=Sum(F('total') - F('paid_amount')),
            count=Count('id')
        )
        data = {
            'total': float(result['total'] or 0),
            'count': result['count'] or 0,
        }

        cache.set(cache_key, data, CACHE_TIMEOUTS['short'])
        return data

    def get_monthly_revenue(self):
        cache_key = get_cache_key('dashboard', 'monthly_revenue')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import Transaction
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = Transaction.objects.filter(
            to_company=self.company,
            status='COMPLETED',
            date__gte=start_of_month
        ).aggregate(total=Sum('amount'))
        total = float(result['total'] or 0)

        cache.set(cache_key, total, CACHE_TIMEOUTS['short'])
        return total

    def get_monthly_expenses(self):
        cache_key = get_cache_key('dashboard', 'monthly_expenses')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import Transaction
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = Transaction.objects.filter(
            from_company=self.company,
            status='COMPLETED',
            date__gte=start_of_month
        ).aggregate(total=Sum('amount'))
        total = float(result['total'] or 0)

        cache.set(cache_key, total, CACHE_TIMEOUTS['short'])
        return total

    def get_overdue_invoices_count(self):
        cache_key = get_cache_key('dashboard', 'overdue_invoices')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import NewInvoice
        today = timezone.now().date()
        count = NewInvoice.objects.filter(
            Q(status='OVERDUE') |
            Q(status__in=['ISSUED', 'PARTIALLY_PAID'], due_date__lt=today)
        ).count()

        cache.set(cache_key, count, CACHE_TIMEOUTS['short'])
        return count

    # ========================================================================
    # CHARTS
    # ========================================================================

    def get_revenue_expenses_by_month(self, months=6):
        cache_key = get_cache_key('dashboard', 'revenue_expenses_chart', months)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import Transaction
        now = timezone.now()

        # Начало периода — первый день (months-1) месяцев назад
        start_dt = (now - timedelta(days=(months - 1) * 30)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        # 1 запрос: доходы по месяцам (вместо 6 отдельных)
        rev_qs = (
            Transaction.objects.filter(
                to_company=self.company,
                status='COMPLETED',
                date__gte=start_dt,
            )
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        rev_map = {row['month'].strftime('%m/%Y'): float(row['total']) for row in rev_qs}

        # 2-й запрос: расходы по месяцам
        exp_qs = (
            Transaction.objects.filter(
                from_company=self.company,
                status='COMPLETED',
                date__gte=start_dt,
            )
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        exp_map = {row['month'].strftime('%m/%Y'): float(row['total']) for row in exp_qs}

        # Собираем результат по всем месяцам периода
        labels = []
        revenue = []
        expenses = []
        for i in range(months - 1, -1, -1):
            dt = now - timedelta(days=i * 30)
            month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            label = month_start.strftime('%m/%Y')
            labels.append(label)
            revenue.append(rev_map.get(label, 0))
            expenses.append(exp_map.get(label, 0))

        data = {'labels': labels, 'revenue': revenue, 'expenses': expenses}
        cache.set(cache_key, data, CACHE_TIMEOUTS['medium'])
        return data

    def get_invoices_by_status(self):
        cache_key = get_cache_key('dashboard', 'invoices_by_status')
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from ..models_billing import NewInvoice
        qs = NewInvoice.objects.values('status').annotate(count=Count('id'))
        result = {row['status']: row['count'] for row in qs}

        cache.set(cache_key, result, CACHE_TIMEOUTS['short'])
        return result

    # ========================================================================
    # RECENT OPERATIONS
    # ========================================================================

    def get_recent_transactions(self, limit=15):
        from ..models_billing import Transaction
        return Transaction.objects.select_related(
            'from_client', 'from_warehouse', 'from_line', 'from_carrier', 'from_company',
            'to_client', 'to_warehouse', 'to_line', 'to_carrier', 'to_company',
            'invoice',
        ).order_by('-date')[:limit]

    def get_recent_invoices(self, limit=15):
        from ..models_billing import NewInvoice
        return NewInvoice.objects.select_related(
            'issuer_company', 'issuer_warehouse', 'issuer_line', 'issuer_carrier',
            'recipient_client', 'recipient_warehouse', 'recipient_line',
            'recipient_carrier', 'recipient_company',
        ).order_by('-date')[:limit]

    # ========================================================================
    # AGGREGATE
    # ========================================================================

    def get_full_dashboard_context(self):
        cars_by_status = self.get_cars_by_status()
        containers_by_status = self.get_containers_by_status()
        monthly_revenue = self.get_monthly_revenue()
        monthly_expenses = self.get_monthly_expenses()

        return {
            'company': self.company,
            # Operational KPIs
            'cars_by_status': cars_by_status,
            'containers_by_status': containers_by_status,
            'cars_on_storage': self.get_cars_on_storage(),
            'active_auto_transports': self.get_active_auto_transports(),
            # Financial KPIs
            'company_balance': self.get_company_balance(),
            'outstanding_invoices': self.get_outstanding_invoices_total(),
            'monthly_revenue': monthly_revenue,
            'monthly_expenses': monthly_expenses,
            'monthly_profit': monthly_revenue - monthly_expenses,
            'overdue_invoices_count': self.get_overdue_invoices_count(),
            # Charts
            'revenue_expenses_chart': self.get_revenue_expenses_by_month(),
            'invoices_by_status': self.get_invoices_by_status(),
            # Recent operations
            'recent_transactions': self.get_recent_transactions(),
            'recent_invoices': self.get_recent_invoices(),
        }
