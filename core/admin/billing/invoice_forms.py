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
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect

from core.models_billing import ExpenseCategory, InvoiceItem, NewInvoice

logger = logging.getLogger(__name__)


class NewInvoiceFormHandlerMixin:
    """Lifecycle хендлеры (`add_view`, `change_view`, save_*) для NewInvoiceAdmin."""

    def get_queryset(self, request):
        """Оптимизация N+1 для списка инвойсов.

        list_display обращается к issuer_* / recipient_* / category /
        linked_invoice / linked_from (reverse OneToOne) / audit / created_by —
        без select_related на каждую строку было до 8 доп. запросов.
        """
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
        2. ``cars`` ограничиваем актуальными (не ``TRANSFERRED``) + 200 шт.
           Уже привязанные к инвойсу машины (любого статуса) добавляются
           поверх, чтобы они оставались видны при редактировании.
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

        # Активные машины + всё, что уже выбрано для редактируемого инвойса.
        cars_qs = Car.objects.exclude(status="TRANSFERRED")
        if selected_car_ids:
            from django.db.models import Q

            cars_qs = Car.objects.filter(Q(pk__in=selected_car_ids) | ~Q(status="TRANSFERRED"))
        extra_context["cars"] = (
            cars_qs.select_related("client")
            .only("id", "vin", "brand", "year", "status", "client__name")
            .order_by("-id")[:200]
        )
        extra_context["expense_categories"] = (
            ExpenseCategory.objects.filter(is_active=True).only("id", "name").order_by("order", "name")
        )

        # Caromoto по умолчанию — кэшируется в Company.get_default_id.
        extra_context["default_company_id"] = Company.get_default_id()

        if invoice:
            try:
                extra_context["pivot_table"] = invoice.get_items_pivot_table()
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

        invoice.status = request.POST.get("status", "ISSUED")
        invoice.notes = request.POST.get("notes", "")
        invoice.external_number = request.POST.get("external_number", "")

        new_doc_type = request.POST.get("document_type", "PROFORMA")
        if new_doc_type not in dict(NewInvoice.DOCUMENT_TYPE_CHOICES):
            new_doc_type = "PROFORMA"
        if object_id and invoice.document_type != new_doc_type:
            old_num = invoice.change_series(new_doc_type, created_by=request.user)
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
            try:
                invoice.issuer_company = Company.objects.get(name="Caromoto Lithuania")
            except Company.DoesNotExist:
                pass

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
                invoice.regenerate_items_from_cars()
                # Помечаем, что save_related не должен делать ту же работу повторно
                invoice._items_regenerated_in_form = True
            messages.success(
                request,
                f"✅ Инвойс {invoice.number} сохранен! Создано {invoice.items.count()} позиций.",
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

        if not object_id and invoice.document_type in NewInvoice.CASH_DOCUMENT_TYPES and invoice.remaining_amount > 0:
            cash_amount = invoice.remaining_amount
            invoice._register_cash_payment(created_by=request.user)
            messages.info(request, f"💵 Оплата наличными зарегистрирована: {cash_amount:.2f} €")

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
        manual_total_str = request.POST.get("manual_total", "0")
        manual_items_raw = request.POST.get("manual_items_json", "[]")

        try:
            manual_total = Decimal(manual_total_str)
        except (InvalidOperation, ValueError, TypeError):
            manual_total = Decimal("0")

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
                try:
                    qty = Decimal(str(item_data.get("quantity", 1)))
                    price = Decimal(str(item_data.get("unit_price", 0)))
                except (InvalidOperation, ValueError, TypeError):
                    continue

                if qty <= 0 and price <= 0:
                    continue

                total_price = qty * price
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    total_price=total_price,
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
        """Сохраняем инвойс и автоматически устанавливаем Caromoto как выставителя."""
        if not obj.issuer_company and not obj.issuer_warehouse and not obj.issuer_line and not obj.issuer_carrier:
            try:
                from core.models import Company

                caromoto = Company.objects.get(name="Caromoto Lithuania")
                obj.issuer_company = caromoto
                logger.info(
                    "Автоматически установлена компания Caromoto Lithuania как выставитель инвойса %s",
                    obj.number,
                )
            except Company.DoesNotExist:
                logger.warning("Компания Caromoto Lithuania не найдена в базе данных")

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
                obj.regenerate_items_from_cars()
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
