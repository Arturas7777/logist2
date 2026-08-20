"""Разбор пакета документов, присланного одним файлом.

Клиент грузит один PDF со всем пакетом («Одним файлом»), а мы:

1. рендерим страницы в JPEG (``scan_extractor.render_document_images``);
2. отправляем их в Claude Vision и просим определить тип каждой страницы;
3. склеиваем идущие подряд страницы одного типа в один документ и режем
   исходный PDF на эти куски (PyMuPDF);
4. сохраняем куски как документы пакета (``TransportRequestDocument``).

Страницы с низкой уверенностью и неизвестные типы уходят в «Остальное» —
клиент видит их в кабинете и может указать тип вручную. Это осознанный
компромисс: лучше положить в «Остальное», чем подсунуть складу платёжку
вместо инвойса.

Точка входа — ``split_upload``; вызывается из Celery-задачи
``core.tasks.process_transport_bulk_upload``.
"""

from __future__ import annotations

import io
import logging
import os

from django.core.files.base import ContentFile
from django.utils import timezone

from core.models.website import (
    TRANSPORT_DOCUMENT_TYPES,
    TransportBulkUpload,
    TransportDocumentPackage,
    TransportRequestDocument,
)

logger = logging.getLogger(__name__)

# Типы, которые модель вправе назначить странице. «Подпись» не даём:
# образец подписи клиент загружает отдельно, а спутать её с пустой
# страницей слишком легко.
CLASSIFIABLE_TYPES = ("TITLE", "PASSPORT", "INVOICE", "PAYMENT_ORDER", "LETTER_USA", "OBLIGATION", "CONTRACT")

FALLBACK_TYPE = "OTHER"

# Сколько страниц отправляем в один запрос к модели. Пакет на одно авто —
# это обычно 5-10 страниц; при больших файлах идём чанками, иначе запрос
# распухает и растёт риск обрыва.
PAGES_PER_CALL = 8

# Больше этого числа страниц в одном файле не разбираем: скорее всего
# клиент прислал пакет сразу на несколько машин — это разбирает сотрудник.
MAX_PAGES = 40

_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)

CLASSIFY_PROMPT = """Ты — сортировщик документов автомобильной логистики. Тебе дают
страницы ОДНОГО отсканированного файла: клиент сложил в него весь пакет документов
на автомобиль. Твоя задача — определить тип КАЖДОЙ страницы.

Возможные типы:
- TITLE — тайтл (Certificate of Title) автомобиля из США: бланк штата с
  водяными знаками, поля VIN / Year / Make / Odometer, owner и lienholder.
- PASSPORT — страница паспорта физического лица (фото, MRZ-строки, серия и номер).
- INVOICE — инвойс / счёт на автомобиль (invoice, seller/buyer, VIN, сумма).
- PAYMENT_ORDER — платёжное поручение, SWIFT, подтверждение банковского платежа.
- LETTER_USA — письмо/заявление на английском в адрес американской компании
  (аукцион, экспедитор, брокер) — обычно на фирменном бланке с подписью.
- OBLIGATION — обязательство/заявление физического лица о ввозе автомобиля
  (текст от первого лица, обязуюсь, декларирование).
- CONTRACT — договор перевозки/оказания услуг (стороны, предмет, реквизиты, разделы).
- OTHER — всё остальное: коносамент, фото авто, пустая страница,
  непонятный документ.

Правила:
- Многостраничный документ (например договор на 3 страницы) — у каждой его
  страницы один и тот же тип.
- confidence: "high" — тип очевиден; "medium" — вероятен; "low" — сомневаюсь.
  Не угадывай: сомневаешься — ставь low, такие страницы уйдут в OTHER.
- Верни ОДИН объект на каждую показанную страницу, в том же порядке.

Верни ТОЛЬКО валидный JSON (без markdown):
{
  "pages": [
    {"page": 1, "doc_type": "PASSPORT", "confidence": "high"},
    {"page": 2, "doc_type": "INVOICE", "confidence": "medium"}
  ]
}
"""


class BulkSplitError(Exception):
    """Разбор не выполнен: текст готов для показа пользователю."""


class TransientSplitError(Exception):
    """Временный сбой (лимиты или сеть Anthropic) — стоит повторить попытку."""


def _is_transient(exc: Exception) -> bool:
    """Ошибка Anthropic, которая обычно проходит сама: лимиты, сеть, 5xx."""
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(
        exc,
        anthropic.RateLimitError
        | anthropic.APIConnectionError
        | anthropic.APITimeoutError
        | anthropic.InternalServerError,
    )


def ai_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", ""))


# Пакет одним файлом заметно тяжелее одиночного документа (20 МБ в
# ``transport_package_actions``): это скан всего пакета целиком.
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


def queue_upload(transport_request, car, upload, user) -> TransportBulkUpload:
    """Сохранить присланный файл и поставить его в очередь на разбор.

    В dev Celery работает в eager-режиме, поэтому разбор произойдёт сразу;
    в проде задачу берёт воркер, а клиент видит статус «обрабатывается».
    """
    from core.models.website import CLIENT_DOCUMENT_ALLOWED_EXTENSIONS

    extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if extension not in CLIENT_DOCUMENT_ALLOWED_EXTENSIONS:
        raise BulkSplitError(f"Файл {upload.name} — допустимы только PDF, JPG и PNG.")
    if upload.size > MAX_UPLOAD_SIZE:
        raise BulkSplitError(f"Файл {upload.name} слишком большой (максимум 50 МБ).")

    record = TransportBulkUpload.objects.create(
        request=transport_request,
        car=car,
        file=upload,
        uploaded_by=user if (user and getattr(user, "is_authenticated", False)) else None,
    )

    from core.tasks import process_transport_bulk_upload

    try:
        process_transport_bulk_upload.delay(record.pk)
        return record
    except Exception:
        logger.warning("Не удалось поставить разбор пакета #%s в очередь", record.pk, exc_info=True)

    # Брокер недоступен (или eager-режим пробросил ошибку) — пробуем разобрать
    # здесь же, иначе загрузка навсегда останется с вечным спиннером.
    try:
        process_transport_bulk_upload(record.pk)  # type: ignore[call-arg]
    except Exception:
        logger.exception("Синхронный разбор пакета #%s не удался", record.pk)
    record.refresh_from_db()
    if record.is_running:
        record.status = TransportBulkUpload.STATUS_ERROR
        record.error_message = "Не удалось начать разбор файла — попробуйте позже или загрузите документы по одному."
        record.save(update_fields=["status", "error_message"])
    return record


def split_upload(upload: TransportBulkUpload) -> dict:
    """Разобрать загруженный файл на документы пакета.

    Возвращает результат разбора (он же пишется в ``upload.result``).
    Ошибки оседают в ``upload.error_message`` — кроме временных сбоев
    Anthropic: они поднимаются как ``TransientSplitError``, чтобы задача
    ушла в повтор, а клиент продолжал видеть «обрабатывается».
    """
    upload.status = TransportBulkUpload.STATUS_PROCESSING
    upload.save(update_fields=["status"])

    try:
        result = _run(upload)
    except BulkSplitError as exc:
        upload.status = TransportBulkUpload.STATUS_ERROR
        upload.error_message = str(exc)
        upload.processed_at = timezone.now()
        upload.save(update_fields=["status", "error_message", "processed_at"])
        return {}
    except Exception as exc:
        if _is_transient(exc):
            logger.warning("Разбор пакета #%s отложен: %s", upload.pk, exc)
            upload.status = TransportBulkUpload.STATUS_PENDING
            upload.save(update_fields=["status"])
            raise TransientSplitError(str(exc)) from exc
        logger.exception("Разбор пакета #%s не удался", upload.pk)
        upload.status = TransportBulkUpload.STATUS_ERROR
        upload.error_message = "Не удалось разобрать файл автоматически — документы можно загрузить по одному."
        upload.processed_at = timezone.now()
        upload.save(update_fields=["status", "error_message", "processed_at"])
        return {}

    upload.status = TransportBulkUpload.STATUS_DONE
    upload.result = result
    upload.processed_at = timezone.now()
    upload.save(update_fields=["status", "result", "processed_at"])
    return result


def _run(upload: TransportBulkUpload) -> dict:
    from core.services.scan_extractor import render_document_images

    if not ai_available():
        raise BulkSplitError("Автоматическая сортировка сейчас недоступна — загрузите документы по одному.")

    path = upload.file.path
    images = render_document_images(path)
    if not images:
        raise BulkSplitError("Не удалось прочитать файл — проверьте, что это PDF, JPG или PNG.")
    if len(images) > MAX_PAGES:
        raise BulkSplitError(
            f"В файле {len(images)} страниц — слишком много для автоматической сортировки "
            f"(максимум {MAX_PAGES}). Разделите файл по автомобилям."
        )

    upload.pages_total = len(images)
    upload.save(update_fields=["pages_total"])

    page_types = _classify_pages(images)
    groups = _group_pages(page_types)
    created = _save_documents(upload, groups)

    unrecognized = [page for page, doc_type in enumerate(page_types, start=1) if doc_type == FALLBACK_TYPE]
    if any(item["doc_type"] == "PASSPORT" for item in created):
        _autofill_from_passport(upload)
    return {"documents": created, "unrecognized": unrecognized}


def _classify_pages(images: list[tuple[str, str]]) -> list[str]:
    """Тип каждой страницы по порядку; неуверенные — ``OTHER``."""
    from core.services.scan_extractor import _call_claude_vision

    types: list[str] = []
    for start in range(0, len(images), PAGES_PER_CALL):
        chunk = images[start : start + PAGES_PER_CALL]
        user_text = (
            f"Определи тип каждой из {len(chunk)} страниц. "
            f"Нумеруй их с 1 в том порядке, в котором они показаны."
        )
        data = _call_claude_vision(chunk, CLASSIFY_PROMPT, user_text)
        types.extend(_read_chunk_answer(data, len(chunk)))
    return types


def _read_chunk_answer(data: dict, expected: int) -> list[str]:
    """Ответ модели по чанку → список типов длиной ``expected``."""
    by_page: dict[int, str] = {}
    for item in (data or {}).get("pages", []):
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page", 0))
        except (TypeError, ValueError):
            continue
        doc_type = str(item.get("doc_type") or "").upper()
        confidence = str(item.get("confidence") or "").lower()
        if doc_type not in CLASSIFIABLE_TYPES or confidence == "low":
            doc_type = FALLBACK_TYPE
        by_page[page] = doc_type
    return [by_page.get(page, FALLBACK_TYPE) for page in range(1, expected + 1)]


def _group_pages(page_types: list[str]) -> list[tuple[str, list[int]]]:
    """Идущие подряд страницы одного типа — один документ.

    Номера страниц — с 1. «Остальное» тоже группируем: обычно это
    несколько подряд идущих непонятных сканов.
    """
    groups: list[tuple[str, list[int]]] = []
    for index, doc_type in enumerate(page_types, start=1):
        if groups and groups[-1][0] == doc_type:
            groups[-1][1].append(index)
        else:
            groups.append((doc_type, [index]))
    return groups


def _save_documents(upload: TransportBulkUpload, groups: list[tuple[str, list[int]]]) -> list[dict]:
    """Нарезать исходник по группам страниц и сохранить как документы пакета."""
    path = upload.file.path
    extension = os.path.splitext(path)[1].lower()
    created: list[dict] = []

    for doc_type, pages in groups:
        if extension == ".pdf":
            content = _extract_pdf_pages(path, pages)
            suffix = ".pdf"
        else:
            # Картинка — одна страница, резать нечего.
            with upload.file.open("rb") as fh:
                content = fh.read()
            suffix = extension

        filename = _build_filename(upload, doc_type, pages, suffix)
        doc = TransportRequestDocument.objects.create(
            request=upload.request,
            car=upload.car,
            doc_type=doc_type,
            file=ContentFile(content, name=filename),
            uploaded_by=upload.uploaded_by,
        )
        created.append(
            {
                "doc_type": doc_type,
                "pages": pages,
                "filename": filename,
                "document_id": doc.pk,
            }
        )
    return created


def _extract_pdf_pages(path: str, pages: list[int]) -> bytes:
    """Новый PDF из указанных страниц исходника (номера с 1)."""
    import fitz  # PyMuPDF

    source = fitz.open(path)
    try:
        target = fitz.open()
        try:
            for page in pages:
                target.insert_pdf(source, from_page=page - 1, to_page=page - 1)
            buffer = io.BytesIO()
            target.save(buffer)
        finally:
            target.close()
    finally:
        source.close()
    return buffer.getvalue()


def _build_filename(upload: TransportBulkUpload, doc_type: str, pages: list[int], suffix: str) -> str:
    vin = (getattr(upload.car, "vin", "") or str(upload.car_id)).replace(" ", "")
    span = f"p{pages[0]}" if len(pages) == 1 else f"p{pages[0]}-{pages[-1]}"
    return f"{doc_type.lower()}_{vin}_{span}{suffix}"


def _autofill_from_passport(upload: TransportBulkUpload) -> None:
    """Заполнить данные покупателя по распознанному паспорту.

    Тот же путь, что при загрузке паспорта вручную: пустые поля пакета
    заполняются, ручной ввод не перетирается.
    """
    from core.services.transport_package_actions import apply_passport_ai

    passport = (
        upload.request.documents.filter(car=upload.car, doc_type="PASSPORT").order_by("-created_at").first()
    )
    if passport is None:
        return
    package, _ = TransportDocumentPackage.objects.get_or_create(request=upload.request, car=upload.car)
    notices: list[tuple[str, str]] = []
    try:
        apply_passport_ai(package, [passport], notices)
    except Exception:
        logger.exception("Автозаполнение по паспорту из пакета #%s не удалось", upload.pk)
        return
    package.save(update_fields=["data", "updated_at"])


def retype_document(doc: TransportRequestDocument, doc_type: str) -> str:
    """Сменить тип документа вручную (клиент правит раскладку AI).

    Возвращает подпись нового типа для сообщения пользователю.
    """
    if doc_type not in _LABELS:
        raise BulkSplitError("Неизвестный тип документа.")
    if doc.is_generated:
        raise BulkSplitError("Сгенерированный документ нельзя переложить в другой слот.")
    doc.doc_type = doc_type
    doc.save(update_fields=["doc_type"])
    return _LABELS[doc_type]
