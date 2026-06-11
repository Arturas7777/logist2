"""Личный кабинет клиента: dashboard, car_detail, container_detail."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, render

from core.models import Car, Container
from core.models_website import CarPhoto, ClientUser, ContainerPhoto

# Размер страницы списка авто в кабинете клиента. Раньше дашборд грузил
# ВСЕ авто клиента (со всеми публичными фото) — для клиента с сотнями
# машин это тяжёлый запрос и большой HTML. Теперь — постранично.
CARS_PER_PAGE = 50
CONTAINERS_PER_PAGE = 50


@login_required
def client_dashboard(request):
    """Главная страница личного кабинета клиента (список авто и контейнеров)."""
    try:
        client_user = request.user.clientuser
        client = client_user.client

        cars_qs = (
            Car.objects.filter(client=client)
            .select_related("warehouse", "container")
            .prefetch_related(
                Prefetch("photos", queryset=CarPhoto.objects.filter(is_public=True)),
                Prefetch("container__photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
            )
            .order_by("-id")
        )

        # Статистика считается одним агрегатом по всей выборке клиента,
        # чтобы цифры были верны независимо от текущей страницы.
        stats = Car.objects.filter(client=client).aggregate(
            total=Count("id"),
            in_transit=Count("id", filter=Q(status__in=("FLOATING", "IN_PORT"))),
            transferred=Count("id", filter=Q(status="TRANSFERRED")),
        )

        paginator = Paginator(cars_qs, CARS_PER_PAGE)
        cars_page = paginator.get_page(request.GET.get("page"))

        # Таблице контейнеров нужны только номер/линия/склад/статус/даты и
        # количество машин — фото и сами машины не выводятся, поэтому вместо
        # prefetch — один Count в SQL. Список постраничный, как и авто.
        containers_qs = (
            Container.objects.filter(client=client)
            .select_related("line", "warehouse")
            .annotate(cars_count=Count("container_cars"))
            .order_by("-id")
        )
        containers_paginator = Paginator(containers_qs, CONTAINERS_PER_PAGE)
        containers_page = containers_paginator.get_page(request.GET.get("cpage"))

        context = {
            "client": client,
            "cars": cars_page,
            "cars_page": cars_page,
            "containers": containers_page,
            "containers_page": containers_page,
            "cars_count": stats["total"] or 0,
            "containers_count": containers_paginator.count,
            "cars_in_transit": stats["in_transit"] or 0,
            "cars_transferred": stats["transferred"] or 0,
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
                # Шаблону нужны только эти поля авто — не тянем всю строку Car.
                Prefetch(
                    "container_cars",
                    queryset=Car.objects.only("id", "vin", "brand", "year", "status", "container"),
                ),
            ),
            id=container_id,
            client=client_user.client,
        )

        return render(request, "website/container_detail.html", {"container": container})
    except ClientUser.DoesNotExist:
        return render(request, "website/not_authorized.html", status=403)
