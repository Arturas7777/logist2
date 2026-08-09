"""Кабинет клиента: заявки с данными автовозов."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.models.website import ClientUser, TransportRequest

from .forms import TransportRequestForm


def _get_client(request):
    try:
        return request.user.clientuser.client
    except ClientUser.DoesNotExist:
        return None


def _known_carriers_json(client):
    """Перевозчики из предыдущих заявок клиента (для быстрого выбора в форме).

    Дедупликация по названию (без учёта регистра), свежие заявки — первыми.
    """
    seen = set()
    carriers = []
    rows = (
        TransportRequest.objects.filter(client=client)
        .exclude(carrier_name="")
        .order_by("-created_at")
        .values_list("carrier_name", "carrier_eori")
    )
    for name, eori in rows:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        carriers.append({"name": name.strip(), "eori": (eori or "").strip()})
    return json.dumps(carriers, ensure_ascii=False)


def _save_request(request, form, client, *, instance=None):
    """Сохранить заявку из формы и вернуть её. Логика статусов:

    * кнопка «Сохранить черновик» → DRAFT;
    * кнопка «Отправить» → SUBMITTED;
    * правка поданной заявки — статус не меняется (SUBMITTED),
      но заявка в статусе «Принята» возвращается в «Подана» —
      администратор должен принять её заново.
    """
    transport_request = form.save(commit=False)
    old_status = instance.status if instance else None

    if "save_draft" in request.POST:
        transport_request.status = "DRAFT"
    elif old_status in (None, "DRAFT", "ACCEPTED"):
        transport_request.status = "SUBMITTED"

    transport_request.client = client
    if instance is None:
        transport_request.created_by = request.user
    transport_request.save()
    form.save_m2m()

    if old_status == "ACCEPTED" and transport_request.status == "SUBMITTED":
        messages.info(
            request,
            f"Заявка {transport_request.number} была изменена после принятия, "
            "поэтому её статус снова «Подана» — администратор рассмотрит её повторно.",
        )
    return transport_request


@login_required
def transport_requests(request):
    """Список заявок на автовоз + форма создания новой."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    if request.method == "POST":
        form = TransportRequestForm(request.POST, client=client)
        if form.is_valid():
            transport_request = _save_request(request, form, client)
            if transport_request.status == "DRAFT":
                messages.success(request, f"Черновик {transport_request.number} сохранён.")
            else:
                messages.success(
                    request,
                    f"Заявка {transport_request.number} подана. Мы свяжемся с вами после обработки.",
                )
            return redirect("website:transport_requests")
    else:
        # Предвыбор авто чекбоксами на дашборде («Создать заявку»):
        # /transport-requests/?cars=1&cars=2 — id не из выборки клиента
        # отбрасываются самим полем формы (queryset уже ограничен).
        initial = {}
        selected_ids = [int(pk) for pk in request.GET.getlist("cars") if pk.isdigit()]
        if selected_ids:
            initial["cars"] = selected_ids
        form = TransportRequestForm(client=client, initial=initial)

    requests_qs = TransportRequest.objects.filter(client=client).prefetch_related("cars")

    return render(
        request,
        "website/client_transport_requests.html",
        {
            "client": client,
            "form": form,
            "transport_requests": requests_qs,
            "editing": None,
            "known_carriers_json": _known_carriers_json(client),
        },
    )


@login_required
def transport_request_edit(request, pk):
    """Редактирование заявки клиентом (до статуса «В процессе»)."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if not transport_request.is_client_editable:
        messages.error(
            request,
            f"Заявка {transport_request.number} уже в работе "
            f"(статус «{transport_request.get_status_display()}») и недоступна для редактирования.",
        )
        return redirect("website:transport_requests")

    if request.method == "POST":
        form = TransportRequestForm(request.POST, client=client, instance=transport_request)
        if form.is_valid():
            saved = _save_request(request, form, client, instance=transport_request)
            if saved.status == "DRAFT":
                messages.success(request, f"Черновик {saved.number} сохранён.")
            else:
                messages.success(request, f"Заявка {saved.number} сохранена.")
            return redirect("website:transport_requests")
    else:
        form = TransportRequestForm(client=client, instance=transport_request)

    requests_qs = TransportRequest.objects.filter(client=client).prefetch_related("cars")

    return render(
        request,
        "website/client_transport_requests.html",
        {
            "client": client,
            "form": form,
            "transport_requests": requests_qs,
            "editing": transport_request,
            "known_carriers_json": _known_carriers_json(client),
        },
    )
