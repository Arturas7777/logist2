"""Админка справочника :class:`ExpenseCategory`."""

from django.contrib import admin

from core.models_billing import ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    """Управление категориями расходов/доходов."""

    list_display = ("name", "short_name", "category_type", "order", "is_active")
    list_editable = ("short_name", "order", "is_active")
    list_filter = ("category_type", "is_active")
    search_fields = ("name", "short_name")
    ordering = ("order", "name")
