"""Inline-формы, используемые в админке биллинга."""

from django.contrib import admin

from core.models_billing import InvoiceItem


class InvoiceItemInline(admin.TabularInline):
    """Inline для редактирования позиций инвойса."""

    model = InvoiceItem
    extra = 3
    fields = ("description", "car", "quantity", "unit_price", "total_price")
    readonly_fields = ("total_price",)
    autocomplete_fields = ["car"]

    verbose_name = "Позиция инвойса"
    verbose_name_plural = "📦 Позиции инвойса (редактируемые)"

    # Позиции выставленного счёта меняются только кредит-нотой или
    # принудительным регеном (с LogEntry), иначе total расходится с оплатами.
    def _editable(self, obj):
        return obj is None or obj.status == "DRAFT"

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Help-text у каждого поля убираем — он раздувает inline-таблицу.
        for field in formset.form.base_fields.values():
            field.help_text = ""
        return formset
