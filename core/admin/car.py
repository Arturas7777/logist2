import logging
import os
from decimal import Decimal

from django.conf import settings
from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.templatetags.static import static
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.admin_export import CSVExportMixin
from core.admin_filters import ClientAutocompleteFilter, MultiStatusFilter, MultiWarehouseFilter
from core.models import (
    Car,
    CarModelImage,
    CarrierService,
    CarService,
    CompanyService,
    DeletedCarService,
    LineService,
    WarehouseService,
)

logger = logging.getLogger(__name__)

CAR_MODELS_DIR = os.path.join(settings.BASE_DIR, 'core', 'static', 'icons', 'car_models')

# Кэш списка файлов car_models. Раньше `find_car_image` делал os.listdir на
# каждый просмотр карточки — при 200+ иконок и 10 операторах это десятки
# системных вызовов в секунду. Кэшируем при первом обращении и
# инвалидируем по mtime директории (если кто-то добавил иконку без
# рестарта процесса — новый файл подхватится в течение 60 сек).
_CAR_IMAGES_CACHE: dict = {"mtime": None, "files_lower": {}}
_CAR_IMAGES_TTL = 60.0  # сек
_CAR_IMAGES_LAST_CHECK = [0.0]


def _get_car_images_index():
    """Возвращает {filename_lower: filename} из CAR_MODELS_DIR, кэшируя его.

    Ключ возврата — lower-case имя; значение — настоящее имя файла, чтобы
    собирать корректный URL без обращения к ФС повторно.
    """
    import time as _t
    now = _t.time()
    # Часто-вызываемая функция: не дёргаем os.stat чаще 1 раза в _CAR_IMAGES_TTL.
    if (now - _CAR_IMAGES_LAST_CHECK[0]) < _CAR_IMAGES_TTL and _CAR_IMAGES_CACHE["mtime"] is not None:
        return _CAR_IMAGES_CACHE["files_lower"]
    _CAR_IMAGES_LAST_CHECK[0] = now
    try:
        mtime = os.path.getmtime(CAR_MODELS_DIR)
    except OSError:
        _CAR_IMAGES_CACHE["files_lower"] = {}
        return _CAR_IMAGES_CACHE["files_lower"]
    if _CAR_IMAGES_CACHE["mtime"] == mtime and _CAR_IMAGES_CACHE["files_lower"]:
        return _CAR_IMAGES_CACHE["files_lower"]
    try:
        files = os.listdir(CAR_MODELS_DIR)
    except FileNotFoundError:
        files = []
    _CAR_IMAGES_CACHE["mtime"] = mtime
    _CAR_IMAGES_CACHE["files_lower"] = {f.lower(): f for f in files if f.endswith('.png')}
    return _CAR_IMAGES_CACHE["files_lower"]


def _load_supplier_costs_map(car_service_pks):
    """Батч-загрузка SupplierCost для списка CarService → {cs_pk: (total, sources)}.

    Заменяет N+1 в `_cost_badge_html` на один запрос на всю карточку авто.
    """
    from core.models_invoice_audit import SupplierCost
    result = {}
    if not car_service_pks:
        return result
    for cost in SupplierCost.objects.filter(car_service_id__in=car_service_pks).only(
        'car_service_id', 'amount', 'source'
    ):
        bucket = result.setdefault(cost.car_service_id, {'total': 0.0, 'sources': set()})
        bucket['total'] += float(cost.amount or 0)
        bucket['sources'].add(cost.source)
    return result


def _cost_badge_html(car_service_pk, current_price=None, costs_map=None):
    """Генерирует HTML-бейдж подтверждённости затрат для CarService.

    Если передан `costs_map` (dict от `_load_supplier_costs_map`) —
    берёт данные оттуда без доп. SQL. Иначе (обратная совместимость)
    делает отдельный запрос.
    """
    if costs_map is not None:
        entry = costs_map.get(car_service_pk)
        if entry:
            total = entry['total']
            sources = entry['sources']
            icon = '📎' if 'INVOICE' in sources else '✍️'
            return (
                f'<div style="font-size:10px; margin-top:4px; padding:2px 6px; border-radius:6px; '
                f'background:#dcfce7; color:#166534; display:inline-flex; align-items:center; gap:3px;">'
                f'{icon} {total:.2f}\u20ac</div>'
            )
        prefill = current_price if current_price is not None else 0
        return (
            '<div style="margin-top:4px;">'
            '<button type="button" class="confirm-cost-btn" '
            'style="font-size:10px; padding:1px 6px; border:1px solid #d97706; border-radius:6px; '
            'background:#fffbeb; color:#92400e; cursor:pointer;" '
            f'onclick="openConfirmCostModal({car_service_pk}, {prefill})">'
            '&#10003; Confirm cost</button>'
            '</div>'
        )

    from core.models_invoice_audit import SupplierCost
    costs = SupplierCost.objects.filter(car_service_id=car_service_pk)
    if costs.exists():
        total = sum(float(c.amount) for c in costs)
        sources = {c.source for c in costs}
        icon = '📎' if 'INVOICE' in sources else '✍️'
        return (
            f'<div style="font-size:10px; margin-top:4px; padding:2px 6px; border-radius:6px; '
            f'background:#dcfce7; color:#166534; display:inline-flex; align-items:center; gap:3px;">'
            f'{icon} {total:.2f}\u20ac</div>'
        )
    else:
        prefill = current_price if current_price is not None else 0
        return (
            '<div style="margin-top:4px;">'
            '<button type="button" class="confirm-cost-btn" '
            'style="font-size:10px; padding:1px 6px; border:1px solid #d97706; border-radius:6px; '
            'background:#fffbeb; color:#92400e; cursor:pointer;" '
            f'onclick="openConfirmCostModal({car_service_pk}, {prefill})">'
            '&#10003; Confirm cost</button>'
            '</div>'
        )


def find_car_image(year, brand):
    """Find best matching image for a car by year+brand.

    Priority: exact match "2018 BMW 430I.png" > brand-only match > fallback.
    Matching is case-insensitive. Использует кэшированный индекс файлов.
    """
    if not brand:
        return None

    files_lower = _get_car_images_index()
    if not files_lower:
        return None

    exact = f"{year} {brand}.png".lower()
    if exact in files_lower:
        return f"icons/car_models/{files_lower[exact]}"

    brand_lower = brand.lower()
    for fname_lower, fname in files_lower.items():
        name = fname_lower.rsplit('.', 1)[0]
        parts = name.split(' ', 1)
        if len(parts) == 2 and parts[1] == brand_lower:
            return f"icons/car_models/{fname}"

    for fname_lower, fname in files_lower.items():
        name = fname_lower.rsplit('.', 1)[0]
        if brand_lower in name:
            return f"icons/car_models/{fname}"

    return None


def find_car_model_image_url(year, brand):
    """Ищет картинку модели в БД (CarModelImage). Возвращает MEDIA-URL или None.

    Приоритет: точное совпадение brand+год > brand без года > частичное
    (brand записи — начало brand авто, напр. «BMW» ⊂ «BMW 430I»; берётся
    самая специфичная запись)."""
    if not brand:
        return None
    from core.models import CarModelImage

    brand = brand.strip()
    qs = CarModelImage.objects.filter(is_active=True).exclude(image='')

    match = qs.filter(brand__iexact=brand, year=year).first()
    if not match:
        match = qs.filter(brand__iexact=brand, year__isnull=True).first()
    if not match:
        brand_lower = brand.lower()
        candidates = [c for c in qs if brand_lower.startswith(c.brand.strip().lower())]
        candidates.sort(key=lambda c: (len(c.brand), c.year == year), reverse=True)
        match = candidates[0] if candidates else None

    if match and match.image:
        try:
            url = match.image.url
        except ValueError:
            return None
        # Сброс кэша: имя файла при перезаливке не меняется (.webp под тем же
        # brand), поэтому добавляем версию по времени обновления — иначе
        # браузер показывал бы старую версию картинки.
        if match.updated_at:
            url = f"{url}?v={int(match.updated_at.timestamp())}"
        return url
    return None


class CarHasUnreadEmailsFilter(SimpleListFilter):
    """Фильтр по наличию писем и непрочитанных писем в карточке машины."""

    title = 'Переписка'
    parameter_name = 'emails'

    def lookups(self, request, model_admin):
        return (
            ('unread', 'Есть непрочитанные'),
            ('need_reply', 'Ждут ответа'),
            ('any', 'Есть переписка'),
            ('none', 'Нет писем'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'unread':
            return queryset.filter(email_links__is_read=False).distinct()
        if value == 'need_reply':
            return queryset.filter(
                email_links__email__needs_reply=True,
                email_links__email__direction='INCOMING',
            ).distinct()
        if value == 'any':
            return queryset.filter(email_links__isnull=False).distinct()
        if value == 'none':
            return queryset.filter(email_links__isnull=True)
        return queryset


@admin.register(CarModelImage)
class CarModelImageAdmin(admin.ModelAdmin):
    list_display = ('preview', 'brand', 'year', 'is_active', 'updated_at')
    list_display_links = ('preview', 'brand')
    list_filter = ('is_active',)
    search_fields = ('brand',)
    list_editable = ('is_active',)
    readonly_fields = ('preview_large', 'created_at', 'updated_at')
    fields = ('brand', 'year', 'is_active', 'image', 'preview_large', 'created_at', 'updated_at')
    ordering = ('brand', '-year')

    @admin.display(description='Превью')
    def preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="width:96px;height:54px;object-fit:contain;'
                'background:linear-gradient(135deg,#f8fafc,#eef2f7);'
                'border:1px solid #e5e7eb;border-radius:6px;">',
                obj.image.url,
            )
        return '—'

    @admin.display(description='Изображение (как в карточке)')
    def preview_large(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="width:360px;max-width:100%;aspect-ratio:16/9;'
                'object-fit:contain;padding:10px;box-sizing:border-box;'
                'background:linear-gradient(135deg,#f8fafc,#eef2f7);'
                'border:1px solid #e5e7eb;border-radius:12px;">',
                obj.image.url,
            )
        return 'Загрузите изображение и сохраните — оно приведётся к единому виду.'


@admin.register(Car)
class CarAdmin(CSVExportMixin, admin.ModelAdmin):
    change_form_template = 'admin/core/car/change_form.html'
    change_list_template = 'admin/core/car/change_list.html'
    list_display = (
        'vin_display', 'brand', 'vehicle_type', 'year_display', 'client', 'colored_status', 'container_display', 'warehouse', 'line',
        'unload_date_display', 'days_display', 'storage_cost_display', 'total_price_display', 'markup_display',
        'has_title', 'title_attached_display'
    )
    list_display_links = ('vin_display',)
    list_editable = ('has_title',)
    list_filter = ('is_important', MultiStatusFilter, ClientAutocompleteFilter, MultiWarehouseFilter, CarHasUnreadEmailsFilter)
    search_fields = ('vin', 'brand', 'client__name', 'container__number')
    list_per_page = 50
    show_full_result_count = False
    # M5: убираем тяжёлые FK-дропдауны на форме car/change_form/. Все target-admin'ы
    # (Client/Warehouse/Line/Carrier/Container) имеют search_fields, autocomplete работает.
    autocomplete_fields = ('client', 'warehouse', 'line', 'carrier', 'container')

    csv_export_filename_prefix = 'cars'
    csv_export_fields = [
        ('vin', 'VIN'),
        ('brand', 'Марка'),
        ('vehicle_type', 'Тип'),
        ('year', 'Год'),
        ('client__name', 'Клиент'),
        ('container__number', 'Контейнер'),
        ('warehouse__name', 'Склад'),
        ('line__name', 'Линия'),
        ('carrier__name', 'Перевозчик'),
        ('status', 'Статус'),
        ('unload_date', 'Разгрузка'),
        ('transfer_date', 'Передача'),
        ('days', 'Дней хранения'),
        ('storage_cost', 'Стоимость хранения'),
        ('total_price', 'Итого'),
        ('has_title', 'Есть тайтл'),
    ]
    # OPTIMIZATION: Preload related objects for list view
    list_select_related = ('client', 'warehouse', 'line', 'carrier', 'container')

    def get_queryset(self, request):
        """Optimized queryset with select_related, prefetch_related, and annotation.

        ВАЖНО: `_total_markup` считается через Subquery, а не через
        Sum('car_services__markup_amount') в общем annotate(). Иначе при
        одновременных JOIN'ах с email_links строки услуг дублируются на
        количество писем у авто, и SUM раздувается ровно во столько же раз.
        Симптом: в столбце «Цена» у машин с привязанными письмами цена
        умножена на число писем. См. карточку авто (services_summary_display)
        — там расчёт через отдельный aggregate, всегда корректен.
        """
        from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum
        from django.db.models.functions import Coalesce

        markup_subquery = (
            CarService.objects.filter(car_id=OuterRef('pk'))
            .values('car_id')
            .annotate(total=Sum('markup_amount'))
            .values('total')
        )

        qs = super().get_queryset(request)
        qs = qs.select_related('client', 'warehouse', 'line', 'carrier', 'container')
        qs = qs.prefetch_related('car_services')
        qs = qs.annotate(
            _total_markup=Coalesce(
                Subquery(markup_subquery, output_field=DecimalField(max_digits=12, decimal_places=2)),
                Decimal('0.00'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            _emails_unread=Count(
                'email_links',
                filter=Q(email_links__is_read=False),
                distinct=True,
            ),
            _emails_need_reply=Count(
                'email_links__email',
                filter=Q(
                    email_links__email__needs_reply=True,
                    email_links__email__direction='INCOMING',
                ),
                distinct=True,
            ),
        )
        return qs
    readonly_fields = (
        'default_warehouse_prices_display', 'total_price', 'storage_cost', 'days', 'warehouse_payment_display',
        'free_days_display', 'rate_display', 'services_summary_display', 'warehouse_services_display', 'line_services_display', 'carrier_services_display', 'company_services_display',
        'title_link_display'
    )
    # inlines = []  # Services managed through sections below
    fieldsets = (
        ('Основные данные', {
            'fields': (
                ('year', 'brand', 'vin', 'vehicle_type', 'weight_kg', 'status'),
                ('client', 'warehouse', 'unload_site'),
                ('has_title', 'title_link_display', 'title_notes'),
                ('is_important', 'notes'),
                ('unload_date', 'transfer_date'),
            )
        }),
        ('Линии', {
            'classes': ('collapse',),
            'fields': (
                'line',
                'line_services_display',
            )
        }),
        ('Склад', {
            'classes': ('collapse',),
            'fields': (
                'warehouse_services_display',
            )
        }),
        ('Перевозчик', {
            'classes': ('collapse',),
            'fields': (
                'carrier',
                'carrier_services_display',
            )
        }),
        ('Услуги компании', {
            'fields': (
                'company_services_display',
            )
        }),
        ('Финансы', {
            'fields': (
                'services_summary_display',
            )
        }),
    )
    actions = ['set_status_floating', 'set_status_in_port', 'set_status_unloaded', 'set_status_transferred', 'set_transferred_today', 'set_title_with_us', 'resend_car_unload_notification', 'resend_car_unload_telegram', 'export_selected_as_csv']

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'year' in form.base_fields:
            form.base_fields['year'].label = 'Год'
        if 'has_title' in form.base_fields:
            form.base_fields['has_title'].label = 'Тайтл'
        if 'title_notes' in form.base_fields:
            form.base_fields['title_notes'].widget.attrs['placeholder'] = 'Примечания к тайтлу...'
        if 'weight_kg' in form.base_fields:
            form.base_fields['weight_kg'].label = 'Масса, кг'
        if 'notes' in form.base_fields:
            from django import forms
            form.base_fields['notes'].widget = forms.Textarea(attrs={
                'rows': 2,
                'placeholder': 'Свободные примечания (видны во всплывающей подсказке у красного значка)...',
                'style': 'width:100%;',
            })
            # Лейбл «Примечания:» прячем — поле визуально стоит справа
            # от чекбокса «Важно», как «Примечания к тайтлу» рядом с «Тайтл»;
            # отдельная подпись делает столбец визуально шумным.
            form.base_fields['notes'].label = ''
        if 'is_important' in form.base_fields:
            form.base_fields['is_important'].label = 'Важно'
        # Inline-style на самом input: пусть растягивается на 100% своей
        # обёртки. Ширины обёрток выставлены в:
        #   * dashboard_admin.css (rule blocks `.field-year .form-multiline`,
        #     версия ?v=38 в base_site.html);
        #   * inline `<style>` блоке шаблона change_form.html через
        #     селекторы `:has(.field-X)` (на современных браузерах);
        #   * core/static/js/car_form_layout.js — JS-фолбэк через
        #     `style.setProperty('flex', ..., 'important')` для совсем
        #     старых браузеров без `:has()`.
        widget_styles = {
            'year':         'width:100% !important;min-width:0 !important;max-width:100% !important;',
            'brand':        'width:100% !important;min-width:0 !important;max-width:100% !important;',
            'vin':          'width:100% !important;min-width:0 !important;max-width:100% !important;',
            'vehicle_type': 'width:100% !important;min-width:0 !important;max-width:100% !important;',
            'weight_kg':    'width:100% !important;min-width:0 !important;max-width:100% !important;',
            'status':       'width:100% !important;min-width:0 !important;max-width:100% !important;',
        }
        for fname, css in widget_styles.items():
            if fname in form.base_fields:
                w = form.base_fields[fname].widget
                existing = w.attrs.get('style', '')
                w.attrs['style'] = (existing + ';' + css) if existing else css
        return form

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Обогащает контекст (изображение авто). БД не мутируем на GET.

        Актуальные дни и суммы рассчитываются динамически в
        `days_display` / `total_price_display` / `services_summary_display`,
        поэтому переписывать БД при каждом открытии карточки не требуется —
        это лишняя запись, провоцирующая post_save-сигналы и нарушающая
        принцип "GET не изменяет состояние".
        """
        if object_id:
            try:
                obj = self.get_object(request, object_id)
                if obj:
                    extra_context = extra_context or {}
                    # 1) картинка из БД (загружается через админку),
                    # 2) фолбэк на старые статические PNG, 3) заглушка.
                    url = find_car_model_image_url(obj.year, obj.brand)
                    if not url:
                        img_path = find_car_image(obj.year, obj.brand)
                        url = static(img_path) if img_path else static('icons/car_unknown.png')
                    extra_context['car_header_image'] = url
            except Exception:
                logger.warning("Car image lookup failed for %s", object_id, exc_info=True)

        return super().change_view(request, object_id, form_url, extra_context)

    def set_transferred_today(self, request, queryset):
        """Sets status to 'Transferred' and transfer date to today"""
        from django.utils import timezone

        today = timezone.now().date()
        updated = 0

        for car in queryset:
            car.status = 'TRANSFERRED'
            car.transfer_date = today
            car.save()
            updated += 1

        self.message_user(request, f"Статус изменён на 'Передан' для {updated} автомобилей. Дата передачи: {today}")
    set_transferred_today.short_description = "Передан сегодня"

    def default_warehouse_prices_display(self, obj):
        details = obj.warehouse_details()
        if "message" in details:
            return details["message"]
        html = '<table style="width:100%; border:1px solid #ddd; border-collapse:collapse;">'
        html += '<tr><th style="border:1px solid #ddd; padding:8px;">Поле</th><th style="border:1px solid #ddd; padding:8px;">Цена</th></tr>'
        for key, value in details.items():
            html += f'<tr><td style="border:1px solid #ddd; padding:8px;">{key}</td><td style="border:1px solid #ddd; padding:8px;">{value}</td></tr>'
        html += '</table>'
        return format_html(html)
    default_warehouse_prices_display.short_description = "Дефолтные цены на услуги склада"

    # --- Warehouse payment display ---
    def warehouse_payment_display(self, obj):
        return f"{obj.warehouse_payment_amount():.2f}"

    warehouse_payment_display.short_description = 'Оплата складу'


    def services_summary_display(self, obj):
        """Displays summary of all services with Caromoto Lithuania markup.

        ВАЖНО (производительность): раньше блок четырежды итерировал
        ``obj.car_services.all()`` (даже с prefetch это 4 прохода + 4
        отдельных filter() для warehouse/company с догрузкой). Также
        для company-сервисов делался отдельный SELECT на каждую услугу
        (CompanyService.objects.get).

        Сейчас:
          1. Один проход по prefetched car_services с распределением по
             категориям через словарь по service_type.
          2. Один батч-SELECT на CompanyService для всех company-услуг.
          3. distributed_markup считается прямо здесь без ещё одного aggregate.
        """
        from core.service_codes import is_ths_service

        line_services_list: list[tuple[str, Decimal]] = []
        warehouse_services_list: list[tuple[str, Decimal]] = []
        company_service_ids: list[int] = []
        company_services_raw: list[tuple[int, str, Decimal]] = []  # (svc_id, svc_name, price)

        line_total = Decimal('0.00')
        warehouse_total = Decimal('0.00')
        carrier_total = Decimal('0.00')
        company_total = Decimal('0.00')
        distributed_markup = Decimal('0.00')

        for cs in obj.car_services.all():
            price = Decimal(str(cs.final_price))
            distributed_markup += cs.markup_amount or Decimal('0')

            if cs.service_type == 'LINE' or is_ths_service(cs):
                line_total += price
                line_services_list.append((cs.get_service_name(), price))
            elif cs.service_type == 'WAREHOUSE':
                warehouse_total += price
                if not is_ths_service(cs):
                    warehouse_services_list.append((cs.get_service_name(), price))
            elif cs.service_type == 'CARRIER':
                carrier_total += price
            elif cs.service_type == 'COMPANY':
                company_total += price
                company_service_ids.append(cs.service_id)
                company_services_raw.append((cs.service_id, cs.get_service_name(), price))

        # Один батч-SELECT на CompanyService вместо N штук.
        company_names_by_id: dict[int, str] = {}
        if company_service_ids:
            company_qs = (
                CompanyService.objects
                .select_related('company')
                .filter(id__in=set(company_service_ids))
                .only('id', 'company__name')
            )
            company_names_by_id = {c.id: c.company.name for c in company_qs}

        # Paid days for display — динамический расчёт (как в days_display)
        if obj.warehouse and obj.unload_date:
            end_date = obj.transfer_date if obj.status == 'TRANSFERRED' and obj.transfer_date else timezone.now().date()
            total_days = (end_date - obj.unload_date).days + 1
            free_days_count = obj.warehouse.free_days or 0
            paid_days = max(0, total_days - free_days_count)
        else:
            paid_days = obj.days or 0

        # Total markup = только распределённая
        total_markup = distributed_markup

        # Base totals (без наценки)
        base_total = line_total + warehouse_total + carrier_total + company_total

        html = ['<div style="margin-top:15px; background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">']
        html.append('<h3 style="margin-top:0; color:#495057;">Сводка по услугам</h3>')

        html.append('<div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr 1fr; gap:15px; margin-bottom:20px;">')

        # Line services (THS, Shipping to Georgia etc.)
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги линий:</strong><br>')
        for name, price in line_services_list:
            html.append(f'<span style="font-size:13px; color:#6c757d;">{name}: {price:.2f}</span><br>')
        html.append(f'<span style="font-size:18px; color:#007bff; font-weight:bold;">Итого: {line_total:.2f}</span>')
        html.append('</div>')

        # Warehouse (без THS)
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги склада:</strong><br>')
        for name, price in warehouse_services_list:
            html.append(f'<span style="font-size:13px; color:#6c757d;">{name}: {price:.2f}</span><br>')
        if obj.warehouse:
            free_days = obj.warehouse.free_days or 0
            html.append(f'<span style="font-size:12px; color:#adb5bd;">Беспл. дней: {free_days}, Плат. дней: {paid_days}</span><br>')
        html.append(f'<span style="font-size:18px; color:#28a745; font-weight:bold;">Итого: {warehouse_total:.2f}</span>')
        html.append('</div>')

        # Carrier
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Перевозчик:</strong><br>')
        html.append(f'<span style="font-size:18px; color:#ffc107;">{carrier_total:.2f}</span>')
        html.append('</div>')

        # Companies
        html.append('<div style="background:white; padding:10px; border-radius:5px; border:1px solid #dee2e6;">')
        html.append('<strong>Услуги компаний:</strong><br>')
        for svc_id, svc_name, price in company_services_raw:
            company_name = company_names_by_id.get(svc_id, '?')
            html.append(f'<span style="font-size:13px; color:#6c757d;">{company_name}: {svc_name}: {price:.2f}</span><br>')
        html.append(f'<span style="font-size:18px; color:#6f42c1; font-weight:bold;">Итого: {company_total:.2f}</span>')
        html.append('</div>')

        # Markup - show distributed amount
        html.append('<div style="background:#fffde7; padding:10px; border-radius:5px; border:1px solid #ffc107;">')
        html.append('<strong style="color:#ff8f00;">Скрытая наценка:</strong><br>')
        html.append(f'<span style="font-size:18px; font-weight:bold; color:#ff8f00;">{distributed_markup:.2f}</span>')
        if distributed_markup > 0:
            html.append('<br><span style="font-size:11px; color:#666;">(распределена по услугам)</span>')
        else:
            html.append('<br><span style="font-size:11px; color:#666;">(введите в жёлтых полях)</span>')
        html.append('</div>')

        html.append('</div>')

        # Grand total
        total_with_markup = base_total + total_markup
        html.append('<div style="background:white; padding:15px; border-radius:5px; border:2px solid #6c757d;">')
        html.append('<strong style="color:#6c757d;">Итого к оплате:</strong><br>')
        html.append(f'<span style="font-size:20px; font-weight:bold; color:#495057;">{total_with_markup:.2f} EUR</span>')
        html.append('</div>')

        html.append('</div>')

        return format_html(''.join(html))
    services_summary_display.short_description = 'Сводка по услугам'

    def colored_status(self, obj):
        color = obj.get_status_color()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span>',
            color,
            obj.get_status_display()
        )
    colored_status.short_description = 'Статус'

    def vin_display(self, obj):
        unread = getattr(obj, '_emails_unread', None)
        if unread is None:
            unread = obj.email_links.filter(is_read=False).count() if obj.pk else 0
        need_reply = getattr(obj, '_emails_need_reply', 0) or 0

        if unread > 0:
            badge_bg, badge_title = '#dc2626', f'{unread} непрочитанных письма'
        else:
            badge_bg, badge_title = '#10b981', 'Непрочитанных писем нет'

        need_reply_html = ''
        if need_reply > 0:
            need_reply_html = format_html(
                '<span title="{}" style="background:#f97316;color:#fff;padding:1px 7px;'
                'border-radius:10px;font-size:11px;font-weight:700;min-width:20px;'
                'text-align:center;line-height:16px;font-variant-numeric:tabular-nums;">🚩 {}</span>',
                f'{need_reply} письмо(-а) ждут ответа',
                need_reply,
            )

        # Красный треугольник для авто, помеченных как «Важное».
        # При наведении показываем полный текст примечаний (data-важно для
        # длинных текстов: используем кастомный tooltip через JS, см.
        # change_list.html → блок extrastyle).
        important_html = ''
        if obj.is_important:
            tooltip_text = (obj.notes or '').strip() or 'Авто помечено как важное (примечаний нет)'
            important_html = format_html(
                '<span class="cm-important-flag" data-tip="{tip}" '
                'style="display:inline-flex;align-items:center;justify-content:center;'
                'width:22px;height:22px;cursor:help;" '
                'title="{tip_short}">'
                '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" '
                'fill="#dc2626" stroke="#7f1d1d" stroke-width="1.5" stroke-linejoin="round">'
                '<path d="M12 2 L22 20 L2 20 Z"/>'
                '<line x1="12" y1="9" x2="12" y2="14" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/>'
                '<circle cx="12" cy="17" r="1.2" fill="#fff" stroke="none"/>'
                '</svg>'
                '</span>',
                tip=tooltip_text,
                tip_short=tooltip_text[:200],
            )

        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px;">'
            '<span class="vin-copy-wrap">'
            '<span class="vin-copy-btn" data-vin="{vin}" title="Копировать VIN">'
            '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
            '</svg>'
            '</span> {vin}</span>'
            '<span title="{badge_title}" style="background:{badge_bg};color:#fff;padding:1px 7px;'
            'border-radius:10px;font-size:11px;font-weight:700;min-width:20px;'
            'text-align:center;line-height:16px;font-variant-numeric:tabular-nums;">{unread}</span>'
            '{need_reply_html}'
            '{important_html}'
            '</span>',
            vin=obj.vin,
            unread=unread,
            badge_bg=badge_bg,
            badge_title=badge_title,
            need_reply_html=need_reply_html,
            important_html=important_html,
        )
    vin_display.short_description = 'VIN'
    vin_display.admin_order_field = 'vin'

    def container_display(self, obj):
        """Displays container number with clickable link and status-based styling"""
        if not obj.container:
            return '-'

        # Use car status for color (like status)
        color = obj.get_status_color()

        # Create link to container
        container_url = f'/admin/core/container/{obj.container.id}/change/'

        return format_html(
            '<a href="{}" target="_blank" style="text-decoration: none;"><span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px;">{}</span></a>',
            container_url,
            color,
            obj.container.number
        )
    container_display.short_description = 'Контейнер'
    container_display.admin_order_field = 'container__number'

    def year_display(self, obj):
        return obj.year
    year_display.short_description = 'Год'
    year_display.admin_order_field = 'year'

    def unload_date_display(self, obj):
        return obj.unload_date
    unload_date_display.short_description = 'Дата разгрузки'
    unload_date_display.admin_order_field = 'unload_date'

    def transfer_date_display(self, obj):
        return obj.transfer_date
    transfer_date_display.short_description = 'Передан'
    transfer_date_display.admin_order_field = 'transfer_date'

    def storage_cost_display(self, obj):
        """Shows storage cost calculated from warehouse fields"""
        try:
            storage_cost = obj.calculate_storage_cost()
            return f"{storage_cost:.2f}"
        except Exception as e:
            logger.error(f"Ошибка расчета стоимости хранения: {e}")
            return f"{obj.storage_cost:.2f}"  # Fallback to old field
    storage_cost_display.short_description = 'Хран'
    storage_cost_display.admin_order_field = 'storage_cost'

    def days_display(self, obj):
        """Shows paid days accounting for free days from warehouse"""
        if obj.warehouse and obj.unload_date:
            # Calculate total storage days
            end_date = obj.transfer_date if obj.status == 'TRANSFERRED' and obj.transfer_date else timezone.now().date()
            total_days = (end_date - obj.unload_date).days + 1

            free_days = obj.warehouse.free_days or 0
            chargeable_days = max(0, total_days - free_days)
            return f"{chargeable_days} (из {total_days})"
        return obj.days if hasattr(obj, 'days') else 0
    days_display.short_description = 'Плат.дн.'
    days_display.admin_order_field = 'days'

    def total_price_display(self, obj):
        # For non-transferred cars calculate price dynamically
        if obj.status != 'TRANSFERRED':
            from decimal import Decimal

            # Calculate services total from preloaded car_services (no extra queries)
            base_total = Decimal('0.00')
            for cs in obj.car_services.all():
                base_total += Decimal(str(cs.final_price))

            # Use _total_markup annotation from get_queryset (if available)
            if hasattr(obj, '_total_markup') and obj._total_markup is not None:
                distributed_markup = obj._total_markup
            else:
                distributed_markup = sum(
                    (cs.markup_amount for cs in obj.car_services.all() if cs.markup_amount),
                    Decimal('0.00')
                )

            total = base_total + distributed_markup
            return f"{total:.2f}"

        return f"{obj.total_price:.2f}"
    total_price_display.short_description = 'Цена'
    total_price_display.admin_order_field = 'total_price'

    def markup_display(self, obj):
        """Displays total hidden markup for car.

        Uses annotation from get_queryset for performance.
        If annotation not available (car card), makes direct query.
        """
        # If annotation from get_queryset exists - use it (for list)
        if hasattr(obj, '_total_markup'):
            return f"{obj._total_markup:.2f}" if obj._total_markup else "0.00"

        # Fallback for car card or if annotation failed
        from django.db.models import Sum

        total_markup = obj.car_services.aggregate(
            total=Sum('markup_amount')
        )['total'] or Decimal('0.00')

        return f"{total_markup:.2f}"

    markup_display.short_description = 'Н'  # Н = Наценка
    markup_display.admin_order_field = '_total_markup'  # Sort by annotation

    def title_attached_display(self, obj):
        """Иконка-ссылка на скан тайтла рядом с чекбоксом has_title (узкая колонка)."""
        try:
            has_file = bool(obj.title_scan and obj.title_scan.name)
        except Exception:
            has_file = False
        if not has_file:
            return ''
        return format_html(
            '<a href="{}" target="_blank" title="Открыть скан тайтла" '
            'style="text-decoration:none;font-size:14px;">📎</a>',
            obj.title_scan.url,
        )
    title_attached_display.short_description = ''

    def title_link_display(self, obj):
        """Иконка-ссылка на скан тайтла в карточке авто, справа от чекбокса."""
        try:
            has_file = bool(obj and obj.pk and obj.title_scan and obj.title_scan.name)
        except Exception:
            has_file = False
        if not has_file:
            return ''
        return format_html(
            '<a href="{}" target="_blank" title="Открыть скан тайтла" '
            'style="text-decoration:none;font-size:18px;">📎</a>',
            obj.title_scan.url,
        )
    title_link_display.short_description = ''

    def changelist_view(self, request, extra_context=None):
        """Override changelist_view to add total markup sum.

        Calculates total markup sum for DISPLAYED cars on the page
        and passes it to template context.
        """
        from django.db.models import Sum

        # Call parent method to get response
        response = super().changelist_view(request, extra_context)

        try:
            # Get changelist from response
            cl = response.context_data['cl']

            # Get filtered queryset (cars being displayed)
            queryset = cl.get_queryset(request)

            # Calculate total markup sum for displayed cars.
            # IMPORTANT: queryset already has annotation _total_markup from get_queryset().
            # We must aggregate on the annotation, NOT on 'car_services__markup_amount',
            # because aggregate() on the same relation as an existing annotation
            # generates a broken subquery that halves the result.
            total_markup_sum = queryset.aggregate(
                total=Sum('_total_markup')
            )['total'] or Decimal('0.00')

            # Add to context
            response.context_data['total_markup_sum'] = total_markup_sum

        except (AttributeError, KeyError):
            # If something went wrong, just don't add the sum
            pass

        return response

    def free_days_display(self, obj):
        """Shows free days from warehouse"""
        if obj.warehouse:
            return obj.warehouse.free_days
        return obj.free_days  # Fallback to old field
    free_days_display.short_description = 'FREE'
    free_days_display.admin_order_field = 'free_days'

    def rate_display(self, obj):
        """Shows daily rate from warehouse 'Storage' service"""
        if obj.warehouse:
            daily_rate = obj._get_storage_daily_rate()
            return f"{daily_rate:.2f}"
        return "0.00"
    rate_display.short_description = 'Ставка/день'

    def set_status_floating(self, request, queryset):
        updated = queryset.update(status='FLOATING')
        for obj in Car.objects.filter(pk__in=queryset.values_list('pk', flat=True)):
            obj.calculate_total_price()
            Car.objects.filter(pk=obj.pk).update(days=obj.days, storage_cost=obj.storage_cost, total_price=obj.total_price)
        self.message_user(request, f"Статус изменён на 'В пути' для {updated} автомобилей.")
    set_status_floating.short_description = "Изменить статус на В пути"

    def set_status_in_port(self, request, queryset):
        updated = queryset.update(status='IN_PORT')
        for obj in Car.objects.filter(pk__in=queryset.values_list('pk', flat=True)):
            obj.calculate_total_price()
            Car.objects.filter(pk=obj.pk).update(days=obj.days, storage_cost=obj.storage_cost, total_price=obj.total_price)
        self.message_user(request, f"Статус изменён на 'В порту' для {updated} автомобилей.")
    set_status_in_port.short_description = "Изменить статус на В порту"

    def set_status_unloaded(self, request, queryset):
        updated = 0
        for obj in Car.objects.filter(pk__in=queryset.values_list('pk', flat=True)):
            if obj.warehouse and obj.unload_date:
                obj.status = 'UNLOADED'
                obj.calculate_total_price()
                Car.objects.filter(pk=obj.pk).update(status=obj.status, days=obj.days, storage_cost=obj.storage_cost, total_price=obj.total_price)
                updated += 1
            else:
                self.message_user(request, f"Автомобиль {obj.vin} не обновлён: требуются поля 'Склад' и 'Дата разгрузки'.", level='warning')
        self.message_user(request, f"Статус изменён на 'Разгружен' для {updated} автомобилей.")
    set_status_unloaded.short_description = "Изменить статус на Разгружен"

    def set_status_transferred(self, request, queryset):
        car_pks = list(queryset.values_list('pk', flat=True))
        updated = queryset.update(status='TRANSFERRED')
        for obj in Car.objects.filter(pk__in=car_pks):
            if not obj.transfer_date:
                obj.transfer_date = timezone.now().date()
            obj.calculate_total_price()
            Car.objects.filter(pk=obj.pk).update(transfer_date=obj.transfer_date, days=obj.days, storage_cost=obj.storage_cost, total_price=obj.total_price)
        self.message_user(request, f"Статус изменён на 'Передан' для {updated} автомобилей.")
    set_status_transferred.short_description = "Изменить статус на Передан"

    def set_title_with_us(self, request, queryset):
        logger.info(f"Setting has_title=True for {queryset.count()} cars")
        updated = queryset.update(has_title=True)
        self.message_user(request, f"Тайтл установлен как 'У нас' для {updated} автомобилей.")
    set_title_with_us.short_description = "Тайтл у нас"

    def resend_car_unload_notification(self, request, queryset):
        """Повторная отправка уведомления о разгрузке для ТС без контейнера"""
        from core.services.email_service import CarNotificationService

        sent = 0
        skipped = 0

        for car in queryset.select_related('client', 'warehouse'):
            if car.container_id:
                skipped += 1
                continue
            if not car.unload_date:
                self.message_user(request, f"ТС {car.vin}: не указана дата разгрузки", level='WARNING')
                continue
            if not car.client:
                self.message_user(request, f"ТС {car.vin}: не указан клиент", level='WARNING')
                continue

            if CarNotificationService.send_car_unload_notification(car, user=request.user):
                sent += 1
            else:
                self.message_user(request, f"ТС {car.vin}: не удалось отправить уведомление", level='WARNING')

        if sent:
            self.message_user(request, f"Уведомления отправлены для {sent} ТС.")
        if skipped:
            self.message_user(request, f"Пропущено {skipped} ТС (привязаны к контейнеру).", level='WARNING')
    resend_car_unload_notification.short_description = "📧 Повторить уведомление о разгрузке ТС"

    def resend_car_unload_telegram(self, request, queryset):
        """Повторная отправка уведомления о разгрузке ТС (без контейнера) в Telegram"""
        from core.services.telegram_service import TelegramNotificationService

        sent = 0
        skipped = 0

        for car in queryset.select_related('client', 'warehouse'):
            if car.container_id:
                skipped += 1
                continue
            if not car.unload_date:
                self.message_user(request, f"ТС {car.vin}: не указана дата разгрузки", level='WARNING')
                continue
            if not car.client:
                self.message_user(request, f"ТС {car.vin}: не указан клиент", level='WARNING')
                continue

            if TelegramNotificationService.send_car_unload_notification(car, user=request.user):
                sent += 1
            else:
                self.message_user(request, f"ТС {car.vin}: Telegram не отправлен (нет chat_id/выключен)", level='WARNING')

        if sent:
            self.message_user(request, f"Telegram-уведомления отправлены для {sent} ТС.")
        if skipped:
            self.message_user(request, f"Пропущено {skipped} ТС (привязаны к контейнеру).", level='WARNING')
    resend_car_unload_telegram.short_description = "📨 Telegram: уведомить о разгрузке ТС"

    class Media:
        # NB: dashboard_admin.css уже подключается в templates/admin/base_site.html.
        # Раньше он дублировался здесь и грузился ПОСЛЕ inline <style> в
        # change_form.html — из-за чего любые наши правки в шаблоне молча
        # перетирались дефолтным `.form-multiline > div { flex:1 1 0 }`.
        # Поэтому в Media только JS — CSS приходит из base_site.
        js = (
            'js/htmx.min.js', 'js/logist2_htmx.js', 'js/warehouse_address.js',
            # Раскладка первой строки + рядов «Тайтл / Важно»: inline-style
            # на обёртках flex-элементов. CSS-каскад в форме ненадёжен
            # (responsive.css media-queries сжимают поля). См. файл.
            'js/car_form_layout.js?v=9',
        )

    def save_model(self, request, obj, form, change):
        """Saves model with service field processing (wrapped in transaction)"""
        with transaction.atomic():
            self._save_model_inner(request, obj, form, change)

    # ----- helpers вынесены ниже метода _save_model_inner --------

    def _process_removed_services(self, request, obj):
        """Сканирует POST на пометки `remove_<prefix>_service_<id>=1`,
        удаляет соответствующие CarService и регистрирует в blacklist
        DeletedCarService. Возвращает множество ключей вида
        ``"<prefix>_<id>"`` для дальнейших проверок.
        """
        removed = set()
        prefix_to_type = {
            'warehouse': 'WAREHOUSE',
            'line': 'LINE',
            'carrier': 'CARRIER',
            'company': 'COMPANY',
        }
        for key, value in request.POST.items():
            if value != '1':
                continue
            for prefix, svc_type in prefix_to_type.items():
                marker = f'remove_{prefix}_service_'
                if not key.startswith(marker):
                    continue
                service_id = key[len(marker):]
                removed.add(f'{prefix}_{service_id}')
                try:
                    CarService.objects.filter(
                        car=obj, service_type=svc_type, service_id=service_id
                    ).delete()
                    DeletedCarService.objects.get_or_create(
                        car=obj, service_type=svc_type, service_id=service_id,
                    )
                except Exception:
                    logger.exception("Error deleting %s service %s", prefix, service_id)
                break
        return removed

    def _update_existing_carservices(self, request, obj, *, prefix, service_type, removed_services):
        """Обновляет custom_price/markup_amount у существующих CarService
        по полям ``<prefix>_service_<id>`` / ``markup_<prefix>_service_<id>``
        из POST-запроса. Возвращает QuerySet существующих CarService этого
        типа (для дальнейшего использования в auto-add блоке).
        """
        existing_qs = CarService.objects.filter(car=obj, service_type=service_type)
        for car_service in existing_qs:
            if f'{prefix}_{car_service.service_id}' in removed_services:
                continue
            field_name = f'{prefix}_service_{car_service.service_id}'
            if field_name not in request.POST:
                continue
            value = request.POST.get(field_name)
            if value:
                try:
                    car_service.custom_price = float(value)
                except (ValueError, TypeError):
                    pass
            markup_field = f'markup_{prefix}_service_{car_service.service_id}'
            markup_value = request.POST.get(markup_field)
            if markup_value is not None:
                try:
                    car_service.markup_amount = float(markup_value) if markup_value else 0
                except (ValueError, TypeError):
                    car_service.markup_amount = 0
            car_service.save()
        return existing_qs

    def _auto_add_default_services(
        self, request, obj, *, prefix, service_type, catalog_model,
        related_field, related_value, removed_services, existing_qs,
    ):
        """Автодобавление дефолтных услуг провайдера при создании авто
        или смене провайдера (warehouse/line/carrier).

        :param catalog_model: модель каталога (WarehouseService/LineService/...).
        :param related_field: имя FK на провайдере в каталоге
            (``warehouse``/``line``/``carrier``).
        :param related_value: текущий провайдер (``obj.warehouse`` и т.п.);
            если None — выходим без действий.
        """
        if related_value is None:
            return
        new_service_ids = set(
            catalog_model.objects.filter(**{related_field: related_value})
            .values_list('id', flat=True)
        )
        DeletedCarService.objects.filter(
            car=obj, service_type=service_type
        ).exclude(service_id__in=new_service_ids).delete()
        services = catalog_model.objects.filter(
            **{related_field: related_value},
            is_active=True,
            add_by_default=True,
        ).only('id', 'default_price', 'default_markup')
        existing_ids = set(existing_qs.values_list('service_id', flat=True))
        blacklisted = set(
            DeletedCarService.objects.filter(
                car=obj, service_type=service_type
            ).values_list('service_id', flat=True)
        )
        for service in services:
            if f'{prefix}_{service.id}' in removed_services:
                continue
            if service.id in blacklisted:
                continue
            if service.id in existing_ids:
                continue
            field_name = f'{prefix}_service_{service.id}'
            value = request.POST.get(field_name) or service.default_price
            default_markup = getattr(service, 'default_markup', 0) or 0
            CarService.objects.create(
                car=obj,
                service_type=service_type,
                service_id=service.id,
                custom_price=float(value),
                markup_amount=float(default_markup),
            )

    def _save_model_inner(self, request, obj, form, change):
        """Внутренний метод save_model, выполняемый внутри transaction.atomic().

        Включаем ``_bulk_updating`` — это глушит пересчёт цены автомобиля
        внутри post_save/post_delete CarService (чтобы не делать N
        пересчётов за одно сохранение карточки). Один итоговый пересчёт
        делается в конце метода (плюс тарифная секция, если применимо).

        Логика обработки услуг (warehouse/line/carrier/company)
        вынесена в три helper'а:
          1. ``_process_removed_services`` — обработать `remove_*` поля.
          2. ``_update_existing_carservices`` — синхронизировать
             custom_price/markup существующих CarService.
          3. ``_auto_add_default_services`` — добавить дефолтные услуги
             поставщика при создании авто или смене провайдера.

        Раньше эти три шага были инлайнены 4 раза подряд (~250 строк
        копипасты), что и было ядром пункта #10 из плана улучшений.
        """
        super().save_model(request, obj, form, change)
        obj._bulk_updating = True

        removed_services = self._process_removed_services(request, obj)
        logger.debug("Removed services: %s", removed_services)

        changed_data = getattr(form, 'changed_data', []) if form else []

        # WAREHOUSE
        existing_warehouse_qs = self._update_existing_carservices(
            request, obj, prefix='warehouse', service_type='WAREHOUSE',
            removed_services=removed_services,
        )
        if (not change) or 'warehouse' in changed_data:
            self._auto_add_default_services(
                request, obj,
                prefix='warehouse', service_type='WAREHOUSE',
                catalog_model=WarehouseService,
                related_field='warehouse', related_value=obj.warehouse,
                removed_services=removed_services,
                existing_qs=existing_warehouse_qs,
            )

        # LINE (включая THS)
        existing_line_qs = self._update_existing_carservices(
            request, obj, prefix='line', service_type='LINE',
            removed_services=removed_services,
        )
        if (not change) or 'line' in changed_data:
            self._auto_add_default_services(
                request, obj,
                prefix='line', service_type='LINE',
                catalog_model=LineService,
                related_field='line', related_value=obj.line,
                removed_services=removed_services,
                existing_qs=existing_line_qs,
            )

        # CARRIER
        existing_carrier_qs = self._update_existing_carservices(
            request, obj, prefix='carrier', service_type='CARRIER',
            removed_services=removed_services,
        )
        if (not change) or 'carrier' in changed_data:
            self._auto_add_default_services(
                request, obj,
                prefix='carrier', service_type='CARRIER',
                catalog_model=CarrierService,
                related_field='carrier', related_value=obj.carrier,
                removed_services=removed_services,
                existing_qs=existing_carrier_qs,
            )

        # COMPANY: блок auto-add отсутствует (компания не привязана к Car).
        self._update_existing_carservices(
            request, obj, prefix='company', service_type='COMPANY',
            removed_services=removed_services,
        )

        # Recalculate storage cost and days when warehouse changes
        if change and form and 'warehouse' in getattr(form, 'changed_data', []):
            logger.debug(f"Склад изменился для автомобиля {obj.vin}, пересчитываем стоимость хранения")
            try:
                # Update fields based on new warehouse
                obj.update_days_and_storage()
                obj.calculate_total_price()
                # Save updated fields
                obj.save(update_fields=['storage_cost', 'days', 'total_price'])
                logger.debug(f"Обновлены поля: storage_cost={obj.storage_cost}, days={obj.days}")
            except Exception as e:
                logger.error(f"Ошибка при пересчете стоимости хранения: {e}")

        # Применяем тариф клиента (FIXED / FLEXIBLE) только когда реально
        # изменилось что-то, влияющее на распределение: клиент / склад /
        # линия / перевозчик, или были правки самих услуг (любые поля
        # *_service_* / markup_* / remove_* в POST).
        # Это убирает лишний пересчёт при сохранении карточки авто без
        # изменений (например, после смены статуса в list_editable).
        client_cleared = change and 'client' in changed_data and not obj.client
        services_touched = any(
            key.startswith(prefix)
            for key in request.POST.keys()
            for prefix in (
                'warehouse_service_', 'line_service_',
                'carrier_service_', 'company_service_',
                'markup_warehouse_service_', 'markup_line_service_',
                'markup_carrier_service_', 'markup_company_service_',
                'remove_warehouse_service_', 'remove_line_service_',
                'remove_carrier_service_', 'remove_company_service_',
            )
        )
        deps_touched = (
            not change
            or any(f in changed_data for f in ('client', 'warehouse', 'line', 'carrier'))
            or services_touched
            or client_cleared
        )
        if obj.status != 'TRANSFERRED' and deps_touched:
            client = obj.client
            try:
                from core.services.car_service_manager import apply_client_tariff_for_car
                if (client and client.tariff_type in ('FIXED', 'FLEXIBLE')) or client_cleared:
                    apply_client_tariff_for_car(obj)
                    obj.calculate_total_price()
                    Car.objects.filter(pk=obj.pk).update(total_price=obj.total_price)
            except Exception:
                logger.exception("Ошибка при пересчете тарифа клиента для car=%s", obj.pk)

        # Финальный пересчёт цены авто после всех манипуляций с CarService.
        # Сигналы CarService.post_save/post_delete были заглушены флагом
        # `_bulk_updating` — делаем один итоговый UPDATE здесь.
        obj._bulk_updating = False
        try:
            obj.calculate_total_price()
            Car.objects.filter(pk=obj.pk).update(
                total_price=obj.total_price,
                days=obj.days,
                storage_cost=obj.storage_cost,
            )
        except Exception:
            logger.exception("Ошибка финального пересчёта цены авто %s", obj.pk)

    def warehouse_services_display(self, obj):
        """Displays editable fields for all warehouse services"""
        try:
            car_services = list(CarService.objects.filter(
                car=obj,
                service_type='WAREHOUSE'
            ))

            service_ids = [cs.service_id for cs in car_services]
            services_by_id = {
                s.id: s
                for s in WarehouseService.objects
                .select_related('warehouse')
                .filter(id__in=service_ids)
            }
            costs_map = _load_supplier_costs_map([cs.pk for cs in car_services])

            html = '<div class="cm-svc-grid">'

            if car_services:
                for car_service in car_services:
                    service = services_by_id.get(car_service.service_id)
                    if service is None:
                        continue
                    try:
                        current_value = car_service.custom_price if car_service.custom_price is not None else service.default_price
                        markup_value = car_service.markup_amount or 0
                        warehouse_name = service.warehouse.name

                        variant = "cm-svc-card--warehouse" if (obj.warehouse and service.warehouse.id == obj.warehouse.id) else "cm-svc-card--warehouse-other"

                        cost_badge = _cost_badge_html(car_service.pk, current_value, costs_map=costs_map)
                        html += f'''
                        <div class="cm-svc-card {variant}">
                            <button type="button" onclick="removeService({service.id}, 'warehouse')" class="cm-svc-remove">×</button>
                            <div class="cm-svc-provider">📦 {warehouse_name}</div>
                            <div class="cm-svc-name">{service.name}</div>
                            <div class="cm-svc-inputs">
                                <input type="number" name="warehouse_service_{service.id}" value="{current_value}" step="0.01" title="Цена услуги">
                                <span>+</span>
                                <input type="number" name="markup_warehouse_service_{service.id}" value="{markup_value}" step="0.01" class="cm-svc-markup" title="Скрытая наценка" placeholder="0">
                            </div>
                            {cost_badge}
                            <input type="hidden" name="remove_warehouse_service_{service.id}" id="remove_warehouse_service_{service.id}" value="">
                        </div>
                        '''
                    except Exception:
                        logger.exception("Skipping warehouse service in display rendering")
                        continue

            html += '</div>'

            # Button to add services - always available
            html += '''
            <div style="margin-top: 10px;">
                <button type="button" class="add-service-btn" onclick="openModal('warehouseServicesModal', 'warehouse')" title="Добавить услуги любого склада">
                    +
                </button>
                <span style="margin-left: 5px; color: #666;">Добавить услуги склада</span>
            </div>
            '''

            # JavaScript for removing services
            html += '''
            <script>
            function removeService(serviceId, serviceType) {
                const serviceDiv = event.target.closest('div');
                serviceDiv.style.display = 'none';
                const hiddenField = document.getElementById('remove_' + serviceType + '_service_' + serviceId);
                hiddenField.value = '1';
                console.log('Removed service:', serviceType, serviceId, 'Field value:', hiddenField.value);
            }
            </script>
            '''

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    warehouse_services_display.short_description = "Услуги склада"

    def line_services_display(self, obj):
        """Displays editable fields for line services"""
        if not obj.line:
            return "Линия не выбрана"

        try:
            car_services = list(CarService.objects.filter(
                car=obj,
                service_type='LINE'
            ))

            service_ids = [cs.service_id for cs in car_services]
            services_by_id = {
                s.id: s for s in LineService.objects.filter(id__in=service_ids)
            }
            costs_map = _load_supplier_costs_map([cs.pk for cs in car_services])

            html = '<div class="cm-svc-grid">'

            for car_service in car_services:
                service = services_by_id.get(car_service.service_id)
                if service is None:
                    continue
                try:
                    current_value = car_service.custom_price if car_service.custom_price is not None else service.default_price
                    markup_value = car_service.markup_amount or 0

                    cost_badge = _cost_badge_html(car_service.pk, current_value, costs_map=costs_map)
                    html += f'''
                    <div class="cm-svc-card cm-svc-card--line">
                        <button type="button" onclick="removeService({service.id}, 'line')" class="cm-svc-remove">×</button>
                        <div class="cm-svc-name">{service.name}</div>
                        <div class="cm-svc-inputs">
                            <input type="number" name="line_service_{service.id}" value="{current_value}" step="0.01" title="Цена услуги">
                            <span>+</span>
                            <input type="number" name="markup_line_service_{service.id}" value="{markup_value}" step="0.01" class="cm-svc-markup" title="Скрытая наценка" placeholder="0">
                        </div>
                        {cost_badge}
                        <input type="hidden" name="remove_line_service_{service.id}" id="remove_line_service_{service.id}" value="">
                    </div>
                    '''
                except Exception:
                    logger.exception("Skipping line service in display rendering")
                    continue

            html += '</div>'

            # Button to add new services
            if obj.line:
                html += '''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('lineServicesModal', 'line')" title="Добавить услуги линии">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги линии</span>
                </div>
                '''

            if not car_services:
                html += '<div style="margin-top: 8px; color: #6c757d;">Услуги еще не добавлены. Используйте кнопку "+".</div>'

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    line_services_display.short_description = "Услуги линии"

    def carrier_services_display(self, obj):
        """Displays editable fields for carrier services"""
        if not obj.carrier:
            return "Перевозчик не выбран"

        try:
            car_services = list(CarService.objects.filter(
                car=obj,
                service_type='CARRIER'
            ))

            service_ids = [cs.service_id for cs in car_services]
            services_by_id = {
                s.id: s for s in CarrierService.objects.filter(id__in=service_ids)
            }
            costs_map = _load_supplier_costs_map([cs.pk for cs in car_services])

            html = '<div class="cm-svc-grid">'

            for car_service in car_services:
                service = services_by_id.get(car_service.service_id)
                if service is None:
                    continue
                try:
                    current_value = car_service.custom_price if car_service.custom_price is not None else service.default_price
                    markup_value = car_service.markup_amount or 0

                    cost_badge = _cost_badge_html(car_service.pk, current_value, costs_map=costs_map)
                    html += f'''
                    <div class="cm-svc-card cm-svc-card--carrier">
                        <button type="button" onclick="removeService({service.id}, 'carrier')" class="cm-svc-remove">×</button>
                        <div class="cm-svc-name">{service.name}</div>
                        <div class="cm-svc-inputs">
                            <input type="number" name="carrier_service_{service.id}" value="{current_value}" step="0.01" title="Цена услуги">
                            <span>+</span>
                            <input type="number" name="markup_carrier_service_{service.id}" value="{markup_value}" step="0.01" class="cm-svc-markup" title="Скрытая наценка" placeholder="0">
                        </div>
                        {cost_badge}
                        <input type="hidden" name="remove_carrier_service_{service.id}" id="remove_carrier_service_{service.id}" value="">
                    </div>
                    '''
                except Exception:
                    logger.exception("Skipping carrier service in display rendering")
                    continue

            html += '</div>'

            # Button to add new services
            if obj.carrier:
                html += '''
                <div style="margin-top: 10px;">
                    <button type="button" class="add-service-btn" onclick="openModal('carrierServicesModal', 'carrier')" title="Добавить услуги перевозчика">
                        +
                    </button>
                    <span style="margin-left: 5px; color: #666;">Добавить услуги перевозчика</span>
                </div>
                '''

            if not car_services:
                html += '<div style="margin-top: 8px; color: #6c757d;">Услуги еще не добавлены. Используйте кнопку "+".</div>'

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    carrier_services_display.short_description = "Услуги перевозчика"

    def company_services_display(self, obj):
        """Displays editable fields for company services"""
        try:
            car_services = list(CarService.objects.filter(
                car=obj,
                service_type='COMPANY'
            ))

            service_ids = [cs.service_id for cs in car_services]
            services_by_id = {
                s.id: s
                for s in CompanyService.objects
                .select_related('company')
                .filter(id__in=service_ids)
            }
            costs_map = _load_supplier_costs_map([cs.pk for cs in car_services])

            html = '<div class="cm-svc-grid">'

            if car_services:
                for car_service in car_services:
                    service = services_by_id.get(car_service.service_id)
                    if service is None:
                        continue
                    try:
                        current_value = car_service.custom_price if car_service.custom_price is not None else service.default_price
                        markup_value = car_service.markup_amount or 0

                        cost_badge = _cost_badge_html(car_service.pk, current_value, costs_map=costs_map)
                        html += f'''
                        <div class="cm-svc-card cm-svc-card--company">
                            <button type="button" onclick="removeService({service.id}, 'company')" class="cm-svc-remove">×</button>
                            <div class="cm-svc-provider">🏢 {service.company.name}</div>
                            <div class="cm-svc-name">{service.name}</div>
                            <div class="cm-svc-inputs">
                                <input type="number" name="company_service_{service.id}" value="{current_value}" step="0.01" title="Цена услуги">
                                <span>+</span>
                                <input type="number" name="markup_company_service_{service.id}" value="{markup_value}" step="0.01" class="cm-svc-markup" title="Скрытая наценка" placeholder="0">
                            </div>
                            {cost_badge}
                            <input type="hidden" name="remove_company_service_{service.id}" id="remove_company_service_{service.id}" value="">
                        </div>
                        '''
                    except Exception:
                        logger.exception("Skipping company service in display rendering")
                        continue

            html += '</div>'

            html += '''
            <div style="margin-top: 10px;">
                <button type="button" class="add-service-btn" onclick="openModal('companyServicesModal', 'company')" title="Добавить услуги компании">
                    +
                </button>
                <span style="margin-left: 5px; color: #666;">Добавить услуги компании</span>
            </div>
            '''

            return mark_safe(html)
        except Exception as e:
            return f"Ошибка загрузки услуг: {e}"
    company_services_display.short_description = "Услуги компаний"

    def get_changelist(self, request, **kwargs):
        """Adds default filtering for statuses 'In Port' and 'Unloaded'"""
        if not request.GET.get('status_multi'):
            get_params = request.GET.copy()
            get_params.setlist('status_multi', ['IN_PORT', 'UNLOADED'])
            request.GET = get_params
        return super().get_changelist(request, **kwargs)
