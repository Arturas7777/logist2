"""Админка :class:`Transaction`."""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from core.admin_export import CSVExportMixin
from core.models_billing import Transaction


@admin.register(Transaction)
class TransactionAdmin(CSVExportMixin, admin.ModelAdmin):
    """Простая админка для транзакций."""

    list_per_page = 50
    show_full_result_count = False

    # sender_display / recipient_display читают obj.sender / obj.recipient,
    # которые перебирают from_*/to_* FK по 5 моделям; invoice_link и
    # trx_category_display тоже тянут FK. Без list_select_related — N+1.
    list_select_related = (
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
        "invoice",
        "category",
    )

    actions = ["export_selected_as_csv"]
    csv_export_filename_prefix = "transactions"
    csv_export_fields = [
        ("number", "Номер"),
        ("date", "Дата"),
        ("type", "Тип"),
        ("method", "Метод"),
        ("amount", "Сумма"),
        ("currency", "Валюта"),
        ("status", "Статус"),
        ("from_client__name", "От клиента"),
        ("from_warehouse__name", "От склада"),
        ("from_line__name", "От линии"),
        ("from_carrier__name", "От перевозчика"),
        ("from_company__name", "От компании"),
        ("to_client__name", "Клиенту"),
        ("to_warehouse__name", "Складу"),
        ("to_line__name", "Линии"),
        ("to_carrier__name", "Перевозчику"),
        ("to_company__name", "Компании"),
        ("invoice__number", "Инвойс"),
        ("description", "Описание"),
    ]

    list_display = (
        "number_display",
        "date",
        "type_display",
        "method_display",
        "sender_display",
        "recipient_display",
        "amount_display",
        "trx_category_display",
        "status_display",
        "invoice_link",
    )

    list_filter = (
        "type",
        "method",
        "status",
        "category",
        "date",
    )

    search_fields = (
        "number",
        "description",
        "invoice__number",
        "from_client__name",
        "to_client__name",
    )

    # M5: транзакция имеет 11 FK на крупные таблицы (5 from_* + 5 to_* + invoice) —
    # без autocomplete каждая страница change/add загружала весь Client/Warehouse/Line/...
    # NewInvoiceAdmin имеет search_fields, остальные target-admins тоже (см. partners.py).
    autocomplete_fields = (
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
        "invoice",
    )

    readonly_fields = (
        "number",
        "date",
        "created_at",
        "created_by",
        "sender_info_display",
        "recipient_info_display",
    )

    # Денежные поля проведённой транзакции заморожены (леджер,
    # Transaction.LEDGER_FROZEN_FIELDS) — показываем это в форме сразу,
    # а не ValidationError-ом после сабмита. Статус остаётся редактируемым
    # (COMPLETED → CANCELLED — легальный путь отмены).
    _LEDGER_FROZEN_FORM_FIELDS = (
        "amount",
        "currency",
        "type",
        "method",
        "invoice",
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
    )

    def get_readonly_fields(self, request, obj=None):
        ro = super().get_readonly_fields(request, obj)
        if obj is not None and obj.status in ("COMPLETED", "CANCELLED"):
            ro = tuple(ro) + self._LEDGER_FROZEN_FORM_FIELDS
        return ro

    def has_delete_permission(self, request, obj=None):
        # Проведённые/отменённые транзакции — иммутабельная история.
        if obj is not None and obj.status in ("COMPLETED", "CANCELLED"):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # Дефолтный bulk delete (queryset.delete()) обходит Transaction.delete()
        # и его защиту леджера — удаляем поштучно через модельный delete().
        for obj in queryset:
            obj.delete()

    fieldsets = (
        (
            "Основная информация",
            {
                "fields": (
                    "number",
                    "date",
                    "type",
                    "method",
                    "status",
                )
            },
        ),
        (
            "Отправитель",
            {
                "fields": (
                    ("from_client", "from_warehouse"),
                    ("from_line", "from_carrier", "from_company"),
                    "sender_info_display",
                )
            },
        ),
        (
            "Получатель",
            {
                "fields": (
                    ("to_client", "to_warehouse"),
                    ("to_line", "to_carrier", "to_company"),
                    "recipient_info_display",
                )
            },
        ),
        (
            "Детали",
            {
                "fields": (
                    "amount",
                    "invoice",
                    "description",
                    "category",
                    "attachment",
                )
            },
        ),
        (
            "Метаданные",
            {
                "fields": (
                    "created_at",
                    "created_by",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    # ------------------------------------------------------------------
    # Отображение полей
    # ------------------------------------------------------------------

    def number_display(self, obj):
        return format_html("<strong>{}</strong>", obj.number)

    number_display.short_description = "Номер"
    number_display.admin_order_field = "number"

    def type_display(self, obj):
        """Тип с иконкой (Bootstrap Icons — единый набор админки)."""
        icons = {
            "PAYMENT": "bi-credit-card",
            "REFUND": "bi-arrow-counterclockwise",
            "ADJUSTMENT": "bi-gear",
            "TRANSFER": "bi-arrow-left-right",
            "BALANCE_TOPUP": "bi-cash-coin",
        }
        icon = icons.get(obj.type, "")
        if icon:
            return format_html('<i class="bi {}" style="color:#6c5ce7;"></i> {}', icon, obj.get_type_display())
        return obj.get_type_display()

    type_display.short_description = "Тип"
    type_display.admin_order_field = "type"

    def method_display(self, obj):
        return obj.get_method_display()

    method_display.short_description = "Способ"
    method_display.admin_order_field = "method"

    def sender_display(self, obj):
        sender = obj.sender
        if sender:
            return format_html("<strong>{}</strong>", str(sender))
        return "-"

    sender_display.short_description = "Отправитель"

    def recipient_display(self, obj):
        recipient = obj.recipient
        if recipient:
            return format_html("<strong>{}</strong>", str(recipient))
        return "-"

    recipient_display.short_description = "Получатель"

    def amount_display(self, obj):
        color = "#28a745" if obj.type == "PAYMENT" else "#dc3545" if obj.type == "REFUND" else "#007bff"
        amount = f"{obj.amount:.2f}"
        return format_html(
            '<span style="color: {}; font-weight: bold; font-size: 1.1em;">{}</span>',
            color,
            amount,
        )

    amount_display.short_description = "Сумма"
    amount_display.admin_order_field = "amount"

    def status_display(self, obj):
        colors = {
            "PENDING": "#ffc107",
            "COMPLETED": "#28a745",
            "FAILED": "#dc3545",
            "CANCELLED": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; '
            'border-radius: 3px; font-size: 0.85em;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_display.short_description = "Статус"
    status_display.admin_order_field = "status"

    def trx_category_display(self, obj):
        if obj.category:
            return format_html(
                '<span style="color:#555;">{}</span>',
                obj.category.short_name or obj.category.name,
            )
        return format_html('<span style="color:#ccc;">—</span>')

    trx_category_display.short_description = "Кат."
    trx_category_display.admin_order_field = "category"

    def invoice_link(self, obj):
        if obj.invoice:
            url = reverse("admin:core_newinvoice_change", args=[obj.invoice.pk])
            return format_html('<a href="{}">{}</a>', url, obj.invoice.number)
        return "-"

    invoice_link.short_description = "Инвойс"

    def save_model(self, request, obj, form, change):
        """Автозаполнение категории из связанного инвойса."""
        if not obj.category and obj.invoice and obj.invoice.category:
            obj.category = obj.invoice.category
        super().save_model(request, obj, form, change)

    def sender_info_display(self, obj):
        """Детальная информация об отправителе для readonly_fields."""
        sender = obj.sender
        if not sender:
            return "Не указан"

        info = f"<strong>{sender}</strong><br>"
        info += f"Тип: {sender.__class__.__name__}<br>"

        if hasattr(sender, "balance"):
            balance_str = f"{sender.balance:.2f}"
            info += f"Баланс: {balance_str}"

        return format_html(info)

    sender_info_display.short_description = "Информация об отправителе"

    def recipient_info_display(self, obj):
        """Детальная информация о получателе для readonly_fields."""
        recipient = obj.recipient
        if not recipient:
            return "Не указан"

        info = f"<strong>{recipient}</strong><br>"
        info += f"Тип: {recipient.__class__.__name__}<br>"

        if hasattr(recipient, "balance"):
            balance_str = f"{recipient.balance:.2f}"
            info += f"Баланс: {balance_str}"

        return format_html(info)

    recipient_info_display.short_description = "Информация о получателе"
