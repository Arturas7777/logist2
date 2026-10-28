from django.contrib import admin

from core.models.bots import (
    BotAuditEntry,
    BotJob,
    BotPrincipal,
    CaromotoClientMap,
    CaromotoImportBatch,
    EtaHistory,
    LedgerEntry,
    Proforma,
)


@admin.register(BotPrincipal)
class BotPrincipalAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "token_prefix", "is_active", "created_at")
    readonly_fields = ("token_hash", "token_prefix", "created_at")


@admin.register(BotAuditEntry)
class BotAuditEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "principal_name", "command", "dry_run", "source_email_id")
    readonly_fields = (
        "principal_name",
        "role",
        "command",
        "created_at",
        "before",
        "after",
        "source_email_id",
        "dry_run",
        "idempotency_key",
    )


@admin.register(BotJob)
class BotJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "role", "status", "created_at")
    list_filter = ("status", "job_type", "role")


@admin.register(CaromotoClientMap)
class CaromotoClientMapAdmin(admin.ModelAdmin):
    list_display = ("office", "caromoto_client_id", "client", "priority")
    search_fields = ("caromoto_client_id", "office", "client__name")


@admin.register(CaromotoImportBatch)
class CaromotoImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "principal_name", "created_at", "applied_at")
    readonly_fields = ("diff", "created_at", "applied_at", "principal_name")


@admin.register(Proforma)
class ProformaAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "status", "amount", "paid_amount", "client")
    search_fields = ("number",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("occurred_on", "kind", "amount", "description")


@admin.register(EtaHistory)
class EtaHistoryAdmin(admin.ModelAdmin):
    list_display = ("container", "eta", "previous_eta", "source", "recorded_at")
