"""Сборка :class:`NewInvoiceAdmin` из миксинов.

См. ``docs/ROADMAP_2026-05_high_medium.md`` § H6b. Файл намеренно держится
небольшим: всё содержательное вынесено в:

* :mod:`invoice_display` — колонки и readonly-поля.
* :mod:`invoice_forms`   — lifecycle (``add_view``, ``save_model``, ...).
* :mod:`invoice_actions` — admin actions.
* :mod:`invoice_urls`    — кастомные admin-URL.

Порядок наследования важен: миксины слева перекрывают ``admin.ModelAdmin``
методы через MRO. ``CSVExportMixin`` ставим первым, как было в исходном
``admin_billing.py``.
"""

from django.contrib import admin

from core.admin_export import CSVExportMixin
from core.models_billing import NewInvoice

from .filters import InvoiceDirectionFilter
from .inlines import InvoiceItemInline
from .invoice_actions import NewInvoiceActionsMixin
from .invoice_display import NewInvoiceDisplayMixin
from .invoice_forms import NewInvoiceFormHandlerMixin
from .invoice_urls import NewInvoiceUrlsMixin


@admin.register(NewInvoice)
class NewInvoiceAdmin(
    CSVExportMixin,
    NewInvoiceDisplayMixin,
    NewInvoiceFormHandlerMixin,
    NewInvoiceActionsMixin,
    NewInvoiceUrlsMixin,
    admin.ModelAdmin,
):
    """Простая и понятная админка для инвойсов."""

    change_form_template = "admin/core/newinvoice/change_form.html"
    list_per_page = 50
    show_full_result_count = False

    csv_export_filename_prefix = "invoices"
    csv_export_fields = [
        ("number", "Номер"),
        ("external_number", "Внеш. номер"),
        ("document_type", "Серия"),
        ("date", "Дата"),
        ("due_date", "Срок оплаты"),
        ("issuer_company__name", "Выставитель (компания)"),
        ("issuer_warehouse__name", "Выставитель (склад)"),
        ("issuer_line__name", "Выставитель (линия)"),
        ("issuer_carrier__name", "Выставитель (перевозчик)"),
        ("recipient_client__name", "Получатель (клиент)"),
        ("recipient_warehouse__name", "Получатель (склад)"),
        ("recipient_line__name", "Получатель (линия)"),
        ("recipient_carrier__name", "Получатель (перевозчик)"),
        ("recipient_company__name", "Получатель (компания)"),
        ("subtotal", "Подытог"),
        ("discount", "Скидка"),
        ("tax", "Налог"),
        ("total", "Итого"),
        ("paid_amount", "Оплачено"),
        ("currency", "Валюта"),
        ("status", "Статус"),
        ("notes", "Примечания"),
    ]

    class Media:
        css = {
            "all": ("admin/css/widgets.css",),
        }
        js = ("admin/js/SelectBox.js", "admin/js/SelectFilter2.js")

    list_display = (
        "number_display",
        "doc_type_badge",
        "direction_badge",
        "linked_badge",
        "category_display",
        "notes_display",
        "issuer_display",
        "recipient_display",
        "total_display",
        "paid_amount_display",
        "remaining_display",
        "status_display",
        "actions_display",
    )

    list_filter = (
        "document_type",
        InvoiceDirectionFilter,
        "status",
        "category",
        "date",
        "recipient_client",
    )

    search_fields = (
        "number",
        "external_number",
        "recipient_client__name",
        "notes",
    )

    readonly_fields = (
        "number",
        "subtotal",
        "total",
        "paid_amount",
        "created_at",
        "updated_at",
        "created_by",
        "audit_status_display",
    )

    fieldsets = (
        (
            "📋 Основная информация",
            {
                "fields": (
                    ("date", "due_date", "status"),
                    "category",
                )
            },
        ),
        (
            "🏢 Выставитель инвойса",
            {
                "fields": ("issuer_company",),
                "description": ("По умолчанию: Caromoto Lithuania. Для входящих инвойсов — укажите контрагента ниже."),
            },
        ),
        (
            "👤 Получатель инвойса",
            {
                "fields": ("recipient_client",),
            },
        ),
        (
            "🚗 Автомобили",
            {
                "fields": ("cars",),
                "description": (
                    "Выберите автомобили - позиции создадутся автоматически из их услуг. "
                    "Для общих расходов (аренда и т.д.) оставьте пустым."
                ),
            },
        ),
        (
            "💰 Финансы",
            {
                "fields": (
                    ("subtotal", "discount", "tax"),
                    ("total", "paid_amount"),
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "📎 Дополнительно",
            {
                "fields": ("notes", "attachment", "linked_invoice", "audit_status_display"),
            },
        ),
        (
            "⚙️ Прочие получатели (если не клиент)",
            {
                "fields": (
                    ("recipient_warehouse", "recipient_line"),
                    ("recipient_carrier", "recipient_company"),
                ),
                "classes": ("collapse",),
                "description": ("Для входящих инвойсов: укажите Caromoto Lithuania как получателя-компанию"),
            },
        ),
        (
            "⚙️ Прочие выставители (если не компания)",
            {
                "fields": (("issuer_warehouse", "issuer_line", "issuer_carrier"),),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [InvoiceItemInline]

    autocomplete_fields = ["linked_invoice"]
    filter_horizontal = ("cars",)

    actions = [
        "mark_as_issued",
        "mark_as_paid",
        "cancel_invoices",
        "regenerate_items",
        "push_to_sitepro",
        "change_series",
        "delete_invoices_with_transactions",
        "recalculate_all_balances",
        "export_selected_as_csv",
    ]
