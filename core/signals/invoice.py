"""Сигналы для ``NewInvoice``:

* :func:`auto_categorize_invoice` — назначает категорию
  ``OPERATIONAL`` инвойсам от логистических контрагентов, если
  пользователь не выбрал категорию вручную.
* :func:`save_old_invoice_status` — снимок старого статуса для
  ``post_save``-обработчиков.
* :func:`auto_push_invoice_to_sitepro` — ставит пуш в site.pro в
  очередь Celery после commit транзакции (с inline-fallback при
  недоступности брокера).
* :func:`sync_linked_invoice_status` — при оплате инвойса автоматически
  переводит парный (BLC ↔ PARDP/FACT) в статус ``LINKED_PAID``.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models_billing import NewInvoice

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=NewInvoice)
def auto_categorize_invoice(sender, instance, **kwargs):
    if instance.category_id:
        return
    if instance.issuer_warehouse_id or instance.issuer_line_id or instance.issuer_carrier_id:
        try:
            from core.models_billing import ExpenseCategory

            logistics_cat = ExpenseCategory.objects.filter(category_type="OPERATIONAL").first()
            if logistics_cat:
                instance.category = logistics_cat
        except Exception as e:
            logger.warning("Не удалось назначить категорию: %s", e)


@receiver(pre_save, sender=NewInvoice)
def save_old_invoice_status(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "status" not in update_fields:
        instance._pre_save_status = None
        return
    if instance.pk:
        try:
            old = NewInvoice.objects.filter(pk=instance.pk).values("status").first()
            instance._pre_save_status = old["status"] if old else None
        except Exception:
            instance._pre_save_status = None
    else:
        instance._pre_save_status = None


@receiver(post_save, sender=NewInvoice)
def auto_push_invoice_to_sitepro(sender, instance, created, **kwargs):
    """Ставит пуш в site.pro в очередь Celery после commit транзакции.

    Синхронный fallback — если Celery недоступен.
    """
    if not instance.pk:
        return

    old_status = getattr(instance, "_pre_save_status", None)
    instance._pre_save_status = None
    if instance.status != "ISSUED" or old_status == "ISSUED":
        return
    if getattr(instance, "document_type", "PROFORMA") != "INVOICE":
        return
    if getattr(instance, "_pushing_to_sitepro", False):
        return

    invoice_id = instance.pk
    invoice_number = instance.number

    def _queue():
        try:
            from core.tasks import push_invoice_to_sitepro_task

            push_invoice_to_sitepro_task.delay(invoice_id)
        except Exception as exc:
            logger.warning(
                "[SitePro] Celery unavailable for invoice %s, pushing inline: %s",
                invoice_number,
                exc,
            )
            try:
                from core.models_accounting import SiteProConnection

                connection = SiteProConnection.objects.filter(is_active=True, auto_push_on_issue=True).first()
                if not connection:
                    return
                instance._pushing_to_sitepro = True
                try:
                    from core.services.sitepro_service import SiteProService

                    SiteProService(connection).push_invoice(instance)
                    logger.info("[SitePro] Auto-pushed invoice %s on ISSUED (sync)", invoice_number)
                finally:
                    instance._pushing_to_sitepro = False
            except Exception as e:
                logger.error("[SitePro] Error auto-pushing invoice %s: %s", invoice_number, e)

    transaction.on_commit(_queue)


@receiver(post_save, sender=NewInvoice)
def sync_linked_invoice_status(sender, instance, **kwargs):
    """When an invoice becomes PAID, mark its linked pair as LINKED_PAID.

    LINKED_PAID — отдельный статус, показывающий что инвойс закрыт не
    собственным платежом, а через связанный документ (BLC ↔ PARDP/FACT).
    Он не учитывается как обычный PAID в ``check_balance_consistency`` —
    оплата уже прошла по парному инвойсу, дублирующий ``Transaction``
    создавать нельзя, иначе поедет баланс склада/линии/перевозчика.
    """
    if instance.status not in ("PAID", "LINKED_PAID"):
        return
    if getattr(instance, "_syncing_linked", False):
        return

    linked = None
    if instance.linked_invoice_id:
        linked = instance.linked_invoice
    else:
        linked = getattr(instance, "linked_from", None)
        if linked is not None:
            try:
                linked = NewInvoice.objects.get(pk=linked.pk)
            except NewInvoice.DoesNotExist:
                linked = None

    if linked and linked.status not in ("PAID", "LINKED_PAID", "CANCELLED"):
        linked._syncing_linked = True
        linked.paid_amount = linked.total
        linked.status = "LINKED_PAID"
        linked.save(update_fields=["paid_amount", "status", "updated_at"])
        logger.info(
            "Linked invoice %s marked LINKED_PAID (paired with %s)",
            linked.number,
            instance.number,
        )
