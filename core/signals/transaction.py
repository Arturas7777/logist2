"""Сигналы для ``Transaction``: пересчёт балансов и ``paid_amount``.

Эти пересчёты делаются **синхронно** — пользователь должен увидеть
актуальный ``paid_amount`` инвойса сразу после ответа на запрос. Расчёт
дешёвый (один SUM + UPDATE), поэтому в очередь не уносим.
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models_billing import Transaction

logger = logging.getLogger(__name__)


def _recalc_transaction_effects(instance):
    # B4 (AUDIT_ROUND3): деньги — исключения НЕ глотаем. Сбой пересчёта
    # баланса/paid_amount означает рассинхрон финансового состояния;
    # пробрасываем, чтобы вся транзакция сохранения откатилась и ошибка
    # ушла в Sentry, а не осталась «тихо неверным» балансом.
    if instance.status != "COMPLETED":
        return
    for entity in (instance.sender, instance.recipient):
        try:
            Transaction.recalculate_entity_balance(entity)
        except Exception:
            logger.exception("Error recalculating balance for %s", entity)
            raise
    if instance.invoice_id:
        try:
            instance.invoice.recalculate_paid_amount()
        except Exception:
            logger.exception("Error recalculating paid_amount for invoice %s", instance.invoice_id)
            raise


@receiver(post_save, sender=Transaction)
def recalculate_on_transaction_save(sender, instance, **kwargs):
    if getattr(instance, "_skip_balance_recalc", False):
        return
    _recalc_transaction_effects(instance)


@receiver(post_delete, sender=Transaction)
def recalculate_on_transaction_delete(sender, instance, **kwargs):
    _recalc_transaction_effects(instance)
