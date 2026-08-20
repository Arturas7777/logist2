"""Действия над пакетом документов заявки: сохранение, загрузка, генерация.

Общий код для двух точек входа — кабинета клиента
(``core.views_website.portal_transport``) и админ-карточки заявки
(``core.views.requests_board``). Права сотрудника шире прав клиента, но сами
операции над пакетом идентичны, поэтому логика живёт здесь, а вьюхи только
раскладывают результат: клиенту — в ``django.contrib.messages``, админке — в
JSON или те же messages.

Функции ничего не знают о ``HttpRequest`` и возвращают список замечаний
``[(level, text), …]``, где level — ``success`` / ``info`` / ``warning`` /
``error`` (совпадает с уровнями messages).
"""

from __future__ import annotations

import logging
import os

from django.core.files.base import ContentFile

from core.models.website import (
    CLIENT_DOCUMENT_ALLOWED_EXTENSIONS,
    TRANSPORT_DOCUMENT_TYPES,
    TRANSPORT_UPLOAD_ONLY_TYPES,
    TransportDocumentPackage,
    TransportRequestDocument,
)
from core.services import transport_docs as docs_service
from core.services.transport_docs import PackageDataError

logger = logging.getLogger(__name__)

Notice = tuple[str, str]

MAX_UPLOAD_SIZE = 20 * 1024 * 1024

DOC_TYPE_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)

# Поля данных пакета, которые принимает каждое модальное окно.
PACKAGE_FIELDS: dict[str, list[str]] = {
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
    "TITLE": [],
    "OTHER": [],
}

# Поля, которые принимает окно «Сгенерировать всё».
GENERATE_ALL_FIELDS = (
    "buyer_name_ru",
    "buyer_address_ru",
    "buyer_name",
    "buyer_passport_number",
    "buyer_birth_date",
    "buyer_passport_issue_date",
    "buyer_address",
    "invoice_number",
    "invoice_date",
    "invoice_amount",
)

# Подписи полей паспорта для сообщения «распознано автоматически».
_PASSPORT_FIELD_LABELS = {
    "buyer_name": "ФИО латиницей",
    "buyer_passport_number": "номер паспорта",
    "buyer_birth_date": "дата рождения",
    "buyer_passport_issue_date": "дата выдачи",
}


class DocActionError(PackageDataError):
    """Действие не выполнено: сообщение уже готово для показа пользователю."""


def update_package_data(package, doc_type, post) -> bool:
    """Перенести поля модального окна в данные пакета (только присланные)."""
    changed = False
    for field in PACKAGE_FIELDS.get(doc_type, []):
        if field in post:
            value = post.get(field, "").strip()
            if package.data.get(field, "") != value:
                package.data[field] = value
                changed = True
    if doc_type == "INVOICE":
        # Доп. строки всегда перечитываем с формы (в т.ч. пустой список = очистка).
        lines = docs_service.normalize_invoice_extra_lines_from_post(post)
        if package.data.get("invoice_extra_lines") != lines:
            package.data["invoice_extra_lines"] = lines
            changed = True
    if doc_type == "PAYMENT_ORDER":
        # Checkbox: отсутствие в POST = выключено.
        flag = "1" if post.get("payment_include_signature") in ("1", "on", "true", "yes") else ""
        if package.data.get("payment_include_signature", "") != flag:
            package.data["payment_include_signature"] = flag
            changed = True
    return changed


def signature_bytes(transport_request, car):
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


def apply_passport_ai(package, saved_docs, notices: list[Notice]) -> None:
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
            notices.append(
                ("info", "«Паспорт»: автораспознавание сейчас недоступно — проверьте и заполните поля вручную.")
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
            notices.append(("success", f"«Паспорт»: распознано автоматически — {', '.join(filled)}."))
        elif not extracted:
            notices.append(("warning", "«Паспорт»: не удалось распознать данные с фото — заполните поля вручную."))

    if not (data.get("buyer_address") or "").strip() and (data.get("buyer_address_ru") or "").strip():
        latin = passport_extractor.transliterate_address(data["buyer_address_ru"])
        if latin:
            data["buyer_address"] = latin
            notices.append(("success", f"«Паспорт»: адрес транслитерирован — {latin}"))


def save_upload_doc(transport_request, car, doc_type, upload, user):
    """Сохранить загруженный файл пакета; для подписи — нормализация."""
    extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if extension not in CLIENT_DOCUMENT_ALLOWED_EXTENSIONS:
        raise DocActionError(f"Файл {upload.name} — допустимы только PDF, JPG и PNG.")
    if upload.size > MAX_UPLOAD_SIZE:
        raise DocActionError(f"Файл {upload.name} слишком большой (максимум 20 МБ).")

    file_to_save = upload
    if doc_type == "SIGNATURE" and extension in {"jpg", "jpeg", "png"}:
        from core.services.signature_normalizer import normalize_signature_image

        normalized = normalize_signature_image(upload.read())
        upload.seek(0)
        if normalized:
            stem = upload.name.rsplit(".", 1)[0] if "." in upload.name else "signature"
            file_to_save = ContentFile(normalized, name=f"{stem}.png")
        # Старые сгенерированные «авто»-подписи заменяем.
        for old in transport_request.documents.filter(car=car, doc_type="SIGNATURE", is_generated=True):
            old.file.delete(save=False)
            old.delete()

    return TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type=doc_type,
        file=file_to_save,
        uploaded_by=user if (user and getattr(user, "is_authenticated", False)) else None,
    )


def sync_title_documents(transport_request) -> int:
    """Положить в пакет тайтлы, которые уже есть у нас в системе.

    Тайтл обязателен для любой процедуры, но обычно клиенту его грузить не
    нужно: скан приходит к нам с принтера и лежит в ``Car.title_scan``
    (см. ``core.services.scan_applier``). Копируем файл в документы заявки,
    чтобы пакет был полным без участия клиента, а слот «Тайтл» в кабинете
    оставался пустым только у машин, тайтла на которые у нас правда нет.

    Копия, а не ссылка: пакет заявки — снимок документов на момент отправки
    складу, и замена тайтла в карточке авто не должна задним числом менять
    уже отправленный пакет. Функция идемпотентна и вызывается при открытии
    страниц заявки — если файл удалили из пакета вручную, он вернётся.
    """
    have_title = set(transport_request.documents.filter(doc_type="TITLE").values_list("car_id", flat=True))
    created = 0
    for car in transport_request.cars.all():
        scan = getattr(car, "title_scan", None)
        if car.pk in have_title or not scan:
            continue
        name = os.path.basename(scan.name or "")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in CLIENT_DOCUMENT_ALLOWED_EXTENSIONS:
            continue
        try:
            with scan.open("rb") as fh:
                raw = fh.read()
        except (OSError, ValueError):
            # Файла может не быть на диске (например, база с продакшена
            # без media) — пакет тогда просто ждёт файл от клиента.
            logger.warning("Тайтл авто %s недоступен: %s", car.vin, scan.name)
            continue
        TransportRequestDocument.objects.create(
            request=transport_request,
            car=car,
            doc_type="TITLE",
            file=ContentFile(raw, name=name),
            is_generated=True,
        )
        created += 1
    return created


def _replace_generated(transport_request, car, doc_type, filename, pdf_bytes, user):
    """Пересгенерированный документ заменяет предыдущий сгенерированный."""
    for old in transport_request.documents.filter(car=car, doc_type=doc_type, is_generated=True):
        old.file.delete(save=False)
        old.delete()
    return TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type=doc_type,
        file=ContentFile(pdf_bytes, name=filename),
        is_generated=True,
        uploaded_by=user if (user and getattr(user, "is_authenticated", False)) else None,
    )


def apply_doc_action(*, transport_request, car, doc_type, post, files, user) -> list[Notice]:
    """Окно документа пакета: сохранить данные/файлы или сгенерировать PDF.

    ``post`` — QueryDict формы, ``files`` — список загруженных файлов.
    Ошибки, которые нужно показать пользователю, поднимаются как
    ``DocActionError``; частичные проблемы возвращаются в списке замечаний.
    """
    if doc_type not in DOC_TYPE_LABELS:
        raise DocActionError("Неизвестный тип документа.")

    package, _ = TransportDocumentPackage.objects.get_or_create(request=transport_request, car=car)
    label = DOC_TYPE_LABELS[doc_type]
    notices: list[Notice] = []

    # Валидация вводимых значений (дат/сумм) до сохранения.
    try:
        update_package_data(package, doc_type, post)
        for field in (
            "buyer_birth_date",
            "buyer_passport_issue_date",
            "invoice_date",
            "payment_date",
            "contract_date",
        ):
            if field in PACKAGE_FIELDS.get(doc_type, []):
                docs_service.parse_date(package.data.get(field))
        if "invoice_amount" in PACKAGE_FIELDS.get(doc_type, []):
            docs_service.parse_amount(package.data.get("invoice_amount"))
    except PackageDataError as exc:
        raise DocActionError(f"«{label}»: {exc}") from exc

    if post.get("action") == "generate":
        if doc_type in TRANSPORT_UPLOAD_ONLY_TYPES:
            raise DocActionError(f"«{label}» нельзя сгенерировать — загрузите реальный файл.")
        try:
            filename, pdf_bytes, gen_notices = docs_service.generate_document(
                transport_request,
                car,
                package.data,
                doc_type,
                signature_bytes=signature_bytes(transport_request, car),
            )
        except PackageDataError as exc:
            package.save(update_fields=["data", "updated_at"])
            raise DocActionError(f"«{label}»: {exc}") from exc
        package.save(update_fields=["data", "updated_at"])
        _replace_generated(transport_request, car, doc_type, filename, pdf_bytes, user)
        notices.append(("success", f"«{label}» сгенерирован."))
        notices += [("info", f"«{label}»: {notice}") for notice in gen_notices]
        return notices

    # Сохранение данных + загрузка файлов.
    saved_docs = []
    for upload in files:
        try:
            saved_docs.append(save_upload_doc(transport_request, car, doc_type, upload, user))
        except DocActionError as exc:
            notices.append(("error", f"«{label}»: {exc}"))

    if doc_type == "PASSPORT":
        apply_passport_ai(package, saved_docs, notices)
        if not (package.data.get("buyer_address") or package.data.get("buyer_address_ru")):
            notices.append(
                (
                    "warning",
                    "«Паспорт»: введите адрес проживания кириллицей — рукописный адрес в паспорте "
                    "плохо читается автоматикой, а латинский вариант подставится сам.",
                )
            )

    package.save(update_fields=["data", "updated_at"])
    if saved_docs:
        notices.append(("success", f"«{label}»: файлы добавлены ({len(saved_docs)} шт.)."))
    else:
        notices.append(("success", f"«{label}»: данные сохранены."))
    return notices


def generate_all_for_car(*, transport_request, car, post, files, user) -> list[Notice]:
    """Сгенерировать полный пакет по авто (без договора на перевозку).

    Принимает паспорт, адрес кириллицей, подпись и данные инвойса; сохраняет
    файлы, распознаёт паспорт и создаёт INVOICE / PAYMENT / LETTER / OBLIGATION.
    """
    package, _ = TransportDocumentPackage.objects.get_or_create(request=transport_request, car=car)
    notices: list[Notice] = []
    prefix = "«Сгенерировать всё»"

    try:
        for field in GENERATE_ALL_FIELDS:
            if field in post:
                package.data[field] = post.get(field, "").strip()
        package.data["invoice_extra_lines"] = docs_service.normalize_invoice_extra_lines_from_post(post)
        docs_service.parse_date(package.data.get("buyer_birth_date"))
        docs_service.parse_date(package.data.get("buyer_passport_issue_date"))
        docs_service.parse_date(package.data.get("invoice_date"))
        docs_service.parse_amount(package.data.get("invoice_amount"))
    except PackageDataError as exc:
        raise DocActionError(f"{prefix}: {exc}") from exc

    if not (package.data.get("buyer_address_ru") or "").strip():
        raise DocActionError(f"{prefix}: укажите адрес проживания кириллицей.")
    if not (package.data.get("buyer_name_ru") or "").strip():
        raise DocActionError(f"{prefix}: укажите ФИО по-русски.")
    if docs_service.parse_amount(package.data.get("invoice_amount")) is None:
        raise DocActionError(f"{prefix}: укажите цену автомобиля в инвойсе.")

    passport_upload = files.get("passport")
    signature_upload = files.get("signature")
    has_passport = transport_request.documents.filter(car=car, doc_type="PASSPORT").exists()
    has_signature = transport_request.documents.filter(car=car, doc_type="SIGNATURE").exists()

    try:
        saved_passport = []
        if passport_upload:
            saved_passport.append(save_upload_doc(transport_request, car, "PASSPORT", passport_upload, user))
        elif not has_passport:
            raise DocActionError("Загрузите файл паспорта.")
        if signature_upload:
            save_upload_doc(transport_request, car, "SIGNATURE", signature_upload, user)
        elif not has_signature:
            raise DocActionError("Загрузите файл подписи.")
    except PackageDataError as exc:
        package.save(update_fields=["data", "updated_at"])
        raise DocActionError(f"{prefix}: {exc}") from exc

    apply_passport_ai(package, saved_passport, notices)
    # После AI адрес/ФИО латиницей могут появиться; без латиницы инвойс не соберётся.
    if not (package.data.get("buyer_address") or "").strip() and (package.data.get("buyer_address_ru") or "").strip():
        from core.services import passport_extractor

        latin = passport_extractor.transliterate_address(package.data["buyer_address_ru"])
        if latin:
            package.data["buyer_address"] = latin

    try:
        results, gen_notices = docs_service.generate_all_documents(
            transport_request,
            car,
            package.data,
            signature_bytes=signature_bytes(transport_request, car),
        )
    except PackageDataError as exc:
        package.save(update_fields=["data", "updated_at"])
        raise DocActionError(f"{prefix}: {exc}") from exc

    package.save(update_fields=["data", "updated_at"])
    for doc_type, filename, pdf_bytes in results:
        _replace_generated(transport_request, car, doc_type, filename, pdf_bytes, user)

    labels = ", ".join(DOC_TYPE_LABELS[dt] for dt, _, _ in results)
    notices.append(("success", f"Пакет сгенерирован: {labels}."))
    notices += [("info", notice) for notice in gen_notices]
    return notices


def delete_doc(transport_request, doc) -> Notice:
    """Удалить файл документа пакета, вернуть замечание для пользователя."""
    label = DOC_TYPE_LABELS.get(doc.doc_type, doc.doc_type)
    doc.file.delete(save=False)
    doc.delete()
    return ("success", f"«{label}»: файл удалён.")
