"""Admin for Task model — раздел «Дела».

Страница объединяет два сценария:
  * **Авто-задачи** из карточки авто (галочка «Важное») — закрываются
    автоматически при снятии галочки или ручным действием.
  * **Ручные задачи** — заводятся прямо здесь, могут иметь дедлайн и
    ссылку на конкретный авто/контейнер. Закрываются ТОЛЬКО ручным
    действием пользователя.
"""

from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.models import Task


class TaskOverdueFilter(SimpleListFilter):
    """Просрочено / срок сегодня / без срока / в норме."""

    title = "Срок"
    parameter_name = "deadline_state"

    def lookups(self, request, model_admin):
        return (
            ("overdue", "Просрочено"),
            ("today", "Срок сегодня"),
            ("soon", "В ближайшие 3 дня"),
            ("no_deadline", "Без срока"),
            ("future", "В будущем"),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timezone.timedelta(days=1)
        soon_end = today_start + timezone.timedelta(days=3)

        value = self.value()
        if value == "overdue":
            return queryset.filter(is_completed=False, deadline__lt=now)
        if value == "today":
            return queryset.filter(deadline__gte=today_start, deadline__lt=today_end)
        if value == "soon":
            return queryset.filter(is_completed=False, deadline__gte=now, deadline__lt=soon_end)
        if value == "no_deadline":
            return queryset.filter(deadline__isnull=True)
        if value == "future":
            return queryset.filter(deadline__gte=soon_end)
        return queryset


class TaskActiveFilter(SimpleListFilter):
    """По умолчанию показываем только открытые дела."""

    title = "Состояние"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (
            ("active", "Открытые"),
            ("completed", "Выполненные"),
            ("all", "Все"),
        )

    def choices(self, changelist):
        # По умолчанию активна опция "Открытые". Чтобы пункт «Все» работал
        # как сброс, явно прокидываем None == active в URL не-перезатирает.
        value = self.value() or "active"
        for lookup, title in self.lookup_choices:
            yield {
                "selected": value == lookup,
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }

    def queryset(self, request, queryset):
        value = self.value() or "active"
        if value == "active":
            return queryset.filter(is_completed=False)
        if value == "completed":
            return queryset.filter(is_completed=True)
        return queryset


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "state_indicator",
        "title_display",
        "priority_display",
        "deadline_display",
        "related_object",
        "auto_created_display",
        "created_at",
        "completed_display",
    )
    list_display_links = ("title_display",)
    list_filter = (TaskActiveFilter, TaskOverdueFilter, "priority", "origin", "auto_created")
    search_fields = ("title", "description", "car__vin", "car__brand", "container__number")
    list_select_related = ("car", "container")
    list_per_page = 50
    autocomplete_fields = ("car", "container")
    actions = ["mark_completed_action", "reopen_action"]

    fieldsets = (
        (
            "Основное",
            {
                "fields": ("title", "description", "priority", "deadline"),
            },
        ),
        (
            "Связи",
            {
                "fields": ("car", "container"),
                "description": "Опционально привяжите дело к конкретному авто и/или контейнеру.",
            },
        ),
        (
            "Состояние",
            {
                "fields": ("is_completed", "completed_at", "completed_by"),
            },
        ),
        (
            "Системное",
            {
                "classes": ("collapse",),
                "fields": (
                    "auto_created",
                    "origin",
                    "source_email",
                    "ai_summary",
                    "created_at",
                    "created_by",
                    "updated_at",
                ),
            },
        ),
    )
    readonly_fields = (
        "auto_created",
        "origin",
        "source_email",
        "ai_summary",
        "created_at",
        "updated_at",
        "completed_at",
    )

    def get_changelist_instance(self, request):
        # Ставим параметр state=active в queryset по умолчанию, если фильтр
        # не задан явно — чтобы открытие /admin/core/task/ показывало именно
        # активные дела.
        if "state" not in request.GET:
            request.GET = request.GET.copy()
            request.GET["state"] = "active"
        return super().get_changelist_instance(request)

    def save_model(self, request, obj, form, change):
        # Авто-заполнение полей создателя/выполнившего из текущего пользователя.
        if not change and not obj.created_by:
            obj.created_by = request.user.username or request.user.get_full_name() or ""
        # Если is_completed только что включили вручную — фиксируем, кто и когда.
        if change and obj.is_completed and not obj.completed_at:
            obj.completed_at = timezone.now()
            if not obj.completed_by:
                obj.completed_by = request.user.username or request.user.get_full_name() or ""
        # И обратная ситуация — открыли заново: чистим completed_*.
        if change and not obj.is_completed:
            obj.completed_at = None
            obj.completed_by = ""
        super().save_model(request, obj, form, change)

    # -------- Display helpers --------

    def state_indicator(self, obj):
        if obj.is_completed:
            return format_html('<span style="color:#10b981;font-size:18px;line-height:1;" title="Выполнено">✓</span>')
        if obj.is_overdue:
            return format_html('<span style="color:#dc2626;font-size:18px;line-height:1;" title="Просрочено">!</span>')
        return format_html('<span style="color:#6b7280;font-size:18px;line-height:1;" title="Открыто">○</span>')

    state_indicator.short_description = ""

    def title_display(self, obj):
        style = "text-decoration:line-through;color:#9ca3af;" if obj.is_completed else "font-weight:600;"
        return format_html('<span style="{}">{}</span>', style, obj.title)

    title_display.short_description = "Название"
    title_display.admin_order_field = "title"

    def priority_display(self, obj):
        colors = {
            "LOW": "#6b7280",
            "MEDIUM": "#0ea5e9",
            "HIGH": "#dc2626",
        }
        color = colors.get(obj.priority, "#6b7280")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;'
            'font-size:11px;font-weight:600;">{}</span>',
            color,
            obj.get_priority_display(),
        )

    priority_display.short_description = "Приоритет"
    priority_display.admin_order_field = "priority"

    def deadline_display(self, obj):
        if not obj.deadline:
            return format_html('<span style="color:#9ca3af;">—</span>')
        formatted = obj.deadline.strftime("%d.%m.%Y %H:%M")
        if obj.is_overdue:
            return format_html(
                '<span style="color:#dc2626;font-weight:700;" title="Просрочено">⏰ {}</span>', formatted
            )
        return format_html('<span style="color:#1f2937;">{}</span>', formatted)

    deadline_display.short_description = "Дедлайн"
    deadline_display.admin_order_field = "deadline"

    def related_object(self, obj):
        parts = []
        if obj.car_id:
            url = reverse("admin:core_car_change", args=[obj.car_id])
            parts.append(
                format_html(
                    '<a href="{}" style="color:#6c5ce7;text-decoration:none;" title="Авто">🚗 {}</a>',
                    url,
                    obj.car.vin if obj.car else f"#{obj.car_id}",
                )
            )
        if obj.container_id:
            url = reverse("admin:core_container_change", args=[obj.container_id])
            parts.append(
                format_html(
                    '<a href="{}" style="color:#0ea5e9;text-decoration:none;" title="Контейнер">📦 {}</a>',
                    url,
                    obj.container.number if obj.container else f"#{obj.container_id}",
                )
            )
        if not parts:
            return format_html('<span style="color:#9ca3af;">—</span>')
        # Объединяем безопасно через format_html_join
        from django.utils.html import format_html_join

        return format_html_join(" ", "{}", ((p,) for p in parts))

    related_object.short_description = "Привязка"

    def auto_created_display(self, obj):
        if obj.auto_created:
            return format_html(
                '<span style="background:#fef3c7;color:#92400e;padding:1px 6px;'
                'border-radius:4px;font-size:11px;font-weight:600;" '
                'title="Создано автоматически из чекбокса «Важное»">авто</span>'
            )
        return format_html(
            '<span style="background:#dcfce7;color:#166534;padding:1px 6px;'
            'border-radius:4px;font-size:11px;font-weight:600;">ручное</span>'
        )

    auto_created_display.short_description = "Тип"
    auto_created_display.admin_order_field = "auto_created"

    def completed_display(self, obj):
        if not obj.is_completed:
            return format_html('<span style="color:#9ca3af;">—</span>')
        date_str = obj.completed_at.strftime("%d.%m.%Y") if obj.completed_at else ""
        by = obj.completed_by or "—"
        return format_html('<span style="color:#10b981;font-size:12px;" title="Выполнил: {}">{}</span>', by, date_str)

    completed_display.short_description = "Выполнено"

    # -------- Actions --------

    def mark_completed_action(self, request, queryset):
        username = request.user.username or request.user.get_full_name() or ""
        updated = 0
        for task in queryset.filter(is_completed=False):
            task.mark_completed(by=username)
            updated += 1
        if updated:
            messages.success(request, f"Закрыто дел: {updated}")
        else:
            messages.info(request, "Все выбранные дела уже выполнены.")

    mark_completed_action.short_description = "✓ Отметить выполненными"

    def reopen_action(self, request, queryset):
        updated = 0
        for task in queryset.filter(is_completed=True):
            task.reopen()
            updated += 1
        if updated:
            messages.success(request, f"Открыто заново: {updated}")
        else:
            messages.info(request, "Все выбранные дела уже открыты.")

    reopen_action.short_description = "↻ Открыть заново"
