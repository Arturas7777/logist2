"""Кабинет клиента: заявки с данными автовозов и пакетом документов."""

import json
import logging
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from core.models.website import (
    TRANSPORT_DOCUMENT_TYPES,
    TRANSPORT_UPLOAD_ONLY_TYPES,
    ClientUser,
    TransportRequest,
    TransportRequestDocument,
    TransportRequestMessage,
)
from core.services import transport_bulk_split as bulk_split
from core.services import transport_docs as docs_service
from core.services import transport_package_actions as package_actions
from core.services.transport_docs import PackageDataError
from core.services.transport_request_check import required_doc_types

from .forms import TransportRequestForm, client_requestable_cars

logger = logging.getLogger(__name__)


def _svg_data_uri(svg: str) -> str:
    return "data:image/svg+xml," + quote(svg.strip(), safe="")


# Паспорт в стиле bi-passport, но с более крупным силуэтом.
_ICON_PASSPORT_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="#6f42c1">
  <path d="M3.232 1.776A1.5 1.5 0 0 0 2 3.252v10.95c0 .445.191.838.49 1.11.367.422.908.688 1.51.688h8a2 2 0 0 0 2-2V4a2 2 0 0 0-1-1.732v-.47A1.5 1.5 0 0 0 11.232.321l-8 1.454ZM4 3h8a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1"/>
  <circle cx="8" cy="6.85" r="2.25"/>
  <path d="M4.55 12.55c.3-2.05 1.7-3.2 3.45-3.2s3.15 1.15 3.45 3.2v.4H4.55z"/>
</svg>
"""


# Дефолтная вкладка кабинета: черновики сверху, затем поданные / принятые / в работе.
PORTAL_CURRENT_STATUSES = ("DRAFT", "SUBMITTED", "ACCEPTED", "IN_PROGRESS")
PORTAL_TAB_DEFS = (
    ("current", _("Текущие"), PORTAL_CURRENT_STATUSES),
    ("DRAFT", _("Черновики"), ("DRAFT",)),
    ("SUBMITTED", _("Подана"), ("SUBMITTED",)),
    ("ACCEPTED", _("Принята"), ("ACCEPTED",)),
    ("IN_PROGRESS", _("В процессе"), ("IN_PROGRESS",)),
    ("COMPLETED", _("Оформлена"), ("COMPLETED",)),
)
_PORTAL_TAB_CODES = {code for code, _label, _statuses in PORTAL_TAB_DEFS}
_PORTAL_TAB_STATUSES = {code: statuses for code, _label, statuses in PORTAL_TAB_DEFS}


def _client_requests(client):
    """Заявки клиента для списка в кабинете (отменённые скрыты).

    Порядок: черновики, затем «Подана» / «Принята» / «В процессе»,
    в конце «Оформлена». Внутри статуса — свежие сверху.
    """
    status_rank = Case(
        When(status="DRAFT", then=0),
        When(status="SUBMITTED", then=1),
        When(status="ACCEPTED", then=2),
        When(status="IN_PROGRESS", then=3),
        When(status="COMPLETED", then=4),
        default=5,
        output_field=IntegerField(),
    )
    return (
        TransportRequest.objects.filter(client=client)
        .exclude(status="CANCELLED")
        .prefetch_related("cars", "documents", "doc_packages", "messages__car", "bulk_uploads")
        .annotate(_status_rank=status_rank)
        .order_by("_status_rank", "-created_at")
    )


def _portal_tab_query_extra(request):
    """Прочие GET-параметры, чтобы вкладки не теряли предвыбор авто и т.п."""
    query = request.GET.copy()
    query.pop("tab", None)
    encoded = query.urlencode()
    return f"&{encoded}" if encoded else ""


def _resolve_portal_tab(request, transport_requests, editing=None):
    """Активная вкладка: явный ``?tab=``, иначе текущие или «Оформлена»."""
    raw = (request.GET.get("tab") or "").strip()
    if raw in _PORTAL_TAB_CODES:
        return raw
    focus = editing
    if focus is None:
        pk = request.GET.get("docs_req", "")
        if pk.isdigit():
            focus = next((item for item in transport_requests if item.pk == int(pk)), None)
    if focus is not None and focus.status == "COMPLETED":
        return "COMPLETED"
    return "current"


def _portal_tabs_context(request, transport_requests, editing=None):
    """Вкладки статусов и набор статусов активной вкладки для шаблона."""
    active_tab = _resolve_portal_tab(request, transport_requests, editing)
    active_statuses = set(_PORTAL_TAB_STATUSES[active_tab])
    counts = {}
    for item in transport_requests:
        counts[item.status] = counts.get(item.status, 0) + 1
    tabs = []
    for code, label, statuses in PORTAL_TAB_DEFS:
        tabs.append(
            {
                "code": code,
                "label": label,
                "count": sum(counts.get(status, 0) for status in statuses),
                "active": code == active_tab,
            }
        )
    return {
        "active_tab": active_tab,
        "active_tab_statuses": active_statuses,
        "visible_count": sum(1 for item in transport_requests if item.status in active_statuses),
        "request_tabs": tabs,
        "tab_query_extra": _portal_tab_query_extra(request),
    }


def _attach_messages(transport_requests):
    """Вешает на заявки данные переписки для шаблона карточки.

    Считаем в Python по уже предзагруженным ``messages`` — иначе на каждую
    заявку ушло бы по два запроса за счётчиками.
    """
    labels = dict(TRANSPORT_DOCUMENT_TYPES)
    for tr in transport_requests:
        msgs = list(tr.messages.all())
        tr.msg_list = msgs
        tr.unread_for_client = sum(1 for m in msgs if m.is_from_staff and m.read_by_client_at is None)
        tr.pending_doc_labels = [labels.get(code, code) for code in tr.pending_requested_doc_types()]


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


_DOC_TYPE_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)

# Иконка для типа документа пакета: (icon, color).
# icon: класс bi-* | "img:static/path" | "data:..." (data-URI).
_DOC_TYPE_ICONS = {
    "TITLE": ("bi-file-earmark-ruled", "#e8590c"),
    "PASSPORT": (_svg_data_uri(_ICON_PASSPORT_SVG), "#6f42c1"),
    "INVOICE": ("bi-currency-dollar", "#0ca678"),
    "SIGNATURE": ("bi-pen", "#228be6"),
    "PAYMENT_ORDER": ("bi-bank2", "#f76707"),
    "LETTER_USA": ("img:website/images/flag-us.svg", "#1c7ed6"),
    "OBLIGATION": ("img:website/images/icon-person-doc.svg", "#7950f2"),
    "CONTRACT": ("bi-truck", "#2f9e44"),
    "OTHER": ("bi-folder2", "#868e96"),
}


def _docs_return_url(transport_request, car=None, *, open_doc=""):
    """Вернуться к списку заявок с прокруткой к карточке (и опционально окну документа).

    ``docs_car`` нужен только чтобы подсветить строку авто. Меню документов
    клиент открывает сам — автопоказ после сохранения прыгал по экрану.
    """
    url = reverse("website:transport_requests")
    params = [f"docs_req={transport_request.pk}"]
    if car is not None:
        params.append(f"docs_car={car.pk}")
    if open_doc and open_doc in _DOC_TYPE_LABELS:
        params.append(f"open_doc={open_doc}")
    return f"{url}?{'&'.join(params)}#req-{transport_request.pk}"


def _open_doc_url(transport_request, doc_type, car_id=""):
    """URL списка с автооткрытием окна документа в карточке заявки."""
    car = None
    if str(car_id).isdigit():
        car = transport_request.cars.filter(pk=int(car_id)).first()
    return _docs_return_url(transport_request, car, open_doc=doc_type)


def _docs_context(transport_request):
    """Секции документов одной заявки: слоты по каждому авто."""
    added_titles = package_actions.sync_title_documents(transport_request)
    cars = list(transport_request.cars.all())
    package_data = {p.car_id: p.data for p in transport_request.doc_packages.all()}
    bulk_by_car: dict[int, list] = {}
    for upload in transport_request.bulk_uploads.all():
        bulk_by_car.setdefault(upload.car_id, []).append(upload)
    # Прикреплённые только что тайтлы в prefetch не попали — перечитываем.
    documents = (
        TransportRequestDocument.objects.filter(request=transport_request)
        if added_titles
        else transport_request.documents.all()
    )
    docs_by_car = {car.pk: {doc_type: [] for doc_type, _ in TRANSPORT_DOCUMENT_TYPES} for car in cars}
    for doc in documents:
        if doc.car_id in docs_by_car:
            docs_by_car[doc.car_id][doc.doc_type].append(doc)

    # Какие документы обязательны — зависит от страны и процедуры заявки
    # (требования таможни, ``TransportDocumentRule``). Клиенту показываем
    # это прямо в списке документов, чтобы он не гадал, что ещё нужно.
    types_by_car = transport_request.declaration_types_by_car()
    required_cache: dict[str, tuple[str, ...]] = {}

    doc_sections = []
    for car in cars:
        procedure = types_by_car.get(car.pk) or ""
        if procedure not in required_cache:
            required_cache[procedure] = required_doc_types(procedure, transport_request.destination_country)
        required_types = required_cache[procedure]
        slots = []
        present_icons = []
        missing_labels = []
        for doc_type, label in TRANSPORT_DOCUMENT_TYPES:
            docs = docs_by_car[car.pk][doc_type]
            icon, color = _DOC_TYPE_ICONS.get(doc_type, ("bi-file-earmark", "#6c757d"))
            if icon.startswith("img:"):
                icon_img, icon_data, icon_bi = icon[4:], "", ""
            elif icon.startswith("data:"):
                icon_img, icon_data, icon_bi = "", icon, ""
            else:
                icon_img, icon_data, icon_bi = "", "", icon
            icon_is_flag = bool(icon_img) and icon_img.endswith("flag-us.svg")
            # Старые первыми: «Инвойс», затем «Инвойс 2»…
            ordered = sorted(docs, key=lambda d: (d.created_at, d.pk))
            doc_rows = [
                {
                    "doc": doc,
                    "display_name": label if idx == 1 else f"{label} {idx}",
                    # Тайтл, прикреплённый нами из карточки авто: клиенту его
                    # не удалять (вернётся сам) и не перекладывать в другой тип.
                    "is_system_title": doc_type == "TITLE" and doc.is_generated,
                }
                for idx, doc in enumerate(ordered, start=1)
            ]
            slot = {
                "type": doc_type,
                "label": label,
                "docs": ordered,
                "doc_rows": doc_rows,
                # «Остальное» клиент не заполняет вручную: пакет загружается
                # через «Одним файлом», а нераспознанное оседает в этом слоте.
                "is_bulk": doc_type == "OTHER",
                "is_required": doc_type in required_types,
                "is_missing": doc_type in required_types and not docs,
                "can_generate": doc_type not in TRANSPORT_UPLOAD_ONLY_TYPES,
                "icon": icon_bi,
                "icon_img": icon_img,
                "icon_data": icon_data,
                "icon_is_flag": icon_is_flag,
                "icon_color": color,
            }
            slots.append(slot)
            if slot["is_missing"]:
                missing_labels.append(label)
            if ordered:
                latest = ordered[-1]
                name = (latest.filename or "").lower()
                present_icons.append(
                    {
                        "type": doc_type,
                        "label": label,
                        "icon": icon_bi,
                        "icon_img": icon_img,
                        "icon_data": icon_data,
                        "icon_is_flag": icon_is_flag,
                        "icon_color": color,
                        "file_url": latest.preview_url,
                        "filename": latest.filename,
                        "is_image": name.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")),
                    }
                )
        uploads = bulk_by_car.get(car.pk, [])
        doc_sections.append(
            {
                "car": car,
                "slots": slots,
                "present_icons": present_icons,
                "bulk_uploads": uploads,
                "bulk_running": any(u.is_running for u in uploads),
                "missing_labels": missing_labels,
            }
        )
    return {
        "doc_sections": doc_sections,
        "package_data": {str(car.pk): package_data.get(car.pk, {}) for car in cars},
    }


def _docs_map_context(transport_requests):
    """Документы всех заявок списка + JSON для JS-модалок.

    На каждый объект заявки вешается ``doc_sections`` для шаблона карточек и
    данные переписки с менеджером.
    """
    _attach_messages(transport_requests)
    package_by_request = {}
    action_urls = {}
    generate_all_urls = {}
    bulk_urls = {}
    bulk_status_urls = {}
    for tr in transport_requests:
        ctx = _docs_context(tr)
        tr.doc_sections = ctx["doc_sections"]
        package_by_request[str(tr.pk)] = ctx["package_data"]
        action_urls[str(tr.pk)] = reverse("website:transport_request_doc_action", args=[tr.pk])
        generate_all_urls[str(tr.pk)] = reverse("website:transport_request_generate_all", args=[tr.pk])
        bulk_urls[str(tr.pk)] = reverse("website:transport_request_bulk_upload", args=[tr.pk])
        bulk_status_urls[str(tr.pk)] = reverse("website:transport_request_bulk_status", args=[tr.pk])
    return {
        "package_data_by_request_json": json.dumps(package_by_request, ensure_ascii=False),
        "doc_action_urls_json": json.dumps(action_urls, ensure_ascii=False),
        "generate_all_urls_json": json.dumps(generate_all_urls, ensure_ascii=False),
        "bulk_upload_urls_json": json.dumps(bulk_urls, ensure_ascii=False),
        "bulk_status_urls_json": json.dumps(bulk_status_urls, ensure_ascii=False),
    }


def _request_form_data(request):
    """Данные POST для формы заявки.

    С дашборда авто приходят в query string (``/transport-requests/?cars=1&cars=2``)
    и рисуются галочками через ``initial``. Само поле формы читает только тело
    POST: если браузер не отправил чекбоксы, без этого слияния форма считает,
    что автомобили не выбраны.
    """
    data = request.POST
    if data.getlist("cars"):
        return data
    from_query = [pk for pk in request.GET.getlist("cars") if str(pk).isdigit()]
    if not from_query:
        return data
    data = data.copy()
    data.setlist("cars", from_query)
    return data


def _save_request(request, form, client, *, instance=None):
    """Сохранить заявку из формы и вернуть её. Логика статусов:

    * кнопка «Сохранить черновик» → DRAFT;
    * кнопка «Отправить» → SUBMITTED;
    * правка поданной заявки — статус не меняется (SUBMITTED).
    """
    transport_request = form.save(commit=False)
    old_status = instance.status if instance else None

    if "save_draft" in request.POST:
        transport_request.status = "DRAFT"
    elif old_status in (None, "DRAFT"):
        transport_request.status = "SUBMITTED"

    transport_request.client = client
    if instance is None:
        transport_request.created_by = request.user
    transport_request.save()
    form.save_m2m()
    return transport_request


@login_required
def transport_requests(request):
    """Список заявок на автовоз + форма создания новой."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    if request.method == "POST":
        form = TransportRequestForm(_request_form_data(request), client=client)
        if form.is_valid():
            transport_request = _save_request(request, form, client)
            # Клик по кнопке документа на форме создания: сохраняем черновик
            # и открываем то же окно уже на странице редактирования.
            open_doc = request.POST.get("open_doc", "").strip()
            if open_doc:
                return redirect(_open_doc_url(transport_request, open_doc, request.POST.get("open_doc_car", "")))
            if transport_request.status != "DRAFT":
                messages.success(request, "Заявка подана. Мы свяжемся с вами после обработки.")
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

    transport_requests = list(_client_requests(client))
    return render(
        request,
        "website/client_transport_requests.html",
        {
            "client": client,
            "form": form,
            "transport_requests": transport_requests,
            "editing": None,
            "editing_car_ids": set(),
            "known_carriers_json": _known_carriers_json(client),
            "doc_types": TRANSPORT_DOCUMENT_TYPES,
            "docs_req": request.GET.get("docs_req", ""),
            "docs_car": request.GET.get("docs_car", ""),
            "open_doc": request.GET.get("open_doc", ""),
            **_portal_tabs_context(request, transport_requests),
            **_docs_map_context(transport_requests),
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
            f"Заявка уже в работе (статус «{transport_request.get_status_display()}») и недоступна для редактирования.",
        )
        return redirect("website:transport_requests")

    if request.method == "POST":
        form = TransportRequestForm(request.POST, client=client, instance=transport_request)
        if form.is_valid():
            saved = _save_request(request, form, client, instance=transport_request)
            # Клик по кнопке документа: изменения формы сохранены, открываем окно.
            open_doc = request.POST.get("open_doc", "").strip()
            if open_doc:
                return redirect(_open_doc_url(saved, open_doc, request.POST.get("open_doc_car", "")))
            if saved.status != "DRAFT":
                messages.success(request, "Заявка сохранена.")
            return redirect("website:transport_requests")
    else:
        form = TransportRequestForm(client=client, instance=transport_request)

    transport_requests = list(_client_requests(client))
    return render(
        request,
        "website/client_transport_requests.html",
        {
            "client": client,
            "form": form,
            "transport_requests": transport_requests,
            "editing": transport_request,
            "editing_car_ids": {str(pk) for pk in transport_request.cars.values_list("pk", flat=True)},
            "known_carriers_json": _known_carriers_json(client),
            "doc_types": TRANSPORT_DOCUMENT_TYPES,
            # docs_req/docs_car/open_doc — только из GET (возврат после работы с документами).
            # Не подставляем pk заявки автоматически: иначе при клике на карандаш
            # карточки JS скроллил к заявке с параметрами документов.
            "docs_req": request.GET.get("docs_req", ""),
            "docs_car": request.GET.get("docs_car", ""),
            "open_doc": request.GET.get("open_doc", ""),
            **_portal_tabs_context(request, transport_requests, editing=transport_request),
            **_docs_map_context(transport_requests),
        },
    )


@login_required
@require_POST
def transport_request_delete(request, pk):
    """Удаление заявки клиентом — мягкое: статус «Отменена».

    Разрешено только в статусах «Черновик»/«Подана». Заявка скрывается из
    кабинета, но остаётся в системе — администратор видит её со статусом
    «Отменена» (неактуальна), а авто снова доступны для новых заявок.
    """
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if not transport_request.is_client_editable:
        messages.error(
            request,
            f"Заявка уже в работе (статус «{transport_request.get_status_display()}») и не может быть удалена.",
        )
        return redirect("website:transport_requests")

    transport_request.status = "CANCELLED"
    transport_request.save(update_fields=["status", "updated_at"])
    messages.success(request, "Заявка удалена.")
    return redirect("website:transport_requests")


@login_required
@require_GET
def transport_request_download_packages(request, pk):
    """Скачать ZIP: по одному PDF-пакету на каждый VIN (+ тайтл из админки)."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(
        TransportRequest.objects.prefetch_related("cars", "documents"),
        pk=pk,
        client=client,
    )
    if transport_request.status == "CANCELLED":
        messages.error(request, "Отменённую заявку скачать нельзя.")
        return redirect("website:transport_requests")

    try:
        filename, zip_bytes = docs_service.build_request_packages_zip(transport_request)
    except PackageDataError as exc:
        messages.error(request, str(exc))
        return redirect("website:transport_requests")

    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(zip_bytes))
    return response


@login_required
@require_POST
def transport_request_submit(request, pk):
    """Подать черновик заявки (DRAFT → SUBMITTED) из карточки в списке."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if transport_request.status != "DRAFT":
        messages.error(request, "Подать можно только черновик.")
        return redirect("website:transport_requests")
    if not transport_request.cars.exists():
        messages.error(request, "Добавьте хотя бы один автомобиль, затем подайте заявку.")
        return redirect("website:transport_requests")

    transport_request.status = "SUBMITTED"
    transport_request.save(update_fields=["status", "updated_at"])
    messages.success(request, "Заявка подана. Мы свяжемся с вами после обработки.")
    return redirect("website:transport_requests")


def _wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )


def _eligible_cars_for_request(client, transport_request):
    """Авто, которые клиент может добавить в заявку (те же правила, что в форме)."""
    return client_requestable_cars(client, exclude_request_pk=transport_request.pk)


def _render_req_car_row(request, transport_request, section):
    return render_to_string(
        "website/partials/transport_req_car_row.html",
        {"req": transport_request, "section": section, "doc_types": TRANSPORT_DOCUMENT_TYPES},
        request=request,
    )


@login_required
@require_POST
def transport_request_add_cars(request, pk):
    """Добавить один или несколько автомобилей в заявку (AJAX из кабинета)."""
    client = _get_client(request)
    if client is None:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if not transport_request.is_client_editable:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "locked"}, status=400)
        messages.error(request, "Заявка уже в работе — состав автомобилей изменить нельзя.")
        return redirect("website:transport_requests")

    raw_ids = request.POST.getlist("car_ids")
    if not raw_ids and request.POST.get("car_ids"):
        raw_ids = [x.strip() for x in request.POST.get("car_ids", "").split(",") if x.strip()]
    car_ids = []
    for raw in raw_ids:
        if str(raw).isdigit():
            car_ids.append(int(raw))
    car_ids = list(dict.fromkeys(car_ids))
    if not car_ids:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "empty"}, status=400)
        messages.error(request, "Не выбраны автомобили.")
        return redirect("website:transport_request_edit", pk=pk)

    already = set(transport_request.cars.values_list("pk", flat=True))
    to_add_ids = [cid for cid in car_ids if cid not in already]
    cars = list(_eligible_cars_for_request(client, transport_request).filter(pk__in=to_add_ids))
    if not cars:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "unavailable"}, status=400)
        messages.error(request, "Выбранные автомобили недоступны для этой заявки.")
        return redirect("website:transport_request_edit", pk=pk)

    transport_request.cars.add(*cars)
    # Свежий prefetch для секций документов добавленных авто.
    transport_request = (
        TransportRequest.objects.filter(pk=transport_request.pk)
        .prefetch_related("cars", "documents", "doc_packages", "bulk_uploads")
        .get()
    )
    sections_by_car = {s["car"].pk: s for s in _docs_context(transport_request)["doc_sections"]}
    added = []
    for car in cars:
        section = sections_by_car.get(car.pk)
        if not section:
            continue
        added.append(
            {
                "id": car.pk,
                "brand": car.brand or "",
                "vin": car.vin or "",
                "year": car.year or "",
                "html": _render_req_car_row(request, transport_request, section),
            }
        )

    if _wants_json(request):
        return JsonResponse(
            {
                "ok": True,
                "request_id": transport_request.pk,
                "added": added,
                "cars_count": transport_request.cars.count(),
            }
        )
    return redirect("website:transport_request_edit", pk=pk)


@login_required
@require_POST
def transport_request_remove_car(request, pk, car_id):
    """Убрать автомобиль из заявки (и связанные документы пакета)."""
    client = _get_client(request)
    if client is None:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
        return render(request, "website/not_authorized.html", status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if not transport_request.is_client_editable:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "locked"}, status=400)
        messages.error(request, "Заявка уже в работе — состав автомобилей изменить нельзя.")
        return redirect("website:transport_requests")

    car = get_object_or_404(transport_request.cars, pk=car_id)
    for doc in transport_request.documents.filter(car=car):
        doc.file.delete(save=False)
        doc.delete()
    transport_request.doc_packages.filter(car=car).delete()
    transport_request.cars.remove(car)
    if _wants_json(request):
        return JsonResponse(
            {
                "ok": True,
                "request_id": transport_request.pk,
                "car_id": car_id,
                "cars_left": transport_request.cars.count(),
            }
        )
    return redirect("website:transport_requests")


# ---------------------------------------------------------------------------
# Пакет документов автовоза (оформление на Беларусь)
# ---------------------------------------------------------------------------


def _edit_url(transport_request, car=None):
    """Совместимость: после действий с документами возвращаем к карточке в списке."""
    return _docs_return_url(transport_request, car)


def _get_editable_request(request, pk):
    """Заявка клиента, доступная для изменения пакета документов, или None."""
    client = _get_client(request)
    if client is None:
        return None, render(request, "website/not_authorized.html", status=403)
    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    if not transport_request.is_client_editable:
        messages.error(request, "Заявка уже в работе — документы изменить нельзя.")
        return None, redirect("website:transport_requests")
    return transport_request, None


def _push_notices(request, notices):
    """Разложить замечания сервиса в ``django.contrib.messages``."""
    for level, text in notices:
        getattr(messages, level, messages.info)(request, text)


@login_required
@require_POST
def transport_request_doc_action(request, pk):
    """Окно документа пакета: сохранить данные/файлы или сгенерировать PDF."""
    transport_request, error_response = _get_editable_request(request, pk)
    if transport_request is None:
        return error_response

    doc_type = request.POST.get("doc_type", "")
    if doc_type not in _DOC_TYPE_LABELS:
        messages.error(request, "Неизвестный тип документа.")
        return redirect(_edit_url(transport_request))

    car = get_object_or_404(transport_request.cars, pk=request.POST.get("car", ""))
    try:
        notices = package_actions.apply_doc_action(
            transport_request=transport_request,
            car=car,
            doc_type=doc_type,
            post=request.POST,
            files=request.FILES.getlist("files"),
            user=request.user,
        )
    except PackageDataError as exc:
        messages.error(request, str(exc))
        return redirect(_edit_url(transport_request, car))
    _push_notices(request, notices)
    return redirect(_edit_url(transport_request, car))


@login_required
@require_POST
def transport_request_doc_delete(request, pk, doc_id):
    """Удаление файла документа из пакета."""
    transport_request, error_response = _get_editable_request(request, pk)
    if transport_request is None:
        return error_response
    doc = get_object_or_404(TransportRequestDocument, pk=doc_id, request=transport_request)
    car = doc.car
    if doc.doc_type == "TITLE" and doc.is_generated:
        # Тайтл из нашей системы: удаление ничего не даст — он прикрепится
        # обратно при следующем открытии кабинета.
        messages.info(request, "«Тайтл» есть у нас в системе — он остаётся в заявке.")
        return redirect(_edit_url(transport_request, car))
    _push_notices(request, [package_actions.delete_doc(transport_request, doc)])
    return redirect(_edit_url(transport_request, car))


@login_required
@require_POST
def transport_request_generate_all(request, pk):
    """Сгенерировать полный пакет по авто (без договора на перевозку).

    Принимает паспорт, адрес кириллицей, подпись и данные инвойса; сохраняет
    файлы, распознаёт паспорт и создаёт INVOICE / PAYMENT / LETTER / OBLIGATION.
    """
    transport_request, error_response = _get_editable_request(request, pk)
    if transport_request is None:
        return error_response

    car = get_object_or_404(transport_request.cars, pk=request.POST.get("car", ""))
    try:
        notices = package_actions.generate_all_for_car(
            transport_request=transport_request,
            car=car,
            post=request.POST,
            files=request.FILES,
            user=request.user,
        )
    except PackageDataError as exc:
        messages.error(request, str(exc))
        return redirect(_edit_url(transport_request, car))
    _push_notices(request, notices)
    return redirect(_edit_url(transport_request, car))


# ---------------------------------------------------------------------------
# Пакет одним файлом: загрузка и автосортировка
# ---------------------------------------------------------------------------


@login_required
@require_POST
def transport_request_bulk_upload(request, pk):
    """«Одним файлом»: загрузить весь пакет и отдать его на автосортировку."""
    transport_request, error_response = _get_editable_request(request, pk)
    if transport_request is None:
        return error_response

    car = get_object_or_404(transport_request.cars, pk=request.POST.get("car", ""))
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Выберите файл с пакетом документов.")
        return redirect(_edit_url(transport_request, car))

    try:
        bulk_split.queue_upload(transport_request, car, upload, request.user)
    except bulk_split.BulkSplitError as exc:
        messages.error(request, str(exc))
        return redirect(_edit_url(transport_request, car))

    messages.success(
        request,
        "Файл принят: разбираем его на документы и раскладываем по типам. "
        "Это займёт до минуты — обновите страницу, чтобы увидеть результат.",
    )
    return redirect(_edit_url(transport_request, car))


@login_required
@require_GET
def transport_request_bulk_status(request, pk):
    """Статусы разбора пакетов заявки — для опроса из кабинета."""
    client = _get_client(request)
    if client is None:
        return JsonResponse({"ok": False, "error": "Нет доступа."}, status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    uploads = [
        {
            "id": upload.pk,
            "car_id": upload.car_id,
            "status": upload.status,
            "running": upload.is_running,
            "filename": upload.filename,
            "sorted": upload.sorted_labels,
            "error": upload.error_message,
        }
        for upload in transport_request.bulk_uploads.all()[:20]
    ]
    return JsonResponse({"ok": True, "uploads": uploads, "running": any(u["running"] for u in uploads)})


@login_required
@require_POST
def transport_request_doc_retype(request, pk, doc_id):
    """Указать тип документа вручную, если автосортировка не угадала."""
    transport_request, error_response = _get_editable_request(request, pk)
    if transport_request is None:
        return error_response

    doc = get_object_or_404(TransportRequestDocument, pk=doc_id, request=transport_request)
    try:
        label = bulk_split.retype_document(doc, request.POST.get("doc_type", ""))
    except bulk_split.BulkSplitError as exc:
        messages.error(request, str(exc))
        return redirect(_edit_url(transport_request, doc.car))
    messages.success(request, f"Документ перемещён в «{label}».")
    return redirect(_edit_url(transport_request, doc.car))


# ---------------------------------------------------------------------------
# Переписка с менеджером по заявке
# ---------------------------------------------------------------------------


@login_required
@require_POST
def transport_request_message_send(request, pk):
    """Ответ клиента менеджеру по заявке (AJAX).

    Разрешён в любом статусе, кроме отменённой: переписка нужна и после того,
    как заявка ушла в работу. Отправка ответа помечает наши сообщения
    прочитанными — клиент их только что видел.
    """
    client = _get_client(request)
    if client is None:
        return JsonResponse({"ok": False, "error": "Нет доступа."}, status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    body = (request.POST.get("body") or "").strip()
    if not body:
        return JsonResponse({"ok": False, "error": "Пустое сообщение."}, status=400)

    message = TransportRequestMessage.objects.create(
        request=transport_request,
        author_kind=TransportRequestMessage.AUTHOR_CLIENT,
        kind=TransportRequestMessage.KIND_MESSAGE,
        author=request.user,
        body=body,
    )
    transport_request.messages.filter(author_kind="STAFF", read_by_client_at__isnull=True).update(
        read_by_client_at=timezone.now()
    )
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": message.pk,
                "body": message.body,
                "created_at": timezone.localtime(message.created_at).strftime("%d.%m.%Y %H:%M"),
            },
        }
    )


@login_required
@require_POST
def transport_request_messages_read(request, pk):
    """Отметить сообщения менеджера прочитанными (клиент открыл переписку)."""
    client = _get_client(request)
    if client is None:
        return JsonResponse({"ok": False, "error": "Нет доступа."}, status=403)

    transport_request = get_object_or_404(TransportRequest, pk=pk, client=client)
    updated = transport_request.messages.filter(author_kind="STAFF", read_by_client_at__isnull=True).update(
        read_by_client_at=timezone.now()
    )
    return JsonResponse({"ok": True, "updated": updated})
