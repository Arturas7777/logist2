"""Доска заявок на автовоз в админке — ``/admin/requests/``.

Карточный интерфейс в стиле кабинета клиента, но с максимальными правами
сотрудника: правка данных автовоза и состава машин, работа с пакетом
документов за клиента, переписка с клиентом, письмо складу на литовском и
создание рейса ``AutoTransport``.

Страницы:
  * ``/admin/requests/`` — доска с табами по состоянию, поиском и счётчиками;
  * ``/admin/requests/<pk>/`` — карточка заявки;
  * ``/admin/requests/<pk>/warehouse-letter/`` — черновик письма складу
    (отдельная страница, а не модалка: в админке нет Bootstrap JS).

POST-экшены возвращают JSON (инлайн-правка, сообщения) либо redirect на
карточку с ``django.contrib.messages`` — как в остальных админ-досках.
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models.website import (
    TRANSPORT_DECLARATION_TYPES,
    TRANSPORT_DESTINATION_COUNTRIES,
    TRANSPORT_DOCUMENT_TYPES,
    TRANSPORT_UPLOAD_ONLY_TYPES,
    TransportRequest,
    TransportRequestDocument,
    TransportRequestMessage,
)
from core.services import transport_bulk_split as bulk_split
from core.services import transport_declarations as declarations
from core.services import transport_package_actions as package_actions
from core.services import warehouse_request_email as wh_letter
from core.services.transport_docs import PackageDataError
from core.services.transport_request_check import check_request

logger = logging.getLogger(__name__)

_DOC_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)
_DECLARATION_LABELS = dict(TRANSPORT_DECLARATION_TYPES)
_COUNTRY_LABELS = dict(TRANSPORT_DESTINATION_COUNTRIES)

# Табы доски: код → (подпись, фильтр по queryset).
BOARD_TABS = [
    ("new", "Новые", Q(status__in=["DRAFT", "SUBMITTED"])),
    ("accepted", "Принятые", Q(status="ACCEPTED")),
    ("sent", "У склада", Q(warehouse_state="SENT")),
    ("confirmed", "Подтверждены складом", Q(warehouse_state="CONFIRMED")),
    ("in_progress", "В процессе", Q(status="IN_PROGRESS")),
    ("done", "Оформленные", Q(status="COMPLETED")),
    ("cancelled", "Отменённые", Q(status="CANCELLED")),
    ("all", "Все", Q()),
]

# Поля заявки, доступные сотруднику для инлайн-правки.
EDITABLE_FIELDS = {
    "carrier_name": str,
    "carrier_eori": str,
    "truck_number": str,
    "trailer_number": str,
    "driver_name": str,
    "driver_phone": str,
    "border_crossing": str,
    "planned_loading_date": "date",
    "comment": str,
    "staff_comment": str,
    "declaration_type": "choice",
    "destination_country": "country",
}

# Подписи полей данных пакета для компактной формы в админ-карточке.
PACKAGE_FIELD_LABELS = {
    "buyer_name": "Покупатель (латиницей)",
    "buyer_name_ru": "Покупатель (по-русски)",
    "buyer_birth_date": "Дата рождения",
    "buyer_passport_number": "Номер паспорта",
    "buyer_passport_issue_date": "Дата выдачи паспорта",
    "buyer_address": "Адрес (латиницей)",
    "buyer_address_ru": "Адрес (кириллицей)",
    "invoice_number": "Номер инвойса",
    "invoice_date": "Дата инвойса",
    "invoice_amount": "Сумма инвойса, USD",
    "contract_number": "Номер договора",
    "contract_date": "Дата договора",
    "carrier_company": "Перевозчик (компания)",
    "carrier_address": "Адрес перевозчика",
    "carrier_director": "Директор перевозчика",
    "carrier_regon": "REGON",
    "carrier_nip": "NIP",
    "carrier_krs": "KRS",
}


def _base_queryset():
    return (
        TransportRequest.objects.select_related("client", "warehouse", "auto_transport")
        .prefetch_related("cars__warehouse", "cars__container", "documents", "doc_packages", "bulk_uploads")
        .annotate(
            unread_client_msgs=Count(
                "messages",
                filter=Q(messages__author_kind="CLIENT", messages__read_by_staff_at__isnull=True),
                distinct=True,
            ),
        )
    )


def _admin_context(request: HttpRequest, **extra):
    from logist2.admin_site import admin_site

    return {**admin_site.each_context(request), **extra}


# ---------------------------------------------------------------------------
# Доска
# ---------------------------------------------------------------------------


@staff_member_required
@require_GET
def requests_board_page(request: HttpRequest):
    """Доска заявок: табы по состоянию, поиск, карточки со сводкой."""
    tab = request.GET.get("tab", "new")
    search = (request.GET.get("q", "") or "").strip()

    tab_filters = {code: flt for code, _label, flt in BOARD_TABS}
    if tab not in tab_filters:
        tab = "new"

    queryset = _base_queryset()
    if search:
        queryset = queryset.filter(
            Q(number__icontains=search)
            | Q(client__name__icontains=search)
            | Q(carrier_name__icontains=search)
            | Q(truck_number__icontains=search)
            | Q(trailer_number__icontains=search)
            | Q(driver_name__icontains=search)
            | Q(cars__vin__icontains=search)
        ).distinct()

    counts = {}
    for code, _label, flt in BOARD_TABS:
        counts[code] = _base_queryset().filter(flt).count()

    # Лимит намеренно небольшой: на каждую карточку считается полнота пакета
    # документов (несколько запросов), а работают с доской через табы и поиск.
    rows = list(queryset.filter(tab_filters[tab]).order_by("-created_at")[:60])
    cards = [_board_card(tr) for tr in rows]

    tabs = [
        {"code": code, "label": label, "count": counts.get(code, 0), "active": code == tab}
        for code, label, _flt in BOARD_TABS
    ]

    return render(
        request,
        "admin/requests_board.html",
        _admin_context(
            request,
            title="Заявки на автовоз",
            tabs=tabs,
            cards=cards,
            search=search,
            active_tab=tab,
            total_unread=sum(card["unread"] for card in cards),
        ),
    )


def _board_card(transport_request) -> dict:
    """Данные одной карточки доски (без тяжёлых запросов на каждую заявку)."""
    readiness = check_request(transport_request)
    rows_by_car = {}
    for status in readiness.cars:
        car = status.car
        rows_by_car[car.pk] = {
            "car": car,
            "warehouse": car.warehouse.name if car.warehouse_id else "",
            "container": car.container.number if car.container_id else "",
            "missing": status.missing_labels,
            "is_complete": status.is_complete,
        }

    # Машины на карточке сгруппированы по декларациям, а не одним списком:
    # сотруднику важно видеть, что именно заказывается складу — одна
    # декларация на несколько авто или несколько отдельных.
    plan = declarations.declaration_plan(transport_request, include_empty=False)
    blocks = [
        {
            "index": index,
            "type_display": line.type_display,
            "is_separate": not line.is_default,
            "note": line.note,
            "cars": [rows_by_car[car.pk] for car in line.cars if car.pk in rows_by_car],
        }
        for index, line in enumerate(plan, start=1)
    ]

    return {
        "request": transport_request,
        "declarations": blocks,
        "readiness": readiness,
        "unread": getattr(transport_request, "unread_client_msgs", 0) or 0,
        "unread_emails": transport_request.email_links.filter(is_read=False).count(),
        "url": reverse("admin_request_card", args=[transport_request.pk]),
    }


# ---------------------------------------------------------------------------
# Карточка заявки
# ---------------------------------------------------------------------------


@staff_member_required
@require_GET
def request_card_page(request: HttpRequest, pk: int):
    """Карточка заявки: данные, машины, документы, переписка, склад."""
    transport_request = get_object_or_404(_base_queryset(), pk=pk)
    if package_actions.sync_title_documents(transport_request):
        # Прикреплённые тайтлы в prefetch не попали — перечитываем заявку.
        transport_request = get_object_or_404(_base_queryset(), pk=pk)
    readiness = check_request(transport_request)
    types_by_car = transport_request.declaration_types_by_car()
    declaration_lines = declarations.declaration_plan(transport_request)
    declaration_by_car = _declaration_labels_by_car(declaration_lines)

    _mark_client_messages_read(transport_request, request.user)

    docs_by_car: dict[int, dict[str, list]] = {}
    for doc in transport_request.documents.all():
        docs_by_car.setdefault(doc.car_id, {}).setdefault(doc.doc_type, []).append(doc)
    package_data = {p.car_id: p.data for p in transport_request.doc_packages.all()}
    bulk_by_car: dict[int, list] = {}
    for upload in transport_request.bulk_uploads.all():
        bulk_by_car.setdefault(upload.car_id, []).append(upload)

    car_blocks = []
    for status in readiness.cars:
        car = status.car
        slots = []
        for doc_type, label in TRANSPORT_DOCUMENT_TYPES:
            docs = sorted(docs_by_car.get(car.pk, {}).get(doc_type, []), key=lambda d: (d.created_at, d.pk))
            slots.append(
                {
                    "type": doc_type,
                    "label": label,
                    "docs": docs,
                    "can_generate": doc_type not in TRANSPORT_UPLOAD_ONLY_TYPES,
                    "is_required": doc_type in status.missing or doc_type in status.present,
                    "is_missing": doc_type in status.missing,
                    "fields": [
                        {
                            "name": field,
                            "label": PACKAGE_FIELD_LABELS.get(field, field),
                            "value": (package_data.get(car.pk) or {}).get(field, ""),
                        }
                        for field in package_actions.PACKAGE_FIELDS.get(doc_type, [])
                    ],
                }
            )
        car_blocks.append(
            {
                "car": car,
                "status": status,
                "declaration_type": types_by_car.get(car.pk) or "",
                "declaration_label": declaration_by_car.get(car.pk, ""),
                "slots": slots,
                "bulk_uploads": bulk_by_car.get(car.pk, []),
            }
        )

    warehouses = wh_letter.warehouses_of_request(transport_request)
    messages_list = list(transport_request.messages.select_related("author", "car").all())

    return render(
        request,
        "admin/requests_card.html",
        _admin_context(
            request,
            title=f"Заявка {transport_request.number}",
            original=transport_request,
            transport_request=transport_request,
            readiness=readiness,
            car_blocks=car_blocks,
            request_messages=messages_list,
            doc_types=TRANSPORT_DOCUMENT_TYPES,
            declaration_types=TRANSPORT_DECLARATION_TYPES,
            destination_countries=TRANSPORT_DESTINATION_COUNTRIES,
            declaration_lines=declaration_lines,
            declaration_panel=_declaration_panel(transport_request, declaration_lines),
            status_choices=TransportRequest.STATUS_CHOICES,
            warehouse_state_choices=TransportRequest.WAREHOUSE_STATE_CHOICES,
            warehouses=warehouses,
            available_cars=_available_cars(transport_request),
            missing_doc_types=readiness.missing_doc_types,
            missing_doc_types_json=json.dumps(readiness.missing_doc_types),
            carrier_match=_carrier_match_info(transport_request),
            admin_change_url=reverse("admin:core_transportrequest_change", args=[transport_request.pk]),
        ),
    )


def _available_cars(transport_request):
    """Авто клиента, которые можно добавить в заявку (не заняты другой заявкой)."""
    from core.views_website.forms import client_requestable_cars

    already = set(transport_request.cars.values_list("pk", flat=True))
    cars = client_requestable_cars(transport_request.client, exclude_request_pk=transport_request.pk)
    return [car for car in cars[:200] if car.pk not in already]


def _carrier_match_info(transport_request) -> dict:
    """Сопоставление текстового перевозчика заявки со справочником."""
    from core.services.transport_request_autotransport import match_carrier

    match = match_carrier(transport_request)
    return {
        "carrier": match.carrier,
        "matched_by": match.matched_by,
        "will_create": match.will_create,
        "candidates": match.candidates,
    }


def _mark_client_messages_read(transport_request, user) -> int:
    """Помечает сообщения клиента прочитанными при открытии карточки."""
    return transport_request.messages.filter(author_kind="CLIENT", read_by_staff_at__isnull=True).update(
        read_by_staff_at=timezone.now()
    )


# ---------------------------------------------------------------------------
# Правка заявки
# ---------------------------------------------------------------------------


@staff_member_required
@require_POST
def request_update(request: HttpRequest, pk: int):
    """Инлайн-правка полей заявки сотрудником (AJAX).

    Принимает любое подмножество ``EDITABLE_FIELDS``; неизвестные поля
    игнорируются, чтобы форма могла присылать служебные ключи.
    """
    transport_request = get_object_or_404(TransportRequest, pk=pk)

    updated = []
    errors = {}
    for field, kind in EDITABLE_FIELDS.items():
        if field not in request.POST:
            continue
        raw = (request.POST.get(field) or "").strip()
        if kind == "date":
            if not raw:
                value = None
            else:
                from django.utils.dateparse import parse_date

                value = parse_date(raw)
                if value is None:
                    errors[field] = "Неверная дата (ожидается ГГГГ-ММ-ДД)."
                    continue
        elif kind == "choice":
            if raw and raw not in _DECLARATION_LABELS:
                errors[field] = "Неизвестный тип декларации."
                continue
            value = raw
        elif kind == "country":
            if raw and raw not in _COUNTRY_LABELS:
                errors[field] = "Неизвестная страна назначения."
                continue
            value = raw
        else:
            value = raw
        if getattr(transport_request, field) != value:
            setattr(transport_request, field, value)
            updated.append(field)

    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    if updated:
        transport_request.save(update_fields=[*updated, "updated_at"])
    return JsonResponse({"ok": True, "updated": updated})


@staff_member_required
@require_POST
def request_status_set(request: HttpRequest, pk: int):
    """Смена статуса заявки и/или внутреннего состояния по складу."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    fields = []

    status = (request.POST.get("status") or "").strip()
    if status:
        if status not in dict(TransportRequest.STATUS_CHOICES):
            return JsonResponse({"ok": False, "error": "Неизвестный статус."}, status=400)
        transport_request.status = status
        fields.append("status")

    warehouse_state = (request.POST.get("warehouse_state") or "").strip()
    if warehouse_state:
        if warehouse_state not in dict(TransportRequest.WAREHOUSE_STATE_CHOICES):
            return JsonResponse({"ok": False, "error": "Неизвестное состояние по складу."}, status=400)
        transport_request.warehouse_state = warehouse_state
        fields.append("warehouse_state")
        if warehouse_state == TransportRequest.WAREHOUSE_CONFIRMED and not transport_request.warehouse_confirmed_at:
            transport_request.warehouse_confirmed_at = timezone.now()
            fields.append("warehouse_confirmed_at")

    if "awaiting_client_docs" in request.POST:
        transport_request.awaiting_client_docs = request.POST.get("awaiting_client_docs") in ("1", "on", "true")
        fields.append("awaiting_client_docs")

    if fields:
        transport_request.save(update_fields=[*fields, "updated_at"])
    return JsonResponse(
        {
            "ok": True,
            "status": transport_request.status,
            "status_display": transport_request.get_status_display(),
            "warehouse_state": transport_request.warehouse_state,
            "warehouse_state_display": transport_request.get_warehouse_state_display(),
            "awaiting_client_docs": transport_request.awaiting_client_docs,
        }
    )


def _declaration_labels_by_car(lines) -> dict[int, str]:
    """``{car_id: "№2 · Транзит (T1)"}`` — подпись декларации для чипа авто."""
    labels: dict[int, str] = {}
    for index, line in enumerate(lines, start=1):
        text = line.type_display or "тип не выбран"
        for car in line.cars:
            labels[car.pk] = f"№{index} · {text}"
    return labels


def _declaration_panel(transport_request, lines) -> dict:
    """Данные панели «Декларации»: строки плана и чекбоксы авто для правки.

    В каждой отдельной декларации показываем все авто заявки: отметить можно
    любое, даже занятое другой декларацией — при сохранении оно переедет
    (авто входит только в одну декларацию).
    """
    index_by_car = {car.pk: index for index, line in enumerate(lines, start=1) for car in line.cars}
    all_cars = list(transport_request.cars.all().order_by("id"))

    blocks = []
    for index, line in enumerate(lines, start=1):
        picks = []
        if not line.is_default:
            own = {car.pk for car in line.cars}
            picks = [
                {
                    "car": car,
                    "checked": car.pk in own,
                    "other_index": 0 if car.pk in own else index_by_car.get(car.pk, 0),
                }
                for car in all_cars
            ]
        blocks.append({"index": index, "line": line, "picks": picks})

    return {
        "blocks": blocks,
        "add_picks": [{"car": car, "other_index": index_by_car.get(car.pk, 0)} for car in all_cars],
    }


@staff_member_required
@require_POST
def request_declarations(request: HttpRequest, pk: int):
    """Ручная разбивка авто заявки на отдельные декларации.

    Действия: ``add`` — новая декларация из выбранных авто, ``update`` —
    тип/примечание/состав, ``delete`` — снять разбивку (авто вернутся к типу
    заявки). Отвечаем redirect'ом, а не JSON: тип декларации меняет набор
    обязательных документов, поэтому карточку надо перерисовать целиком.
    """
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    action = request.POST.get("action", "")
    car_ids = request.POST.getlist("cars")
    anchor = f"{reverse('admin_request_card', args=[pk])}#rc-declarations"

    try:
        if action == "add":
            if not car_ids:
                messages.error(request, "Выберите хотя бы одно авто для отдельной декларации.")
                return redirect(anchor)
            group = declarations.create_group(
                transport_request,
                request.POST.get("declaration_type", ""),
                car_ids,
                request.POST.get("note", ""),
            )
            messages.success(request, f"Добавлена отдельная декларация: {group.get_declaration_type_display()}.")
        elif action in ("update", "delete"):
            group_id = request.POST.get("group", "")
            if not group_id.isdigit():
                messages.error(request, "Не указана декларация.")
                return redirect(anchor)
            group = get_object_or_404(transport_request.declaration_groups, pk=int(group_id))
            if action == "delete":
                declarations.delete_group(group)
                messages.success(request, "Отдельная декларация удалена — авто вернулись к типу заявки.")
            else:
                declarations.update_group(
                    group,
                    declaration_type=request.POST.get("declaration_type", ""),
                    note=request.POST.get("note", ""),
                    car_ids=car_ids,
                )
                messages.success(request, "Декларация обновлена.")
        else:
            messages.error(request, "Неизвестное действие с декларациями.")
    except declarations.DeclarationError as exc:
        messages.error(request, str(exc))
    return redirect(anchor)


@staff_member_required
@require_POST
def request_car_toggle(request: HttpRequest, pk: int):
    """Добавить или убрать авто из заявки (сотрудник, без ограничений статуса)."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    action = request.POST.get("action", "")
    car_id = request.POST.get("car", "")
    if not car_id.isdigit():
        return JsonResponse({"ok": False, "error": "Не указан автомобиль."}, status=400)

    if action == "remove":
        car = get_object_or_404(transport_request.cars, pk=car_id)
        transport_request.cars.remove(car)
        transport_request.documents.filter(car=car).delete()
        transport_request.doc_packages.filter(car=car).delete()
        declarations.sync_group_cars(transport_request)
    elif action == "add":
        from core.models import Car

        car = get_object_or_404(Car, pk=car_id)
        transport_request.cars.add(car)
    else:
        return JsonResponse({"ok": False, "error": "Неизвестное действие."}, status=400)

    return JsonResponse({"ok": True, "cars_left": transport_request.cars.count()})


# ---------------------------------------------------------------------------
# Пакет документов (та же логика, что в кабинете клиента)
# ---------------------------------------------------------------------------


@staff_member_required
@require_POST
def request_doc_action(request: HttpRequest, pk: int):
    """Сохранить данные/файлы или сгенерировать документ пакета за клиента."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    car = get_object_or_404(transport_request.cars, pk=request.POST.get("car", ""))
    try:
        notices = package_actions.apply_doc_action(
            transport_request=transport_request,
            car=car,
            doc_type=request.POST.get("doc_type", ""),
            post=request.POST,
            files=request.FILES.getlist("files"),
            user=request.user,
        )
    except PackageDataError as exc:
        messages.error(request, str(exc))
        return redirect(_card_url(transport_request, car))
    for level, text in notices:
        getattr(messages, level, messages.info)(request, text)
    return redirect(_card_url(transport_request, car))


@staff_member_required
@require_POST
def request_generate_all(request: HttpRequest, pk: int):
    """Сгенерировать весь пакет по авто за клиента."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
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
        return redirect(_card_url(transport_request, car))
    for level, text in notices:
        getattr(messages, level, messages.info)(request, text)
    return redirect(_card_url(transport_request, car))


@staff_member_required
@require_POST
def request_doc_delete(request: HttpRequest, pk: int, doc_id: int):
    """Удалить файл документа пакета."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    doc = get_object_or_404(TransportRequestDocument, pk=doc_id, request=transport_request)
    car = doc.car
    level, text = package_actions.delete_doc(transport_request, doc)
    getattr(messages, level, messages.info)(request, text)
    return redirect(_card_url(transport_request, car))


@staff_member_required
@require_POST
def request_bulk_upload(request: HttpRequest, pk: int):
    """«Одним файлом»: пакет клиента на автосортировку по типам документов."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    car = get_object_or_404(transport_request.cars, pk=request.POST.get("car", ""))
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Выберите файл с пакетом документов.")
        return redirect(_card_url(transport_request, car))
    try:
        bulk_split.queue_upload(transport_request, car, upload, request.user)
    except bulk_split.BulkSplitError as exc:
        messages.error(request, str(exc))
        return redirect(_card_url(transport_request, car))
    messages.success(request, "Файл принят: разбираем на документы и раскладываем по типам.")
    return redirect(_card_url(transport_request, car))


@staff_member_required
@require_POST
def request_doc_retype(request: HttpRequest, pk: int, doc_id: int):
    """Переложить документ в другой слот (правка автосортировки)."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    doc = get_object_or_404(TransportRequestDocument, pk=doc_id, request=transport_request)
    try:
        label = bulk_split.retype_document(doc, request.POST.get("doc_type", ""))
    except bulk_split.BulkSplitError as exc:
        messages.error(request, str(exc))
        return redirect(_card_url(transport_request, doc.car))
    messages.success(request, f"Документ перемещён в «{label}».")
    return redirect(_card_url(transport_request, doc.car))


@staff_member_required
@require_GET
def request_package_download(request: HttpRequest, pk: int, car_id: int):
    """Скачать склеенный PDF-пакет по авто (то же, что уходит складу)."""
    from core.services import transport_docs

    transport_request = get_object_or_404(TransportRequest, pk=pk)
    car = get_object_or_404(transport_request.cars, pk=car_id)
    pdf_bytes = transport_docs.build_car_package_pdf(transport_request, car)
    if not pdf_bytes:
        raise Http404("По этому автомобилю нет документов.")
    import io

    return FileResponse(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        filename=transport_docs.package_pdf_filename(car),
        content_type="application/pdf",
    )


def _card_url(transport_request, car=None) -> str:
    url = reverse("admin_request_card", args=[transport_request.pk])
    if car is not None:
        url = f"{url}?car={car.pk}#car-{car.pk}"
    return url


# ---------------------------------------------------------------------------
# Переписка с клиентом
# ---------------------------------------------------------------------------


@staff_member_required
@require_POST
def request_message_send(request: HttpRequest, pk: int):
    """Сообщение или запрос документов клиенту + уведомление email/Telegram."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    body = (request.POST.get("body") or "").strip()
    doc_types = [code for code in request.POST.getlist("requested_doc_types") if code in _DOC_LABELS]
    kind = TransportRequestMessage.KIND_DOC_REQUEST if doc_types else TransportRequestMessage.KIND_MESSAGE

    if not body and not doc_types:
        return JsonResponse({"ok": False, "error": "Пустое сообщение."}, status=400)

    car = None
    car_id = request.POST.get("car", "")
    if car_id.isdigit():
        car = transport_request.cars.filter(pk=int(car_id)).first()

    message = TransportRequestMessage.objects.create(
        request=transport_request,
        author_kind=TransportRequestMessage.AUTHOR_STAFF,
        kind=kind,
        author=request.user,
        car=car,
        body=body,
        requested_doc_types=doc_types,
    )

    if doc_types and not transport_request.awaiting_client_docs:
        # Разрешаем клиенту догрузить документы даже если заявка уже в работе.
        transport_request.awaiting_client_docs = True
        transport_request.save(update_fields=["awaiting_client_docs", "updated_at"])

    delivery = {"email": 0, "telegram": 0}
    try:
        from core.services.transport_request_notify import notify_client_about_message

        delivery = notify_client_about_message(message, user=request.user)
    except Exception:
        logger.exception("Уведомление клиента по заявке %s не отправлено", transport_request.number)

    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": message.pk,
                "body": message.body,
                "kind": message.kind,
                "requested": message.requested_doc_labels,
                "author": request.user.get_full_name() or request.user.username,
                "created_at": timezone.localtime(message.created_at).strftime("%d.%m.%Y %H:%M"),
            },
            "delivery": delivery,
            "awaiting_client_docs": transport_request.awaiting_client_docs,
        }
    )


# ---------------------------------------------------------------------------
# Письмо складу
# ---------------------------------------------------------------------------


@staff_member_required
@require_GET
def warehouse_letter_page(request: HttpRequest, pk: int):
    """Черновик письма складу: получатели, литовский текст, вложения."""
    transport_request = get_object_or_404(_base_queryset(), pk=pk)
    warehouses = wh_letter.warehouses_of_request(transport_request)

    warehouse = None
    wh_id = request.GET.get("warehouse", "")
    if wh_id.isdigit():
        warehouse = next((w for w in warehouses if w.pk == int(wh_id)), None)
    if warehouse is None and len(warehouses) == 1:
        warehouse = warehouses[0]

    draft = None
    error = ""
    if warehouse is not None:
        try:
            draft = wh_letter.build_letter_draft(transport_request, warehouse)
        except wh_letter.WarehouseLetterError as exc:
            error = str(exc)

    return render(
        request,
        "admin/requests_warehouse_letter.html",
        _admin_context(
            request,
            title=f"Письмо складу — {transport_request.number}",
            transport_request=transport_request,
            warehouses=warehouses,
            warehouse=warehouse,
            draft=draft,
            error=error,
            readiness=check_request(transport_request),
            card_url=reverse("admin_request_card", args=[transport_request.pk]),
        ),
    )


@staff_member_required
@require_POST
def warehouse_letter_send(request: HttpRequest, pk: int):
    """Отправка письма складу и отметка заявки как отправленной."""
    transport_request = get_object_or_404(TransportRequest, pk=pk)
    wh_id = request.POST.get("warehouse", "")
    warehouse = next((w for w in wh_letter.warehouses_of_request(transport_request) if str(w.pk) == wh_id), None)
    if warehouse is None:
        messages.error(request, "Не выбран склад-получатель.")
        return redirect(reverse("admin_request_warehouse_letter", args=[pk]))

    car_ids = [int(cid) for cid in request.POST.getlist("cars") if cid.isdigit()]
    try:
        wh_letter.send_letter(
            transport_request=transport_request,
            warehouse=warehouse,
            user=request.user,
            to=request.POST.get("to", ""),
            cc=request.POST.get("cc", ""),
            bcc=request.POST.get("bcc", ""),
            subject=request.POST.get("subject", ""),
            body_text=request.POST.get("body_text", ""),
            car_ids=car_ids,
        )
    except Exception as exc:
        logger.exception("Письмо складу по заявке %s не отправлено", transport_request.number)
        messages.error(request, f"Письмо не отправлено: {exc}")
        return redirect(f"{reverse('admin_request_warehouse_letter', args=[pk])}?warehouse={warehouse.pk}")

    messages.success(request, f"Письмо складу «{warehouse.name}» отправлено.")
    return redirect(reverse("admin_request_card", args=[pk]))


# ---------------------------------------------------------------------------
# Рейс из заявки
# ---------------------------------------------------------------------------


@staff_member_required
@require_POST
def request_create_autotransport(request: HttpRequest, pk: int):
    """Создать рейс ``AutoTransport`` по заявке и связать его с заявкой."""
    from core.services.transport_request_autotransport import (
        AutoTransportBuildError,
        create_autotransport,
    )

    transport_request = get_object_or_404(TransportRequest, pk=pk)
    carrier = None
    carrier_id = request.POST.get("carrier", "")
    if carrier_id.isdigit():
        from core.models.carriers import Carrier

        carrier = Carrier.objects.filter(pk=int(carrier_id)).first()

    try:
        auto_transport = create_autotransport(
            transport_request,
            user=request.user,
            carrier=carrier,
            create_carrier=request.POST.get("create_carrier") in ("1", "on", "true"),
        )
    except AutoTransportBuildError as exc:
        messages.error(request, str(exc))
        return redirect(reverse("admin_request_card", args=[pk]))

    messages.success(request, f"Рейс {auto_transport.number} создан по заявке {transport_request.number}.")
    return redirect(reverse("admin:core_autotransport_change", args=[auto_transport.pk]))
