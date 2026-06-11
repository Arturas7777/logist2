"""Mixin с admin actions для :class:`NewInvoiceAdmin`.

Действия отделены, чтобы их легко переиспользовать/тестировать и держать
основной класс компактным. Связь с админкой — через имена в атрибуте
``actions`` собранного класса (см. :mod:`invoice`).
"""

import logging

from django.contrib import admin, messages
from django.shortcuts import render

from core.models_billing import NewInvoice, Transaction
from core.services.billing_service import BillingService

logger = logging.getLogger(__name__)


class NewInvoiceActionsMixin:
    """Admin actions для NewInvoiceAdmin."""

    def mark_as_issued(self, request, queryset):
        """Пометить как выставленные."""
        updated = 0
        for invoice in queryset:
            if invoice.status not in ("ISSUED", "PAID", "CANCELLED"):
                invoice.status = "ISSUED"
                invoice.save(update_fields=["status", "updated_at"])
                updated += 1

        self.message_user(request, f"Выставлено: {updated} инвойсов", messages.SUCCESS)

    mark_as_issued.short_description = "📤 Пометить как выставленные"

    def mark_as_paid(self, request, queryset):
        """Пометить как оплаченные — создаёт транзакцию через BillingService.

        Для ``AVBLC`` переводит в ``PARBLC`` + CASH-платёж. Для
        ``INCBLC/PARBLC`` регистрирует кассовый платёж на остаток.
        """
        updated = 0
        errors = 0
        for invoice in queryset:
            if invoice.status == "PAID":
                continue

            if invoice.document_type == "PROFORMA_BLC":
                BillingService.change_invoice_series(invoice, "INVOICE_BLC", created_by=request.user)
                updated += 1
                continue

            if invoice.document_type in NewInvoice.CASH_DOCUMENT_TYPES and invoice.remaining_amount > 0:
                BillingService.register_cash_payment(invoice, created_by=request.user)
                updated += 1
                continue

            # Перед тем как ставить PAID, подтянем актуальный paid_amount —
            # могли остаться расхождения из-за удалённых транзакций или ручных
            # правок. Защита: если реально остаток > 0, мы не должны
            # молча проставить PAID.
            try:
                invoice.recalculate_paid_amount()
            except Exception:
                logger.exception("recalculate_paid_amount failed for invoice %s", invoice.pk)

            remaining = invoice.remaining_amount
            if remaining <= 0:
                invoice.status = "PAID"
                invoice.save(update_fields=["status", "updated_at"])
                updated += 1
                continue
            payer = invoice.recipient
            if not payer:
                errors += 1
                continue
            try:
                method = "OTHER"
                BillingService.pay_invoice(
                    invoice=invoice,
                    amount=remaining,
                    method=method,
                    payer=payer,
                    description="Отмечено как оплаченное через массовое действие",
                    created_by=request.user,
                )
                updated += 1
            except Exception as e:
                logger.error("mark_as_paid failed for invoice %s: %s", invoice.number, e)
                errors += 1

        if updated:
            self.message_user(
                request,
                f"Помечено как оплаченные: {updated} инвойсов",
                messages.SUCCESS,
            )
        if errors:
            self.message_user(
                request,
                f"Ошибок: {errors} (проверьте получателя инвойса)",
                messages.WARNING,
            )

    mark_as_paid.short_description = "✓ Пометить как оплаченные"

    def cancel_invoices(self, request, queryset):
        cancelled = 0
        errors = 0

        for invoice in queryset:
            try:
                BillingService.cancel_invoice(invoice, reason="Массовая отмена через админку")
                cancelled += 1
            except ValueError:
                errors += 1

        if cancelled > 0:
            self.message_user(
                request,
                f"Отменено: {cancelled} инвойсов",
                messages.SUCCESS,
            )
        if errors > 0:
            self.message_user(
                request,
                f"Ошибок: {errors} инвойсов (возможно, уже были платежи)",
                messages.WARNING,
            )

    cancel_invoices.short_description = "✗ Отменить инвойсы"

    def regenerate_items(self, request, queryset):
        """Пересоздать позиции из автомобилей (пропускает PAID и CANCELLED)."""
        count = 0
        skipped = 0
        for invoice in queryset:
            if invoice.status in ("PAID", "CANCELLED"):
                skipped += 1
                continue
            if invoice.cars.exists():
                invoice.regenerate_items_from_cars()
                count += 1

        if count > 0:
            self.message_user(
                request,
                f"Позиции пересозданы для {count} инвойсов",
                messages.SUCCESS,
            )
        if skipped > 0:
            self.message_user(
                request,
                f"Пропущено {skipped} оплаченных/отменённых инвойсов",
                messages.WARNING,
            )
        if count == 0 and skipped == 0:
            self.message_user(request, "Выберите инвойсы с автомобилями", messages.WARNING)

    regenerate_items.short_description = "Пересоздать позиции из автомобилей"

    def push_to_sitepro(self, request, queryset):
        """Отправить выбранные инвойсы в site.pro (бухгалтерия)."""
        from core.models_accounting import SiteProConnection

        connection = SiteProConnection.objects.filter(is_active=True).first()
        if not connection:
            self.message_user(
                request,
                'Нет активного подключения к site.pro. Настройте подключение в разделе "Подключения site.pro".',
                messages.ERROR,
            )
            return

        from core.services.sitepro_service import SiteProService

        service = SiteProService(connection)

        from core.mixins import ACTIVE_INVOICE_STATUSES

        eligible = queryset.filter(
            status__in=ACTIVE_INVOICE_STATUSES,
            document_type="INVOICE",
        )
        if not eligible.exists():
            non_invoice = queryset.exclude(document_type="INVOICE").count()
            if non_invoice:
                self.message_user(
                    request,
                    f"В site.pro отправляются только счета-фактуры (PARDP). "
                    f"{non_invoice} документов других серий пропущено.",
                    messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    'Выберите счета-фактуры (PARDP) со статусом "Выставлен" или "Оплачен".',
                    messages.WARNING,
                )
            return

        result = service.push_invoices(eligible)

        if result["sent"] > 0:
            self.message_user(
                request,
                f"Отправлено в site.pro: {result['sent']} инвойсов",
                messages.SUCCESS,
            )
        if result["skipped"] > 0:
            self.message_user(
                request,
                f"Пропущено (уже отправлены): {result['skipped']}",
                messages.INFO,
            )
        if result["failed"] > 0:
            error_details = "; ".join(result["errors"][:3])
            self.message_user(
                request,
                f"Ошибок: {result['failed']}. {error_details}",
                messages.ERROR,
            )

    push_to_sitepro.short_description = "📤 Отправить в site.pro (бухгалтерия)"

    def change_series(self, request, queryset):
        """Сменить серию (тип документа) выбранных инвойсов."""
        if "apply" in request.POST:
            new_type = request.POST.get("new_document_type")
            valid_types = dict(NewInvoice.DOCUMENT_TYPE_CHOICES)
            if new_type not in valid_types:
                self.message_user(request, "Неверный тип документа.", messages.ERROR)
                return None

            changed = 0
            for inv in queryset:
                old_number = BillingService.change_invoice_series(inv, new_type, created_by=request.user)
                if old_number != inv.number:
                    changed += 1
                    logger.info("Invoice %s -> %s (series %s)", old_number, inv.number, new_type)

            self.message_user(
                request,
                f"Серия изменена для {changed} инвойсов на {valid_types[new_type]}.",
                messages.SUCCESS,
            )
            return None

        return render(
            request,
            "admin/core/newinvoice/change_series.html",
            {
                "invoices": queryset,
                "document_type_choices": NewInvoice.DOCUMENT_TYPE_CHOICES,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
            },
        )

    change_series.short_description = "🔄 Сменить серию"

    def delete_invoices_with_transactions(self, request, queryset):
        """Force-delete выбранных инвойсов вместе с транзакциями + пересчёт балансов.

        Оптимизация: при удалении N инвойсов с M транзакциями КАЖДЫЙ delete
        триггерил бы ``Transaction.post_delete`` →
        ``recalculate_entity_balance`` → N*M лишних пересчётов. Глушим
        сигналы Transaction и NewInvoice на время удаления, а в конце
        делаем ОДИН пересчёт для уникальных затронутых сущностей.
        """
        from django.db.models.signals import post_delete, post_save

        from core.signal_utils import signals_muted

        if "confirm" in request.POST:
            affected_entities = set()
            total_tx = 0
            total_inv = 0

            with signals_muted(post_save, post_delete, senders=(Transaction, NewInvoice)):
                for inv in queryset.select_related(
                    "issuer_company",
                    "issuer_warehouse",
                    "issuer_line",
                    "issuer_carrier",
                    "recipient_client",
                    "recipient_company",
                    "recipient_warehouse",
                    "recipient_line",
                    "recipient_carrier",
                ):
                    for field in [
                        inv.issuer_company,
                        inv.issuer_warehouse,
                        inv.issuer_line,
                        inv.issuer_carrier,
                        inv.recipient_client,
                        inv.recipient_company,
                        inv.recipient_warehouse,
                        inv.recipient_line,
                        inv.recipient_carrier,
                    ]:
                        if field and hasattr(field, "balance"):
                            affected_entities.add(field)

                    txs = inv.transactions.all()
                    for tx in txs:
                        for attr in [
                            "from_client",
                            "from_warehouse",
                            "from_line",
                            "from_carrier",
                            "from_company",
                            "to_client",
                            "to_warehouse",
                            "to_line",
                            "to_carrier",
                            "to_company",
                        ]:
                            entity = getattr(tx, attr, None)
                            if entity and hasattr(entity, "balance"):
                                affected_entities.add(entity)
                    tx_count = txs.count()
                    txs.delete()
                    total_tx += tx_count

                    try:
                        if hasattr(inv, "audit"):
                            inv.audit.delete()
                    except Exception:
                        logger.debug("No audit to delete for invoice %s", inv.pk, exc_info=True)

                    inv.items.all().delete()
                    inv.delete(force=True)
                    total_inv += 1

            for entity in affected_entities:
                try:
                    entity.refresh_from_db()
                    Transaction.recalculate_entity_balance(entity)
                except Exception:
                    logger.warning("Failed to recalc balance for %s", entity, exc_info=True)

            self.message_user(
                request,
                f"Удалено {total_inv} инвойсов и {total_tx} транзакций. "
                f"Пересчитаны балансы {len(affected_entities)} сущностей.",
                messages.SUCCESS,
            )
            return None

        tx_count = Transaction.objects.filter(invoice__in=queryset).count()
        return render(
            request,
            "admin/core/newinvoice/confirm_delete_invoices.html",
            {
                "invoices": queryset,
                "transaction_count": tx_count,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
            },
        )

    delete_invoices_with_transactions.short_description = "🗑 Удалить с транзакциями (принудительно)"

    def recalculate_all_balances(self, request, queryset):
        """Пересчитать балансы всех сущностей и paid_amount всех инвойсов."""
        from core.models import Carrier, Client, Company, Line, Warehouse

        report = []
        for Model, label in [
            (Client, "Клиенты"),
            (Company, "Компании"),
            (Warehouse, "Склады"),
            (Line, "Линии"),
            (Carrier, "Перевозчики"),
        ]:
            changed = 0
            for entity in Model.objects.all():
                old_balance = entity.balance
                Transaction.recalculate_entity_balance(entity)
                entity.refresh_from_db()
                if entity.balance != old_balance:
                    changed += 1
            report.append(f"{label}: пересчитано {changed}")

        inv_changed = 0
        for inv in NewInvoice.objects.exclude(status__in=["CANCELLED", "DRAFT"]):
            old_paid = inv.paid_amount
            inv.recalculate_paid_amount()
            if inv.paid_amount != old_paid:
                inv_changed += 1
        report.append(f"Инвойсы (paid_amount): пересчитано {inv_changed}")

        self.message_user(request, " | ".join(report), messages.SUCCESS)

    recalculate_all_balances.short_description = "🔄 Пересчитать все балансы"
