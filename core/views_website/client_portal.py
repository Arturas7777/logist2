"""Личный кабинет клиента: dashboard, car_detail, container_detail."""

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from core.models import Car, Container
from core.models_website import CarPhoto, ClientUser, ContainerPhoto


@login_required
def client_dashboard(request):
    """Главная страница личного кабинета клиента (список авто и контейнеров)."""
    try:
        client_user = request.user.clientuser
        client = client_user.client

        cars = list(
            Car.objects.filter(client=client)
            .select_related("warehouse", "container")
            .prefetch_related(
                Prefetch("photos", queryset=CarPhoto.objects.filter(is_public=True)),
                Prefetch("container__photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
            )
            .order_by("-id")
        )

        containers = list(
            Container.objects.filter(client=client)
            .select_related("line", "warehouse")
            .prefetch_related(
                Prefetch("photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
                "container_cars",
            )
            .order_by("-id")
        )

        cars_in_transit = sum(1 for c in cars if c.status in ("FLOATING", "IN_PORT"))
        cars_transferred = sum(1 for c in cars if c.status == "TRANSFERRED")

        context = {
            "client": client,
            "cars": cars,
            "containers": containers,
            "cars_count": len(cars),
            "containers_count": len(containers),
            "cars_in_transit": cars_in_transit,
            "cars_transferred": cars_transferred,
        }

        return render(request, "website/client_dashboard.html", context)
    except ClientUser.DoesNotExist:
        return render(request, "website/not_authorized.html", status=403)


@login_required
def car_detail(request, car_id):
    try:
        client_user = request.user.clientuser
        car = get_object_or_404(
            Car.objects.select_related("warehouse", "container", "line", "carrier").prefetch_related(
                Prefetch("photos", queryset=CarPhoto.objects.filter(is_public=True))
            ),
            id=car_id,
            client=client_user.client,
        )

        return render(request, "website/car_detail.html", {"car": car})
    except ClientUser.DoesNotExist:
        return render(request, "website/not_authorized.html", status=403)


@login_required
def container_detail(request, container_id):
    try:
        client_user = request.user.clientuser
        container = get_object_or_404(
            Container.objects.select_related("line", "warehouse").prefetch_related(
                Prefetch("photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
                Prefetch("container_cars", queryset=Car.objects.all()),
            ),
            id=container_id,
            client=client_user.client,
        )

        return render(request, "website/container_detail.html", {"container": container})
    except ClientUser.DoesNotExist:
        return render(request, "website/not_authorized.html", status=403)
