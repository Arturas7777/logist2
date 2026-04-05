"""
Reusable model mixins.

These mixins provide shared behaviour without defining database fields,
so they can be added to existing models without generating migrations.
"""
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class BalanceMethodsMixin:
    """
    Provides ``get_balance_breakdown()`` and ``get_balance_info()`` for any
    model that has a ``balance`` DecimalField.

    Does NOT declare database fields — those stay on the concrete models.
    """

    def get_balance_breakdown(self):
        from django.db.models import Sum, Q

        model_name = self.__class__.__name__.lower()
        from core.models_billing import Transaction

        incoming_filter = Q(**{f'to_{model_name}': self})
        outgoing_filter = Q(**{f'from_{model_name}': self})

        transactions = Transaction.objects.filter(
            (incoming_filter | outgoing_filter),
            status='COMPLETED',
        )

        breakdown = {}
        for method in ('CASH', 'CARD', 'TRANSFER'):
            incoming = transactions.filter(
                incoming_filter, method=method,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            outgoing = transactions.filter(
                outgoing_filter, method=method,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            breakdown[method.lower()] = incoming - outgoing

        breakdown['total'] = self.balance
        return breakdown

    def get_balance_info(self):
        balance = self.balance
        if balance > 0:
            status, color = 'ПЕРЕПЛАТА', '#28a745'
            description = f'Переплата {balance:.2f}'
        elif balance < 0:
            status, color = 'ДОЛГ', '#dc3545'
            description = f'Долг {abs(balance):.2f}'
        else:
            status, color = 'БАЛАНС', '#6c757d'
            description = 'Баланс нулевой'

        return {
            'balance': balance,
            'status': status,
            'color': color,
            'description': description,
            'breakdown': self.get_balance_breakdown(),
        }
