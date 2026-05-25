"""Mixin с кастомными URL и view-функциями для :class:`NewInvoiceAdmin`.

Регистрирует:

* ``calc-cars-total/`` — AJAX-расчёт суммы по выбранным авто и выставителю
  (используется в JS на change-форме инвойса).
* ``<int:invoice_id>/pay/`` — форма быстрой оплаты (кнопка «💳 Оплатить»
  в колонке ``actions_display``).
"""

from decimal import Decimal

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path

from core.models_billing import NewInvoice
from core.services.billing_service import BillingService


class NewInvoiceUrlsMixin:
    """Кастомные admin-URL для NewInvoiceAdmin."""

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:invoice_id>/pay/",
                self.admin_site.admin_view(self.pay_invoice_view),
                name="pay_invoice",
            ),
            path(
                "calc-cars-total/",
                self.admin_site.admin_view(self.calc_cars_total_view),
                name="calc_cars_total",
            ),
            path(
                "cars-autocomplete/",
                self.admin_site.admin_view(self.cars_autocomplete_view),
                name="newinvoice_cars_autocomplete",
            ),
        ]
        return custom_urls + urls

    def cars_autocomplete_view(self, request):
        """Server-side поиск машин для Select2 на change-форме инвойса.

        Раньше change_form рендерил пред-список 200 машин и Select2
        фильтровал локально — машины вне топ-200 не находились по VIN.
        Теперь — server-side поиск по term без лимита top-N.

        Query params:
          term — подстрока для поиска (VIN / brand / client name);
                 пустая → 20 последних созданных активных машин.

        Response (Select2-совместимый):
          {"results": [{"id": int, "text": str, "status": str}, ...]}
        """
        from django.db.models import Q

        from core.models import Car

        term = (request.GET.get("term") or "").strip()
        # Машины, уже находящиеся в TRANSFERRED, скрываем — соответствует
        # старой логике `cars_qs.exclude(status="TRANSFERRED")`.
        qs = (
            Car.objects.exclude(status="TRANSFERRED")
            .select_related("client")
            .only("id", "vin", "brand", "year", "status", "client__name")
        )
        if term:
            qs = qs.filter(
                Q(vin__icontains=term)
                | Q(brand__icontains=term)
                | Q(client__name__icontains=term)
            )
        qs = qs.order_by("-id")[:20]

        def _text(car):
            label = f"{car.brand or ''} {car.year or ''} ({car.vin})".strip()
            if car.client_id:
                label += f" - {car.client.name}"
            return label

        return JsonResponse(
            {
                "results": [
                    {"id": c.pk, "text": _text(c), "status": c.status} for c in qs
                ],
            }
        )

    def calc_cars_total_view(self, request):
        """AJAX: предварительная сумма по выбранным автомобилям и выставителю."""
        from core.models import Car, Carrier, Company, Line, Warehouse

        car_ids = request.GET.getlist("car_ids[]") or request.GET.getlist("car_ids")
        issuer_value = request.GET.get("issuer", "")

        if not car_ids:
            return JsonResponse({"total": "0.00", "count": 0})

        issuer = None
        issuer_type = ""
        if issuer_value and "_" in issuer_value:
            itype, iid = issuer_value.rsplit("_", 1)
            try:
                if itype == "company":
                    issuer = Company.objects.get(pk=iid)
                    issuer_type = "Company"
                elif itype == "warehouse":
                    issuer = Warehouse.objects.get(pk=iid)
                    issuer_type = "Warehouse"
                elif itype == "line":
                    issuer = Line.objects.get(pk=iid)
                    issuer_type = "Line"
                elif itype == "carrier":
                    issuer = Carrier.objects.get(pk=iid)
                    issuer_type = "Carrier"
            except Exception:
                pass

        if not issuer:
            try:
                issuer = Company.objects.get(name="Caromoto Lithuania")
                issuer_type = "Company"
            except Company.DoesNotExist:
                return JsonResponse({"total": "0.00", "count": 0})

        is_company = issuer_type == "Company"
        cars = Car.objects.filter(pk__in=car_ids)
        grand_total = Decimal("0")

        for car in cars:
            car.update_days_and_storage()
            car.calculate_total_price()

            if issuer_type == "Warehouse":
                services = car.get_warehouse_services()
            elif issuer_type == "Line":
                services = car.get_line_services()
            elif issuer_type == "Carrier":
                services = car.get_carrier_services()
            elif issuer_type == "Company":
                services = car.car_services.all()
            else:
                continue

            for service in services:
                sname = service.get_service_name()
                from core.service_codes import NAME_TO_CODE, ServiceCode

                if sname == "Услуга не найдена" or NAME_TO_CODE.get(sname) == ServiceCode.STORAGE:
                    continue
                if is_company:
                    price = (
                        service.custom_price if service.custom_price is not None else service.get_default_price()
                    ) + (service.markup_amount if service.markup_amount is not None else Decimal("0"))
                else:
                    price = service.custom_price if service.custom_price is not None else service.get_default_price()
                grand_total += price * service.quantity

            # Хранение
            if is_company or issuer_type == "Warehouse":
                if car.storage_cost and car.storage_cost > 0 and car.days and car.days > 0:
                    daily_rate = car._get_storage_daily_rate() if car.warehouse else Decimal("0")
                    grand_total += daily_rate * car.days

        return JsonResponse(
            {
                "total": str(grand_total.quantize(Decimal("0.01"))),
                "count": cars.count(),
            }
        )

    def pay_invoice_view(self, request, invoice_id):
        """Форма оплаты инвойса."""
        invoice = NewInvoice.objects.get(pk=invoice_id)

        if request.method == "POST":
            try:
                if invoice.document_type == "PROFORMA_BLC":
                    old_num = invoice.change_series("INVOICE_BLC", created_by=request.user)
                    messages.success(
                        request,
                        f"Серия {old_num} → {invoice.number}, оплата наличными зарегистрирована.",
                    )
                    return redirect("admin:core_newinvoice_change", invoice_id)

                if invoice.document_type in NewInvoice.CASH_DOCUMENT_TYPES and invoice.remaining_amount > 0:
                    cash_amount = invoice.remaining_amount
                    invoice._register_cash_payment(created_by=request.user)
                    messages.success(
                        request,
                        f"💵 Оплата наличными зарегистрирована: {cash_amount:.2f} €",
                    )
                    return redirect("admin:core_newinvoice_change", invoice_id)

                amount = Decimal(request.POST.get("amount", 0))
                method = request.POST.get("method", "CASH")
                description = request.POST.get("description", "")

                payer = invoice.recipient

                result = BillingService.pay_invoice(
                    invoice=invoice,
                    amount=amount,
                    method=method,
                    payer=payer,
                    description=description,
                    created_by=request.user,
                )

                messages.success(
                    request,
                    f"Платеж успешно проведен! Транзакция: {result['transaction'].number}",
                )

                if result["overpayment"] > 0:
                    messages.warning(
                        request,
                        f"Внимание: переплата {result['overpayment']:.2f}",
                    )

                return redirect("admin:core_newinvoice_change", invoice_id)

            except Exception as e:
                messages.error(request, f"Ошибка при проведении платежа: {e!s}")

        client_balance = None
        if invoice.recipient_client:
            client_balance = invoice.recipient_client.balance

        context = {
            "invoice": invoice,
            "remaining": invoice.remaining_amount,
            "client_balance": client_balance,
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
        }

        return render(request, "admin/invoice_pay.html", context)
