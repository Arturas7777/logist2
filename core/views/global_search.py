"""Глобальный поиск админки (командная палитра Ctrl+K).

Ищет по основным сущностям — Car, Container, Client, NewInvoice —
и возвращает JSON для выпадающей палитры в топбаре.
Каждая выборка лимитирована, поиск идёт по индексированным полям
(pg_trgm GIN-индексы на vin/brand/client_name/inv_number).
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from core.models import Car, Client, Container
from core.models.billing import NewInvoice

RESULTS_PER_GROUP = 5


@staff_member_required
def global_search(request):
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"groups": []})

    groups = []

    cars = (
        Car.objects.filter(Q(vin__icontains=query) | Q(brand__icontains=query))
        .select_related("client")
        .order_by("-id")[:RESULTS_PER_GROUP]
    )
    if cars:
        groups.append(
            {
                "name": "Автомобили",
                "icon": "bi-car-front-fill",
                "items": [
                    {
                        "label": f"{car.brand} {car.year} — {car.vin}",
                        "sub": ", ".join(
                            part
                            for part in (
                                car.client.name if car.client_id else "",
                                car.get_status_display(),
                            )
                            if part
                        ),
                        "url": reverse("admin:core_car_change", args=[car.pk]),
                    }
                    for car in cars
                ],
            }
        )

    containers = (
        Container.objects.filter(number__icontains=query).select_related("client").order_by("-id")[:RESULTS_PER_GROUP]
    )
    if containers:
        groups.append(
            {
                "name": "Контейнеры",
                "icon": "bi-box-seam-fill",
                "items": [
                    {
                        "label": c.number,
                        "sub": ", ".join(
                            part
                            for part in (
                                c.client.name if c.client_id else "",
                                c.get_status_display(),
                            )
                            if part
                        ),
                        "url": reverse("admin:core_container_change", args=[c.pk]),
                    }
                    for c in containers
                ],
            }
        )

    clients = Client.objects.filter(name__icontains=query).order_by("name")[:RESULTS_PER_GROUP]
    if clients:
        groups.append(
            {
                "name": "Клиенты",
                "icon": "bi-people-fill",
                "items": [
                    {
                        "label": cl.name,
                        "sub": "",
                        "url": reverse("admin:core_client_change", args=[cl.pk]),
                    }
                    for cl in clients
                ],
            }
        )

    invoices = (
        NewInvoice.objects.filter(Q(number__icontains=query) | Q(external_number__icontains=query))
        .select_related("recipient_client")
        .order_by("-date")[:RESULTS_PER_GROUP]
    )
    if invoices:
        groups.append(
            {
                "name": "Инвойсы",
                "icon": "bi-receipt",
                "items": [
                    {
                        "label": inv.number,
                        "sub": ", ".join(
                            part
                            for part in (
                                inv.recipient_name if inv.recipient_client_id else "",
                                f"{inv.total} €",
                                inv.get_status_display(),
                            )
                            if part
                        ),
                        "url": reverse("admin:core_newinvoice_change", args=[inv.pk]),
                    }
                    for inv in invoices
                ],
            }
        )

    return JsonResponse({"groups": groups})
