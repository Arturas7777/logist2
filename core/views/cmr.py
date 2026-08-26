"""Редактор CMR по заявке на автовоз — ``/admin/requests/<pk>/cmr/``.

Список машин заявки и бланк одной машины. Печать — из браузера
(``window.print``), без серверного PDF.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.models.website import TransportCmr, TransportRequest
from core.services.cmr import apply_prefill, parse_cmr_post, pay_rows, prefill_cmr
from core.views.requests_board import _admin_context, _base_queryset


def _request_or_404(pk: int) -> TransportRequest:
    return get_object_or_404(_base_queryset(), pk=pk)


def _car_of_request(transport_request: TransportRequest, car_id: int):
    car = transport_request.cars.filter(pk=car_id).first()
    if car is None:
        raise Http404("Автомобиль не входит в эту заявку.")
    return car


def _get_or_prepare(transport_request: TransportRequest, car, user) -> TransportCmr:
    cmr, created = TransportCmr.objects.get_or_create(
        request=transport_request,
        car=car,
        defaults={"data": prefill_cmr(transport_request, car), "updated_by": user},
    )
    if created:
        return cmr
    if not cmr.data:
        cmr.data = prefill_cmr(transport_request, car)
        cmr.updated_by = user
        cmr.save(update_fields=["data", "updated_by", "updated_at"])
    return cmr


@staff_member_required
@require_http_methods(["GET"])
def cmr_list(request: HttpRequest, pk: int) -> HttpResponse:
    transport_request = _request_or_404(pk)
    cars = list(transport_request.cars.select_related("warehouse", "client").order_by("brand", "vin"))
    existing = {row.car_id: row for row in transport_request.cmr_documents.select_related("car")}
    rows = []
    for car in cars:
        cmr = existing.get(car.pk)
        rows.append({"car": car, "cmr": cmr})
    return render(
        request,
        "admin/cmr_list.html",
        _admin_context(
            request,
            title=f"CMR — {transport_request.number}",
            transport_request=transport_request,
            rows=rows,
            card_url=reverse("admin_request_card", args=[transport_request.pk]),
        ),
    )


@staff_member_required
@require_http_methods(["GET", "POST"])
def cmr_editor(request: HttpRequest, pk: int, car_id: int) -> HttpResponse:
    transport_request = _request_or_404(pk)
    car = _car_of_request(transport_request, car_id)
    cmr = _get_or_prepare(transport_request, car, request.user)

    if request.method == "POST":
        action = request.POST.get("action") or "save"
        if action == "prefill":
            cmr.data = apply_prefill(parse_cmr_post(request.POST), prefill_cmr(transport_request, car))
            messages.success(request, "Автополя подставлены из заявки. Ручные графы не изменены.")
        else:
            cmr.data = parse_cmr_post(request.POST)
            messages.success(request, "CMR сохранён.")
        cmr.updated_by = request.user
        cmr.save(update_fields=["data", "updated_by", "updated_at"])
        return redirect(reverse("admin_request_cmr_editor", args=[pk, car_id]))

    return render(
        request,
        "admin/cmr_editor.html",
        {
            "transport_request": transport_request,
            "car": car,
            "cmr": cmr,
            "d": cmr.data or {},
            "pay_rows": pay_rows(cmr.data),
            "list_url": reverse("admin_request_cmr_list", args=[pk]),
            "card_url": reverse("admin_request_card", args=[pk]),
            "auto_print": request.GET.get("print") == "1",
        },
    )
