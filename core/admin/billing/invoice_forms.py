"""Mixin с обработчиками формы (add/change/save/related) для :class:`NewInvoiceAdmin`.

Здесь сосредоточена ВСЯ нетривиальная логика жизненного цикла инвойса в
админке: построение extra_context для кастомного шаблона, поиск
кандидатов на связку, сохранение по кастомной форме (issuer/recipient как
полиморфные ссылки, manual_items, AI-аудит) и сигналы.

См. :mod:`invoice_display` для колонок и :mod:`invoice_actions` для admin
actions.
"""

import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect

from core.admin.billing.invoice_display import build_items_pivot_table
from core.models_billing import ExpenseCategory, InvoiceItem, NewInvoice
from core.services.billing_service import BillingService
from core.utils import parse_money, quantize_money

logger = logging.getLogger(__name__)


class NewInvoiceFormHandlerMixin:
    """Lifecycle хендлеры (`add_view`, `change_view`, save_*) для NewInvoiceAdmin."""

    # Остальные статусы — следствие оплат и срока (update_status), отмена —
    # через BillingService.cancel_invoice.
    _FORM_SETTABLE_STATUSES = ("DRAFT", "ISSUED")

    def get_queryset(self, request):
        """Оптимизация N+1 для списка инвойсов.

        list_display обращается к issuer_* / recipient_* / category /
        linked_invoice / linked_from (reverse OneToOne) / audit / created_by —
        без select_related на каждую строку было до 8 доп. запросов.
        """
        from django.db.models import Exists, OuterRef

        from core.models.accounting import SiteProInvoiceSync

        qs = super().get_queryset(request)
        return qs.select_related(
            "issuer_company",
            "issuer_warehouse",
            "issuer_line",
            "issuer_carrier",
            "recipient_client",
            "recipient_warehouse",
            "recipient_line",
            "recipient_carrier",
            "recipient_company",
            "category",
            "linked_invoice",
            "linked_from",
            "created_by",
            # Колонка «Сигналы» (V5): статус AI-аудита без запроса на строку.
            "audit",
        ).annotate(
            _sitepro_sent=Exists(
                SiteProInvoiceSync.objects.filter(
                    invoice_id=OuterRef("pk"),
                    sync_status__in=("SENT", "PDF_READY"),
                )
            ),
        )

    def add_view(self, request, form_url="", extra_context=None):
        if request.method == "POST":
            return self._handle_custom_form(request, None)

        extra_context = self._get_extra_context(None, extra_context)
        return super().add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "POST":
            return self._handle_custom_form(request, object_id)

        extra_context = self._get_extra_context(object_id, extra_context)
        return super().change_view(request, object_id, form_url, extra_context)

    def _get_extra_context(self, object_id, extra_context=None):
        """Контекст для кастомного шаблона change_form.

        Раньше на каждое открытие формы загружалось ``Company.objects.all()``,
        ``Client.objects.all()`` (>100 объектов) и ``Car.objects.all()[:500]``
        со ``select_related``. Шаблон при этом использует только ``cars``
        для рендеринга ``<option>``-ов; ``companies`` / ``clients`` — мёртвые
        переменные, оставшиеся от старой версии формы (issuer/recipient
        выбираются через AJAX-эндпоинты ``/core/api/search-*``). Поэтому:

        1. Убираем ``companies`` / ``clients`` из контекста полностью.
        2. ``cars`` теперь — только УЖЕ выбранные машины этого инвойса
           (или пустой queryset при создании). Раньше клали 200 свежих
           машин для локального Select2-поиска; теперь поиск идёт
           server-side через ``cars-autocomplete/`` AJAX-endpoint, и
           прокачивать сотни <option> на каждый рендер change-формы
           больше не нужно (страница стала легче ≈30–50 KB).
        """
        import os

        from django.conf import settings

        from core.models import Car, Company

        extra_context = extra_context or {}

        # Сначала вычисляем selected_car_ids — нужны до построения cars.
        selected_car_ids = []
        invoice = None
        if object_id:
            invoice = NewInvoice.objects.filter(pk=object_id).first()
            if invoice:
                selected_car_ids = list(invoice.cars.values_list("pk", flat=True))

        # Только выбранные — Select2 их покажет как pre-selected, а
        # дальше пользователь ищет server-side. При создании нового
        # инвойса список пуст.
        if selected_car_ids:
            extra_context["cars"] = (
                Car.objects.filter(pk__in=selected_car_ids)
                .select_related("client")
                .only("id", "vin", "brand", "year", "status", "client__name")
                .order_by("-id")
            )
        else:
            extra_context["cars"] = Car.objects.none()
        # category_type нужен шаблону (get_category_type_display) — без него
        # в only() каждый <option> делал отдельный запрос за отложенным полем.
        extra_context["expense_categories"] = (
            ExpenseCategory.objects.filter(is_active=True).only("id", "name", "category_type").order_by("order", "name")
        )

        # Caromoto по умолчанию — кэшируется в Company.get_default_id.
        extra_context["default_company_id"] = Company.get_default_id()
        extra_context["default_company_name"] = settings.COMPANY_NAME

        current_status = invoice.status if invoice else "ISSUED"
        settable = self._FORM_SETTABLE_STATUSES + (("CANCELLED",) if invoice else ())
        extra_context["form_status_current"] = current_status
        extra_context["form_status_options"] = [
            (value, label) for value, label in NewInvoice.STATUS_CHOICES if value in settable or value == current_status
        ]
        extra_context["composition_locked"] = bool(invoice) and invoice.status != "DRAFT"

        if invoice:
            try:
                extra_context["pivot_table"] = build_items_pivot_table(invoice)
                extra_context["is_incoming"] = invoice.direction == "INCOMING"
                if invoice.attachment:
                    file_path = os.path.join(settings.MEDIA_ROOT, str(invoice.attachment))
                    extra_context["attachment_exists"] = os.path.isfile(file_path)
                else:
                    extra_context["attachment_exists"] = False
                # Бейдж AI-аудита
                try:
                    extra_context["audit_status"] = self.audit_status_display(invoice)
                except Exception:
                    extra_context["audit_status"] = None
                # B1: выставленный счёт не пересобирается автоматически —
                # показываем расхождение позиций с услугами вместо тихой перезаписи.
                try:
                    extra_context["items_sync_warning"] = self.items_sync_display(invoice)
                except Exception:
                    logger.exception("items_sync_display failed for invoice %s", invoice.pk)
                    extra_context["items_sync_warning"] = None
                # Обратная связь: кто ссылается на этот инвойс
                try:
                    extra_context["linked_from_invoice"] = invoice.linked_from
                except NewInvoice.DoesNotExist:
                    extra_context["linked_from_invoice"] = None

                # Кандидаты для связки (тот же контрагент, близкая сумма, не связан)
                extra_context["link_candidates"] = self._find_link_candidates(invoice)
            except NewInvoice.DoesNotExist:
                pass
        extra_context["selected_car_ids"] = selected_car_ids

        return extra_context

    def _find_link_candidates(self, invoice):
        """Найти инвойсы-кандидаты для связки в пару real ↔ official."""
        if invoice.linked_invoice_id:
            return []
        try:
            if invoice.linked_from:
                return []
        except NewInvoice.DoesNotExist:
            pass

        if not invoice.total or invoice.total <= 0:
            return []

        # Допуск ±1% суммы, но не меньше 0.05 € — защита от округлений и
        # расхождений на копейки при конвертации валют или банковских комиссиях.
        tolerance = max(invoice.total * Decimal("0.01"), Decimal("0.05"))
        total_min = invoice.total - tolerance
        total_max = invoice.total + tolerance

        qs = (
            NewInvoice.objects.filter(
                total__gte=total_min,
                total__lte=total_max,
                linked_invoice__isnull=True,
            )
            .exclude(
                pk=invoice.pk,
            )
            .exclude(
                pk__in=NewInvoice.objects.filter(linked_invoice__isnull=False).values_list(
                    "linked_invoice_id", flat=True
                )
            )
        )

        # Валюты должны совпадать: нельзя связать EUR-инвойс с USD.
        invoice_currency = getattr(invoice, "currency", None)
        if invoice_currency:
            qs = qs.filter(currency=invoice_currency)

        issuer = invoice.issuer
        if issuer:
            issuer_type = issuer.__class__.__name__.lower()
            qs = qs.filter(**{f"issuer_{issuer_type}": issuer})

        cash_or_proposal = ("INVOICE_BLC", "PROFORMA_BLC", "INVOICE_INCBLC")
        official = ("INVOICE", "PROFORMA", "INVOICE_FACT")
        if invoice.document_type in cash_or_proposal:
            qs = qs.filter(document_type__in=official)
        else:
            qs = qs.filter(document_type__in=cash_or_proposal)

        return list(qs.order_by("-date")[:5])

    def _handle_custom_form(self, request, object_id):
        """Точка входа в обработку нашей кастомной формы.

        Всё тело операции — ОДНА атомарная транзакция. Если любая
        из стадий (save, set M2M, regenerate items, register cash payment,
        AI audit trigger) упадёт, мы откатим всё и не оставим orphan-инвойс
        без позиций/без платежа.
        """
        from datetime import datetime

        from django.db import transaction as db_transaction

        from core.models import Car, Client, Company

        try:
            with db_transaction.atomic():
                return self._handle_custom_form_inner(
                    request,
                    object_id,
                    Car,
                    Client,
                    Company,
                    datetime,
                )
        except Exception:
            logger.exception("Invoice form save failed (object_id=%s)", object_id)
            messages.error(request, "Ошибка при сохранении инвойса — см. логи.")
            if object_id:
                return redirect("admin:core_newinvoice_change", object_id)
            return redirect("admin:core_newinvoice_add")

    def _handle_custom_form_inner(self, request, object_id, Car, Client, Company, datetime):
        if object_id:
            invoice = NewInvoice.objects.get(pk=object_id)
        else:
            invoice = NewInvoice()

        date_str = request.POST.get("date")
        if date_str:
            invoice.date = datetime.strptime(date_str, "%Y-%m-%d").date()

        due_date_str = request.POST.get("due_date")
        if due_date_str:
            invoice.due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        else:
            invoice.due_date = None

        cancel_requested = self._apply_form_status(request, invoice, is_new=not object_id)
        invoice.notes = request.POST.get("notes", "")
        invoice.external_number = request.POST.get("external_number", "")

        new_doc_type = request.POST.get("document_type", "PROFORMA")
        if new_doc_type not in dict(NewInvoice.DOCUMENT_TYPE_CHOICES):
            new_doc_type = "PROFORMA"
        if object_id and invoice.document_type != new_doc_type:
            old_num = BillingService.change_invoice_series(invoice, new_doc_type, created_by=request.user)
            messages.info(request, f"Серия изменена: {old_num} → {invoice.number}")
        else:
            invoice.document_type = new_doc_type

        category_id = request.POST.get("category")
        if category_id:
            invoice.category = ExpenseCategory.objects.filter(pk=category_id).first()
        else:
            invoice.category = None

        if "attachment" in request.FILES:
            invoice.attachment = request.FILES["attachment"]

        linked_val = request.POST.get("linked_invoice", "").strip()
        if linked_val:
            if linked_val.isdigit():
                invoice.linked_invoice = NewInvoice.objects.filter(pk=linked_val).first()
            else:
                invoice.linked_invoice = NewInvoice.objects.filter(number=linked_val).first()
            if not invoice.linked_invoice and linked_val:
                messages.warning(request, f"Связанный счёт «{linked_val}» не найден")
        else:
            invoice.linked_invoice = None

        invoice.skip_ai_comparison = "skip_ai_comparison" in request.POST

        if object_id and invoice.status != "DRAFT":
            self._save_locked_invoice(request, invoice)
        else:
            self._save_invoice_composition(request, invoice, Car, Client, Company)

        if not object_id and invoice.document_type in NewInvoice.CASH_DOCUMENT_TYPES and invoice.remaining_amount > 0:
            cash_amount = invoice.remaining_amount
            BillingService.register_cash_payment(invoice, created_by=request.user)
            messages.info(request, f"💵 Оплата наличными зарегистрирована: {cash_amount:.2f} €")

        if cancel_requested:
            try:
                invoice = BillingService.cancel_invoice(invoice, reason=f"через форму ({request.user})")
                messages.success(request, f"Инвойс {invoice.number} отменён")
            except ValueError as e:
                messages.error(request, str(e))

        has_audit = False
        try:
            has_audit = invoice.audit is not None
        except Exception:
            logger.debug("audit check failed for invoice %s", invoice.pk, exc_info=True)
        if invoice.attachment and invoice.direction == "INCOMING" and not has_audit:
            self._trigger_invoice_audit(request, invoice)

        if "_save" in request.POST:
            return redirect("admin:core_newinvoice_changelist")
        elif "_continue" in request.POST:
            return redirect("admin:core_newinvoice_change", invoice.pk)
        elif "_addanother" in request.POST:
            return redirect("admin:core_newinvoice_add")
        else:
            return redirect("admin:core_newinvoice_changelist")

    def _apply_form_status(self, request, invoice, *, is_new) -> bool:
        """Статус из формы. Возвращает True, если запрошена отмена (выполняется после сохранения)."""
        requested = request.POST.get("status") or ("ISSUED" if is_new else invoice.status)
        if is_new:
            invoice.status = requested if requested in self._FORM_SETTABLE_STATUSES else "ISSUED"
            return False
        if requested == invoice.status:
            return False
        if requested == "CANCELLED":
            return True
        if requested in self._FORM_SETTABLE_STATUSES and invoice.status in self._FORM_SETTABLE_STATUSES:
            invoice.status = requested
            return False
        label = dict(NewInvoice.STATUS_CHOICES).get(requested, requested)
        messages.warning(
            request,
            f"Статус «{label}» вручную не ставится: оплата и просрочка считаются по платежам и сроку оплаты.",
        )
        return False

    @staticmethod
    def _party_value(entity):
        return f"{entity.__class__.__name__.lower()}_{entity.pk}" if entity else ""

    def _locked_changes_requested(self, request, invoice) -> bool:
        post = request.POST
        if post.get("issuer") and post["issuer"] != self._party_value(invoice.issuer):
            return True
        if "recipient" in post and post["recipient"] != self._party_value(invoice.recipient):
            return True
        if "cars" in post and {int(pk) for pk in post.getlist("cars") if pk.isdigit()} != set(
            invoice.cars.values_list("pk", flat=True)
        ):
            return True
        try:
            if json.loads(post.get("manual_items_json") or "[]"):
                return True
        except (json.JSONDecodeError, TypeError):
            pass
        manual_total = parse_money(post.get("manual_total"))
        return manual_total is not None and manual_total != invoice.total

    def _save_locked_invoice(self, request, invoice):
        """Выставленный счёт: стороны, машины и позиции не меняются."""
        if self._locked_changes_requested(request, invoice):
            messages.warning(
                request,
                f"Инвойс {invoice.number} уже {invoice.get_status_display().lower()}: выставитель, получатель, "
                "автомобили и позиции не изменены. Исправление — кредит-нота, «Пересоздать позиции "
                "(принудительно)» или возврат в черновик (если оплат нет).",
            )
        invoice.save()
        messages.success(request, f"✅ Инвойс {invoice.number} сохранен! Сумма: {invoice.total:.2f} €")

    def _save_invoice_composition(self, request, invoice, Car, Client, Company):
        """Новый счёт или черновик: стороны, машины и позиции из формы."""
        invoice.issuer_company = None
        invoice.issuer_warehouse = None
        invoice.issuer_line = None
        invoice.issuer_carrier = None
        invoice.recipient_client = None
        invoice.recipient_company = None
        invoice.recipient_warehouse = None
        invoice.recipient_line = None
        invoice.recipient_carrier = None

        issuer_value = request.POST.get("issuer", "")
        if issuer_value and "_" in issuer_value:
            issuer_type, issuer_id = issuer_value.rsplit("_", 1)
            if issuer_type == "company":
                invoice.issuer_company = Company.objects.get(pk=issuer_id)
            elif issuer_type == "warehouse":
                from core.models import Warehouse

                invoice.issuer_warehouse = Warehouse.objects.get(pk=issuer_id)
            elif issuer_type == "line":
                from core.models import Line

                invoice.issuer_line = Line.objects.get(pk=issuer_id)
            elif issuer_type == "carrier":
                from core.models import Carrier

                invoice.issuer_carrier = Carrier.objects.get(pk=issuer_id)
        else:
            invoice.issuer_company = Company.get_default()

        recipient_value = request.POST.get("recipient", "")
        if recipient_value and "_" in recipient_value:
            recipient_type, recipient_id = recipient_value.rsplit("_", 1)
            if recipient_type == "client":
                invoice.recipient_client = Client.objects.get(pk=recipient_id)
            elif recipient_type == "company":
                invoice.recipient_company = Company.objects.get(pk=recipient_id)
            elif recipient_type == "warehouse":
                from core.models import Warehouse

                invoice.recipient_warehouse = Warehouse.objects.get(pk=recipient_id)
            elif recipient_type == "line":
                from core.models import Line

                invoice.recipient_line = Line.objects.get(pk=recipient_id)
            elif recipient_type == "carrier":
                from core.models import Carrier

                invoice.recipient_carrier = Carrier.objects.get(pk=recipient_id)

        invoice.save()

        car_ids = request.POST.getlist("cars")
        # Режим «Без сверки с базой» = пользователь берёт на себя управление
        # суммой и позициями. Мы обязаны уважать его ручной ввод даже если
        # уже есть AI-аудит, иначе manual_total/manual_items из формы просто
        # игнорируются и пользователь видит «сумма вернулась неправильная».
        skip_ai = bool(getattr(invoice, "skip_ai_comparison", False))

        if car_ids:
            cars = Car.objects.filter(pk__in=car_ids)
            invoice.cars.set(cars)
            has_audit = False
            try:
                has_audit = invoice.direction == "INCOMING" and invoice.audit is not None
            except Exception:
                logger.debug("audit check failed for invoice %s", invoice.pk, exc_info=True)
            if skip_ai:
                # Ручной override: выкидываем ВСЕ позиции (в т.ч. AI-позиции
                # с привязкой к car) и строим из manual_items/manual_total.
                self._handle_manual_items(request, invoice, wipe_all=True)
            elif not has_audit:
                regenerated = invoice.regenerate_items_from_cars()
                # Помечаем, что save_related не должен делать ту же работу повторно
                invoice._items_regenerated_in_form = True
                if not regenerated:
                    messages.warning(
                        request,
                        f"Инвойс {invoice.number} выставлен — позиции не пересобраны автоматически. "
                        "Для перезаписи используйте действие «Пересоздать позиции (принудительно)».",
                    )
            messages.success(
                request,
                f"✅ Инвойс {invoice.number} сохранен! Позиций: {invoice.items.count()}.",
            )
        else:
            invoice.cars.clear()
            has_audit = False
            try:
                has_audit = invoice.direction == "INCOMING" and invoice.audit is not None
            except Exception:
                logger.debug("audit check failed for invoice %s", invoice.pk, exc_info=True)
            if skip_ai:
                self._handle_manual_items(request, invoice, wipe_all=True)
            elif not has_audit:
                self._handle_manual_items(request, invoice)
            messages.success(
                request,
                f"✅ Инвойс {invoice.number} сохранен! Сумма: {invoice.total:.2f} €",
            )

    def _handle_manual_items(self, request, invoice, wipe_all=False):
        """Обработка ручного ввода суммы и позиций (для инвойсов без автомобилей).

        Логика:

        1. Если переданы ручные позиции (``manual_items_json``) — создаём
           ``InvoiceItem`` для каждой, пересчитываем total из позиций.
        2. Если нет позиций, но задан ``manual_total > 0`` — создаём одну
           позицию ``Оплата по счёту {номер}`` с указанной суммой.
        3. Если ``manual_total = 0`` и нет позиций — ничего не делаем
           (total остаётся 0).

        ``wipe_all=True``: удалить ВСЕ позиции (включая созданные AI-аудитом
        из PDF с привязкой к ``car``). Используется в режиме «Без сверки
        с базой», когда пользователь явно перехватывает управление суммой
        и не хочет, чтобы результаты AI остались в позициях.
        """
        manual_items_raw = request.POST.get("manual_items_json", "[]")
        manual_total = parse_money(request.POST.get("manual_total")) or Decimal("0")

        manual_items = []
        try:
            manual_items = json.loads(manual_items_raw)
            if not isinstance(manual_items, list):
                manual_items = []
        except (json.JSONDecodeError, TypeError):
            manual_items = []

        if wipe_all:
            # Режим ручного override — убираем и AI-позиции с car тоже.
            invoice.items.all().delete()
        else:
            # Обычный режим — только ручные позиции (без car).
            invoice.items.filter(car__isnull=True).delete()

        if manual_items:
            order = 0
            for item_data in manual_items:
                desc = str(item_data.get("description", "")).strip()
                if not desc:
                    continue
                qty = parse_money(item_data.get("quantity", 1))
                price = parse_money(item_data.get("unit_price", 0))
                if qty is None or price is None:
                    continue

                if qty <= 0 and price <= 0:
                    continue

                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    total_price=quantize_money(qty * price),
                    order=order,
                )
                order += 1

            invoice.calculate_totals()
            invoice.save(update_fields=["subtotal", "total"])

        elif manual_total > 0:
            # Нет ручных позиций, но задана сумма — создаём одну позицию.
            ext_num = invoice.external_number or invoice.number
            description = f"Оплата по счёту {ext_num}"

            InvoiceItem.objects.create(
                invoice=invoice,
                description=description,
                quantity=Decimal("1"),
                unit_price=manual_total,
                total_price=manual_total,
                order=0,
            )

            invoice.calculate_totals()
            invoice.save(update_fields=["subtotal", "total"])
        elif wipe_all:
            # Удалили все items и ничего не создали — total надо занулить,
            # иначе останется старое AI-значение в БД.
            invoice.calculate_totals()
            invoice.save(update_fields=["subtotal", "total"])

    def save_model(self, request, obj, form, change):
        """Сохраняем инвойс; без выставителя — компания по умолчанию (``COMPANY_NAME``)."""
        if not obj.issuer_company and not obj.issuer_warehouse and not obj.issuer_line and not obj.issuer_carrier:
            from core.models import Company

            obj.issuer_company = Company.get_default()
            if obj.issuer_company is None:
                logger.warning("Компания по умолчанию (COMPANY_NAME) не найдена в базе данных")

        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        """После сохранения M2M создаём позиции из автомобилей и запускаем AI-анализ PDF."""
        super().save_related(request, form, formsets, change)

        obj = form.instance

        # Если `_handle_custom_form_inner` уже перегенерировал позиции в рамках
        # той же транзакции — не делаем это второй раз здесь. Иначе для каждого
        # сохранения выполнялись бы ДВЕ регенерации позиций (и два email/invoice
        # recalc сигнала).
        if getattr(obj, "_items_regenerated_in_form", False):
            obj._items_regenerated_in_form = False
            # AI-анализ всё равно может быть нужен, так что не выходим.
        else:
            has_audit = False
            try:
                has_audit = obj.direction == "INCOMING" and obj.audit is not None
            except Exception:
                logger.debug("audit check failed for invoice %s", obj.pk, exc_info=True)
            # skip_ai_comparison = пользователь берёт контроль, не перезаписываем.
            if obj.cars.exists() and not has_audit and not obj.skip_ai_comparison:
                if obj.regenerate_items_from_cars():
                    messages.success(
                        request,
                        f"Автоматически создано {obj.items.count()} позиций из услуг автомобилей!",
                    )

        has_audit = False
        try:
            has_audit = obj.audit is not None
        except Exception:
            logger.debug("audit check failed for invoice %s", obj.pk, exc_info=True)
        if obj.attachment and obj.direction == "INCOMING" and not has_audit:
            self._trigger_invoice_audit(request, obj)

    def _trigger_invoice_audit(self, request, obj):
        """Создать ``InvoiceAudit`` из вложения и поставить задачу в Celery.

        Использует Celery (``process_invoice_audit_task``). Если Celery
        временно недоступен — деградируем к синхронному вызову в потоке, но
        уже не теряем retry/visibility: задача ушла бы в брокер.
        """
        import os

        from django.db import transaction as db_transaction

        from core.models_invoice_audit import InvoiceAudit

        try:
            audit = InvoiceAudit.objects.filter(invoice=obj).first()
            if audit:
                return

            filename = os.path.basename(obj.attachment.name) if obj.attachment else ""
            audit = InvoiceAudit.objects.create(
                pdf_file=obj.attachment,
                original_filename=filename,
                invoice=obj,
                created_by=request.user,
                status=InvoiceAudit.STATUS_PENDING,
            )

            audit_pk = audit.pk

            def _enqueue():
                try:
                    from core.tasks import process_invoice_audit_task

                    process_invoice_audit_task.delay(audit_pk)
                except Exception:
                    logger.exception(
                        "Celery unavailable for InvoiceAudit #%s — no sync fallback, will retry via cron",
                        audit_pk,
                    )

            db_transaction.on_commit(_enqueue)

            messages.info(
                request,
                f"AI-анализ PDF поставлен в очередь (Audit #{audit.pk}). Обновите страницу через минуту.",
            )
        except Exception:
            logger.exception("Error triggering invoice audit for NewInvoice #%s", obj.pk)
            messages.warning(request, "Не удалось запустить AI-анализ PDF — см. логи.")
