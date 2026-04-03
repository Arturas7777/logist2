"""
Централизованный менеджер для управления балансами всех сущностей.

Баланс рассчитывается строго из COMPLETED-транзакций (incoming − outgoing).
Ручное обновление баланса не требуется — сигналы post_save/post_delete
на Transaction автоматически вызывают recalculate_entity_balance().

Этот класс предоставляет удобные обёртки для:
- Пересчёта баланса одной или всех сущностей
- Валидации консистентности
"""

from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
import logging

logger = logging.getLogger(__name__)


class BalanceManager:

    @staticmethod
    def quantize_amount(amount) -> Decimal:
        if amount is None:
            return Decimal('0.00')
        return Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @classmethod
    def recalculate_entity_balance(cls, entity):
        """Пересчитать баланс одной сущности по транзакциям."""
        from core.models_billing import Transaction
        Transaction.recalculate_entity_balance(entity)

    @classmethod
    def recalculate_all_balances(cls):
        """Пересчитать балансы ВСЕХ сущностей по транзакциям."""
        from core.models import Client, Warehouse, Line, Company, Carrier
        from core.models_billing import Transaction

        updated = 0
        with transaction.atomic():
            for model in [Client, Warehouse, Line, Company, Carrier]:
                for entity in model.objects.all():
                    try:
                        Transaction.recalculate_entity_balance(entity)
                        updated += 1
                    except Exception as e:
                        logger.error(f"Failed to recalculate balance for {entity}: {e}")

        logger.info(f"Recalculated balances for {updated} entities")
        return {'success': True, 'entities_updated': updated}

    @classmethod
    def recalculate_all_invoice_paid_amounts(cls):
        """Пересчитать paid_amount для ВСЕХ открытых инвойсов по транзакциям."""
        from core.models_billing import NewInvoice

        updated = 0
        invoices = NewInvoice.objects.exclude(status__in=['CANCELLED'])
        for inv in invoices:
            try:
                inv.recalculate_paid_amount()
                updated += 1
            except Exception as e:
                logger.error(f"Failed to recalculate paid_amount for invoice {inv.number}: {e}")

        logger.info(f"Recalculated paid_amount for {updated} invoices")
        return {'success': True, 'invoices_updated': updated}

    @classmethod
    def validate_balance_consistency(cls, entity) -> dict:
        """Проверить, совпадает ли entity.balance с расчётом из транзакций."""
        from core.models_billing import Transaction
        from django.db.models import Sum

        if not hasattr(entity, 'balance'):
            return {'is_valid': True, 'issues': [], 'entity': str(entity)}

        model_name = entity.__class__.__name__.lower()
        incoming = Transaction.objects.filter(
            status='COMPLETED', **{f'to_{model_name}': entity}
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        outgoing = Transaction.objects.filter(
            status='COMPLETED', **{f'from_{model_name}': entity}
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        expected = incoming - outgoing

        issues = []
        if entity.balance != expected:
            issues.append(
                f"Balance mismatch: stored={entity.balance}, expected={expected} "
                f"(incoming={incoming}, outgoing={outgoing})"
            )

        return {
            'is_valid': len(issues) == 0,
            'issues': issues,
            'entity': str(entity),
            'stored_balance': entity.balance,
            'expected_balance': expected,
        }
