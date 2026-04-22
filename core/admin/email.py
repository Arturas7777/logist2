"""Admin для ContainerEmail и GmailSyncState.

Основной use-case — ручная ревизия UNMATCHED писем и массовая привязка к
контейнеру. Под обычной работой админ отдельно заходить не будет — переписка
показывается прямо в карточке контейнера (см. _emails_panel.html).
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.models import Container
from core.models_email import (
    CarEmailLink,
    ContainerEmail, ContainerEmailLink,
    EmailGroup, EmailGroupMember, EmailIngestFilter, GmailSyncState,
)


class MatchedByListFilter(admin.SimpleListFilter):
    title = 'Привязка'
    parameter_name = 'matched'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Привязано'),
            ('no', 'Не привязано'),
            ('container', 'По номеру контейнера'),
            ('booking', 'По букингу'),
            ('thread', 'По треду'),
            ('manual', 'Вручную'),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val == 'yes':
            return queryset.filter(containers__isnull=False).distinct()
        if val == 'no':
            return queryset.filter(containers__isnull=True)
        mapping = {
            'container': ContainerEmail.MATCHED_BY_CONTAINER_NUMBER,
            'booking': ContainerEmail.MATCHED_BY_BOOKING_NUMBER,
            'thread': ContainerEmail.MATCHED_BY_THREAD,
            'manual': ContainerEmail.MATCHED_BY_MANUAL,
        }
        if val in mapping:
            return queryset.filter(matched_by=mapping[val])
        return queryset


class ContainerEmailLinkInline(admin.TabularInline):
    """Инлайн-редактирование привязок письма к контейнерам (M2M through).

    ``is_read`` — per-карточка: письмо может быть прочитано в карточке-
    источнике и не прочитано в карточке, куда попало по упоминанию.
    """
    model = ContainerEmailLink
    extra = 0
    fk_name = 'email'
    autocomplete_fields = ('container',)
    fields = ('container', 'matched_by', 'is_read', 'created_at')
    readonly_fields = ('created_at',)


class CarEmailLinkInline(admin.TabularInline):
    """Инлайн-редактирование привязок письма к машинам (M2M through по VIN).

    ``is_read`` per-ссылка; матч обычно по VIN. Ручная привязка из UI
    появится позже (Phase 3) — пока редактируется только в админке.
    """
    model = CarEmailLink
    extra = 0
    fk_name = 'email'
    autocomplete_fields = ('car',)
    fields = ('car', 'matched_by', 'is_read', 'created_at')
    readonly_fields = ('created_at',)


class AttachToContainerForm(forms.Form):
    container = forms.ModelChoiceField(
        queryset=Container.objects.all().order_by('-id'),
        label='Контейнер',
        help_text='К какому контейнеру привязать выбранные письма.',
    )
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)


@admin.register(ContainerEmail)
class ContainerEmailAdmin(admin.ModelAdmin):
    list_display = (
        'received_at_short', 'direction_badge', 'from_short', 'subject_short',
        'container_link', 'matched_by_badge', 'read_status',
    )
    list_filter = (MatchedByListFilter, 'direction', 'matched_by')
    search_fields = ('subject', 'from_addr', 'to_addrs', 'message_id', 'thread_id')
    autocomplete_fields = ('sent_from_container',)
    inlines = [ContainerEmailLinkInline, CarEmailLinkInline]
    readonly_fields = (
        'message_id', 'thread_id', 'in_reply_to', 'references',
        'gmail_id', 'gmail_history_id', 'labels_json', 'attachments_json',
        'created_at',
    )
    date_hierarchy = 'received_at'
    list_per_page = 50
    ordering = ('-received_at',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related(
            'containers', 'container_links', 'cars', 'car_links',
        ).select_related(
            'sent_from_container',
        )
    actions = ['action_attach_to_container', 'action_mark_read', 'action_mark_unread']

    fieldsets = (
        ('Привязка', {
            'fields': ('sent_from_container', 'matched_by'),
            'description': 'Привязки к контейнерам и флаги «прочитано» '
                           'редактируются ниже в инлайне '
                           '«Связи письма с контейнерами».',
        }),
        ('Заголовки', {
            'fields': (
                'direction', 'from_addr', 'to_addrs', 'cc_addrs',
                'subject', 'received_at',
            ),
        }),
        ('Содержимое', {
            'fields': ('snippet', 'body_text', 'body_html'),
            'classes': ('collapse',),
        }),
        ('Служебное (Gmail)', {
            'fields': (
                'message_id', 'thread_id', 'in_reply_to', 'references',
                'gmail_id', 'gmail_history_id', 'labels_json',
                'attachments_json', 'created_at',
            ),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # list-display helpers
    # ------------------------------------------------------------------

    @admin.display(ordering='received_at', description='Получено')
    def received_at_short(self, obj: ContainerEmail) -> str:
        return obj.received_at.strftime('%d.%m.%Y %H:%M') if obj.received_at else ''

    @admin.display(ordering='direction', description='→')
    def direction_badge(self, obj: ContainerEmail) -> str:
        if obj.direction == ContainerEmail.DIRECTION_OUTGOING:
            return format_html(
                '<span style="color:#2563eb;font-weight:600;" title="Исходящее">→</span>'
            )
        return format_html(
            '<span style="color:#16a34a;font-weight:600;" title="Входящее">←</span>'
        )

    @admin.display(description='От')
    def from_short(self, obj: ContainerEmail) -> str:
        return (obj.from_addr or '')[:40]

    @admin.display(description='Тема')
    def subject_short(self, obj: ContainerEmail) -> str:
        return (obj.subject or '(без темы)')[:70]

    @admin.display(description='Контейнеры')
    def container_link(self, obj: ContainerEmail) -> str:
        # Показываем все привязанные контейнеры; origin-карточку
        # (sent_from_container) подсвечиваем жирным.
        containers = list(obj.containers.all()[:6])
        if not containers:
            return format_html('<span style="color:#b91c1c;">—</span>')
        origin_id = obj.sent_from_container_id
        parts: list[str] = []
        for c in containers:
            url = reverse('admin:core_container_change', args=[c.id])
            label = c.number or str(c.id)
            if origin_id and c.id == origin_id:
                parts.append(format_html(
                    '<a href="{}" style="font-weight:700;">{}</a>', url, label,
                ))
            else:
                parts.append(format_html('<a href="{}">{}</a>', url, label))
        return mark_safe(', '.join(parts))

    @admin.display(description='Прочитано')
    def read_status(self, obj: ContainerEmail) -> str:
        """Агрегированный статус прочтения по всем связям этого письма.

        Показывает 'X/Y' — сколько связей прочитано из общего числа. Полный
        per-карточка статус виден в инлайне ниже.
        """
        total = 0
        read = 0
        for link in obj.container_links.all():
            total += 1
            if link.is_read:
                read += 1
        if total == 0:
            return format_html('<span style="color:#94a3b8;">—</span>')
        color = '#16a34a' if read == total else ('#b91c1c' if read == 0 else '#d97706')
        return format_html(
            '<span style="color:{};" title="{} из {} карточек прочитали">{}/{}</span>',
            color, read, total, read, total,
        )

    @admin.display(ordering='matched_by', description='Как')
    def matched_by_badge(self, obj: ContainerEmail) -> str:
        colors = {
            ContainerEmail.MATCHED_BY_CONTAINER_NUMBER: '#16a34a',
            ContainerEmail.MATCHED_BY_BOOKING_NUMBER: '#2563eb',
            ContainerEmail.MATCHED_BY_THREAD: '#0891b2',
            ContainerEmail.MATCHED_BY_MANUAL: '#7c3aed',
            ContainerEmail.MATCHED_BY_UNMATCHED: '#b91c1c',
        }
        color = colors.get(obj.matched_by, '#6b7280')
        return format_html(
            '<span style="color:{};font-size:0.75rem;">{}</span>',
            color, obj.get_matched_by_display(),
        )

    # ------------------------------------------------------------------
    # bulk actions
    # ------------------------------------------------------------------

    @admin.action(description='Привязать к контейнеру…')
    def action_attach_to_container(self, request, queryset):
        if 'apply' in request.POST:
            form = AttachToContainerForm(request.POST)
            if form.is_valid():
                container = form.cleaned_data['container']
                # Добавляем link к container для каждого выбранного письма
                # (M2M). Если link уже есть — пропускаем через ignore_conflicts.
                # matched_by у письма в целом не трогаем: это «первичная»
                # причина, а здесь мы добавляем дополнительную ручную привязку.
                email_ids = list(queryset.values_list('id', flat=True))
                links = [
                    ContainerEmailLink(
                        email_id=eid,
                        container_id=container.id,
                        matched_by=ContainerEmail.MATCHED_BY_MANUAL,
                    )
                    for eid in email_ids
                ]
                ContainerEmailLink.objects.bulk_create(
                    links, ignore_conflicts=True,
                )
                self.message_user(
                    request,
                    f'Привязано {len(email_ids)} писем к контейнеру '
                    f'{container}',
                    level=messages.SUCCESS,
                )
                return redirect(request.get_full_path())
        else:
            form = AttachToContainerForm(initial={
                '_selected_action': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
            })

        return render(request, 'admin/core/containeremail/attach_to_container.html', {
            'form': form,
            'emails': queryset,
            'title': 'Привязать письма к контейнеру',
            'opts': self.model._meta,
            'action': 'action_attach_to_container',
            **self.admin_site.each_context(request),
        })

    @admin.action(description='Отметить прочитанными (во всех карточках)')
    def action_mark_read(self, request, queryset):
        email_ids = list(queryset.values_list('id', flat=True))
        updated = ContainerEmailLink.objects.filter(
            email_id__in=email_ids, is_read=False,
        ).update(is_read=True)
        self.message_user(
            request,
            f'{updated} связей письмо↔контейнер отмечено прочитанными.',
        )

    @admin.action(description='Отметить непрочитанными (во всех карточках)')
    def action_mark_unread(self, request, queryset):
        email_ids = list(queryset.values_list('id', flat=True))
        updated = ContainerEmailLink.objects.filter(
            email_id__in=email_ids, is_read=True,
        ).update(is_read=False)
        self.message_user(
            request,
            f'{updated} связей письмо↔контейнер отмечено непрочитанными.',
        )

    # ------------------------------------------------------------------
    # custom URLs: ручной триггер sync
    # ------------------------------------------------------------------

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'trigger-sync/',
                self.admin_site.admin_view(self.trigger_sync_view),
                name='core_containeremail_trigger_sync',
            ),
        ]
        return custom + urls

    def trigger_sync_view(self, request):
        if request.method != 'POST':
            return redirect('admin:core_containeremail_changelist')
        from core.tasks_email import sync_emails_from_gmail
        try:
            sync_emails_from_gmail.delay()
            self.message_user(request, 'Синхронизация запущена в фоне.', level=messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f'Не удалось запустить синхронизацию: {exc}', level=messages.ERROR)
        return redirect('admin:core_containeremail_changelist')


@admin.register(ContainerEmailLink)
class ContainerEmailLinkAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'container', 'matched_by', 'is_read', 'created_at')
    list_filter = ('matched_by', 'is_read')
    search_fields = (
        'email__subject', 'email__message_id', 'container__number',
    )
    autocomplete_fields = ('email', 'container')
    readonly_fields = ('created_at',)


@admin.register(CarEmailLink)
class CarEmailLinkAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'car', 'matched_by', 'is_read', 'created_at')
    list_filter = ('matched_by', 'is_read')
    search_fields = (
        'email__subject', 'email__message_id', 'car__vin', 'car__brand',
    )
    autocomplete_fields = ('email', 'car')
    readonly_fields = ('created_at',)


@admin.register(GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'last_history_id', 'last_sync_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('user_email',)


# =====================================================================
# Email-группы (для быстрой вставки получателей в composer)
# =====================================================================


class EmailGroupMemberInline(admin.TabularInline):
    model = EmailGroupMember
    extra = 1
    fields = ('position', 'email', 'display_name')
    ordering = ('position', 'email')


@admin.register(EmailGroup)
class EmailGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'members_preview', 'members_count_display',
                    'description', 'created_by', 'updated_at')
    search_fields = ('name', 'description', 'members__email', 'members__display_name')
    readonly_fields = ('created_at', 'updated_at', 'created_by')
    inlines = [EmailGroupMemberInline]
    ordering = ('name',)

    fieldsets = (
        (None, {
            'fields': ('name', 'description'),
            'description': (
                'Группа используется в «Написать письмо» / «Ответить» карточки '
                'контейнера — нажатие на кнопку «📇 Группы» разворачивает список '
                'участников в активное поле получателей (To / Cc / Bcc).'
            ),
        }),
        ('Служебное', {
            'classes': ('collapse',),
            'fields': ('created_by', 'created_at', 'updated_at'),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('members')

    @admin.display(description='Участники')
    def members_preview(self, obj: EmailGroup) -> str:
        names = []
        for m in obj.members.all()[:6]:
            names.append(m.display_name or m.email)
        rest = max(0, obj.members.count() - 6)
        if rest:
            names.append(f'… и ещё {rest}')
        return ', '.join(names) or '—'

    @admin.display(description='Кол-во', ordering='name')
    def members_count_display(self, obj: EmailGroup) -> int:
        return obj.members.count()

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Фильтры входящих писем по ключевым фразам
# =====================================================================


@admin.register(EmailIngestFilter)
class EmailIngestFilterAdmin(admin.ModelAdmin):
    """CRUD для фильтров Gmail-ингеста.

    Любое входящее письмо, у которого тема/тело матчит хотя бы один
    активный фильтр, **не линкуется** к карточкам (контейнерам / машинам /
    автовозам). Сам ``ContainerEmail`` сохраняется в БД, чтобы Gmail-sync
    остался идемпотентным.

    После изменения фильтров можно вручную прогнать
    ``python manage.py apply_email_filters`` — команда разлинкует уже
    загруженные письма, попадающие под актуальные фильтры.
    """

    list_display = (
        'phrase', 'scope', 'match_type', 'is_active',
        'notes_short', 'updated_at',
    )
    list_filter = ('is_active', 'scope', 'match_type')
    list_editable = ('is_active',)
    search_fields = ('phrase', 'notes')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-is_active', 'phrase')

    fieldsets = (
        (None, {
            'fields': ('phrase', 'scope', 'match_type', 'is_active', 'notes'),
            'description': (
                'Письма, в которых найдено совпадение с фразой, не будут '
                'попадать в карточки. Сами письма остаются в базе — если '
                'снять галочку «Активен» или удалить фильтр, можно '
                'прогнать <code>python manage.py apply_email_filters '
                '--restore</code>, чтобы вернуть их в карточки.'
            ),
        }),
        ('Служебное', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Комментарий')
    def notes_short(self, obj: EmailIngestFilter) -> str:
        return (obj.notes or '')[:80]
