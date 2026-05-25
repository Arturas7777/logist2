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

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Help-text у каждого поля убираем — он раздувает inline-таблицу.
        for field in formset.form.base_fields.values():
            field.help_text = ""
        return formset
