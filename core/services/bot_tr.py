"""Чтение и правка заявки на автовоз для роли neto.

Документ всегда принадлежит заявке и машине этой заявки. Запись в базу
идёт только при ``apply=True``; уведомлений клиенту нет.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from django.conf import settings

from core.services.bot_docs import eori_problem
from core.services.bot_runtime import BotError

# Типы, которые tr-docs показывает. TITLE сюда не входит: он у машины.
LIST_DOC_TYPES = (
    "OBLIGATION",
    "INVOICE",
    "PASSPORT",
    "PAYMENT_ORDER",
    "LETTER_USA",
    "CONTRACT",
    "SIGNATURE",
    "OTHER",
)
UPLOAD_DOC_TYPES = frozenset({"CONTRACT", "SIGNATURE", "OTHER"})

_LIMITS = {
    "driver_name": 100,
    "driver_passport": 50,
    "driver_phone": 20,
    "carrier_name": 255,
    "carrier_eori": 50,
    "truck_number": 20,
    "trailer_number": 20,
    "border_crossing": 100,
}


def neto_tmp_dir() -> Path:
    """Общая папка файлов neto: ZIP пакета, скачивание и загрузка."""
    path = Path(settings.MEDIA_ROOT) / "bot_tmp" / "neto"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_transport_request(raw: str):
    from core.models.website import TransportRequest

    token = (raw or "").strip()
    if not token:
        raise BotError("Нужен --tr (номер или id заявки).")
    by_number = TransportRequest.objects.filter(number__iexact=token).first()
    if by_number is not None:
        return by_number
    if token.isdigit():
        by_id = TransportRequest.objects.filter(pk=int(token)).first()
        if by_id is not None:
            return by_id
    raise BotError(f"Заявка {token} не найдена.")


def _stored_size(field) -> int:
    try:
        return int(field.size or 0)
    except (OSError, ValueError):
        return 0


def _doc_row(doc) -> dict:
    return {
        "id": doc.pk,
        "type": doc.doc_type,
        "filename": doc.filename,
        "size": _stored_size(doc.file),
        "vin": doc.car.vin,
        "car_id": doc.car_id,
    }


def list_tr_docs(raw: str) -> dict:
    request = resolve_transport_request(raw)
    docs = request.documents.filter(doc_type__in=LIST_DOC_TYPES).select_related("car").order_by("id")
    return {
        "number": request.number,
        "id": request.pk,
        "documents": [_doc_row(doc) for doc in docs],
    }


def _docs_of_type(request, doc_type: str):
    return list(request.documents.filter(doc_type=doc_type).select_related("car").order_by("id"))


def download_tr_doc(raw: str, doc_type: str, doc_id: int | None) -> dict:
    request = resolve_transport_request(raw)
    kind = (doc_type or "").strip().upper()
    if kind not in LIST_DOC_TYPES:
        raise BotError(f"Неизвестный тип документа: {doc_type or '—'}.")
    if doc_id:
        doc = request.documents.filter(pk=doc_id).select_related("car").first()
        if doc is None:
            raise BotError("Документ не относится к этой заявке.")
        if doc.doc_type != kind:
            raise BotError(f"Документ {doc_id} имеет тип {doc.doc_type}, а не {kind}.")
    else:
        matches = _docs_of_type(request, kind)
        if not matches:
            raise BotError(f"Документов типа {kind} нет.")
        if len(matches) > 1:
            raise BotError(f"Несколько документов типа {kind}. Укажите --doc-id.")
        doc = matches[0]
    source = _source_path(doc)
    filename = os.path.basename(doc.file.name) or f"{kind.lower()}.bin"
    target = neto_tmp_dir() / filename
    shutil.copy2(source, target)
    return {
        "id": doc.pk,
        "type": doc.doc_type,
        "filename": filename,
        "path": str(target),
        "size": target.stat().st_size,
    }


def _source_path(doc) -> str:
    field = doc.file
    try:
        path = field.path
    except (OSError, ValueError, NotImplementedError):
        path = os.path.join(settings.MEDIA_ROOT, field.name)
    if not path or not os.path.isfile(path):
        raise BotError(f"Файл документа {doc.pk} не найден на диске.")
    return path


def _resolve_upload_path(raw: str) -> Path:
    folder = neto_tmp_dir().resolve()
    given = (raw or "").strip()
    if not given:
        raise BotError("Нужен --file внутри media/bot_tmp/neto/.")
    candidate = Path(given)
    if not candidate.is_absolute():
        candidate = folder / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BotError(f"Файл не найден: {given}") from exc
    if not resolved.is_relative_to(folder):
        raise BotError(f"Файл должен лежать в {folder}.")
    if not resolved.is_file():
        raise BotError("Нужен файл, не каталог.")
    return resolved


def _car_for_upload(request, *, vin: str, car_id: int | None):
    cars = list(request.cars.all().order_by("id"))
    if car_id:
        car = next((item for item in cars if item.pk == car_id), None)
        if car is None:
            raise BotError("Машина не в этой заявке.")
        return car
    token = (vin or "").strip()
    if token:
        matches = [item for item in cars if (item.vin or "").upper() == token.upper()]
        if not matches:
            raise BotError("Машина не в этой заявке.")
        if len(matches) > 1:
            raise BotError("Несколько машин с таким VIN. Укажите --car-id.")
        return matches[0]
    if len(cars) == 1:
        return cars[0]
    if not cars:
        raise BotError("В заявке нет машин: документ привязан к авто.")
    raise BotError("В заявке несколько машин. Укажите --vin или --car-id.")


def upload_tr_doc(raw: str, doc_type: str, file_path: str, *, vin: str, car_id: int | None, apply: bool) -> dict:
    from django.core.files import File

    from core.models.website import CLIENT_DOCUMENT_ALLOWED_EXTENSIONS
    from core.services.transport_package_actions import MAX_UPLOAD_SIZE, DocActionError, save_upload_doc

    request = resolve_transport_request(raw)
    kind = (doc_type or "").strip().upper()
    if kind not in UPLOAD_DOC_TYPES:
        raise BotError("Загрузить можно только CONTRACT, SIGNATURE или OTHER.")
    source = _resolve_upload_path(file_path)
    extension = source.suffix.lower().lstrip(".")
    if extension not in CLIENT_DOCUMENT_ALLOWED_EXTENSIONS:
        raise BotError(f"Файл {source.name} — допустимы только PDF, JPG и PNG.")
    size = source.stat().st_size
    if size > MAX_UPLOAD_SIZE:
        raise BotError(f"Файл {source.name} слишком большой (максимум 20 МБ).")
    car = _car_for_upload(request, vin=vin, car_id=car_id)
    preview = {
        "number": request.number,
        "type": kind,
        "filename": source.name,
        "vin": car.vin,
        "car_id": car.pk,
        "size": size,
    }
    if not apply:
        return {
            "before": {},
            "after": preview,
            "result": {"outcome": "would_upload", **preview},
            "notifications": [],
        }
    try:
        with source.open("rb") as handle:
            upload = File(handle, name=source.name)
            doc = save_upload_doc(request, car, kind, upload, None)
    except DocActionError as exc:
        raise BotError(str(exc)) from exc
    return {
        "before": {},
        "after": {"id": doc.pk, **preview},
        "result": {"outcome": "uploaded", "id": doc.pk, **preview},
        "notifications": [],
    }


def update_tr(
    raw: str,
    *,
    driver_name: str | None = None,
    driver_passport: str | None = None,
    driver_phone: str | None = None,
    carrier_name: str | None = None,
    carrier_eori: str | None = None,
    tractor_plate: str | None = None,
    trailer_plate: str | None = None,
    border_crossing: str | None = None,
    apply: bool,
) -> dict:
    request = resolve_transport_request(raw)
    incoming = {
        "driver_name": driver_name,
        "driver_passport": driver_passport,
        "driver_phone": driver_phone,
        "carrier_name": carrier_name,
        "carrier_eori": carrier_eori,
        "truck_number": tractor_plate,
        "trailer_number": trailer_plate,
        "border_crossing": border_crossing,
    }
    changes = {}
    for field, value in incoming.items():
        if value is None:
            continue
        text = str(value).strip()
        limit = _LIMITS[field]
        if len(text) > limit:
            raise BotError(f"{field} длиннее {limit} символов.")
        if field == "carrier_eori":
            problem = eori_problem(text)
            if problem == "EORI":
                raise BotError("EORI: пустой код.")
            if problem == "EORI_FORMAT":
                raise BotError("EORI_FORMAT: нужен код страны и до 15 букв или цифр.")
            text = text.upper()
        changes[field] = text
    if not changes:
        raise BotError("Нечего менять: укажите хотя бы одно поле.")
    before = {field: getattr(request, field) or "" for field in changes}
    if apply:
        for field, value in changes.items():
            setattr(request, field, value)
        request.save(update_fields=[*changes.keys(), "updated_at"])
    return {
        "before": before,
        "after": changes,
        "result": {
            "outcome": "updated" if apply else "would_update",
            "number": request.number,
            "id": request.pk,
            "fields": changes,
        },
        "notifications": [],
    }
