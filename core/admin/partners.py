import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.db import models
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from core.admin._balance_display import annotate_partner_balance, render_total_balance
from core.admin._service_handling import process_service_fields
from core.admin.inlines import (
    CarrierDriverInline,
    CarrierServiceInline,
    CarrierTruckInline,
    ClientTariffRateInline,
    CompanyServiceInline,
    LineServiceInline,
    LineTHSCoefficientInline,
    WarehouseServiceInline,
)
from core.forms import CarrierForm, LineForm
from core.models import (
    AutoTransport,
    Car,
    Carrier,
    CarrierDriver,
    CarrierService,
    CarrierTruck,
    Client,
    Company,
    Container,
    CounterpartyBankAccount,
    Line,
    LineService,
    Warehouse,
    WarehouseService,
)
from core.models_billing import NewInvoice

logger = logging.getLogger(__name__)


# ==============================================================================
# Общее для всех контрагентов: счета + раскладка реквизитов
# ==============================================================================


class CounterpartyBankAccountInline(GenericTabularInline):
    """Банковские счета контрагента — свёрнутый блок на карточке."""

    model = CounterpartyBankAccount
    extra = 0
    classes = ["collapse"]
    fields = ("bank_name", "iban", "swift", "currency", "is_primary", "notes")
    verbose_name = "Счёт"
    verbose_name_plural = "Счета контрагента"


# Единая раскладка реквизитов в «Основной информации» — два ряда:
# название + регистрационные коды → страна/адрес/контакты/сайт.
_REQUISITES_FIELDS = (
    ("name", "imones_kodas", "vat_code", "eori_code"),
    ("registration_country", "physical_address", "phone", "general_email", "website"),
)


# ==============================================================================
# WarehouseAdmin
# ==============================================================================


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "free_days", "balance_display")
    list_filter = ("free_days",)
    search_fields = ("name", "address", "imones_kodas", "vat_code")
    readonly_fields = ("balance",)
    exclude = (
        "default_unloading_fee",
        "delivery_to_warehouse",
        "loading_on_trawl",
        "documents_fee",
        "transfer_fee",
        "transit_declaration",
        "export_declaration",
        "additional_expenses",
        "complex_fee",
    )
    inlines = [CounterpartyBankAccountInline, WarehouseServiceInline]
    fieldsets = (
        ("Основные данные", {"classes": ("cm-requisites",), "fields": _REQUISITES_FIELDS}),
        (
            "Площадки",
            {
                "fields": (
                    ("address_name", "address"),
                    ("address2_name", "address2"),
                    ("address3_name", "address3"),
                )
            },
        ),
        (
            "Настройки хранения",
            {
                "fields": ("free_days",),
                "description": 'Бесплатные дни хранения. Ставка за сутки берётся из услуги "Хранение" в списке услуг склада.',
            },
        ),
        ("Баланс", {"fields": ("balance",), "description": "Баланс склада"}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return annotate_partner_balance(qs, "warehouse")

    def get_urls(self):
        custom_urls = [
            path(
                "<int:warehouse_id>/addresses/",
                self.admin_site.admin_view(self.addresses_api),
                name="core_warehouse_addresses",
            ),
        ]
        return custom_urls + super().get_urls()

    def addresses_api(self, request, warehouse_id):
        try:
            warehouse = Warehouse.objects.get(pk=warehouse_id)
            sites = warehouse.get_available_sites()
            return JsonResponse({"addresses": [{"value": num, "label": label} for num, label in sites]})
        except Warehouse.DoesNotExist:
            return JsonResponse({"addresses": []})

    def balance_display(self, obj):
        """Итоговый баланс склада-контрагента (с учётом открытых FACT/PARDP)."""
        return render_total_balance(obj)

    balance_display.short_description = "Баланс"

    def balance_summary_display(self, obj):
        """Shows warehouse balance summary"""
        try:
            balance = obj.balance or 0
            color = "#28a745" if balance >= 0 else "#dc3545"

            return format_html(
                '<div style="background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">'
                '<h3 style="margin-top:0; color:#495057;">Баланс склада</h3>'
                '<div style="background:white; padding:15px; border-radius:5px; border:2px solid {color};">'
                '<strong style="color:{color};">Баланс:</strong><br>'
                '<span style="font-size:24px; font-weight:bold; color:{color};">{balance}</span>'
                "</div></div>",
                color=color,
                balance=f"{balance:.2f}",
            )
        except Exception as e:
            return format_html('<p style="color:#dc3545;">Ошибка загрузки баланса: {}</p>', e)

    balance_summary_display.short_description = "Сводка по балансу"

    def balance_transactions_display(self, obj):
        """Shows warehouse transactions (модель Payment заменена на Transaction)."""
        try:
            from core.models_billing import Transaction

            payments = Transaction.objects.filter(models.Q(from_warehouse=obj) | models.Q(to_warehouse=obj)).order_by(
                "-date", "-id"
            )[:20]

            if not payments.exists():
                return format_html('<p style="color:#6c757d;">Нет платежей</p>')

            # sender/recipient/description — пользовательские строки, поэтому
            # только format_html_join с плейсхолдерами (иначе XSS).
            rows = format_html_join(
                "",
                "<tr>"
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px; color:{}; font-weight:bold;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                "</tr>",
                (
                    (
                        payment.date.strftime("%d.%m.%Y"),
                        payment.get_type_display(),
                        "#28a745" if payment.to_warehouse_id == obj.pk else "#dc3545",
                        f"{payment.amount:.2f}",
                        payment.sender_name,
                        payment.recipient_name,
                        payment.description or "-",
                    )
                    for payment in payments
                ),
            )
            return format_html(
                '<div style="margin-top:15px;">'
                '<h4 style="margin-bottom:10px; color:#495057;">Последние платежи</h4>'
                '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
                '<tr style="background-color:#f8f9fa;">'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Дата</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Тип</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Отправитель</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Получатель</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Описание</th>'
                "</tr>{}</table></div>",
                rows,
            )
        except Exception as e:
            return format_html('<p style="color:#dc3545;">Ошибка загрузки платежей: {}</p>', e)

    balance_transactions_display.short_description = "Платежи"

    def reset_warehouse_balance(self, request, queryset):
        """Recalculates balances for selected warehouses from transaction history."""
        from django.contrib import messages

        from core.models_billing import Transaction

        try:
            for warehouse in queryset:
                Transaction.recalculate_entity_balance(warehouse)

            messages.success(request, f"Балансы {queryset.count()} складов пересчитаны из истории транзакций")
        except Exception as e:
            messages.error(request, f"Ошибка при пересчёте балансов: {e}")

    reset_warehouse_balance.short_description = "Пересчитать балансы выбранных складов"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Override change_view for service handling"""
        extra_context = extra_context or {}

        if object_id:
            obj = self.get_object(request, object_id)

            if request.method == "POST":
                process_service_fields(request, obj, WarehouseService, "warehouse")

                # Process legacy fields on Warehouse itself (not migrated to WarehouseService yet).
                old_fields_mapping = {
                    "service_price_complex": "complex_fee",
                    "service_price_unloading": "default_unloading_fee",
                    "service_price_delivery": "delivery_to_warehouse",
                    "service_price_loading": "loading_on_trawl",
                    "service_price_documents": "documents_fee",
                    "service_price_transfer": "transfer_fee",
                    "service_price_transit": "transit_declaration",
                    "service_price_export": "export_declaration",
                    "service_price_additional": "additional_expenses",
                    "service_price_rate": "rate",
                }

                for key, value in request.POST.items():
                    if key.startswith("clear_field_"):
                        field_name = key.replace("clear_field_", "")
                        setattr(obj, field_name, Decimal("0"))
                        obj.save()

                for field_name, model_field in old_fields_mapping.items():
                    if field_name in request.POST:
                        raw = request.POST[field_name]
                        try:
                            # Денежные поля — DecimalField; пишем Decimal,
                            # а не float, чтобы избежать артефактов вида
                            # 0.1 + 0.2 при последующих вычислениях.
                            value = Decimal(raw) if raw else Decimal("0")
                            setattr(obj, model_field, value)
                            obj.save()
                        except (InvalidOperation, ValueError):
                            logger.warning(
                                "Invalid legacy field value '%s' for %s on warehouse %s",
                                raw,
                                model_field,
                                obj.pk,
                            )

        return super().change_view(request, object_id, form_url, extra_context)


# ==============================================================================
# ClientAdmin
# ==============================================================================


class ClientDebtFilter(admin.SimpleListFilter):
    """Фильтр клиентов по состоянию полного баланса (с учётом открытых инвойсов)."""

    title = "Состояние баланса"
    parameter_name = "debt_state"

    def lookups(self, request, model_admin):
        return (
            ("debt", "С долгом"),
            ("zero", "Нулевой баланс"),
            ("overpayment", "Переплата"),
            ("no_debt", "Без долга (нулевой или переплата)"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == "debt":
            return queryset.filter(_total_balance__lt=0)
        if value == "zero":
            return queryset.filter(_total_balance=0)
        if value == "overpayment":
            return queryset.filter(_total_balance__gt=0)
        if value == "no_debt":
            return queryset.filter(_total_balance__gte=0)
        return queryset


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    change_form_template = "admin/client_change.html"
    list_display = (
        "name",
        "tariff_display",
        "emails_display",
        "telegram_display",
        "notification_enabled",
        "new_balance_display",
        "balance_status_new",
    )
    list_filter = (ClientDebtFilter, "notification_enabled", "telegram_enabled", "tariff_type")
    search_fields = (
        "name",
        "email",
        "email2",
        "email3",
        "email4",
        "telegram_chat_id",
        "telegram_chat_id2",
        "telegram_chat_id3",
        "telegram_chat_id4",
        "imones_kodas",
        "vat_code",
    )
    actions = ["reset_balances", "recalculate_balance", "reset_client_balance"]
    list_per_page = 50
    show_full_result_count = False
    readonly_fields = (
        "balance",
        "balance_updated_at",
        "new_invoices_display",
        "new_transactions_display",
        "telegram_link_display",
    )
    inlines = [CounterpartyBankAccountInline, ClientTariffRateInline]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Пояснение по типам тарифа уже есть в описании секции «Тариф» —
        # дублирующий help_text под селектом убираем, чтобы не было «каши».
        field = form.base_fields.get("tariff_type")
        if field is not None:
            field.help_text = ""
        return form

    def get_queryset(self, request):
        """OPTIMIZATION: Use with_balance_info for pre-calculated data.

        For the changelist view each client is additionally annotated with
        `_open_debt` (sum of remaining amounts on ISSUED/OVERDUE/PARTIALLY_PAID
        invoices) and `_total_balance` (balance − open debt). These are used by
        the debt filter and by the balance-column sort order.
        """
        qs = super().get_queryset(request)

        url_name = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
        is_changelist = url_name.endswith("_changelist")

        if is_changelist:
            from django.db.models import Count, DecimalField, F, OuterRef, Subquery, Sum, Value
            from django.db.models.functions import Coalesce

            from core.mixins import OPEN_INVOICE_STATUSES

            open_debt_sq = (
                NewInvoice.objects.filter(
                    recipient_client=OuterRef("pk"),
                    status__in=OPEN_INVOICE_STATUSES,
                )
                .values("recipient_client")
                .annotate(s=Sum(F("total") - F("paid_amount")))
                .values("s")[:1]
            )

            dec_field = DecimalField(max_digits=15, decimal_places=2)
            zero = Value(Decimal("0"), output_field=dec_field)

            return qs.annotate(
                _tariff_rates_count=Count("tariff_rates"),
                _open_debt=Coalesce(Subquery(open_debt_sq, output_field=dec_field), zero),
            ).annotate(_total_balance=F("balance") - F("_open_debt"))
        return qs

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if "autocomplete" in request.path:
            from django.db.models import Count

            queryset = queryset.annotate(_car_count=Count("car")).order_by("-_car_count", "name")
        return queryset, use_distinct

    fieldsets = (
        ("Основная информация", {"classes": ("cm-requisites",), "fields": _REQUISITES_FIELDS}),
        (
            "🔔 Уведомления",
            {
                "fields": (
                    ("notification_enabled", "telegram_enabled"),
                    ("email", "telegram_chat_id"),
                    ("email2", "telegram_chat_id2"),
                    ("email3", "telegram_chat_id3"),
                    ("email4", "telegram_chat_id4"),
                    "telegram_link_display",
                ),
            },
        ),
        (
            "📊 Тариф",
            {
                "fields": ("tariff_type",),
                "description": (
                    "<b>Без тарифа</b> — обычные наценки услуг. "
                    "<b>Фикс. цена</b> — фиксированная цена за авто, не зависит от количества. "
                    "<b>Гибкая цена</b> — цена зависит от количества авто в контейнере (мотоциклы считаются отдельно). "
                    "Цена тарифа = сумма всех услуг склада, <u>кроме хранения</u> (хранение добавляется сверху). "
                    "Сами ставки заполняются в таблице «Тарифы клиента» ниже — там есть подсказка с примером."
                ),
            },
        ),
        (
            "💰 Баланс",
            {
                "fields": ("balance", "balance_updated_at", "new_invoices_display", "new_transactions_display"),
                "description": "Единый баланс клиента с историей транзакций",
            },
        ),
    )

    def tariff_display(self, obj):
        """Tariff display in client list"""
        if obj.tariff_type == "NONE":
            return format_html('<span style="color: #999;">—</span>')
        rates_count = getattr(obj, "_tariff_rates_count", None)
        if rates_count is None:
            rates_count = obj.tariff_rates.count()
        if obj.tariff_type == "FIXED":
            return format_html('<span style="color: #007bff;">Фикс. ({} ставок)</span>', rates_count)
        return format_html('<span style="color: #28a745;">Гибкий ({} ставок)</span>', rates_count)

    tariff_display.short_description = "Тариф"

    def emails_display(self, obj):
        """Displays email count"""
        emails = obj.get_notification_emails()
        count = len(emails)
        if count == 0:
            return format_html('<span style="color: #999;">—</span>')
        elif count == 1:
            return format_html('<span title="{}">{}</span>', emails[0], emails[0])
        else:
            all_emails = ", ".join(emails)
            return format_html('<span title="{}">{} (+{})</span>', all_emails, emails[0], count - 1)

    emails_display.short_description = "Email"

    def telegram_display(self, obj):
        """Показывает статус Telegram-уведомлений клиента."""
        chat_ids = obj.get_telegram_chat_ids()
        if not chat_ids:
            return format_html('<span style="color: #999;">—</span>')
        title = ", ".join(chat_ids)
        suffix = f" (+{len(chat_ids) - 1})" if len(chat_ids) > 1 else ""
        if not obj.telegram_enabled:
            return format_html('<span style="color: #999;" title="{}">выкл{}</span>', title, suffix)
        return format_html('<span style="color: #229ED9;" title="{}">✓ TG{}</span>', title, suffix)

    telegram_display.short_description = "Telegram"

    def telegram_link_display(self, obj):
        """Персональная ссылка-приглашение клиента в Telegram."""
        if not obj or not obj.pk:
            return format_html('<span style="color:#999;">Сохраните клиента, чтобы получить ссылку.</span>')
        link = obj.get_telegram_deep_link()
        if not link:
            return format_html('<span style="color:#999;">Не задан TELEGRAM_BOT_USERNAME.</span>')
        return format_html(
            '<div style="max-width:760px;">'
            '<a href="{}" target="_blank" style="color:#229ED9;font-weight:600;word-break:break-all;">{}</a>'
            '<div style="color:#6b7280;font-size:12px;margin-top:6px;">'
            "Отправьте эту персональную ссылку клиенту. Когда он перейдёт по ней и нажмёт "
            "«Start», его Telegram автоматически привяжется к этому клиенту (chat_id заполнится сам, "
            "обычно в течение минуты). Можно привязать несколько получателей — каждый переходит "
            "по этой же ссылке. <b>Если кнопка «Запустить» не появилась</b> (бот уже был запущен "
            "ранее) — пусть клиент просто <b>перешлёт эту ссылку боту сообщением</b>, привязка "
            "оформится автоматически. Ссылка индивидуальна — не путать между клиентами."
            "</div></div>",
            link,
            link,
        )

    telegram_link_display.short_description = "Ссылка-приглашение в Telegram"

    def real_balance_display(self, obj):
        """Shows invoice-balance of client (invoices - payments)"""
        balance = obj.real_balance
        color = obj.balance_color
        sign = "" if balance == 0 else ("+" if balance > 0 else "")
        formatted = f"{balance:.2f}"

        return format_html(
            '<span style="color:{}; font-weight:bold; font-size:14px;">{} {}</span>', color, sign, formatted
        )

    real_balance_display.short_description = "Инвойс-баланс (Долг/Переплата)"
    real_balance_display.admin_order_field = "_real_balance_annotated"

    def balance_status_display(self, obj):
        """Shows balance status with colored badge"""
        status = obj.balance_status
        color = obj.balance_color
        color.replace("#", "")

        return format_html(
            '<span style="background-color:{}; color:white; padding:4px 8px; border-radius:4px; font-size:11px; font-weight:bold;">{}</span>',
            color,
            status,
        )

    balance_status_display.short_description = "Статус"

    def new_balance_display(self, obj):
        """Полный баланс клиента = сальдо транзакций - долг по открытым инвойсам."""
        ann_balance = getattr(obj, "_total_balance", None)
        ann_debt = getattr(obj, "_open_debt", None)

        if ann_balance is not None:
            balance = ann_balance
            debt = ann_debt or Decimal("0")
        else:
            balance = obj.total_balance
            debt = obj.open_invoices_debt

        cash = obj.balance

        if balance > 0:
            color = "#28a745"
            text = f"+{balance:.2f}"
        elif balance < 0:
            color = "#dc3545"
            text = f"{balance:.2f}"
        else:
            color = "#6c757d"
            text = "0.00"

        tooltip = f"Сальдо транзакций: {cash:.2f} €\nДолг по открытым инвойсам: {debt:.2f} €"
        return format_html(
            '<span title="{}" style="color:{}; font-weight:bold; font-size:15px;">{}</span>', tooltip, color, text
        )

    new_balance_display.short_description = "Баланс"
    new_balance_display.admin_order_field = "_total_balance"

    def balance_status_new(self, obj):
        """Статус по полному балансу (с учётом открытых инвойсов)."""
        balance = getattr(obj, "_total_balance", None)
        if balance is None:
            balance = obj.total_balance

        if balance > 0:
            return format_html(
                '<span style="background:#28a745; color:white; padding:3px 8px; border-radius:3px;">ПЕРЕПЛАТА</span>'
            )
        elif balance < 0:
            return format_html(
                '<span style="background:#dc3545; color:white; padding:3px 8px; border-radius:3px;">ДОЛГ</span>'
            )
        else:
            return format_html(
                '<span style="background:#6c757d; color:white; padding:3px 8px; border-radius:3px;">OK</span>'
            )

    balance_status_new.short_description = "Статус"

    def new_invoices_display(self, obj):
        """Shows invoices from new system"""
        from core.models_billing import NewInvoice

        invoices = NewInvoice.objects.filter(recipient_client=obj).order_by("-date")[:10]

        if not invoices:
            return format_html(
                '<p style="color:#999;">Инвойсов еще нет. <a href="/admin/core/newinvoice/add/">Создать первый инвойс</a></p>'
            )

        # Строки собираются ТОЛЬКО через format_html_join с плейсхолдерами:
        # f-string + format_html(готовый html) не экранирует значения (XSS)
        # и падает на фигурных скобках в данных.
        rows = format_html_join(
            "",
            '<tr style="border-bottom:1px solid #ddd;">'
            '<td style="padding:8px;"><a href="/admin/core/newinvoice/{}/change/">{}</a></td>'
            '<td style="padding:8px;">{}</td>'
            '<td style="padding:8px;">{}</td>'
            '<td style="padding:8px;">{}</td>'
            '<td style="padding:8px;"><span style="background:{}; color:white; '
            'padding:2px 6px; border-radius:3px;">{}</span></td>'
            "</tr>",
            (
                (
                    inv.pk,
                    inv.number,
                    inv.date.strftime("%d.%m.%Y"),
                    f"{inv.total:.2f}",
                    f"{inv.paid_amount:.2f}",
                    {"PAID": "#28a745", "ISSUED": "#007bff", "OVERDUE": "#dc3545"}.get(inv.status, "#6c757d"),
                    inv.get_status_display(),
                )
                for inv in invoices
            ),
        )
        return format_html(
            '<table style="width:100%; border-collapse:collapse;">'
            '<tr style="background:#f5f5f5;"><th style="padding:8px;">Номер</th>'
            "<th>Дата</th><th>Сумма</th><th>Оплачено</th><th>Статус</th></tr>{}</table>",
            rows,
        )

    new_invoices_display.short_description = "Инвойсы"

    def new_transactions_display(self, obj):
        """Shows transactions from new system"""
        from core.models_billing import Transaction

        transactions = Transaction.objects.filter(models.Q(from_client=obj) | models.Q(to_client=obj)).order_by(
            "-date"
        )[:10]

        if not transactions:
            return format_html('<p style="color:#999;">Транзакций еще нет</p>')

        rows = format_html_join(
            "",
            '<tr style="border-bottom:1px solid #ddd;">'
            '<td style="padding:8px;"><a href="/admin/core/transaction/{}/change/">{}</a></td>'
            '<td style="padding:8px;">{}</td>'
            '<td style="padding:8px;"><span style="background:{}; color:white; '
            'padding:2px 6px; border-radius:3px;">{}</span></td>'
            '<td style="padding:8px; font-weight:bold;">{}</td>'
            '<td style="padding:8px;">{}</td>'
            "</tr>",
            (
                (
                    trx.pk,
                    trx.number,
                    trx.date.strftime("%d.%m.%Y %H:%M"),
                    {"PAYMENT": "#28a745", "REFUND": "#dc3545"}.get(trx.type, "#007bff"),
                    trx.get_type_display(),
                    f"{trx.amount:.2f}",
                    "↑ Получено" if trx.to_client == obj else "↓ Отправлено",
                )
                for trx in transactions
            ),
        )
        return format_html(
            '<table style="width:100%; border-collapse:collapse;">'
            '<tr style="background:#f5f5f5;"><th style="padding:8px;">Номер</th>'
            "<th>Дата</th><th>Тип</th><th>Сумма</th><th>Направление</th></tr>{}</table>",
            rows,
        )

    new_transactions_display.short_description = "Транзакции"

    def recalculate_balance(self, request, queryset):
        """Recalculates invoice-balance for selected clients"""
        from django.contrib import messages

        count = 0
        for client in queryset:
            try:
                client.sync_balance_fields()
                count += 1
                messages.success(request, f"Инвойс-баланс клиента {client.name} пересчитан")
            except Exception as e:
                messages.error(request, f"Ошибка при пересчете инвойс-баланса клиента {client.name}: {e}")

        if count > 0:
            messages.success(request, f"Успешно пересчитан инвойс-баланс для {count} клиентов")
        else:
            messages.warning(request, "Не удалось пересчитать инвойс-баланс ни для одного клиента")

    recalculate_balance.short_description = "Пересчитать инвойс-баланс"

    def sync_all_balances(self, request, queryset):
        """Syncs balance fields with invoice-balance for selected clients"""
        from django.contrib import messages

        count = 0
        for client in queryset:
            try:
                client.sync_balance_fields()
                count += 1
                messages.success(request, f"Инвойс-баланс клиента {client.name} синхронизирован")
            except Exception as e:
                messages.error(request, f"Ошибка при синхронизации инвойс-баланса клиента {client.name}: {e}")

        if count > 0:
            messages.success(request, f"Успешно синхронизирован инвойс-баланс для {count} клиентов")
        else:
            messages.warning(request, "Не удалось синхронизировать инвойс-баланс ни для одного клиента")

    sync_all_balances.short_description = "Синхронизировать инвойс-балансы"

    def reset_balances(self, request, queryset):
        if "confirm" in request.POST:
            client_ids = request.POST.getlist("_selected_action")
            clients = Client.objects.filter(id__in=client_ids)
            from django.core.management import call_command

            failed_clients = []
            for client in clients:
                try:
                    call_command("reset_client_balances", client_id=client.id)
                except Exception as e:
                    logger.error(f"Failed to reset balance for client {client.name}: {e}")
                    failed_clients.append(f"{client.name}: {e!s}")
            if failed_clients:
                self.message_user(
                    request,
                    f"Не удалось сбросить балансы для некоторых клиентов: {'; '.join(failed_clients)}",
                    level="error",
                )
            else:
                self.message_user(request, f"Балансы для {len(clients)} клиентов успешно обнулены")
            return HttpResponseRedirect(request.get_full_path())
        return render(
            request,
            "admin/confirm_reset_balances.html",
            {"clients": queryset, "action": "reset_balances", "action_name": "Обнулить балансы"},
        )

    reset_balances.short_description = "Обнулить балансы клиентов"

    def reset_client_balance(self, request, queryset):
        """Обнуляет балансы выбранных клиентов корректирующими транзакциями.

        A3 (AUDIT_ROUND3): раньше action писал ``client.balance = 0`` напрямую
        — мутация финансового состояния в обход леджера (следующий пересчёт
        по транзакциям вернул бы старое значение). Теперь — через
        ``BillingService.adjust_balance`` (ADJUSTMENT-транзакция).
        """
        from decimal import Decimal as D

        from django.contrib import messages

        from core.services.billing_service import BillingService

        try:
            reset_count = 0
            for client in queryset:
                if client.balance != D("0.00"):
                    BillingService.adjust_balance(
                        entity=client,
                        amount=-client.balance,
                        reason=f"Обнуление баланса (был: {client.balance}EUR)",
                        created_by=request.user if request.user.is_authenticated else None,
                    )
                    reset_count += 1

            messages.success(
                request, f"Балансы обнулены: {reset_count} из {queryset.count()} клиентов (остальные уже были нулевыми)"
            )
        except Exception as e:
            messages.error(request, f"Ошибка при обнулении балансов: {e}")

    reset_client_balance.short_description = "Обнулить балансы выбранных клиентов"

    # ========================================================================
    # BALANCE TOP-UP AND MANAGEMENT
    # ========================================================================

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom_urls = [
            path("<int:client_id>/topup/", self.admin_site.admin_view(self.topup_balance_view), name="client_topup"),
            path(
                "<int:client_id>/reset-balance/",
                self.admin_site.admin_view(self.reset_balance_view),
                name="client_reset_balance",
            ),
            path(
                "<int:client_id>/recalc-balance/",
                self.admin_site.admin_view(self.recalc_balance_view),
                name="client_recalc_balance",
            ),
            path(
                "<int:client_id>/cars-in-warehouse/",
                self.admin_site.admin_view(self.cars_in_warehouse_view),
                name="client_cars_in_warehouse",
            ),
        ]
        return custom_urls + urls

    def topup_balance_view(self, request, client_id):
        """Balance top-up page for client"""
        from django.contrib import messages
        from django.shortcuts import redirect, render

        from core.services.billing_service import BillingService

        client = Client.objects.get(pk=client_id)

        if request.method == "POST":
            try:
                amount = Decimal(request.POST.get("amount", 0))
                method = request.POST.get("method", "CASH")
                description = request.POST.get("description", "")

                if amount <= 0:
                    raise ValueError("Сумма должна быть положительной")

                trx = BillingService.topup_balance(
                    entity=client,
                    amount=amount,
                    method=method,
                    description=description or f"Пополнение баланса клиента {client.name}",
                    created_by=request.user,
                )

                messages.success(request, f"Баланс пополнен! Транзакция: {trx.number}, сумма: {amount}EUR")

                return redirect("admin:core_client_change", client_id)

            except Exception as e:
                messages.error(request, f"Ошибка: {e}")

        context = {
            "client": client,
            "opts": self.model._meta,
            "title": f"Пополнение баланса - {client.name}",
        }

        return render(request, "admin/client_topup.html", context)

    def reset_balance_view(self, request, client_id):
        """Reset client balance by creating an adjustment transaction (POST only)."""
        from django.contrib import messages
        from django.shortcuts import redirect

        from core.services.billing_service import BillingService

        client = Client.objects.get(pk=client_id)

        if request.method != "POST":
            messages.error(request, "Обнуление баланса доступно только через POST-запрос.")
            return redirect("admin:core_client_change", client_id)

        if not request.POST.get("confirm"):
            messages.warning(request, "Обнуление баланса не подтверждено.")
            return redirect("admin:core_client_change", client_id)

        old_balance = client.balance
        if old_balance != Decimal("0.00"):
            BillingService.adjust_balance(
                entity=client,
                amount=-old_balance,
                reason=f"Обнуление баланса (был: {old_balance}EUR)",
                created_by=request.user if request.user.is_authenticated else None,
            )
            client.refresh_from_db()

        messages.success(request, f"Баланс клиента {client.name} обнулён (был: {old_balance}EUR)")
        return redirect("admin:core_client_change", client_id)

    def recalc_balance_view(self, request, client_id):
        """Recalculate client balance using canonical logic (sum all COMPLETED transactions)."""
        from django.contrib import messages
        from django.shortcuts import redirect

        from core.models_billing import Transaction

        client = Client.objects.get(pk=client_id)
        old_balance = client.balance

        Transaction.recalculate_entity_balance(client)
        client.refresh_from_db()

        messages.success(
            request,
            f"Баланс пересчитан: {old_balance}EUR → {client.balance}EUR "
            f"(canonical: sum(incoming) - sum(outgoing) всех COMPLETED транзакций)",
        )
        return redirect("admin:core_client_change", client_id)

    def cars_in_warehouse_view(self, request, client_id):
        """Shows list of all client's unloaded cars in warehouse"""
        from django.shortcuts import render

        client = Client.objects.get(pk=client_id)

        # Get all client's cars with UNLOADED status (in warehouse)
        cars = (
            Car.objects.filter(client=client, status="UNLOADED")
            .select_related("warehouse", "container")
            .order_by("warehouse__name", "-unload_date")
        )

        # Group by warehouses
        warehouses_data = {}
        for car in cars:
            wh_name = car.warehouse.name if car.warehouse else "Без склада"
            if wh_name not in warehouses_data:
                warehouses_data[wh_name] = []
            warehouses_data[wh_name].append(car)

        # Form text for copying
        text_for_copy = f"Авто на складе - {client.name}\n"
        text_for_copy += f"Дата: {timezone.now().strftime('%d.%m.%Y')}\n"
        text_for_copy += "=" * 40 + "\n\n"

        for wh_name, wh_cars in warehouses_data.items():
            text_for_copy += f"{wh_name} ({len(wh_cars)} авто)\n"
            text_for_copy += "-" * 30 + "\n"
            for car in wh_cars:
                text_for_copy += f"* {car.vin} - {car.brand} {car.year}"
                if car.unload_date:
                    text_for_copy += f" (разгр. {car.unload_date.strftime('%d.%m.%Y')})"
                text_for_copy += "\n"
            text_for_copy += "\n"

        text_for_copy += f"Итого: {cars.count()} авто"

        context = {
            "client": client,
            "cars": cars,
            "warehouses_data": warehouses_data,
            "text_for_copy": text_for_copy,
            "total_count": cars.count(),
            "opts": self.model._meta,
            "title": f"Авто на складе - {client.name}",
        }

        return render(request, "admin/client_cars_in_warehouse.html", context)


# ==============================================================================
# CompanyAdmin
# ==============================================================================


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    change_form_template = "admin/company_change.html"
    list_display = ("name", "balance_display", "is_main_company", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("name", "imones_kodas", "vat_code")
    readonly_fields = ("created_at", "updated_at", "balance")
    actions = ["reset_company_balance"]
    inlines = [CounterpartyBankAccountInline, CompanyServiceInline]

    fieldsets = (
        ("Основная информация", {"classes": ("cm-requisites",), "fields": _REQUISITES_FIELDS}),
        ("Баланс", {"fields": ("balance",), "description": "Баланс компании"}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return annotate_partner_balance(qs, "company")

    def balance_display(self, obj):
        """Итоговый баланс компании-контрагента (с учётом открытых FACT/PARDP)."""
        return render_total_balance(obj)

    balance_display.short_description = "Баланс"

    def is_main_company(self, obj):
        """Shows if company is the main one"""
        return obj.name == "Caromoto Lithuania"

    is_main_company.boolean = True
    is_main_company.short_description = "Главная компания"

    def balance_summary_display(self, obj):
        """Shows company balance summary"""
        try:
            # У Company один агрегированный balance (полей cash/card/invoice
            # нет) — раньше обращение к obj.cash_balance всегда падало в
            # AttributeError, и блок показывал только текст ошибки.
            balance = obj.balance or 0

            color = "#28a745" if balance >= 0 else "#dc3545"
            dashboard_btn = ""
            if obj.name == "Caromoto Lithuania":
                dashboard_btn = format_html(
                    '<div style="margin-top:20px; text-align:center;">'
                    '<a href="/company-dashboard/" style="display:inline-block; padding:12px 24px; '
                    "background:#667eea; color:white; text-decoration:none; border-radius:8px; "
                    'font-weight:600; font-size:16px;">Открыть дашборд компании</a></div>'
                )

            return format_html(
                '<div style="background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6;">'
                '<h3 style="margin-top:0; color:#495057;">Баланс компании</h3>'
                '<div style="background:white; padding:15px; border-radius:5px; border:2px solid {color};">'
                '<strong style="color:{color};">Баланс:</strong><br>'
                '<span style="font-size:24px; font-weight:bold; color:{color};">{balance}</span>'
                "</div>{dashboard_btn}</div>",
                color=color,
                balance=f"{balance:.2f}",
                dashboard_btn=dashboard_btn,
            )
        except Exception as e:
            return format_html('<p style="color:#dc3545;">Ошибка загрузки баланса: {}</p>', e)

    balance_summary_display.short_description = "Сводка по балансу"

    def balance_transactions_display(self, obj):
        """Shows company transactions (модель Payment заменена на Transaction)."""
        try:
            from core.models_billing import Transaction

            payments = Transaction.objects.filter(models.Q(from_company=obj) | models.Q(to_company=obj)).order_by(
                "-date", "-id"
            )[:20]

            if not payments.exists():
                return format_html('<p style="color:#6c757d;">Нет платежей</p>')

            rows = format_html_join(
                "",
                "<tr>"
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px; color:{}; font-weight:bold;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                '<td style="border:1px solid #dee2e6; padding:8px;">{}</td>'
                "</tr>",
                (
                    (
                        payment.date.strftime("%d.%m.%Y"),
                        payment.get_type_display(),
                        "#28a745" if payment.to_company_id == obj.pk else "#dc3545",
                        f"{payment.amount:.2f}",
                        payment.sender_name,
                        payment.recipient_name,
                        payment.description or "-",
                    )
                    for payment in payments
                ),
            )
            return format_html(
                '<div style="margin-top:15px;">'
                '<h4 style="margin-bottom:10px; color:#495057;">Последние платежи</h4>'
                '<table style="width:100%; border-collapse:collapse; font-size:12px;">'
                '<tr style="background-color:#f8f9fa;">'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Дата</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Тип</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Сумма</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Отправитель</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Получатель</th>'
                '<th style="border:1px solid #dee2e6; padding:8px; text-align:left;">Описание</th>'
                "</tr>{}</table></div>",
                rows,
            )
        except Exception as e:
            return format_html('<p style="color:#dc3545;">Ошибка загрузки платежей: {}</p>', e)

    balance_transactions_display.short_description = "Платежи"

    def reset_company_balance(self, request, queryset):
        """Resets balances for selected companies"""
        from django.contrib import messages

        try:
            for company in queryset:
                company.balance = 0
                company.save()

            messages.success(request, f"Балансы {queryset.count()} компаний успешно обнулены")
        except Exception as e:
            messages.error(request, f"Ошибка при обнулении балансов: {e}")

    reset_company_balance.short_description = "Обнулить балансы выбранных компаний"


# ==============================================================================
# LineAdmin
# ==============================================================================


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    change_form_template = "admin/line_change.html"
    form = LineForm
    list_display = ("name", "services_count_display", "ths_fee_display", "open_invoices_display", "balance_display")
    list_filter = ("balance_updated_at",)
    search_fields = ("name", "imones_kodas", "vat_code")
    readonly_fields = ("balance",)
    actions = ["reset_line_balance"]
    exclude = ("ocean_freight_rate", "documentation_fee", "handling_fee", "additional_fees")
    inlines = [CounterpartyBankAccountInline, LineTHSCoefficientInline, LineServiceInline]
    fieldsets = (
        ("Основные данные", {"classes": ("cm-requisites",), "fields": _REQUISITES_FIELDS}),
        (
            "THS по умолчанию",
            {
                "fields": ("ths_fee",),
                "description": "Значение по умолчанию для поля «Оплата линиям» при создании "
                "контейнера с этой линией. В карточке контейнера его можно изменить.",
            },
        ),
        ("Баланс", {"fields": ("balance",), "description": "Баланс линии"}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.annotate(
            _services_count=models.Count("services", distinct=True),
            _open_invoices=models.Count(
                "received_invoices_new",
                filter=models.Q(received_invoices_new__status__in=("ISSUED", "PARTIALLY_PAID", "OVERDUE")),
                distinct=True,
            ),
        )
        return annotate_partner_balance(qs, "line")

    def balance_display(self, obj):
        """Итоговый баланс линии-контрагента (с учётом открытых FACT/PARDP)."""
        return render_total_balance(obj)

    balance_display.short_description = "Баланс"

    def services_count_display(self, obj):
        count = getattr(obj, "_services_count", 0)
        return count or "—"

    services_count_display.short_description = "Услуги"
    services_count_display.admin_order_field = "_services_count"

    def ths_fee_display(self, obj):
        if obj.ths_fee:
            return f"{obj.ths_fee:.2f} €"
        return "—"

    ths_fee_display.short_description = "THS"
    ths_fee_display.admin_order_field = "ths_fee"

    def open_invoices_display(self, obj):
        count = getattr(obj, "_open_invoices", 0)
        if not count:
            return "—"
        return format_html(
            '<span style="background:#fffbeb;color:#b45309;padding:2px 8px;'
            'border-radius:10px;font-size:11px;font-weight:600;">{}</span>',
            count,
        )

    open_invoices_display.short_description = "Открытые инвойсы"
    open_invoices_display.admin_order_field = "_open_invoices"

    def reset_line_balance(self, request, queryset):
        """Resets balances for selected lines"""
        from django.contrib import messages

        try:
            for line in queryset:
                line.balance = 0
                line.save()

            messages.success(request, f"Балансы {queryset.count()} линий успешно обнулены")
        except Exception as e:
            messages.error(request, f"Ошибка при обнулении балансов: {e}")

    reset_line_balance.short_description = "Обнулить балансы выбранных линий"

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:object_id>/recalculate_ths/",
                self.admin_site.admin_view(self.recalculate_ths_view),
                name="core_line_recalculate_ths",
            ),
            path(
                "<int:line_id>/ths/",
                self.admin_site.admin_view(self.ths_api),
                name="core_line_ths",
            ),
        ]
        return custom_urls + urls

    def ths_api(self, request, line_id):
        """Возвращает THS по умолчанию для линии (для автоподстановки в форме контейнера)."""
        try:
            line = Line.objects.only("id", "ths_fee").get(pk=line_id)
            return JsonResponse({"ths_fee": str(line.ths_fee or "")})
        except Line.DoesNotExist:
            return JsonResponse({"ths_fee": ""})

    def recalculate_ths_view(self, request, object_id):
        """Recalculates THS for all line cars with UNLOADED and IN_PORT status"""
        import logging

        from django.contrib import messages
        from django.db import transaction
        from django.shortcuts import redirect

        from core.models import Line
        from core.services.car_service_manager import (
            apply_client_tariffs_for_container,
            create_ths_services_for_container,
        )

        logger = logging.getLogger(__name__)

        logger.info(f"=== RECALCULATE THS VIEW CALLED === object_id={object_id}")

        # Get line directly by ID
        try:
            line = Line.objects.get(pk=object_id)
        except Line.DoesNotExist:
            messages.error(request, "Линия не найдена")
            return redirect("admin:core_line_changelist")
        logger.info(f"[RECALC THS] Starting for line {line.name}")

        # Find all containers of this line with cars in needed statuses
        containers = Container.objects.filter(line=line, container_cars__status__in=["UNLOADED", "IN_PORT"]).distinct()

        updated_containers = 0
        updated_cars = 0

        try:
            with transaction.atomic():
                for container in containers:
                    if container.ths:
                        logger.info(f"[RECALC THS] Container {container.number}, THS={container.ths}")

                        # Recalculate THS services
                        created = create_ths_services_for_container(container)
                        logger.info(f"[RECALC THS] Created {created} THS services")
                        # Apply client tariffs
                        apply_client_tariffs_for_container(container)
                        updated_containers += 1

                        # Recalculate car prices
                        for car in container.container_cars.filter(status__in=["UNLOADED", "IN_PORT"]):
                            old_price = car.total_price
                            # Clear cache and get fresh data
                            car.refresh_from_db()
                            if hasattr(car, "_prefetched_objects_cache"):
                                car._prefetched_objects_cache.clear()

                            car.calculate_total_price()
                            car.save(update_fields=["total_price", "storage_cost", "days"])
                            logger.info(f"[RECALC THS] Car {car.vin}: {old_price} -> {car.total_price}")
                            updated_cars += 1

            messages.success(request, f"Пересчитано: {updated_containers} контейнеров, {updated_cars} машин")
        except Exception as e:
            logger.error(f"[RECALC THS] Error: {e}", exc_info=True)
            messages.error(request, f"Ошибка при пересчёте: {e}")

        from django.urls import reverse

        return redirect(reverse("admin:core_line_change", args=[object_id]))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Override change_view for service handling"""
        extra_context = extra_context or {}

        if object_id:
            obj = self.get_object(request, object_id)

            if request.method == "POST":
                process_service_fields(request, obj, LineService, "line")

        return super().change_view(request, object_id, form_url, extra_context)


# ==============================================================================
# CarrierAdmin
# ==============================================================================


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    change_form_template = "admin/carrier_change.html"
    form = CarrierForm
    list_display = ("name", "eori_code", "contact_person", "phone", "balance_display")
    search_fields = ("name", "eori_code", "contact_person", "phone", "email", "imones_kodas", "vat_code")
    list_filter = ("created_at",)
    readonly_fields = ("created_at", "updated_at", "balance")
    exclude = ("transport_rate", "loading_fee", "unloading_fee", "fuel_surcharge", "additional_fees")
    inlines = [CounterpartyBankAccountInline, CarrierServiceInline, CarrierTruckInline, CarrierDriverInline]
    # У Carrier телефон/email/EORI — собственные исторические поля,
    # поэтому раскладка своя (та же логика: коды → адрес → контакты → сайт).
    fieldsets = (
        (
            "Основная информация",
            {
                "classes": ("cm-requisites",),
                "fields": (
                    ("name", "short_name", "imones_kodas", "vat_code", "eori_code"),
                    ("registration_country", "physical_address", "contact_person", "phone", "email", "website"),
                ),
            },
        ),
        ("Баланс", {"fields": ("balance",)}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return annotate_partner_balance(qs, "carrier")

    def balance_display(self, obj):
        """Итоговый баланс перевозчика-контрагента (с учётом открытых FACT/PARDP)."""
        return render_total_balance(obj)

    balance_display.short_description = "Баланс"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Override change_view for service handling"""
        extra_context = extra_context or {}

        if object_id:
            obj = self.get_object(request, object_id)

            if request.method == "POST":
                process_service_fields(request, obj, CarrierService, "carrier")

        return super().change_view(request, object_id, form_url, extra_context)


# ==============================================================================
# AutoTransportAdmin
# ==============================================================================


class AutoTransportHasUnreadEmailsFilter(admin.SimpleListFilter):
    """Фильтр по наличию писем/непрочитанных писем среди машин автовоза."""

    title = "Переписка"
    parameter_name = "emails"

    def lookups(self, request, model_admin):
        return (
            ("unread", "Есть непрочитанные"),
            ("need_reply", "Ждут ответа"),
            ("any", "Есть переписка"),
            ("none", "Нет писем"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "unread":
            return queryset.filter(cars__email_links__is_read=False).distinct()
        if value == "need_reply":
            return queryset.filter(
                cars__email_links__email__needs_reply=True,
                cars__email_links__email__direction="INCOMING",
            ).distinct()
        if value == "any":
            return queryset.filter(cars__email_links__isnull=False).distinct()
        if value == "none":
            return queryset.exclude(cars__email_links__isnull=False)
        return queryset


@admin.register(AutoTransport)
class AutoTransportAdmin(admin.ModelAdmin):
    """
    Admin for forming auto-transports for loading

    Features:
    - AJAX car selection
    - Auto-fill EORI code from carrier
    - Auto-fill driver phone
    - Invoice generation for clients on save
    """

    change_form_template = "admin/core/autotransport/change_form.html"
    change_list_template = "admin/core/autotransport/change_list.html"

    list_display = (
        "number_with_unread",
        "carrier",
        "truck_display",
        "driver_display",
        "cars_count_display",
        "status_display",
        "loading_date",
        "actions_display",
    )
    list_display_links = ("number_with_unread",)

    # Без явного значения Django берёт 100; при тяжёлых колонках
    # (number_with_unread, actions_display) это лишняя нагрузка.
    # show_full_result_count убирает второй COUNT(*) по всей таблице.
    list_per_page = 50
    show_full_result_count = False

    list_filter = (
        "status",
        "carrier",
        "loading_date",
        "created_at",
        AutoTransportHasUnreadEmailsFilter,
    )

    actions = ["mark_delivered"]

    search_fields = (
        "number",
        "carrier__name",
        "truck_number_manual",
        "driver_name_manual",
        "border_crossing",
    )

    readonly_fields = (
        "number",
        "created_at",
        "updated_at",
        "created_by",
        "cars_count",
    )

    # M5: carrier — длинный FK-список, переводим на autocomplete.
    # `cars` M2M НЕ трогаем: change_form.html уже использует кастомный AJAX-UI
    # (см. _get_extra_context + admin/core/autotransport/change_form.html).
    autocomplete_fields = ("carrier",)
    filter_horizontal = ("cars",)

    fieldsets = (
        ("Основная информация", {"fields": ("number", "status")}),
        ("Перевозчик", {"fields": ("carrier", "eori_code")}),
        (
            "Автовоз",
            {
                "fields": (("truck", "truck_number_manual", "trailer_number_manual"),),
                "description": "Выберите автовоз из списка или введите номера вручную",
            },
        ),
        (
            "Водитель",
            {
                "fields": (("driver", "driver_name_manual", "driver_phone"),),
                "description": "Выберите водителя из списка или введите данные вручную",
            },
        ),
        ("Граница и маршрут", {"fields": ("border_crossing",)}),
        ("Автомобили", {"fields": ("cars", "cars_count"), "description": "Выберите автомобили для загрузки в автовоз"}),
        (
            "Даты",
            {
                "fields": (
                    "loading_date",
                    "departure_date",
                    "estimated_delivery_date",
                    "actual_delivery_date",
                )
            },
        ),
        ("Дополнительно", {"fields": ("notes",), "classes": ("collapse",)}),
    )

    # dashboard_admin.css подключается глобально в base_site.html

    def get_changelist_instance(self, request):
        """Скрываем доставленные автовозы по умолчанию (только в списке, не в форме)"""
        # Подменяем queryset только если пользователь не выбрал фильтр по статусу
        if "status" not in request.GET and "status__exact" not in request.GET:
            self._exclude_delivered = True
        else:
            self._exclude_delivered = False
        return super().get_changelist_instance(request)

    def get_queryset(self, request):
        from django.db.models import Count, Prefetch, Q

        from core.models_billing import NewInvoice

        qs = super().get_queryset(request)
        if getattr(self, "_exclude_delivered", False):
            qs = qs.exclude(status="DELIVERED")
            self._exclude_delivered = False
        # FK в list_display (carrier, truck, driver) + cars_count_display
        # вытягивает self.cars.count(); actions_display перебирает
        # NewInvoice.objects.filter(auto_transport=...) на каждой строке.
        # Делаем JOIN'ы и prefetch заранее, чтобы убрать N+1.
        qs = (
            qs.select_related("carrier", "truck", "driver")
            .prefetch_related(
                Prefetch(
                    "invoices",
                    queryset=NewInvoice.objects.exclude(status="CANCELLED").only(
                        "id",
                        "status",
                        "auto_transport_id",
                    ),
                    to_attr="_active_invoices",
                ),
            )
            .annotate(
                _cars_count=Count("cars", distinct=True),
                _emails_unread=Count(
                    "cars__email_links__email",
                    filter=Q(cars__email_links__is_read=False),
                    distinct=True,
                ),
                _emails_need_reply=Count(
                    "cars__email_links__email",
                    filter=Q(
                        cars__email_links__email__needs_reply=True,
                        cars__email_links__email__direction="INCOMING",
                    ),
                    distinct=True,
                ),
            )
        )
        return qs

    def mark_delivered(self, request, queryset):
        """Массовое присвоение статуса 'Доставлен'.

        Ошибки по отдельным автовозам (например, недопустимый переход
        статуса из CANCELLED/DRAFT) не должны ронять весь запрос: собираем
        их в messages, продолжаем обрабатывать остальные.
        """
        from django.core.exceptions import ValidationError

        updated = 0
        failed = []
        for at in queryset.exclude(status="DELIVERED"):
            try:
                at.status = "DELIVERED"
                if not at.actual_delivery_date:
                    at.actual_delivery_date = timezone.now().date()
                at._transfer_date_override = at.actual_delivery_date or timezone.now().date()
                at.save()
                updated += 1
            except ValidationError as exc:
                failed.append(f"{at.number}: {'; '.join(exc.messages)}")
            except Exception as exc:
                logger.exception("mark_delivered failed for AutoTransport %s", at.pk)
                failed.append(f"{at.number}: {exc}")

        if updated:
            messages.success(request, f'Статус "Доставлен" присвоен {updated} автовозам.')
        if failed:
            messages.warning(
                request,
                f"Не удалось обновить {len(failed)}: " + "; ".join(failed[:10]) + ("…" if len(failed) > 10 else ""),
            )

    mark_delivered.short_description = '🚛 Присвоить статус "Доставлен"'

    def get_urls(self):
        custom_urls = [
            path(
                "<int:pk>/mark-loaded/",
                self.admin_site.admin_view(self.mark_loaded_view),
                name="core_autotransport_mark_loaded",
            ),
        ]
        return custom_urls + super().get_urls()

    def mark_loaded_view(self, request, pk):
        """AJAX endpoint: пометить автовоз как Загружен, авто → Передан"""
        if request.method != "POST":
            return JsonResponse({"error": "POST only"}, status=405)

        try:
            obj = AutoTransport.objects.get(pk=pk)
        except AutoTransport.DoesNotExist:
            return JsonResponse({"error": "Автовоз не найден"}, status=404)

        if obj.status not in ("FORMED", "DRAFT"):
            return JsonResponse(
                {"error": f'Нельзя загрузить автовоз в статусе "{obj.get_status_display()}"'}, status=400
            )

        # Определяем дату передачи
        date_str = request.POST.get("transfer_date", "").strip()
        transfer_date = None
        if date_str:
            try:
                transfer_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse({"error": "Неверный формат даты"}, status=400)
        else:
            transfer_date = timezone.now().date()

        # Меняем статус автовоза
        obj.status = "LOADED"
        obj.loading_date = transfer_date
        # Передаём дату в сигнал через атрибут экземпляра
        obj._transfer_date_override = transfer_date
        obj.save()

        cars_count = obj.cars.filter(status="TRANSFERRED").count()
        return JsonResponse(
            {
                "success": True,
                "message": f"Автовоз {obj.number} загружен. {cars_count} авто переданы ({transfer_date}).",
                "new_status": "LOADED",
                "new_status_display": "Загружен",
            }
        )

    def save_model(self, request, obj, form, change):
        """Save auto-transport with auto-fill fields"""
        if not change:
            obj.created_by = request.user.username

        # Если статус меняется на LOADED/IN_TRANSIT/DELIVERED — передаём дату
        if change and obj.status in ("LOADED", "IN_TRANSIT", "DELIVERED"):
            if not hasattr(obj, "_transfer_date_override"):
                obj._transfer_date_override = obj.loading_date or timezone.now().date()

        super().save_model(request, obj, form, change)

        # Invoice generation is handled by autotransport_post_save signal
        # via Celery task to avoid duplicate creation race condition.

        if obj.status in ("LOADED", "IN_TRANSIT", "DELIVERED"):
            transferred_count = obj.cars.filter(status="TRANSFERRED").count()
            if transferred_count:
                messages.info(request, f"{transferred_count} авто переданы (статус TRANSFERRED)")

    def number_with_unread(self, obj):
        """Номер автовоза с бейджем непрочитанных писем и флажком «ждут ответа»."""
        unread = getattr(obj, "_emails_unread", None)
        if unread is None:
            from core.models_email import ContainerEmail

            unread = (
                ContainerEmail.objects.filter(car_links__car__in=obj.cars.all(), car_links__is_read=False)
                .distinct()
                .count()
                if obj.pk
                else 0
            )
        need_reply = getattr(obj, "_emails_need_reply", 0) or 0

        if unread > 0:
            bg, title = "#dc2626", f"{unread} непрочитанных письма"
        else:
            bg, title = "#10b981", "Непрочитанных писем нет"

        need_reply_html = ""
        if need_reply > 0:
            need_reply_html = format_html(
                '<span title="{}" style="background:#f97316;color:#fff;padding:1px 7px;'
                "border-radius:10px;font-size:11px;font-weight:700;min-width:20px;"
                'text-align:center;line-height:16px;font-variant-numeric:tabular-nums;">'
                '<i class="bi bi-flag-fill"></i> {}</span>',
                f"{need_reply} письмо(-а) ждут ответа",
                need_reply,
            )

        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px;">'
            "<span>{}</span>"
            '<span title="{}" style="background:{};color:#fff;padding:1px 7px;'
            "border-radius:10px;font-size:11px;font-weight:700;min-width:20px;"
            'text-align:center;line-height:16px;font-variant-numeric:tabular-nums;">{}</span>'
            "{}"
            "</span>",
            obj.number or "—",
            title,
            bg,
            unread,
            need_reply_html,
        )

    number_with_unread.short_description = "№"
    number_with_unread.admin_order_field = "number"

    def truck_display(self, obj):
        """Display truck number"""
        return obj.truck_full_number

    truck_display.short_description = "Автовоз"

    def driver_display(self, obj):
        """Display driver"""
        return f"{obj.driver_full_name} ({obj.driver_phone or 'нет тел.'})"

    driver_display.short_description = "Водитель"

    def cars_count_display(self, obj):
        """Car count"""
        # Используем аннотацию из get_queryset (избегаем COUNT-запроса
        # на каждую строку списка); fallback к property для change-form.
        count = getattr(obj, "_cars_count", None)
        if count is None:
            count = obj.cars_count
        return format_html('<span style="font-weight:bold;">{} авто</span>', count)

    cars_count_display.short_description = "Количество авто"

    def status_display(self, obj):
        """Colored status"""
        colors = {
            "DRAFT": "#6c757d",
            "FORMED": "#28a745",
            "LOADED": "#17a2b8",
            "IN_TRANSIT": "#ffc107",
            "DELIVERED": "#28a745",
            "CANCELLED": "#dc3545",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html('<span style="color:{}; font-weight:bold;">{}</span>', color, obj.get_status_display())

    status_display.short_description = "Статус"

    def actions_display(self, obj):
        """Action buttons: Сформировать, Загружен + дата, Инвойсы (цветная)"""
        html = []

        # Кнопка "Сформировать" для черновиков
        if obj.status == "DRAFT":
            html.append(
                format_html(
                    '<a class="button" href="{}" style="margin:2px;">Сформировать</a>',
                    reverse("admin:core_autotransport_change", args=[obj.id]),
                )
            )

        # Кнопка "Загружен" + поле даты для FORMED (и DRAFT)
        if obj.status in ("DRAFT", "FORMED"):
            mark_loaded_url = reverse("admin:core_autotransport_mark_loaded", args=[obj.id])
            html.append(
                format_html(
                    '<span class="at-load-group" style="display:inline-flex;align-items:center;gap:3px;margin:2px;">'
                    '<input type="date" class="at-load-date" data-at-id="{}" '
                    '  style="padding:2px 4px;font-size:11px;border:1px solid #ccc;border-radius:3px;width:120px;">'
                    '<button type="button" class="button at-load-btn" data-at-id="{}" data-url="{}" '
                    '  style="padding:3px 8px;font-size:11px;background:#17a2b8;color:#fff;border:none;'
                    '  border-radius:3px;cursor:pointer;" title="Изменить статус на Загружен">'
                    '<i class="bi bi-truck"></i> Загружен</button>'
                    "</span>",
                    obj.id,
                    obj.id,
                    mark_loaded_url,
                )
            )

        # Кнопка "Инвойсы" с цветовой индикацией
        if obj.id:
            invoice_url = reverse("admin:core_newinvoice_changelist") + f"?auto_transport__id__exact={obj.id}"
            # Используем prefetched список из get_queryset (to_attr='_active_invoices');
            # fallback на запрос — только если страница вызвана вне changelist.
            prefetched = getattr(obj, "_active_invoices", None)
            if prefetched is None:
                statuses = list(
                    NewInvoice.objects.filter(auto_transport=obj)
                    .exclude(status="CANCELLED")
                    .values_list("status", flat=True)
                )
            else:
                statuses = [inv.status for inv in prefetched]

            if not statuses:
                # Нет инвойсов — серая
                html.append(
                    format_html(
                        '<a class="button" href="{}" '
                        'style="margin:2px;padding:3px 8px;font-size:11px;background:#6c757d;color:#fff;'
                        'border:none;border-radius:3px;text-decoration:none;">Инвойсы</a>',
                        invoice_url,
                    )
                )
            else:
                segments = self._get_invoice_color_segments(statuses)

                if len(segments) == 1:
                    # Один цвет — простая кнопка
                    html.append(
                        format_html(
                            '<a class="button" href="{}" '
                            'style="margin:2px;padding:3px 8px;font-size:11px;background:{};color:#fff;'
                            'border:none;border-radius:3px;text-decoration:none;">Инвойсы</a>',
                            invoice_url,
                            segments[0][1],
                        )
                    )
                else:
                    # Несколько цветов — градиентная кнопка
                    gradient_parts = []
                    step = 100 / len(segments)
                    for i, (_, color) in enumerate(segments):
                        start = round(i * step)
                        end = round((i + 1) * step)
                        gradient_parts.append(f"{color} {start}%, {color} {end}%")
                    gradient = f"linear-gradient(90deg, {', '.join(gradient_parts)})"
                    html.append(
                        format_html(
                            '<a class="button" href="{}" '
                            'style="margin:2px;padding:3px 8px;font-size:11px;background:{};color:#fff;'
                            'border:none;border-radius:3px;text-decoration:none;">Инвойсы</a>',
                            invoice_url,
                            gradient,
                        )
                    )

        # Каждый фрагмент уже экранирован через format_html(...) с
        # плейсхолдерами; format_html_join склеивает SafeString'и без
        # повторного экранирования.
        return format_html_join("", "{}", ((fragment,) for fragment in html))

    actions_display.short_description = "Действия"

    @staticmethod
    def _get_invoice_color_segments(statuses):
        """Возвращает список (label, color) сегментов для инвойс-кнопки"""
        color_map = {
            "PAID": "#28a745",  # зеленый — оплачен
            "PARTIALLY_PAID": "#ffc107",  # желтый — частично оплачен
            "ISSUED": "#dc3545",  # красный — выставлен, не оплачен
            "OVERDUE": "#dc3545",  # красный — просрочен
            "DRAFT": "#6c757d",  # серый — черновик
        }
        segments = []
        for s in statuses:
            color = color_map.get(s, "#6c757d")
            segments.append((s, color))
        return segments

    def add_view(self, request, form_url="", extra_context=None):
        """Custom add view processing"""
        extra_context = self._get_extra_context(None, extra_context)
        return super().add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Custom change view processing"""
        extra_context = self._get_extra_context(object_id, extra_context)
        return super().change_view(request, object_id, form_url, extra_context)

    def _get_extra_context(self, object_id, extra_context=None):
        """Get context for template"""
        extra_context = extra_context or {}

        from core.models import Car

        # Авто с пометкой «Важное» исключаем из списка доступных для
        # добавления в автовоз (см. m2m_changed pre_add в core/signals.py
        # — там аналогичный запрет на уровне БД на случай, если кто-то
        # обойдёт UI).
        extra_context["cars"] = (
            Car.objects.filter(
                status__in=["UNLOADED", "IN_PORT", "FLOATING"],
                is_important=False,
            )
            .select_related("client")
            .order_by("-id")[:200]
        )

        # If editing existing auto-transport - pass selected IDs
        if object_id:
            try:
                autotransport = self.get_object(None, object_id)
                extra_context["selected_car_ids"] = list(autotransport.cars.values_list("pk", flat=True))
            except Exception:
                logger.exception("Failed to load selected car ids for autotransport %s", object_id)
                extra_context["selected_car_ids"] = []
        else:
            extra_context["selected_car_ids"] = []

        return extra_context


# ==============================================================================
# CarrierTruck / CarrierDriver — отдельные ModelAdmin
# ==============================================================================
#
# Регистрируем явно (раньше были только inline в CarrierAdmin), потому что:
#   1) `AutoTransportAdmin.autocomplete_fields = ('truck', 'driver')`
#      (если такое появится) требует search_fields в target-админке.
#   2) При нескольких сотнях автовозов/водителей удобно искать/
#      фильтровать через стандартный changelist.
#   3) Inline в CarrierAdmin сохраняется — это два независимых способа
#      редактирования (массовый через changelist vs точечный inline).


@admin.register(CarrierTruck)
class CarrierTruckAdmin(admin.ModelAdmin):
    list_display = ("truck_number", "trailer_number", "carrier", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("truck_number", "trailer_number", "carrier__name")
    autocomplete_fields = ("carrier",)
    list_select_related = ("carrier",)
    ordering = ("-created_at",)


@admin.register(CarrierDriver)
class CarrierDriverAdmin(admin.ModelAdmin):
    list_display = ("full_name", "carrier", "phone", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("first_name", "last_name", "phone", "carrier__name")
    autocomplete_fields = ("carrier",)
    list_select_related = ("carrier",)
    ordering = ("last_name", "first_name")
