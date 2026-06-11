"""Сигналы для ``BankTransaction``: авто-создание платежа при ручной
привязке банковской транзакции к инвойсу.

Когда пользователь вручную проставляет ``matched_invoice`` у банковской
транзакции (в админ-форме, через API и т.д.) — автоматически создаётся
``Transaction(PAYMENT)`` так, чтобы инвойс корректно пересчитал
``paid_amount`` и сменил статус на ``PAID``. Логика повторяет admin action
``BankTransactionAdmin.link_to_invoice``.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models_banking import BankTransaction
from core.models_billing import NewInvoice, Transaction

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=BankTransaction)
def _track_bt_matched_invoice_change(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_matched_invoice_id = None
        return
    update_fields = kwargs.get("update_fields")
    if (
        update_fields is not None
        and "matched_invoice" not in update_fields
        and "matched_invoice_id" not in update_fields
    ):
        instance._old_matched_invoice_id = getattr(instance, "_old_matched_invoice_id", None)
        return
    try:
        old = BankTransaction.objects.filter(pk=instance.pk).values("matched_invoice_id").first()
        instance._old_matched_invoice_id = old["matched_invoice_id"] if old else None
    except Exception:
        instance._old_matched_invoice_id = None


@receiver(post_save, sender=BankTransaction)
def auto_create_payment_on_bt_match(sender, instance, **kwargs):
    """Create a COMPLETED PAYMENT ``Transaction`` when ``matched_invoice``
    is set manually.

    Supports both directions:

    * Incoming bank (``bt.amount > 0``) + OUTGOING invoice (client pays us):
      ``from_client=recipient_client``, ``to_company=Caromoto``.
    * Outgoing bank (``bt.amount < 0``) + INCOMING invoice (we pay supplier):
      ``from_company=Caromoto``, ``to_<issuer_type>=issuer entity``.

    Conditions:

    * ``matched_invoice`` changed from NULL to a real invoice
    * ``matched_transaction`` is still NULL
    * ``reconciliation_skipped`` is False
    * invoice not CANCELLED, remaining amount > 0
    * bank amount direction matches invoice direction
    """
    if getattr(instance, "_creating_payment", False):
        return

    old_invoice_id = getattr(instance, "_old_matched_invoice_id", None)
    instance._old_matched_invoice_id = None

    if not instance.matched_invoice_id:
        return
    if old_invoice_id == instance.matched_invoice_id:
        return
    if instance.matched_transaction_id:
        return
    if instance.reconciliation_skipped:
        return

    bt_pk = instance.pk

    def _do():
        try:
            from core.models import Company

            with transaction.atomic():
                bt = BankTransaction.objects.select_for_update().get(pk=bt_pk)
                if bt.matched_transaction_id or not bt.matched_invoice_id:
                    return
                try:
                    invoice = NewInvoice.objects.select_for_update().get(pk=bt.matched_invoice_id)
                except NewInvoice.DoesNotExist:
                    return
                if invoice.status == "CANCELLED":
                    return

                remaining = invoice.total - invoice.paid_amount
                payment_amount = min(abs(bt.amount), remaining)
                if payment_amount <= 0:
                    return

                company = Company.get_default()
                direction = invoice.direction

                if bt.amount > 0 and direction == "OUTGOING":
                    # Входящий платёж по нашему инвойсу. Для клиентов
                    # BillingService создаёт пару BALANCE_TOPUP + PAYMENT(BALANCE),
                    # чтобы авансовый счёт клиента не уходил в минус.
                    from core.services.billing_service import BillingService

                    if not invoice.recipient:
                        logger.info(
                            "[BT auto-pay] Skipping BT %s: invoice %s has no recipient",
                            bt.pk,
                            invoice.number,
                        )
                        return
                    tx = BillingService.register_incoming_bank_payment(
                        invoice,
                        payment_amount,
                        date=bt.created_at,
                        description=(f"Авто-привязка банковского платежа {bt.counterparty_name} -> {invoice.number}"),
                    )
                    if tx is None:
                        return

                elif bt.amount < 0 and direction == "INCOMING":
                    issuer = invoice.issuer
                    if not issuer:
                        logger.info(
                            "[BT auto-pay] Skipping BT %s: invoice %s has no issuer",
                            bt.pk,
                            invoice.number,
                        )
                        return
                    tx = Transaction(
                        type="PAYMENT",
                        method="TRANSFER",
                        status="COMPLETED",
                        amount=payment_amount,
                        currency=invoice.currency or "EUR",
                        invoice=invoice,
                        from_company=company,
                        description=(f"Авто-привязка банковского платежа {bt.counterparty_name} -> {invoice.number}"),
                        date=bt.created_at,
                    )
                    setattr(tx, f"to_{issuer.__class__.__name__.lower()}", issuer)
                    tx.save()

                else:
                    logger.info(
                        "[BT auto-pay] Skipping BT %s: direction mismatch (amount=%s, invoice direction=%s)",
                        bt.pk,
                        bt.amount,
                        direction,
                    )
                    return

                bt._creating_payment = True
                try:
                    bt.matched_transaction = tx
                    if not bt.reconciliation_note:
                        bt.reconciliation_note = f"Привязано вручную к {invoice.number}"
                    bt.save(update_fields=["matched_transaction", "reconciliation_note", "fetched_at"])
                finally:
                    bt._creating_payment = False

                logger.info(
                    "[BT auto-pay] Created Transaction %s for invoice %s (%.2f %s) from BT %s",
                    tx.number,
                    invoice.number,
                    float(payment_amount),
                    tx.currency,
                    bt.pk,
                )
        except Exception as e:
            logger.error("[BT auto-pay] Error for BT %s: %s", bt_pk, e, exc_info=True)

    transaction.on_commit(_do)
