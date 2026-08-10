"""Кабинет клиента: заявки с данными автовозов и пакетом документов."""

import json
import logging
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models.website import (
    CLIENT_DOCUMENT_ALLOWED_EXTENSIONS,
    TRANSPORT_DOCUMENT_TYPES,
    TRANSPORT_UPLOAD_ONLY_TYPES,
    ClientUser,
    TransportDocumentPackage,
    TransportRequest,
    TransportRequestDocument,
)
from core.services import transport_docs as docs_service
from core.services.transport_docs import PackageDataError

from .forms import MAX_UPLOAD_SIZE, TransportRequestForm

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

def _client_requests(client):
    """Заявки клиента для списка в кабинете (отменённые скрыты)."""
    return (
        TransportRequest.objects.filter(client=client)
        .exclude(status="CANCELLED")
        .prefetch_related("cars", "documents", "doc_packages")
    )


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


# Поля данных пакета, которые принимает каждое модальное окно.
_PACKAGE_FIELDS = {
    "PASSPORT": [
        "buyer_name",
        "buyer_name_ru",
        "buyer_birth_date",
        "buyer_passport_number",
        "buyer_passport_issue_date",
        "buyer_address",
        "buyer_address_ru",
    ],
    "INVOICE": ["invoice_number", "invoice_date", "invoice_amount"],
    "PAYMENT_ORDER": [],
    "LETTER_USA": [],
    "OBLIGATION": [],
    "CONTRACT": [
        "contract_number",
        "contract_date",
        "carrier_company",
        "carrier_address",
        "carrier_director",
        "carrier_regon",
        "carrier_nip",
        "carrier_krs",
    ],
    "SIGNATURE": [],
    "OTHER": [],
}

_DOC_TYPE_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)

# Иконка для типа документа пакета: (icon, color).
# icon: класс bi-* | "img:static/path" | "data:..." (data-URI).
_DOC_TYPE_ICONS = {
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
    """Вернуться к списку заявок с раскрытой карточкой / авто / окном документа."""
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
    cars = list(transport_request.cars.all())
    package_data = {p.car_id: p.data for p in transport_request.doc_packages.all()}
    docs_by_car = {car.pk: {doc_type: [] for doc_type, _ in TRANSPORT_DOCUMENT_TYPES} for car in cars}
    for doc in transport_request.documents.all():
        if doc.car_id in docs_by_car:
            docs_by_car[doc.car_id][doc.doc_type].append(doc)

    doc_sections = []
    for car in cars:
        slots = []
        present_icons = []
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
            slot = {
                "type": doc_type,
                "label": label,
                "docs": docs,
                "can_generate": doc_type not in TRANSPORT_UPLOAD_ONLY_TYPES,
                "icon": icon_bi,
                "icon_img": icon_img,
                "icon_data": icon_data,
                "icon_is_flag": icon_is_flag,
                "icon_color": color,
            }
            slots.append(slot)
            if docs:
                latest = docs[0]
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
                        "file_url": latest.file.url,
                        "filename": latest.filename,
                        "is_image": name.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")),
                    }
                )
        doc_sections.append({"car": car, "slots": slots, "present_icons": present_icons})
    return {
        "doc_sections": doc_sections,
        "package_data": {str(car.pk): package_data.get(car.pk, {}) for car in cars},
    }


def _docs_map_context(transport_requests):
    """Документы всех заявок списка + JSON для JS-модалок.

    На каждый объект заявки вешается ``doc_sections`` для шаблона карточек.
    """
    package_by_request = {}
    action_urls = {}
    for tr in transport_requests:
        ctx = _docs_context(tr)
        tr.doc_sections = ctx["doc_sections"]
        package_by_request[str(tr.pk)] = ctx["package_data"]
        action_urls[str(tr.pk)] = reverse("website:transport_request_doc_action", args=[tr.pk])
    return {
        "package_data_by_request_json": json.dumps(package_by_request, ensure_ascii=False),
        "doc_action_urls_json": json.dumps(action_urls, ensure_ascii=False),
    }


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
        form = TransportRequestForm(request.POST, client=client)
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
            "known_carriers_json": _known_carriers_json(client),
            "doc_types": TRANSPORT_DOCUMENT_TYPES,
            "docs_req": request.GET.get("docs_req", ""),
            "docs_car": request.GET.get("docs_car", ""),
            "open_doc": request.GET.get("open_doc", ""),
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
            "known_carriers_json": _known_carriers_json(client),
            "doc_types": TRANSPORT_DOCUMENT_TYPES,
            "docs_req": request.GET.get("docs_req", "") or str(transport_request.pk),
            "docs_car": request.GET.get("docs_car", "") or request.GET.get("car", ""),
            "open_doc": request.GET.get("open_doc", ""),
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


def _update_package_data(package, doc_type, post):
    """Перенести поля модального окна в данные пакета (только присланные)."""
    changed = False
    for field in _PACKAGE_FIELDS.get(doc_type, []):
        if field in post:
            value = post.get(field, "").strip()
            if package.data.get(field, "") != value:
                package.data[field] = value
                changed = True
    return changed


# Подписи полей паспорта для сообщения «распознано автоматически».
_PASSPORT_FIELD_LABELS = {
    "buyer_name": "ФИО латиницей",
    "buyer_passport_number": "номер паспорта",
    "buyer_birth_date": "дата рождения",
    "buyer_passport_issue_date": "дата выдачи",
}


def _apply_passport_ai(request, package, saved_docs):
    """Автозаполнение данных пакета после загрузки паспорта.

    * Из фото/скана главной страницы паспорта РБ распознаются номер,
      ФИО латиницей и даты (заполняются только пустые поля — ручной
      ввод не перетирается).
    * Адрес, введённый кириллицей, транслитерируется в латиницу для
      инвойса и платёжки, если латинский вариант ещё не заполнен.

    Подпись из паспорта не вырезаем — качество crop слишком низкое;
    нужна отдельная загрузка в слот «Подпись».
    """
    from core.services import passport_extractor

    if not passport_extractor.ai_available():
        if saved_docs:
            messages.info(
                request,
                "«Паспорт»: автораспознавание сейчас недоступно — проверьте и заполните поля вручную.",
            )
        return
    data = package.data

    if saved_docs:
        try:
            extracted = passport_extractor.extract_passport(saved_docs[0].file.path)
        except Exception:
            logger.exception("Распознавание паспорта не удалось (документ %s)", saved_docs[0].pk)
            extracted = {}
        filled = []
        for key, value in extracted.items():
            if not (data.get(key) or "").strip():
                data[key] = value
                filled.append(_PASSPORT_FIELD_LABELS.get(key, key))
        if filled:
            messages.success(request, f"«Паспорт»: распознано автоматически — {', '.join(filled)}.")
        elif not extracted:
            messages.warning(
                request,
                "«Паспорт»: не удалось распознать данные с фото — заполните поля вручную.",
            )

    if not (data.get("buyer_address") or "").strip() and (data.get("buyer_address_ru") or "").strip():
        latin = passport_extractor.transliterate_address(data["buyer_address_ru"])
        if latin:
            data["buyer_address"] = latin
            messages.success(request, f"«Паспорт»: адрес транслитерирован — {latin}")


def _signature_bytes(transport_request, car):
    """Загруженная подпись (jpg/png) для простановки в генерируемые документы.

    Повторно нормализуем при чтении: старые загрузки / слабый порог иначе
    дают серый прямоугольник фона в PDF.
    """
    doc = transport_request.documents.filter(car=car, doc_type="SIGNATURE").order_by("-created_at").first()
    if doc is None or not doc.file.name.lower().endswith((".jpg", ".jpeg", ".png")):
        return None
    with doc.file.open("rb") as fh:
        raw = fh.read()
    from core.services.signature_normalizer import normalize_signature_image

    return normalize_signature_image(raw) or raw


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
    package, _ = TransportDocumentPackage.objects.get_or_create(request=transport_request, car=car)
    label = _DOC_TYPE_LABELS[doc_type]

    # Валидация вводимых значений (дат/сумм) до сохранения.
    try:
        _update_package_data(package, doc_type, request.POST)
        for field in (
            "buyer_birth_date",
            "buyer_passport_issue_date",
            "invoice_date",
            "payment_date",
            "contract_date",
        ):
            if field in _PACKAGE_FIELDS.get(doc_type, []):
                docs_service.parse_date(package.data.get(field))
        if "invoice_amount" in _PACKAGE_FIELDS.get(doc_type, []):
            docs_service.parse_amount(package.data.get("invoice_amount"))
    except PackageDataError as exc:
        messages.error(request, f"«{label}»: {exc}")
        return redirect(_edit_url(transport_request, car))

    if request.POST.get("action") == "generate":
        if doc_type in TRANSPORT_UPLOAD_ONLY_TYPES:
            messages.error(request, f"«{label}» нельзя сгенерировать — загрузите реальный файл.")
            return redirect(_edit_url(transport_request, car))
        try:
            filename, pdf_bytes, notices = docs_service.generate_document(
                transport_request,
                car,
                package.data,
                doc_type,
                signature_bytes=_signature_bytes(transport_request, car),
            )
        except PackageDataError as exc:
            package.save(update_fields=["data", "updated_at"])
            messages.error(request, f"«{label}»: {exc}")
            return redirect(_edit_url(transport_request, car))
        package.save(update_fields=["data", "updated_at"])
        # Пересгенерированный документ заменяет предыдущий сгенерированный.
        for old in transport_request.documents.filter(car=car, doc_type=doc_type, is_generated=True):
            old.file.delete(save=False)
            old.delete()
        TransportRequestDocument.objects.create(
            request=transport_request,
            car=car,
            doc_type=doc_type,
            file=ContentFile(pdf_bytes, name=filename),
            is_generated=True,
            uploaded_by=request.user,
        )
        messages.success(request, f"«{label}» сгенерирован.")
        for notice in notices:
            messages.info(request, f"«{label}»: {notice}")
        return redirect(_edit_url(transport_request, car))

    # Сохранение данных + загрузка файлов.
    files = request.FILES.getlist("files")
    saved_docs = []
    for upload in files:
        extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if extension not in CLIENT_DOCUMENT_ALLOWED_EXTENSIONS:
            messages.error(request, f"«{label}»: файл {upload.name} — допустимы только PDF, JPG и PNG.")
            continue
        if upload.size > MAX_UPLOAD_SIZE:
            messages.error(request, f"«{label}»: файл {upload.name} слишком большой (максимум 20 МБ).")
            continue
        # Ручная подпись заменяет ранее вырезанную «авто»-версию.
        if doc_type == "SIGNATURE" and not saved_docs:
            for old in transport_request.documents.filter(car=car, doc_type="SIGNATURE", is_generated=True):
                old.file.delete(save=False)
                old.delete()
        file_to_save = upload
        if doc_type == "SIGNATURE" and extension in {"jpg", "jpeg", "png"}:
            from core.services.signature_normalizer import normalize_signature_image

            normalized = normalize_signature_image(upload.read())
            upload.seek(0)
            if normalized:
                stem = upload.name.rsplit(".", 1)[0] if "." in upload.name else "signature"
                file_to_save = ContentFile(normalized, name=f"{stem}.png")
            else:
                messages.warning(
                    request,
                    f"«{label}»: не удалось нормализовать {upload.name} — сохранён исходный файл.",
                )
        saved_docs.append(
            TransportRequestDocument.objects.create(
                request=transport_request,
                car=car,
                doc_type=doc_type,
                file=file_to_save,
                uploaded_by=request.user,
            )
        )

    if doc_type == "PASSPORT":
        _apply_passport_ai(request, package, saved_docs)
        if not (package.data.get("buyer_address") or package.data.get("buyer_address_ru")):
            messages.warning(
                request,
                "«Паспорт»: введите адрес проживания кириллицей — рукописный адрес в паспорте "
                "плохо читается автоматикой, а латинский вариант подставится сам.",
            )

    package.save(update_fields=["data", "updated_at"])
    if saved_docs:
        messages.success(request, f"«{label}»: файлы добавлены ({len(saved_docs)} шт.).")
    else:
        messages.success(request, f"«{label}»: данные сохранены.")
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
    doc.file.delete(save=False)
    doc.delete()
    messages.success(request, f"«{_DOC_TYPE_LABELS[doc.doc_type]}»: файл удалён.")
    return redirect(_edit_url(transport_request, car))
