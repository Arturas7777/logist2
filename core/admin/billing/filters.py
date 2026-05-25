"""Кастомные фильтры для админки биллинга."""

from django.contrib import admin


class InvoiceDirectionFilter(admin.SimpleListFilter):
    """Фильтр входящих/исходящих инвойсов."""

    title = "Направление"
    parameter_name = "direction"

    def lookups(self, request, model_admin):
        return [
            ("outgoing", "Исходящие (мы выставили)"),
            ("incoming", "Входящие (нам выставили)"),
        ]

    def queryset(self, request, queryset):
        from core.models import Company

        default_id = Company.get_default_id()
        if self.value() == "outgoing":
            return queryset.filter(issuer_company_id=default_id)
        if self.value() == "incoming":
            return queryset.filter(recipient_company_id=default_id)
        return queryset
