"""Админка для модели Contact + регистрация GenericTabularInline на карточки
всех контрагентов (Line / Carrier / Client / Warehouse / Company).

Страница «Контакты» — кастомный ``changelist_view`` с группировкой
``Тип контрагента → Наименование → Должность``.
"""

from __future__ import annotations

from collections import OrderedDict

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Prefetch
from django.shortcuts import render
from django.utils.html import format_html

from core.models import Carrier, Client, Company, Line, Warehouse
from core.models_contact import Contact, ContactEmail, ContactPhone


# ═══════════════════════════════════════════════════════════════════════════
# Inlines на странице самого контакта: emails / phones
# ═══════════════════════════════════════════════════════════════════════════


class ContactEmailInline(admin.TabularInline):
    model = ContactEmail
    extra = 1
    fields = ('email', 'is_primary', 'position')
    ordering = ('-is_primary', 'position', 'email')


class ContactPhoneInline(admin.TabularInline):
    model = ContactPhone
    extra = 1
    fields = ('phone', 'is_primary', 'position')
    ordering = ('-is_primary', 'position', 'phone')


# ═══════════════════════════════════════════════════════════════════════════
# Generic inline — подмешивается к карточкам всех контрагентов
# ═══════════════════════════════════════════════════════════════════════════


class ContactInline(GenericTabularInline):
    """Список контактов контрагента. Emails/телефоны редактируются в полной
    форме контакта (кнопка «Изменить» справа), потому что GenericTabularInline
    сам не поддерживает вложенные inlines.
    """

    model = Contact
    extra = 0
    fields = (
        'position', 'name',
        'emails_preview_inline', 'phones_preview_inline',
        'is_primary', 'comment',
    )
    readonly_fields = ('emails_preview_inline', 'phones_preview_inline')
    ordering = ('-is_primary', 'position', 'name')
    show_change_link = True
    verbose_name = 'Контакт'
    verbose_name_plural = 'Контакты'

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .prefetch_related('emails', 'phones')
            # Осиротевшие видны только в своей глобальной вкладке, не в карточке.
            .filter(is_orphan=False)
        )

    def emails_preview_inline(self, obj):
        if not obj.pk:
            return '—'
        preview = obj.emails_preview
        return preview or '—'
    emails_preview_inline.short_description = 'Emails'

    def phones_preview_inline(self, obj):
        if not obj.pk:
            return '—'
        preview = obj.phones_preview
        return preview or '—'
    phones_preview_inline.short_description = 'Телефоны'


# ═══════════════════════════════════════════════════════════════════════════
# ContactAdmin — полноценная страница + кастомная группировка
# ═══════════════════════════════════════════════════════════════════════════


class OrphanFilter(admin.SimpleListFilter):
    title = 'Привязка'
    parameter_name = 'orphan_state'

    def lookups(self, request, model_admin):
        return (
            ('linked', 'Привязан к контрагенту'),
            ('orphan', 'Осиротевший (без контрагента)'),
        )

    def queryset(self, request, queryset):
        v = self.value()
        if v == 'linked':
            return queryset.filter(is_orphan=False, content_type__isnull=False)
        if v == 'orphan':
            return queryset.filter(is_orphan=True)
        return queryset


class CounterpartyTypeFilter(admin.SimpleListFilter):
    title = 'Тип контрагента'
    parameter_name = 'ct'

    def lookups(self, request, model_admin):
        return (
            ('line',      'Линии'),
            ('carrier',   'Перевозчики'),
            ('client',    'Клиенты'),
            ('warehouse', 'Склады'),
            ('company',   'Компании'),
        )

    def queryset(self, request, queryset):
        v = self.value()
        model_map = {
            'line':      Line,
            'carrier':   Carrier,
            'client':    Client,
            'warehouse': Warehouse,
            'company':   Company,
        }
        cls = model_map.get(v)
        if not cls:
            return queryset
        ct = ContentType.objects.get_for_model(cls)
        return queryset.filter(content_type=ct)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    """Общая страница «Контакты» — регистрируется в сайдбаре Django admin.

    Представление ``changelist_view`` кастомно группирует контакты по:
      1. Типу контрагента (Линии / Перевозчики / ... / Осиротевшие)
      2. Наименованию контрагента внутри типа
      3. Должности (position) внутри контрагента

    При этом стандартные возможности админки (поиск / CRUD / экшены)
    остаются полностью рабочими — они вызываются в GET без параметра
    ``group=off``.
    """

    change_list_template = 'admin/core/contact/change_list_grouped.html'

    list_display = (
        'name', 'position', 'counterparty_display',
        'emails_display', 'phones_display',
        'is_primary', 'is_orphan',
    )
    list_filter = (CounterpartyTypeFilter, OrphanFilter, 'is_primary')
    search_fields = (
        'name', 'position', 'comment',
        'emails__email', 'phones__phone',
    )
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Основное', {
            'fields': ('name', 'position', 'is_primary', 'comment'),
        }),
        ('Привязка к контрагенту', {
            'fields': ('content_type', 'object_id', 'is_orphan'),
            'description': 'Выберите тип контрагента и введите его ID. '
                           'Для осиротевших контактов оба поля можно оставить пустыми.',
        }),
        ('Служебное', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    inlines = [ContactEmailInline, ContactPhoneInline]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('content_type')
            .prefetch_related(
                Prefetch('emails', queryset=ContactEmail.objects.order_by('-is_primary', 'position', 'email')),
                Prefetch('phones', queryset=ContactPhone.objects.order_by('-is_primary', 'position', 'phone')),
            )
        )

    # ── Display helpers ─────────────────────────────────────────────────

    def counterparty_display(self, obj):
        if obj.is_orphan or obj.content_type_id is None:
            return format_html('<span style="color:#b91c1c;">Осиротевший</span>')
        cp_name = obj.counterparty_name
        type_name = obj.counterparty_type
        return format_html(
            '<span>{}</span> <span style="color:#64748b;font-size:11px;">({})</span>',
            cp_name, type_name,
        )
    counterparty_display.short_description = 'Контрагент'

    def emails_display(self, obj):
        preview = ', '.join(e.email for e in obj.emails.all()[:3])
        more = obj.emails.count() - 3
        if more > 0:
            preview += f' +{more}'
        return preview or '—'
    emails_display.short_description = 'Emails'

    def phones_display(self, obj):
        preview = ', '.join(p.phone for p in obj.phones.all()[:3])
        more = obj.phones.count() - 3
        if more > 0:
            preview += f' +{more}'
        return preview or '—'
    phones_display.short_description = 'Телефоны'

    # ── Группированный changelist (страница «Контакты») ────────────────

    # Порядок для верхнего уровня группировки.
    _TYPE_ORDER = [
        (Line,      'Линии'),
        (Carrier,   'Перевозчики'),
        (Client,    'Клиенты'),
        (Warehouse, 'Склады'),
        (Company,   'Компании'),
    ]

    def changelist_view(self, request, extra_context=None):
        # По умолчанию показываем группированный вид. Чтобы получить
        # стандартный табличный changelist, добавьте ?view=table к URL.
        if request.GET.get('view') == 'table':
            return super().changelist_view(request, extra_context)

        qs = self.get_queryset(request)

        # Применяем поиск и фильтры "как в обычной админке".
        search_term = request.GET.get('q', '').strip()
        if search_term:
            qs, _use_distinct = self.get_search_results(request, qs, search_term)

        # Применяем list_filter если GET содержит их параметры.
        # Минимально: CounterpartyTypeFilter и OrphanFilter.
        ct_filter = CounterpartyTypeFilter(
            request, request.GET.copy(), Contact, self,
        )
        qs = ct_filter.queryset(request, qs) or qs
        orph_filter = OrphanFilter(
            request, request.GET.copy(), Contact, self,
        )
        qs = orph_filter.queryset(request, qs) or qs

        qs = qs.order_by('name')

        # Раскладываем по (type_label, counterparty_key, counterparty_label, position)
        groups: OrderedDict = OrderedDict()

        # Индекс moделей типа (ContentType → (order_idx, label))
        ct_index = {}
        for idx, (model_cls, label) in enumerate(self._TYPE_ORDER):
            ct = ContentType.objects.get_for_model(model_cls)
            ct_index[ct.pk] = (idx, label, model_cls)
            groups.setdefault(label, OrderedDict())
        groups['Осиротевшие'] = OrderedDict()

        # Загружаем имена контрагентов батчем (чтобы не дёргать БД на каждого).
        # Собираем id per-model, потом одним запросом.
        wanted: dict = {}  # model_cls -> set(ids)
        for c in qs:
            if c.is_orphan or c.content_type_id is None:
                continue
            entry = ct_index.get(c.content_type_id)
            if not entry:
                continue
            _, _, model_cls = entry
            wanted.setdefault(model_cls, set()).add(c.object_id)

        cp_name_map: dict = {}  # (model_cls, id) -> name
        for model_cls, ids in wanted.items():
            for obj in model_cls.objects.filter(pk__in=ids).only('id', 'name'):
                cp_name_map[(model_cls, obj.pk)] = obj.name

        for c in qs:
            if c.is_orphan or c.content_type_id is None:
                top_label = 'Осиротевшие'
                cp_key = (None, None)
                cp_name = '—'
            else:
                entry = ct_index.get(c.content_type_id)
                if not entry:
                    top_label = 'Осиротевшие'
                    cp_key = (None, None)
                    cp_name = '—'
                else:
                    _, top_label, model_cls = entry
                    cp_name = cp_name_map.get((model_cls, c.object_id), f'#{c.object_id}')
                    cp_key = (model_cls.__name__, c.object_id)

            type_bucket = groups.setdefault(top_label, OrderedDict())
            cp_bucket = type_bucket.setdefault(cp_key, {
                'name': cp_name,
                'positions': OrderedDict(),
            })
            position = (c.position or '').strip() or '—'
            cp_bucket['positions'].setdefault(position, []).append(c)

        # Чистим пустые группы и сортируем контрагентов по имени.
        cleaned = OrderedDict()
        for top_label, buckets in groups.items():
            if not buckets:
                continue
            sorted_buckets = OrderedDict(sorted(buckets.items(), key=lambda kv: (kv[1]['name'] or '').lower()))
            # Сортируем должности: "—" (без должности) — в конец.
            for k, b in sorted_buckets.items():
                positions = b['positions']
                def _pos_sort(item):
                    pos = item[0]
                    return (1, pos) if pos == '—' else (0, pos.lower())
                b['positions'] = OrderedDict(sorted(positions.items(), key=_pos_sort))
            cleaned[top_label] = sorted_buckets

        total_contacts = qs.count()

        context = {
            **self.admin_site.each_context(request),
            'title': 'Контакты',
            'opts': self.model._meta,
            'cl': None,
            'groups': cleaned,
            'total_contacts': total_contacts,
            'search_term': search_term,
            'ct_value': request.GET.get('ct', ''),
            'orphan_value': request.GET.get('orphan_state', ''),
            'table_view_url': request.path + '?view=table',
        }
        return render(request, self.change_list_template, context)


# ═══════════════════════════════════════════════════════════════════════════
# Регистрация GenericTabularInline на карточки всех контрагентов
# ═══════════════════════════════════════════════════════════════════════════
#
# Мы делаем это пост-фактум через admin.site._registry, потому что админки
# контрагентов уже зарегистрированы в core/admin/partners.py. Чтобы изменения
# применились, core/admin/__init__.py должен импортировать этот модуль ПОСЛЕ
# partners.


def _attach_contact_inline():
    for model_cls in [Line, Carrier, Client, Warehouse, Company]:
        admin_instance = admin.site._registry.get(model_cls)
        if admin_instance is None:
            continue
        existing = list(getattr(admin_instance, 'inlines', []) or [])
        if ContactInline in existing:
            continue
        admin_instance.__class__.inlines = existing + [ContactInline]


_attach_contact_inline()
