"""Личный кабинет клиента: dashboard, car_detail, container_detail."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, render

from core.models import Car, CarModelImage, Container
from core.models.website import TransportRequest
from core.models_website import CarPhoto, ClientUser, ContainerPhoto

# Размер страницы списка авто в кабинете клиента. Раньше дашборд грузил
# ВСЕ авто клиента (со всеми публичными фото) — для клиента с сотнями
# машин это тяжёлый запрос и большой HTML. Теперь — постранично.
CARS_PER_PAGE = 50
CONTAINERS_PER_PAGE = 50


def _attach_model_images(cars):
    """Проставляет каждому авто ``model_image_url`` — мини-картинку модели.

    Повторяет логику подбора ``find_car_model_image_url`` (админка), но одним
    запросом на страницу вместо 2–3 запросов на каждое авто: записей
    CarModelImage мало (десятки), подбор делается в памяти.
    """
    records = list(CarModelImage.objects.filter(is_active=True).exclude(image=""))
    if not records:
        return
    recs = [((r.brand or "").strip().lower(), r) for r in records]

    for car in cars:
        car.model_image_url = None
        brand = (car.brand or "").strip().lower()
        if not brand:
            continue
        best = None
        best_score = None
        for brand_norm, rec in recs:
            if brand_norm != brand and not brand.startswith(brand_norm):
                continue
            # Длиннее совпадение по названию > точный год > запись «на все годы».
            year_score = 2 if rec.year == car.year else (1 if rec.year is None else 0)
            score = (len(brand_norm), year_score)
            if best_score is None or score > best_score:
                best, best_score = rec, score
        if best is None:
            continue
        image_field = best.thumbnail or best.image
        try:
            url = image_field.url
        except ValueError:
            continue
        if best.updated_at:
            url = f"{url}?v={int(best.updated_at.timestamp())}"
        car.model_image_url = url


@login_required
def client_dashboard(request):
    """Главная страница личного кабинета клиента (список авто и контейнеров)."""
    try:
        client_user = request.user.clientuser
        client = client_user.client

        search_query = request.GET.get("q", "").strip()
        # Мультивыбор в фильтре статусов: несколько статусов сразу +
        # спец-значения IN_REQUEST («Уже в заявке») / NO_REQUEST («Без заявки»).
        selected_statuses = [s for s in request.GET.getlist("status") if s.strip()]

        cars_qs = (
            Car.objects.filter(client=client)
            .select_related("warehouse", "container")
            .prefetch_related(Prefetch("photos", queryset=CarPhoto.objects.filter(is_public=True)))
            # Метка «Уже в заявке»: авто состоит в активной заявке на автовоз.
            .annotate(
                in_active_request=Exists(
                    TransportRequest.objects.filter(client=client, cars=OuterRef("pk")).exclude(status="COMPLETED")
                )
            )
        )

        if search_query:
            cars_qs = cars_qs.filter(
                Q(vin__icontains=search_query)
                | Q(brand__icontains=search_query)
                | Q(container__number__icontains=search_query)
            )

        valid_statuses = {code for code, _label in Container.STATUS_CHOICES}
        status_codes = [s for s in selected_statuses if s in valid_statuses]
        if status_codes:
            cars_qs = cars_qs.filter(status__in=status_codes)
        in_request = "IN_REQUEST" in selected_statuses
        no_request = "NO_REQUEST" in selected_statuses
        # Оба сразу = «все», фильтровать нечего.
        if in_request != no_request:
            cars_qs = cars_qs.filter(in_active_request=in_request)

        cars_qs = cars_qs.order_by("-id")

        paginator = Paginator(cars_qs, CARS_PER_PAGE)
        cars_page = paginator.get_page(request.GET.get("page"))
        _attach_model_images(cars_page.object_list)

        # Параметры поиска/фильтра — для сохранения в ссылках пагинации.
        filter_params = []
        if search_query:
            filter_params.append(("q", search_query))
        valid_filter_values = valid_statuses | {"IN_REQUEST", "NO_REQUEST"}
        filter_params += [("status", s) for s in selected_statuses if s in valid_filter_values]
        qs_extra = urlencode(filter_params)

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
            "search_query": search_query,
            "selected_statuses": selected_statuses,
            "car_status_choices": Container.STATUS_CHOICES,
            "qs_extra": qs_extra,
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
