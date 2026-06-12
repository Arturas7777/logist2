"""Админки моделей AI-агента: журнал, память, вопросы, политики.

Основная работа с агентом идёт на странице /admin/tasks-board/;
здесь — просмотр журнала, ручная правка памяти и настройка автономии.
"""

from django.contrib import admin
from django.utils.html import format_html

from core.models import AgentAction, AgentMemory, AgentPolicy, AgentQuestion, AgentRun


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status_badge", "input_ref", "started_at", "tokens", "cost_display")
    list_filter = ("kind", "status")
    readonly_fields = [field.name for field in AgentRun._meta.fields]
    date_hierarchy = "started_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def status_badge(self, obj):
        colors = {"RUNNING": "#0ea5e9", "SUCCESS": "#10b981", "ERROR": "#dc2626"}
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;'
            'font-size:11px;font-weight:600;">{}</span>',
            colors.get(obj.status, "#6b7280"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Статус"
    status_badge.admin_order_field = "status"

    def tokens(self, obj):
        return f"{obj.input_tokens} / {obj.output_tokens}"

    tokens.short_description = "Токены (in/out)"

    def cost_display(self, obj):
        return f"${obj.cost_usd:.4f}"

    cost_display.short_description = "Стоимость"
    cost_display.admin_order_field = "cost_usd"


@admin.register(AgentAction)
class AgentActionAdmin(admin.ModelAdmin):
    list_display = ("id", "action_type", "title", "status", "risk_level", "created_at", "decided_by")
    list_filter = ("status", "action_type", "risk_level")
    search_fields = ("title", "reasoning")
    readonly_fields = (
        "run",
        "action_type",
        "risk_level",
        "title",
        "payload",
        "reasoning",
        "confidence",
        "source_email",
        "task",
        "created_at",
        "decided_at",
        "decided_by",
        "executed_at",
        "result_json",
        "error",
    )
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False


@admin.register(AgentQuestion)
class AgentQuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "question_short", "status", "created_at", "answered_by")
    list_filter = ("status",)
    search_fields = ("question", "answer")
    readonly_fields = ("run", "source_email", "context_json", "created_at", "memory")
    list_per_page = 50

    def question_short(self, obj):
        return obj.question[:100]

    question_short.short_description = "Вопрос"


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    """Память агента — редактируемая: владелец может править формулировки."""

    list_display = ("id", "kind", "content_short", "source", "is_active", "times_used", "created_at")
    list_filter = ("kind", "source", "is_active")
    search_fields = ("content",)
    readonly_fields = ("embedding", "times_used", "last_used_at", "created_at", "updated_at")
    fields = (
        "kind",
        "content",
        "is_active",
        "source",
        "created_by",
        "times_used",
        "last_used_at",
        "created_at",
        "updated_at",
        "embedding",
    )
    actions = ["deactivate_action", "reembed_action"]
    list_per_page = 50

    def content_short(self, obj):
        return obj.content[:120]

    content_short.short_description = "Содержание"

    def save_model(self, request, obj, form, change):
        # При правке текста пересчитываем embedding, чтобы retrieval не
        # работал по устаревшему вектору.
        if change and "content" in form.changed_data:
            from core.services.agent.memory import embed_text

            obj.embedding = embed_text(obj.content)
        if not change and not obj.created_by:
            obj.created_by = request.user.username or ""
        super().save_model(request, obj, form, change)

    def deactivate_action(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Деактивировано записей: {updated}")

    deactivate_action.short_description = "Деактивировать (агент перестанет учитывать)"

    def reembed_action(self, request, queryset):
        from core.services.agent.memory import embed_text

        updated = 0
        for memory in queryset:
            memory.embedding = embed_text(memory.content)
            memory.save(update_fields=["embedding", "updated_at"])
            updated += 1
        self.message_user(request, f"Пересчитан embedding: {updated}")

    reembed_action.short_description = "Пересчитать embedding"


@admin.register(AgentPolicy)
class AgentPolicyAdmin(admin.ModelAdmin):
    """Политика автономии: ASK (спрашивать) / AUTO (сам) / DISABLED."""

    list_display = ("action_type", "mode", "comment", "updated_by", "updated_at")
    list_filter = ("mode",)
    readonly_fields = ("updated_at",)

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user.username or ""
        super().save_model(request, obj, form, change)
