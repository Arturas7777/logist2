"""Админки личных карт и переводов между картами."""

from django.contrib import admin
from django.utils.html import format_html

from core.models_billing import PersonalCard, PersonalTransfer


@admin.register(PersonalCard)
class PersonalCardAdmin(admin.ModelAdmin):
    list_display = ("name", "last_four", "balance", "color_preview", "is_active", "order")
    list_editable = ("order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("order", "name")

    def color_preview(self, obj):
        return format_html(
            '<span style="display:inline-block;width:18px;height:18px;border-radius:4px;'
            'background:{};vertical-align:middle"></span> {}',
            obj.color,
            obj.color,
        )

    color_preview.short_description = "Цвет"


@admin.register(PersonalTransfer)
class PersonalTransferAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "transfer_type",
        "from_card",
        "to_card",
        "amount",
        "description_short",
        "category",
    )
    list_filter = ("transfer_type", "from_card", "to_card")
    list_select_related = ("from_card", "to_card", "category")
    search_fields = ("description",)
    ordering = ("-date",)
    raw_id_fields = ("linked_transaction",)
    readonly_fields = ("linked_transaction", "created_at", "created_by")

    def description_short(self, obj):
        desc = obj.description or ""
        return desc[:60] + "..." if len(desc) > 60 else desc

    description_short.short_description = "Описание"
