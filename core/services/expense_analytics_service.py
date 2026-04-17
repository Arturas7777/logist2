"""
ExpenseAnalyticsService
=======================
Aggregates personal expense data and generates AI-powered insights.
Used by the analytics page and the dashboard widget.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

PERIOD_MAP = {
    '1m': 1,
    '3m': 3,
    '6m': 6,
    '1y': 12,
    'all': None,
}


class ExpenseAnalyticsService:
    """Analytics for personal cash expenses."""

    def __init__(self, company=None):
        from core.models import Company
        self.company = company or Company.objects.filter(name__icontains='Caromoto').first()

    def _base_queryset(self, period='1m'):
        from core.models_billing import ExpenseCategory, Transaction

        personal_cats = list(ExpenseCategory.objects.filter(
            category_type='PERSONAL'
        ).values_list('id', flat=True))

        qs = Transaction.objects.filter(
            from_company=self.company,
            status='COMPLETED',
            method='CASH',
            category_id__in=personal_cats,
        )

        months = PERIOD_MAP.get(period)
        if months is not None:
            start = timezone.now() - timedelta(days=months * 30)
            qs = qs.filter(date__gte=start)

        return qs

    def get_category_breakdown(self, period='1m'):
        """Returns list of {category, total, count, percentage} sorted by total desc."""
        qs = self._base_queryset(period)

        data = list(qs.values(
            'category__name', 'category__id'
        ).annotate(
            total=Sum('amount'),
            count=Count('id'),
        ).order_by('-total'))

        grand_total = sum(d['total'] for d in data) if data else Decimal('0')

        result = []
        for d in data:
            pct = (d['total'] / grand_total * 100) if grand_total > 0 else Decimal('0')
            result.append({
                'category_id': d['category__id'],
                'category': d['category__name'],
                'total': float(d['total']),
                'count': d['count'],
                'percentage': round(float(pct), 1),
            })
        return result

    def get_monthly_trend(self, months=6):
        """Returns list of {month: 'YYYY-MM', total: float} for the last N months."""
        from django.db.models.functions import TruncMonth

        from core.models_billing import ExpenseCategory, Transaction

        personal_cats = list(ExpenseCategory.objects.filter(
            category_type='PERSONAL'
        ).values_list('id', flat=True))

        now = timezone.now()
        start = (now - timedelta(days=months * 30)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        qs = Transaction.objects.filter(
            from_company=self.company,
            status='COMPLETED',
            method='CASH',
            category_id__in=personal_cats,
            date__gte=start,
        ).annotate(
            month=TruncMonth('date')
        ).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')

        return [
            {'month': d['month'].strftime('%Y-%m'), 'total': float(d['total'])}
            for d in qs
        ]

    def get_top_items(self, period='1m', limit=15):
        """Extract top purchased items from receipt_data across transactions."""
        qs = self._base_queryset(period).exclude(
            receipt_data__isnull=True
        )

        items_agg = defaultdict(lambda: {'qty': 0, 'total': Decimal('0'), 'count': 0})

        for tx in qs.only('receipt_data'):
            data = tx.receipt_data or {}
            for item in data.get('items', []):
                name = (item.get('name') or '').strip()
                if not name:
                    continue
                key = name.lower()
                items_agg[key]['name'] = name
                items_agg[key]['qty'] += item.get('qty', 1)
                price = Decimal(str(item.get('price', 0)))
                qty = item.get('qty', 1)
                items_agg[key]['total'] += price * qty
                items_agg[key]['count'] += 1

        sorted_items = sorted(items_agg.values(), key=lambda x: x['total'], reverse=True)
        return [
            {
                'name': it['name'],
                'qty': it['qty'],
                'total': float(it['total']),
                'count': it['count'],
            }
            for it in sorted_items[:limit]
        ]

    def get_ai_insights(self, period='3m'):
        """
        Generate AI-powered spending analysis.
        Sends expense summary to Claude and gets textual insights back.
        """
        cache_key = f'expense_insights_{period}'
        cached = cache.get(cache_key)
        if cached:
            return cached

        breakdown = self.get_category_breakdown(period)
        trend = self.get_monthly_trend(months=PERIOD_MAP.get(period, 3) or 12)
        top_items = self.get_top_items(period, limit=20)

        qs = self._base_queryset(period)
        descriptions = list(
            qs.exclude(description='').values_list('description', flat=True)[:50]
        )

        total = sum(b['total'] for b in breakdown)
        if total == 0:
            return {
                'summary': 'Нет данных о расходах за выбранный период.',
                'recommendations': [],
                'highlights': [],
            }

        prompt_data = {
            'period': period,
            'total_spent': total,
            'by_category': breakdown,
            'monthly_trend': trend,
            'top_items_from_receipts': top_items,
            'expense_descriptions': descriptions[:30],
        }

        try:
            result = self._call_ai_for_insights(prompt_data)
            cache.set(cache_key, result, 3600)
            return result
        except Exception as e:
            logger.error("AI insights generation failed: %s", e, exc_info=True)
            return {
                'summary': f'Общие расходы за период: {total:.2f} €. '
                           f'Топ категория: {breakdown[0]["category"] if breakdown else "—"}.',
                'recommendations': [],
                'highlights': [],
                'error': str(e),
            }

    def _call_ai_for_insights(self, data: dict) -> dict:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed")

        api_key = os.getenv('ANTHROPIC_API_KEY', '')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        client = anthropic.Anthropic(api_key=api_key)

        system = """Ты — финансовый аналитик личных расходов. Пользователь ведёт учёт наличных трат.
Проанализируй данные и дай полезные инсайты на русском языке.

Верни ТОЛЬКО валидный JSON:
{
  "summary": "2-3 предложения: общая картина трат за период",
  "highlights": [
    "Важное наблюдение 1",
    "Важное наблюдение 2"
  ],
  "recommendations": [
    "Рекомендация по оптимизации 1",
    "Рекомендация по оптимизации 2"
  ]
}"""

        user_msg = (
            f"Вот данные о личных расходах.\n\n"
            f"```json\n{json.dumps(data, ensure_ascii=False, default=str)}\n```\n\n"
            f"Проанализируй и дай инсайты. Верни ТОЛЬКО JSON."
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            temperature=0.3,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            lines = [l for l in lines if not l.strip().startswith('```')]
            text = '\n'.join(lines)

        return json.loads(text)
