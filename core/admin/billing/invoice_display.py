"""Mixin с методами отображения колонок и readonly-полей для :class:`NewInvoiceAdmin`.

Методы вынесены сюда, чтобы держать собственно класс
:class:`~core.admin.billing.invoice.NewInvoiceAdmin` под порогом ~700 строк
(см. ``docs/ROADMAP_2026-05_high_medium.md`` § H6b).

Никакие методы здесь НЕ переопределяют ``Admin``-lifecycle (``save_*``,
``add_view``, ``get_queryset`` и т.п.) — только колонки списка и
readonly-поля. Лайфсайкл — в :mod:`invoice_forms`.
"""

from collections import OrderedDict
from decimal import Decimal

from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe


def build_items_pivot_table(invoice):
    """Данные для табличного отображения инвойса в change-форме админки.

    Строки = авто, столбцы = группы услуг (short_name), крайний правый
    столбец = итого. Для входящих инвойсов каждая ячейка содержит и цену
    из инвойса, и цену для клиента (+ профит).

    Перенесено из ``NewInvoice.get_items_pivot_table`` (A2, AUDIT_ROUND3):
    это presentation-код для одного шаблона админки, модели он не нужен.
    """
    items = invoice.items.all().select_related("car").order_by("order")

    if not items.exists():
        return None

    is_incoming = invoice.direction == "INCOMING"
    has_client_prices = is_incoming and items.filter(client_price__isnull=False).exists()

    columns = []
    seen_cols = set()
    car_rows = OrderedDict()

    for item in items:
        raw_desc = item.description or ""
        col_name = raw_desc.split(":")[0].strip() if ":" in raw_desc else raw_desc
        if col_name not in seen_cols:
            columns.append(col_name)
            seen_cols.add(col_name)

        car_key = item.car_id or f"nocar_{item.pk}"
        if car_key not in car_rows:
            if item.car:
                car_label = f"{item.car.brand}, {item.car.vin}"
            else:
                car_label = item.description or "Без авто"
            car_rows[car_key] = {
                "car": item.car,
                "car_label": car_label,
                "services": {},
                "client_services": {},
                "total": Decimal("0"),
                "client_total": Decimal("0"),
            }

        car_rows[car_key]["services"][col_name] = item.unit_price
        car_rows[car_key]["total"] += item.total_price
        if item.client_price is not None:
            car_rows[car_key]["client_services"][col_name] = item.client_price
            car_rows[car_key]["client_total"] += item.client_price

    single_cols = set()
    if has_client_prices:
        for col in columns:
            has_any_client = any(col in row["client_services"] for row in car_rows.values())
            if not has_any_client:
                single_cols.add(col)

    columns_info = [{"name": col, "single": col in single_cols} for col in columns]

    column_totals = {}
    client_column_totals = {}
    for col in columns:
        column_totals[col] = sum(row["services"].get(col, Decimal("0")) for row in car_rows.values())
        if has_client_prices:
            client_column_totals[col] = sum(row["client_services"].get(col, Decimal("0")) for row in car_rows.values())

    grand_total = sum(row["total"] for row in car_rows.values())
    client_grand_total = sum(row["client_total"] for row in car_rows.values()) if has_client_prices else None

    rows = []
    for car_data in car_rows.values():
        cells = []
        for col in columns:
            val = car_data["services"].get(col, None)
            is_single = col in single_cols
            if has_client_prices:
                client_val = car_data["client_services"].get(col, None)
                profit = None
                if client_val is not None and val is not None:
                    profit = client_val - val
                cells.append(
                    {
                        "invoice": val,
                        "client": client_val,
                        "profit": profit,
                        "single": is_single,
                    }
                )
            else:
                cells.append(val)

        row_profit = None
        if has_client_prices:
            row_profit = car_data["client_total"] - car_data["total"]

        rows.append(
            {
                "car_label": car_data["car_label"],
                "cells": cells,
                "total": car_data["total"],
                "client_total": car_data["client_total"] if has_client_prices else None,
                "profit": row_profit,
            }
        )

    col_totals_list = [column_totals[col] for col in columns]

    if has_client_prices:
        col_totals_paired = []
        for col in columns:
            inv = column_totals[col]
            cli = client_column_totals.get(col, Decimal("0"))
            col_totals_paired.append(
                {
                    "invoice": inv,
                    "client": cli,
                    "profit": cli - inv,
                    "single": col in single_cols,
                }
            )
        profit_grand = client_grand_total - grand_total
    else:
        col_totals_paired = None
        profit_grand = None

    return {
        "columns": columns_info,
        "rows": rows,
        "col_totals": col_totals_list,
        "col_totals_paired": col_totals_paired,
        "grand_total": grand_total,
        "client_grand_total": client_grand_total,
        "profit_grand": profit_grand,
        "has_client_prices": has_client_prices,
    }


class NewInvoiceDisplayMixin:
    """Колонки list_display и readonly_fields для NewInvoiceAdmin."""

    # ------------------------------------------------------------------
    # ОТОБРАЖЕНИЕ ПОЛЕЙ В СПИСКЕ
    # ------------------------------------------------------------------

    def number_display(self, obj):
        """Номер инвойса с ссылкой на change-форму."""
        url = reverse("admin:core_newinvoice_change", args=[obj.pk])
        return format_html('<a href="{}" style="font-weight: bold;">{}</a>', url, obj.number)

    number_display.short_description = "Номер"
    number_display.admin_order_field = "number"

    def doc_type_badge(self, obj):
        """Цветной бейдж серии документа (PARDP / AV / FACT / ...)."""
        badge_map = {
            "INVOICE": ("#dbeafe", "#1e40af", "PARDP"),
            "PROFORMA": ("#fef3c7", "#92400e", "AV"),
            "INVOICE_BLC": ("#1e293b", "#f8fafc", "PARBLC"),
            "PROFORMA_BLC": ("#e2e8f0", "#475569", "AVBLC"),
            "INVOICE_FACT": ("#fce7f3", "#9d174d", "FACT"),
            "INVOICE_INCBLC": ("#e8d4b0", "#6b4423", "INCBLC"),
            "CREDIT_NOTE": ("#fee2e2", "#991b1b", "KRE"),
        }
        bg, fg, label = badge_map.get(obj.document_type, ("#e2e8f0", "#475569", "?"))
        return format_html(
            '<span style="background:{};color:{};padding:2px 7px;'
            'border-radius:10px;font-size:11px;font-weight:600;">{}</span>',
            bg,
            fg,
            label,
        )

    doc_type_badge.short_description = "Тип"
    doc_type_badge.admin_order_field = "document_type"

    def direction_badge(self, obj):
        """Бейдж направления: Исходящий / Входящий / Внутренний."""
        direction = obj.direction
        styles = {
            "OUTGOING": ("background:#007bff;", "↗ Исх"),
            "INCOMING": ("background:#fd7e14;", "↙ Вх"),
            "INTERNAL": ("background:#6c757d;", "↔ Внутр"),
        }
        style, label = styles.get(direction, ("background:#6c757d;", "?"))
        return format_html(
            '<span style="{}color:white;padding:2px 6px;border-radius:3px;'
            'font-size:0.85em;white-space:nowrap;">{}</span>',
            style,
            label,
        )

    direction_badge.short_description = "Напр."

    def linked_badge(self, obj):
        """Бейдж пары инвойсов (real ↔ official).

        Запрос предварительно делает ``select_related('linked_invoice',
        'linked_from')``, поэтому здесь без дополнительных SQL.
        """
        from django.core.exceptions import ObjectDoesNotExist

        linked = None
        if obj.linked_invoice_id:
            linked = obj.linked_invoice
        else:
            try:
                linked = obj.linked_from
            except ObjectDoesNotExist:
                pass
        if not linked:
            return format_html('<span style="color:#ccc;">—</span>')
        url = reverse("admin:core_newinvoice_change", args=[linked.pk])
        return format_html(
            '<a href="{}" style="text-decoration:none;" title="{}">'
            '<span style="background:#e0e7ff;color:#3730a3;padding:2px 7px;'
            'border-radius:10px;font-size:11px;font-weight:600;">'
            '<i class="bi bi-link-45deg"></i> {}</span></a>',
            url,
            linked.number,
            linked.number,
        )

    linked_badge.short_description = "Пара"

    def category_display(self, obj):
        """Категория расхода/дохода."""
        if obj.category:
            return format_html(
                '<span style="color:#555;" title="{}">{}</span>',
                obj.category.get_category_type_display(),
                obj.category.short_name or obj.category.name,
            )
        return format_html('<span style="color:#ccc;">—</span>')

    category_display.short_description = "Кат."
    category_display.admin_order_field = "category"

    def notes_display(self, obj):
        """Усечённые примечания (≤ 50 символов) с тултипом."""
        if obj.notes:
            notes_text = obj.notes[:50] + "..." if len(obj.notes) > 50 else obj.notes
            return format_html('<span title="{}">{}</span>', obj.notes, notes_text)
        return format_html('<span style="color: #999;">—</span>')

    notes_display.short_description = "Примечания"
    notes_display.admin_order_field = "notes"

    def issuer_display(self, obj):
        issuer = obj.issuer
        if issuer:
            return format_html("<strong>{}</strong>", str(issuer))
        return "-"

    issuer_display.short_description = "Выставитель"

    def recipient_display(self, obj):
        recipient = obj.recipient
        if recipient:
            return format_html("<strong>{}</strong>", str(recipient))
        return "-"

    recipient_display.short_description = "Получатель"

    def total_display(self, obj):
        amount = f"{obj.total:.2f}"
        return format_html(
            '<span style="font-weight: bold; font-size: 1.1em;">{}</span>',
            amount,
        )

    total_display.short_description = "Итого"
    total_display.admin_order_field = "total"

    def paid_amount_display(self, obj):
        if obj.paid_amount > 0:
            color = "#28a745" if obj.paid_amount >= obj.total else "#ffc107"
            amount = f"{obj.paid_amount:.2f}"
            return format_html(
                '<span style="color: {}; font-weight: bold;">{}</span>',
                color,
                amount,
            )
        return format_html('<span style="color: #999;">0.00</span>')

    paid_amount_display.short_description = "Оплачено"
    paid_amount_display.admin_order_field = "paid_amount"

    def remaining_display(self, obj):
        """Остаток к оплате — в списке (компактно)."""
        remaining = obj.remaining_amount
        if remaining > 0:
            amount = f"{remaining:.2f}"
            return format_html(
                '<span style="color: #dc3545; font-weight: bold;">{}</span>',
                amount,
            )
        return format_html('<span style="color: #28a745;">✓</span>')

    remaining_display.short_description = "Остаток"

    def status_display(self, obj):
        """Статус с цветом и иконкой просрочки."""
        colors = {
            "DRAFT": "#6c757d",
            "ISSUED": "#007bff",
            "PARTIALLY_PAID": "#ffc107",
            "PAID": "#28a745",
            "OVERDUE": "#dc3545",
            "CANCELLED": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")

        icon = "⚠ " if obj.is_overdue else ""

        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 0.9em;">{}{}</span>',
            color,
            icon,
            obj.get_status_display(),
        )

    status_display.short_description = "Статус"
    status_display.admin_order_field = "status"

    def actions_display(self, obj):
        """Кнопка быстрой оплаты для активных инвойсов."""
        if obj.status in ["ISSUED", "PARTIALLY_PAID", "OVERDUE"]:
            pay_url = reverse("admin:pay_invoice", args=[obj.pk])
            return format_html(
                '<a href="{}" class="button" style="background: #28a745; color: white; '
                'padding: 3px 10px; border-radius: 3px; text-decoration: none;">'
                '<i class="bi bi-credit-card"></i> Оплатить</a>',
                pay_url,
            )
        elif obj.status == "PAID":
            return format_html('<span style="color: #28a745;">✓ Оплачен</span>')
        return "-"

    actions_display.short_description = "Действия"

    # ------------------------------------------------------------------
    # ДОПОЛНИТЕЛЬНЫЕ READONLY ПОЛЯ
    # ------------------------------------------------------------------

    def remaining_amount_display(self, obj):
        """Остаток к оплате (крупный шрифт для readonly_fields)."""
        remaining = obj.remaining_amount
        if remaining > 0:
            amount = f"{remaining:.2f}"
            return format_html(
                '<span style="font-size: 1.2em; color: #dc3545; font-weight: bold;">{}</span>',
                amount,
            )
        return format_html('<span style="color: #28a745; font-size: 1.2em;">✓ Полностью оплачен</span>')

    remaining_amount_display.short_description = "Остаток к оплате"

    def status_info_display(self, obj):
        """Блок предупреждений (просрочка, переплата) на change-форме."""
        info = []

        if obj.is_overdue:
            days_overdue = abs(obj.days_until_due)
            info.append(
                format_html(
                    '<div style="background: #fff3cd; border-left: 4px solid #dc3545; '
                    'padding: 10px; margin: 5px 0;">'
                    "<strong>⚠ ПРОСРОЧЕН</strong><br>"
                    "Просрочка: {} дн."
                    "</div>",
                    days_overdue,
                )
            )
        elif obj.days_until_due <= 3 and obj.status not in ["PAID", "CANCELLED"]:
            info.append(
                format_html(
                    '<div style="background: #fff3cd; border-left: 4px solid #ffc107; '
                    'padding: 10px; margin: 5px 0;">'
                    "<strong>⚠ СРОЧНО</strong><br>"
                    "До срока оплаты: {} дн."
                    "</div>",
                    obj.days_until_due,
                )
            )

        if obj.paid_amount > obj.total:
            overpayment = obj.paid_amount - obj.total
            overpayment_str = f"{overpayment:.2f}"
            info.append(
                format_html(
                    '<div style="background: #d1ecf1; border-left: 4px solid #17a2b8; '
                    'padding: 10px; margin: 5px 0;">'
                    "<strong>ℹ ПЕРЕПЛАТА</strong><br>"
                    "Переплачено: {}"
                    "</div>",
                    overpayment_str,
                )
            )

        # Фрагменты уже экранированы через format_html с плейсхолдерами;
        # повторный format_html поверх готовой строки лишний и хрупкий.
        return mark_safe("".join(info)) if info else "Нет предупреждений"

    status_info_display.short_description = "Статус и предупреждения"

    def payment_history_display(self, obj):
        """История платежей по инвойсу в виде HTML-таблицы."""
        transactions = obj.transactions.all().order_by("-date")

        if not transactions:
            return format_html('<p style="color: #999;">Платежей еще не было</p>')

        # format_html_join экранирует значения (number — пользовательская
        # строка) и не падает на фигурных скобках в данных.
        rows = format_html_join(
            "",
            '<tr style="border-bottom: 1px solid #ddd;">'
            '<td style="padding: 8px;">{}</td>'
            '<td style="padding: 8px;">{}</td>'
            '<td style="padding: 8px;">{}</td>'
            '<td style="padding: 8px;">{}</td>'
            '<td style="padding: 8px; text-align: right; color: {}; font-weight: bold;">{}</td>'
            "</tr>",
            (
                (
                    trx.date.strftime("%d.%m.%Y %H:%M"),
                    trx.number,
                    trx.get_type_display(),
                    trx.get_method_display(),
                    "#28a745" if trx.type == "PAYMENT" else "#dc3545",
                    f"{trx.amount:.2f}",
                )
                for trx in transactions
            ),
        )
        return format_html(
            '<table style="width: 100%; border-collapse: collapse;">'
            '<tr style="background: #f5f5f5;">'
            '<th style="padding: 8px; text-align: left;">Дата</th>'
            '<th style="padding: 8px; text-align: left;">Номер</th>'
            '<th style="padding: 8px; text-align: left;">Тип</th>'
            '<th style="padding: 8px; text-align: left;">Способ</th>'
            '<th style="padding: 8px; text-align: right;">Сумма</th></tr>{}</table>',
            rows,
        )

    payment_history_display.short_description = "История платежей"

    def audit_status_display(self, obj):
        """Бейдж статуса AI-аудита PDF (для readonly_fields)."""
        if not obj.pk:
            return format_html('<span style="color:#94a3b8;">Сохраните инвойс для запуска анализа</span>')

        try:
            audit = obj.audit
        except Exception:
            audit = None

        if not audit:
            if obj.attachment and obj.direction == "INCOMING":
                return format_html('<span style="color:#d97706;">AI-анализ запустится после сохранения</span>')
            return format_html('<span style="color:#94a3b8;">—</span>')

        status_map = {
            "PENDING": ("#94a3b8", "bi-hourglass-split", "Ожидает обработки"),
            "PROCESSING": ("#d97706", "bi-arrow-repeat", "Обрабатывается..."),
            "OK": ("#16a34a", "bi-check-circle-fill", "Всё совпадает"),
            "HAS_ISSUES": ("#dc2626", "bi-exclamation-triangle-fill", "Есть расхождения"),
            "ERROR": ("#1e293b", "bi-x-circle-fill", "Ошибка"),
        }
        color, icon, label = status_map.get(audit.status, ("#94a3b8", "bi-question-circle", "?"))
        detail_url = f"/admin/invoice-audit/{audit.pk}/"

        extra = ""
        if audit.status in ("OK", "HAS_ISSUES"):
            extra = (
                f" &middot; найдено {audit.cars_found} авто"
                f"{f', расхождений: {audit.issues_count}' if audit.issues_count else ''}"
            )

        return format_html(
            '<a href="{}" style="text-decoration:none;">'
            '<span style="display:inline-flex;align-items:center;gap:5px;padding:3px 10px;'
            'border-radius:8px;background:{}15;color:{};font-size:.85rem;font-weight:600;">'
            '<i class="bi {}"></i> {}{}</span></a>',
            detail_url,
            color,
            color,
            icon,
            label,
            extra,
        )

    audit_status_display.short_description = "AI-анализ PDF"
