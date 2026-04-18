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

from core.models import Container
from core.models_email import ContainerEmail, GmailSyncState


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
            return queryset.filter(container__isnull=False)
        if val == 'no':
            return queryset.filter(container__isnull=True)
        mapping = {
            'container': ContainerEmail.MATCHED_BY_CONTAINER_NUMBER,
            'booking': ContainerEmail.MATCHED_BY_BOOKING_NUMBER,
            'thread': ContainerEmail.MATCHED_BY_THREAD,
            'manual': ContainerEmail.MATCHED_BY_MANUAL,
        }
        if val in mapping:
            return queryset.filter(matched_by=mapping[val])
        return queryset


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
        'container_link', 'matched_by_badge', 'is_read',
    )
    list_filter = (MatchedByListFilter, 'direction', 'is_read', 'matched_by')
    search_fields = ('subject', 'from_addr', 'to_addrs', 'message_id', 'thread_id')
    autocomplete_fields = ('container',)
    readonly_fields = (
        'message_id', 'thread_id', 'in_reply_to', 'references',
        'gmail_id', 'gmail_history_id', 'labels_json', 'attachments_json',
        'created_at',
    )
    date_hierarchy = 'received_at'
    list_per_page = 50
    ordering = ('-received_at',)
    actions = ['action_attach_to_container', 'action_mark_read', 'action_mark_unread']

    fieldsets = (
        ('Привязка', {
            'fields': ('container', 'matched_by', 'is_read'),
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

    @admin.display(ordering='container', description='Контейнер')
    def container_link(self, obj: ContainerEmail) -> str:
        if not obj.container_id:
            return format_html('<span style="color:#b91c1c;">—</span>')
        url = reverse('admin:core_container_change', args=[obj.container_id])
        return format_html(
            '<a href="{}">{}</a>', url, obj.container.number if obj.container else obj.container_id
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
                updated = queryset.update(
                    container=container,
                    matched_by=ContainerEmail.MATCHED_BY_MANUAL,
                )
                self.message_user(
                    request,
                    f'Привязано {updated} писем к контейнеру {container}',
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

    @admin.action(description='Отметить прочитанными')
    def action_mark_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(request, f'{updated} писем отмечено прочитанными.')

    @admin.action(description='Отметить непрочитанными')
    def action_mark_unread(self, request, queryset):
        updated = queryset.update(is_read=False)
        self.message_user(request, f'{updated} писем отмечено непрочитанными.')

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


@admin.register(GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'last_history_id', 'last_sync_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('user_email',)
