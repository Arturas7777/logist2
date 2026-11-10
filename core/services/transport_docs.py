"""Пакет документов автовоза (оформление на Беларусь) — бизнес-логика.

Правила пакета (со слов владельца процесса):

* ключевые «реальные» файлы — паспорт, инвойс, подпись (их нельзя сгенерировать);
* платёжка и все остальные документы не могут быть датированы раньше инвойса;
  платёжка — не раньше чем через ``PAYMENT_MIN_DAYS_AFTER_INVOICE`` дней после
  инвойса (клиенту нужно время, чтобы получить инвойс и оплатить его);
* дата любого генерируемого документа не должна попадать на выходной или
  праздничный день (страна праздников зависит от документа: инвойс и письмо —
  США, платёжка и обязательство — Беларусь, договор перевозки — Литва/Польша);
* дата письма USA выбирается автоматически: рабочий день США не раньше чем
  через 3 недели после инвойса и не позже чем за неделю до генерации;
* дата обязательства — автоматически: рабочий день Беларуси в последние
  3 недели до генерации (и не раньше даты инвойса).

Данные пакета хранятся в ``TransportDocumentPackage.data`` (JSON) отдельно для
каждого авто заявки. Отрисовка PDF — в :mod:`.transport_docs_pdf`.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import random
import zipfile
from decimal import Decimal, InvalidOperation

import holidays as holidays_lib
from django.utils import timezone

logger = logging.getLogger(__name__)

# Какие праздники учитывать для даты каждого генерируемого документа.
DOC_HOLIDAY_COUNTRIES = {
    "INVOICE": ("US",),
    "LETTER_USA": ("US",),
    "PAYMENT_ORDER": ("BY",),
    "OBLIGATION": ("BY",),
    "CONTRACT": ("LT", "PL"),
}

# Документы, дата которых не может быть раньше даты инвойса.
DOCS_NOT_BEFORE_INVOICE = {"PAYMENT_ORDER", "LETTER_USA", "OBLIGATION", "CONTRACT"}

PAYMENT_MIN_DAYS_AFTER_INVOICE = 2
PAYMENT_MAX_DAYS_AFTER_INVOICE = 30

# Реквизиты плательщика для пакета на Беларусь — фиксированные, в UI не спрашиваем.
DEFAULT_BELARUS_PAYER_IBAN = "BY04RSHN38455615894156834963"
DEFAULT_BELARUS_PAYER_BANK = 'ZAO "BTA Bank", Minsk, RB'
DEFAULT_BELARUS_PAYER_BANK_CODE = "AEBKBY2X"


class PackageDataError(ValueError):
    """Не хватает данных пакета или данные некорректны."""


def is_business_day(day: datetime.date, countries: tuple[str, ...]) -> bool:
    """Рабочий ли день: не суббота/воскресенье и не праздник в указанных странах."""
    if day.weekday() >= 5:
        return False
    return all(day not in holidays_lib.country_holidays(country, years=day.year) for country in countries)


def shift_to_business_day(day: datetime.date, countries: tuple[str, ...]) -> datetime.date:
    """Ближайший рабочий день начиная с ``day`` (вперёд)."""
    while not is_business_day(day, countries):
        day += datetime.timedelta(days=1)
    return day


def parse_date(value: str | None) -> datetime.date | None:
    """Дата из строки формы (ISO ``YYYY-MM-DD``) или ``None``."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise PackageDataError(f"Некорректная дата: {value}") from exc


def parse_amount(value: str | None) -> Decimal | None:
    """Сумма из строки формы («2,850.00», «2850», «2 850,50») или ``None``."""
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip().replace(" ", "").replace("$", "")
    # «2 850,50» / «2850,50» — запятая как десятичный разделитель
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise PackageDataError(f"Некорректная сумма: {value}") from exc
    if amount <= 0:
        raise PackageDataError("Сумма должна быть больше нуля.")
    return amount


def parse_invoice_extra_lines(data: dict) -> list[dict]:
    """Доп. строки инвойса из ``data['invoice_extra_lines']``.

    Каждая строка: ``{"description": str, "amount": Decimal}``.
    Пустые/битые элементы пропускаются с ошибкой только если заполнена
    ровно одна из двух частей (описание без суммы или наоборот).
    """
    raw = data.get("invoice_extra_lines") or []
    if not isinstance(raw, list):
        raise PackageDataError("Некорректный формат дополнительных строк инвойса.")
    lines: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        amount_raw = item.get("amount")
        amount_str = "" if amount_raw is None else str(amount_raw).strip()
        if not description and not amount_str:
            continue
        if not description:
            raise PackageDataError("У доп. строки инвойса укажите описание.")
        if not amount_str:
            raise PackageDataError(f"У доп. строки «{description}» укажите сумму.")
        lines.append({"description": description, "amount": parse_amount(amount_str)})
    return lines


def invoice_total_amount(data: dict) -> Decimal | None:
    """Итоговая сумма инвойса: цена авто + доп. строки."""
    base = parse_amount(data.get("invoice_amount"))
    if base is None:
        return None
    total = base
    for line in parse_invoice_extra_lines(data):
        total += line["amount"]
    return total


def normalize_invoice_extra_lines_from_post(post) -> list[dict]:
    """Собрать ``invoice_extra_lines`` из списков полей формы.

    Ожидает ``invoice_extra_desc`` / ``invoice_extra_amount`` (getlist).
    Возвращает список словарей со строковыми суммами (как в JSON пакета).
    """
    descs = post.getlist("invoice_extra_desc")
    amounts = post.getlist("invoice_extra_amount")
    # Выравниваем длины на случай рассинхрона полей.
    size = max(len(descs), len(amounts))
    lines: list[dict] = []
    for idx in range(size):
        description = (descs[idx] if idx < len(descs) else "").strip()
        amount_str = (amounts[idx] if idx < len(amounts) else "").strip()
        if not description and not amount_str:
            continue
        if not description:
            raise PackageDataError("У доп. строки инвойса укажите описание.")
        if not amount_str:
            raise PackageDataError(f"У доп. строки «{description}» укажите сумму.")
        parse_amount(amount_str)  # валидация
        lines.append({"description": description, "amount": amount_str})
    return lines


def resolve_document_date(
    doc_type: str,
    requested: datetime.date | None,
    invoice_date: datetime.date | None,
) -> datetime.date:
    """Вычислить дату генерируемого документа.

    Если дата не задана — берётся сегодня (но не раньше даты инвойса).
    Дата, попавшая на выходной/праздник, сдвигается вперёд на ближайший
    рабочий день. Дата раньше инвойса недопустима.
    """
    countries = DOC_HOLIDAY_COUNTRIES[doc_type]
    day = requested or timezone.localdate()
    if doc_type in DOCS_NOT_BEFORE_INVOICE and invoice_date:
        if requested and requested < invoice_date:
            raise PackageDataError(
                f"Дата документа ({requested.strftime('%d.%m.%Y')}) не может быть раньше "
                f"даты инвойса ({invoice_date.strftime('%d.%m.%Y')})."
            )
        day = max(day, invoice_date)
    return shift_to_business_day(day, countries)


def get_invoice_date(package_data: dict) -> datetime.date | None:
    return parse_date(package_data.get("invoice_date"))


def require(package_data: dict, key: str, message: str) -> str:
    value = (package_data.get(key) or "").strip()
    if not value:
        raise PackageDataError(message)
    return value


def next_portal_number(series: str, seed: int) -> int:
    """Следующий номер портальной серии (инвойсы, договоры)."""
    from core.models.series import SeriesCounter

    return SeriesCounter.next_value(series, seed)


def payment_date_bounds(invoice_date: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Допустимый диапазон даты платёжки относительно даты инвойса (включительно)."""
    return (
        invoice_date + datetime.timedelta(days=PAYMENT_MIN_DAYS_AFTER_INVOICE),
        invoice_date + datetime.timedelta(days=PAYMENT_MAX_DAYS_AFTER_INVOICE),
    )


def pick_payment_date(invoice_date: datetime.date) -> datetime.date:
    """Случайный рабочий день Беларуси в течение месяца после даты инвойса.

    Диапазон: от инвойса + ``PAYMENT_MIN_DAYS_AFTER_INVOICE`` до
    + ``PAYMENT_MAX_DAYS_AFTER_INVOICE`` дней. Если в диапазоне нет рабочих
    дней (крайне редко) — берётся ближайший рабочий день после начала диапазона.
    """
    countries = DOC_HOLIDAY_COUNTRIES["PAYMENT_ORDER"]
    start, end = payment_date_bounds(invoice_date)
    candidates = [
        day
        for offset in range((end - start).days + 1)
        if is_business_day(day := start + datetime.timedelta(days=offset), countries)
    ]
    if candidates:
        return random.choice(candidates)
    return shift_to_business_day(start, countries)


def pick_letter_usa_date(
    invoice_date: datetime.date,
    *,
    today: datetime.date | None = None,
) -> datetime.date:
    """Случайный рабочий день США для письма USA.

    Диапазон: не раньше чем через 3 недели после даты инвойса и не позже
    чем за неделю до даты генерации на сайте. Только будни без US-праздников.
    """
    today = today or timezone.localdate()
    countries = DOC_HOLIDAY_COUNTRIES["LETTER_USA"]
    start = invoice_date + datetime.timedelta(weeks=3)
    end = today - datetime.timedelta(weeks=1)
    if end < start:
        raise PackageDataError(
            "Письмо USA пока нельзя датировать: нужно, чтобы с даты инвойса "
            "прошло не меньше четырёх недель (3 недели до письма + неделя "
            "до генерации). Укажите более раннюю дату инвойса или подождите."
        )
    candidates = [
        day
        for offset in range((end - start).days + 1)
        if is_business_day(day := start + datetime.timedelta(days=offset), countries)
    ]
    if not candidates:
        raise PackageDataError("В допустимом диапазоне нет рабочих дней США для письма USA. Проверьте дату инвойса.")
    return random.choice(candidates)


def pick_obligation_date(
    invoice_date: datetime.date | None = None,
    *,
    today: datetime.date | None = None,
) -> datetime.date:
    """Случайный рабочий день Беларуси для обязательства клиента.

    Диапазон: последние 3 недели до даты генерации (включительно),
    но не раньше даты инвойса.
    """
    today = today or timezone.localdate()
    countries = DOC_HOLIDAY_COUNTRIES["OBLIGATION"]
    start = today - datetime.timedelta(weeks=3)
    if invoice_date is not None:
        start = max(start, invoice_date)
    end = today
    if end < start:
        # Инвойс в будущем относительно «сегодня» — берём ближайший рабочий день.
        return shift_to_business_day(start, countries)
    candidates = [
        day
        for offset in range((end - start).days + 1)
        if is_business_day(day := start + datetime.timedelta(days=offset), countries)
    ]
    if candidates:
        return random.choice(candidates)
    return shift_to_business_day(start, countries)


def make_payment_number(payment_date: datetime.date, used: set[str] | None = None) -> str:
    """Номер платёжки вида «2005-128» (ДДММ + случайное 1…999).

    ``used`` — уже занятые номера в заявке, чтобы не совпадали у разных авто.
    """
    used = used or set()
    prefix = f"{payment_date:%d%m}"
    for _ in range(200):
        candidate = f"{prefix}-{random.randint(1, 999)}"
        if candidate not in used:
            return candidate
    # Крайне маловероятный запасной вариант при исчерпании 1…999.
    return f"{prefix}-{random.randint(1000, 9999)}"


def _used_payment_numbers(transport_request, *, exclude_car=None) -> set[str]:
    used: set[str] = set()
    for pkg in transport_request.doc_packages.all():
        if exclude_car is not None and pkg.car_id == exclude_car.pk:
            continue
        number = (pkg.data or {}).get("payment_number") or ""
        number = str(number).strip()
        if number:
            used.add(number)
    return used


# Префиксы имён генерируемых файлов (как в реальных пакетах: «INVOICE MALIBU 1248»).
DOC_FILE_PREFIX = {
    "INVOICE": "INVOICE",
    "PAYMENT_ORDER": "PAYMENT",
    "LETTER_USA": "LETTER USA",
    "OBLIGATION": "OBLIGATION",
    "CONTRACT": "CONTRACT",
}


def document_filename(doc_type: str, car) -> str:
    model_word = car.brand.split()[-1].upper() if car.brand else "CAR"
    return f"{DOC_FILE_PREFIX[doc_type]} {model_word} {car.vin[-4:]}.pdf"


def _buyer_from(data: dict) -> dict:
    kind = (data.get("buyer_id_kind") or "").strip().lower()
    return {
        "name": (data.get("buyer_name") or "").strip(),
        "name_ru": (data.get("buyer_name_ru") or "").strip(),
        "passport_number": (data.get("buyer_passport_number") or "").strip(),
        "id_kind": kind if kind in {"passport", "id_card"} else "passport",
        "birth_date": parse_date(data.get("buyer_birth_date")),
        "passport_issue_date": parse_date(data.get("buyer_passport_issue_date")),
        "address": (data.get("buyer_address") or "").strip(),
        "address_ru": (data.get("buyer_address_ru") or "").strip(),
        "car_category": (data.get("car_category") or "").strip(),
    }


def generate_document(
    transport_request,
    car,
    data: dict,
    doc_type: str,
    signature_bytes: bytes | None = None,
) -> tuple[str, bytes, list[str]]:
    """Сгенерировать PDF документа пакета.

    Возвращает ``(имя файла, байты PDF, список уведомлений)``. Подставленные
    значения (номер, скорректированная дата) записываются обратно в ``data``.
    """
    from . import transport_docs_pdf as pdf

    notices: list[str] = []

    def resolve(key: str, invoice_date=None):
        requested = parse_date(data.get(key))
        final = resolve_document_date(doc_type, requested, invoice_date)
        if requested and final != requested:
            notices.append(
                f"Дата {requested.strftime('%d.%m.%Y')} — выходной или праздник; "
                f"взят ближайший рабочий день: {final.strftime('%d.%m.%Y')}."
            )
        data[key] = final.isoformat()
        return final

    if doc_type == "INVOICE":
        require(data, "buyer_name", "Сначала заполните данные покупателя в окне «Паспорт» (ФИО латиницей).")
        require(data, "buyer_address", "Сначала укажите адрес покупателя в окне «Паспорт».")
        amount = parse_amount(data.get("invoice_amount"))
        if amount is None:
            raise PackageDataError("Укажите цену автомобиля в инвойсе.")
        extra_lines = parse_invoice_extra_lines(data)
        number = (data.get("invoice_number") or "").strip() or str(next_portal_number("PORTAL-USA-INVOICE", 32545))
        data["invoice_number"] = number
        date = resolve("invoice_date")
        pdf_bytes = pdf.generate_invoice_pdf(
            car,
            number=number,
            date=date,
            amount=amount,
            buyer=_buyer_from(data),
            extra_lines=extra_lines,
        )

    elif doc_type == "PAYMENT_ORDER":
        invoice_number = require(
            data,
            "invoice_number",
            "Платёжка не может быть выписана раньше инвойса: сначала добавьте инвойс "
            "и укажите его номер, дату и сумму в окне «Инвойс».",
        )
        invoice_date = get_invoice_date(data)
        if invoice_date is None:
            raise PackageDataError("Укажите дату инвойса в окне «Инвойс».")
        amount = invoice_total_amount(data)
        if amount is None:
            raise PackageDataError("Укажите сумму инвойса в окне «Инвойс».")
        require(data, "buyer_name", "Сначала заполните данные покупателя в окне «Паспорт».")
        stored_date = parse_date(data.get("payment_date"))
        earliest, latest = payment_date_bounds(invoice_date)
        if stored_date is None or stored_date < earliest or stored_date > latest:
            date = pick_payment_date(invoice_date)
            if stored_date is not None:
                notices.append(
                    f"Дата платёжки {stored_date.strftime('%d.%m.%Y')} слишком близко к инвойсу "
                    f"({invoice_date.strftime('%d.%m.%Y')}) — платёжка должна быть не раньше чем "
                    f"через {PAYMENT_MIN_DAYS_AFTER_INVOICE} дня после инвойса. "
                    f"Новая дата: {date.strftime('%d.%m.%Y')}."
                    if stored_date < earliest
                    else f"Дата платёжки обновлена: {date.strftime('%d.%m.%Y')}."
                )
        else:
            date = shift_to_business_day(stored_date, DOC_HOLIDAY_COUNTRIES["PAYMENT_ORDER"])
        data["payment_date"] = date.isoformat()
        number = (data.get("payment_number") or "").strip()
        # Номер начинается с ДДММ даты платёжки — при смене даты он должен смениться тоже.
        if not number.startswith(f"{date:%d%m}-"):
            number = make_payment_number(date, _used_payment_numbers(transport_request, exclude_car=car))
        data["payment_number"] = number
        data["payer_bank_name"] = DEFAULT_BELARUS_PAYER_BANK
        data["payer_bank_code"] = DEFAULT_BELARUS_PAYER_BANK_CODE
        buyer = _buyer_from(data)
        payer = {
            "name": buyer["name"],
            "passport_number": buyer["passport_number"],
            "address": buyer["address"],
            "iban": DEFAULT_BELARUS_PAYER_IBAN,
            "bank_name": DEFAULT_BELARUS_PAYER_BANK,
            "bank_code": DEFAULT_BELARUS_PAYER_BANK_CODE,
        }
        # Подпись клиента на платёжке — только по явному флагу из UI (по умолчанию выкл.).
        include_signature = str(data.get("payment_include_signature") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        payment_signature = signature_bytes if include_signature else None
        if include_signature and payment_signature is None:
            notices.append("Подпись не загружена — платёжка сформирована без подписи плательщика.")
        pdf_bytes = pdf.generate_payment_order_pdf(
            car,
            number=number,
            date=date,
            amount=amount,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            payer=payer,
            signature_bytes=payment_signature,
        )

    elif doc_type == "LETTER_USA":
        invoice_date = get_invoice_date(data)
        if invoice_date is None:
            raise PackageDataError("Сначала укажите дату инвойса в окне «Инвойс».")
        date = parse_date(data.get("letter_date"))
        latest = timezone.localdate() - datetime.timedelta(weeks=1)
        earliest = invoice_date + datetime.timedelta(weeks=3)
        if (
            date is None
            or date < earliest
            or date > latest
            or not is_business_day(date, DOC_HOLIDAY_COUNTRIES["LETTER_USA"])
        ):
            date = pick_letter_usa_date(invoice_date)
        data["letter_date"] = date.isoformat()
        pdf_bytes = pdf.generate_letter_usa_pdf(car, date=date)

    elif doc_type == "OBLIGATION":
        require(
            data,
            "buyer_passport_number",
            "Сначала заполните данные покупателя в окне «Паспорт» (номер паспорта или ID-карты).",
        )
        if not (data.get("buyer_name_ru") or data.get("buyer_name")):
            raise PackageDataError("Сначала заполните ФИО покупателя в окне «Паспорт».")
        if not (data.get("buyer_address_ru") or data.get("buyer_address")):
            raise PackageDataError("Сначала укажите адрес покупателя в окне «Паспорт».")
        invoice_date = get_invoice_date(data)
        today = timezone.localdate()
        earliest = today - datetime.timedelta(weeks=3)
        if invoice_date is not None:
            earliest = max(earliest, invoice_date)
        date = parse_date(data.get("obligation_date"))
        if (
            date is None
            or date < earliest
            or date > today
            or not is_business_day(date, DOC_HOLIDAY_COUNTRIES["OBLIGATION"])
        ):
            date = pick_obligation_date(invoice_date, today=today)
        data["obligation_date"] = date.isoformat()
        if signature_bytes is None:
            notices.append("Подпись не загружена — обязательство сформировано без подписи.")
        pdf_bytes = pdf.generate_obligation_pdf(
            car, date=date, buyer=_buyer_from(data), signature_bytes=signature_bytes
        )

    elif doc_type == "CONTRACT":
        forwarder_name = (data.get("carrier_company") or "").strip() or (transport_request.carrier_name or "").strip()
        if not forwarder_name:
            raise PackageDataError("Укажите название перевозчика для договора.")
        date = resolve("contract_date", get_invoice_date(data))
        number = (data.get("contract_number") or "").strip() or str(next_portal_number("PORTAL-CONTRACT", 490))
        data["contract_number"] = number
        data["carrier_company"] = forwarder_name
        forwarder = {
            "name": forwarder_name,
            "address": (data.get("carrier_address") or "").strip(),
            "director": (data.get("carrier_director") or "").strip(),
            "regon": (data.get("carrier_regon") or "").strip(),
            "nip": (data.get("carrier_nip") or "").strip(),
            "krs": (data.get("carrier_krs") or "").strip(),
        }
        pdf_bytes = pdf.generate_contract_pdf(number=number, date=date, forwarder=forwarder)

    else:
        raise PackageDataError("Этот документ нельзя сгенерировать — загрузите реальный файл.")

    return document_filename(doc_type, car), pdf_bytes, notices


# Полный пакет по кнопке «Сгенерировать всё» — без договора на перевозку.
GENERATE_ALL_DOC_TYPES = ("INVOICE", "PAYMENT_ORDER", "LETTER_USA", "OBLIGATION")


def generate_all_documents(
    transport_request,
    car,
    data: dict,
    signature_bytes: bytes | None = None,
) -> tuple[list[tuple[str, str, bytes]], list[str]]:
    """Сгенерировать INVOICE → PAYMENT → LETTER → OBLIGATION.

    Возвращает ``([(doc_type, filename, pdf_bytes), ...], notices)``.
    Данные ``data`` мутируются (номера/даты) как при одиночной генерации.
    """
    results: list[tuple[str, str, bytes]] = []
    notices: list[str] = []
    for doc_type in GENERATE_ALL_DOC_TYPES:
        filename, pdf_bytes, doc_notices = generate_document(
            transport_request,
            car,
            data,
            doc_type,
            signature_bytes=signature_bytes,
        )
        results.append((doc_type, filename, pdf_bytes))
        notices.extend(doc_notices)
    return results, notices


# ---------------------------------------------------------------------------
# Архив пакетов: один PDF на VIN + тайтл из админки
# ---------------------------------------------------------------------------

# Подпись — входной файл для генерации, в пакет для скачивания не кладём.
_ARCHIVE_SKIP_DOC_TYPES = frozenset({"SIGNATURE"})

_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})


def store_normalized_passport(doc) -> str:
    """Перезаписать файл паспорта нормализованным PDF.

    Возвращает ``updated``. Если файла нет или он не читается — бросает
    ``PackageDataError``.
    """
    from django.core.files.base import ContentFile

    data = _read_storage_file(doc.file)
    if not data:
        raise PackageDataError(f"Файл паспорта недоступен: {doc.filename or doc.pk}")
    pdf = normalize_passport_to_pdf(data, doc.filename)
    stem = os.path.splitext(doc.filename or "passport")[0] or "passport"
    old_name = doc.file.name
    doc.file.save(f"{stem}.pdf", ContentFile(pdf), save=True)
    if old_name and old_name != doc.file.name:
        try:
            doc.file.storage.delete(old_name)
        except Exception:
            logger.warning("passport normalize: old file %s not deleted", old_name, exc_info=True)
    return "updated"


def package_pdf_filename(car) -> str:
    """Имя единого PDF-пакета на авто: ``PACKAGE MALIBU 1248.pdf``."""
    model_word = car.brand.split()[-1].upper() if car.brand else "CAR"
    return f"PACKAGE {model_word} {car.vin[-4:]}.pdf"


def _read_storage_file(field_file) -> bytes | None:
    """Прочитать FileField; ``None``, если файла нет или он недоступен."""
    if not field_file or not getattr(field_file, "name", None):
        return None
    try:
        field_file.open("rb")
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("archive: cannot open %s: %s", getattr(field_file, "name", "?"), exc)
        return None
    try:
        return field_file.read()
    finally:
        field_file.close()


def _apply_exif_orientation(data: bytes) -> bytes:
    """Повернуть изображение по EXIF, если телефон сохранил его боком."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return data
    upright = ImageOps.exif_transpose(img)
    if upright is img:
        return data
    buf = io.BytesIO()
    fmt = (img.format or "JPEG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt == "JPEG" and upright.mode not in ("RGB", "L"):
        upright = upright.convert("RGB")
    if fmt == "JPEG":
        upright.save(buf, format=fmt, quality=90)
    else:
        upright.save(buf, format=fmt)
    return buf.getvalue()


def _image_bytes_to_pdf(data: bytes, filename: str) -> bytes:
    """Картинка → одностраничный PDF. EXIF уже учтён вызывающим кодом."""
    import fitz

    ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
    filetype = "jpeg" if ext in {"jpg", "jpeg"} else ext
    try:
        img = fitz.open(stream=data, filetype=filetype or None)
    except Exception:
        img = fitz.open(stream=data)
    try:
        return img.convert_to_pdf()
    finally:
        img.close()


def _corners_are_margin(samples: bytes, width: int, height: int, n: int) -> bool:
    """Углы одного цвета — вокруг документа есть поле, его можно срезать."""
    if width < 4 or height < 4 or n < 3:
        return False

    def px(x: int, y: int) -> bytes:
        i = (y * width + x) * n
        return samples[i : i + 3]

    corners = (px(0, 0), px(width - 1, 0), px(0, height - 1), px(width - 1, height - 1))
    base = corners[0]
    for other in corners[1:]:
        if sum(abs(base[i] - other[i]) for i in range(3)) > 36:
            return False
    return True


def _ink_bbox(samples: bytes, width: int, height: int, n: int) -> tuple[int, int, int, int] | None:
    """Рамка пикселей, которые не совпадают с цветом полей."""

    def px(x: int, y: int) -> bytes:
        i = (y * width + x) * n
        return samples[i : i + 3]

    bg = px(0, 0)
    step = 2 if width * height > 1_500_000 else 1
    minx, miny, maxx, maxy = width, height, 0, 0
    found = False
    for y in range(0, height, step):
        row = y * width * n
        for x in range(0, width, step):
            i = row + x * n
            if sum(abs(samples[i + c] - bg[c]) for c in range(3)) <= 48:
                continue
            found = True
            if x < minx:
                minx = x
            if y < miny:
                miny = y
            if x > maxx:
                maxx = x
            if y > maxy:
                maxy = y
    if not found:
        return None
    return minx, miny, min(width, maxx + step), min(height, maxy + step)


def _content_clip(page):
    """Прямоугольник содержимого страницы. Поля того же цвета, что углы, срезаются."""
    import fitz

    full = page.rect
    pix = page.get_pixmap(alpha=False, colorspace=fitz.csRGB)
    if not _corners_are_margin(pix.samples, pix.width, pix.height, pix.n):
        return full
    bbox = _ink_bbox(pix.samples, pix.width, pix.height, pix.n)
    if bbox is None:
        return full
    x0, y0, x1, y1 = bbox
    pad = max(2, int(0.015 * max(pix.width, pix.height)))
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(pix.width, x1 + pad)
    y1 = min(pix.height, y1 + pad)
    area = (x1 - x0) * (y1 - y0)
    if area < 0.05 * pix.width * pix.height or area > 0.97 * pix.width * pix.height:
        return full
    sx = full.width / pix.width
    sy = full.height / pix.height
    return fitz.Rect(full.x0 + x0 * sx, full.y0 + y0 * sy, full.x0 + x1 * sx, full.y0 + y1 * sy)


def _projection_energy(values: list[int]) -> int:
    """Насколько проекция «полосатая»: у текста по строкам энергия высокая."""
    energy = 0
    previous = values[0] if values else 0
    for value in values[1:]:
        delta = value - previous
        energy += delta * delta
        previous = value
    return energy


def _ink_grid(samples: bytes, width: int, height: int, n: int) -> tuple[bytearray, int, int]:
    """Грубая маска содержимого. Фон — цвет левого верхнего пикселя."""
    bg = samples[:3]
    pixels = width * height
    if pixels > 1_500_000:
        step = 4
    elif pixels > 400_000:
        step = 2
    else:
        step = 1
    grid_w = (width + step - 1) // step
    grid_h = (height + step - 1) // step
    grid = bytearray(grid_w * grid_h)
    for y in range(grid_h):
        sample_y = min(height - 1, y * step)
        row = sample_y * width * n
        base = y * grid_w
        for x in range(grid_w):
            sample_x = min(width - 1, x * step)
            i = row + sample_x * n
            if sum(abs(samples[i + c] - bg[c]) for c in range(3)) > 48:
                grid[base + x] = 1
    return grid, grid_w, grid_h


def _rotate_grid(grid: bytearray, grid_w: int, grid_h: int, turns: int) -> tuple[bytearray, int, int]:
    """Повернуть маску по часовой на 0/90/180/270."""
    turns %= 4
    if turns == 0:
        return grid, grid_w, grid_h
    if turns == 2:
        out = bytearray(grid_w * grid_h)
        for y in range(grid_h):
            for x in range(grid_w):
                if grid[y * grid_w + x]:
                    out[(grid_h - 1 - y) * grid_w + (grid_w - 1 - x)] = 1
        return out, grid_w, grid_h
    out = bytearray(grid_w * grid_h)
    if turns == 1:
        for y in range(grid_h):
            for x in range(grid_w):
                if grid[y * grid_w + x]:
                    out[x * grid_h + (grid_h - 1 - y)] = 1
        return out, grid_h, grid_w
    for y in range(grid_h):
        for x in range(grid_w):
            if grid[y * grid_w + x]:
                out[(grid_w - 1 - x) * grid_h + y] = 1
    return out, grid_h, grid_w


def _line_scores(grid: bytearray, grid_w: int, grid_h: int) -> tuple[int, float]:
    """Энергия горизонтальных строк и доля чернил слева (текст обычно слева)."""
    rows = [0] * grid_h
    cols = [0] * grid_w
    for y in range(grid_h):
        base = y * grid_w
        ink = 0
        for x in range(grid_w):
            if grid[base + x]:
                ink += 1
                cols[x] += 1
        rows[y] = ink
    total = sum(cols)
    if total <= 0:
        return 0, 0.5
    weighted = sum(value * (grid_w - x) for x, value in enumerate(cols))
    return _projection_energy(rows), weighted / (total * grid_w)


def _has_text_lines(samples: bytes, width: int, height: int) -> bool:
    """Есть ли на странице строка текста, а не пустое поле или сплошная плашка."""
    step = 2
    limit = max(12, ((width + step - 1) // step) * 0.08)
    best = 0
    for y in range(0, height, step):
        row = y * width * 3
        previous = None
        changes = 0
        for x in range(0, width, step):
            i = row + x * 3
            is_dark = (samples[i] + samples[i + 1] + samples[i + 2]) < 420
            if previous is not None and is_dark != previous:
                changes += 1
            previous = is_dark
        if changes > best:
            best = changes
    return best >= limit


def _clockwise_rotation(samples: bytes, width: int, height: int, n: int) -> int:
    """На сколько градусов по часовой повернуть, чтобы текст лёг горизонтально.

    Сравниваются четыре положения. Если ни одно не сильнее текущего,
    страница с текстом остаётся как есть (открытый паспорт может быть
    горизонтальным). Пустой горизонтальный лист ставится вертикально.
    """
    grid, grid_w, grid_h = _ink_grid(samples, width, height, n)
    if sum(grid) < 8:
        return 90 if width > height else 0
    scored: list[tuple[int, float, int]] = []
    for turns in range(4):
        turned, turned_w, turned_h = _rotate_grid(grid, grid_w, grid_h, turns)
        energy, left_ratio = _line_scores(turned, turned_w, turned_h)
        scored.append((energy, left_ratio, turns))
    baseline = scored[0][0]
    best = max(item[0] for item in scored)
    # Порог высокий: орнамент паспорта даёт слабый перевес, его не крутим.
    # Явный боковой текст (строки против полос) сильнее в десятки раз.
    if best >= max(1, baseline) * 6:
        contenders = [item for item in scored if item[0] >= best * 0.85]
        contenders.sort(key=lambda item: (item[1], -item[2]))
        return contenders[-1][2] * 90
    if width > height and not _has_text_lines(samples, width, height):
        return 90
    return 0


def _rotation_to_upright(page) -> int:
    import fitz

    pix = page.get_pixmap(alpha=False, colorspace=fitz.csRGB)
    if pix.width < 8 or pix.height < 8:
        return 90 if pix.width > pix.height else 0
    return _clockwise_rotation(pix.samples, pix.width, pix.height, pix.n)


def normalize_passport_to_pdf(data: bytes, filename: str = "") -> bytes:
    """Паспорт → PDF.

    Каждая страница обрезается по содержимому (белые поля и фон стола того же
    цвета, что углы). Главная страница доворачивается по направлению текста,
    даже если лист уже вертикальный, а содержимое лежит боком. Если направления
    не видно, горизонтальный лист ставится вертикально.
    Картинка сначала выравнивается по EXIF.
    """
    import fitz

    ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
    is_pdf = ext == "pdf" or data[:4] == b"%PDF"
    if not is_pdf:
        data = _apply_exif_orientation(data)
        data = _image_bytes_to_pdf(data, filename or "passport.jpg")
    src = fitz.open(stream=data, filetype="pdf")
    out = fitz.open()
    try:
        if src.page_count < 1:
            raise PackageDataError("В паспорте нет страниц.")
        for index in range(src.page_count):
            clip = _content_clip(src[index])
            if clip.width < 1 or clip.height < 1:
                clip = src[index].rect
            holder = fitz.open()
            try:
                placed = holder.new_page(width=clip.width, height=clip.height)
                placed.show_pdf_page(placed.rect, src, index, clip=clip)
                if index == 0:
                    rotation = _rotation_to_upright(placed)
                    if rotation:
                        placed.set_rotation(rotation)
                # get_pixmap учитывает /Rotate, show_pdf_page — нет.
                # Картинку вшиваем как есть, чтобы предпросмотр не зависел от флага.
                pix = placed.get_pixmap(alpha=False, colorspace=fitz.csRGB)
                dest = out.new_page(width=pix.width, height=pix.height)
                dest.insert_image(dest.rect, pixmap=pix)
            finally:
                holder.close()
        return out.tobytes()
    finally:
        src.close()
        out.close()


def _append_file_bytes(out_doc, data: bytes, filename: str, *, portrait_first_page: bool = False) -> None:
    """Добавить PDF или изображение в объединённый документ (PyMuPDF).

    ``portrait_first_page`` — паспорт: обрезать поля и поставить главную
    страницу вертикально, результат вставляется уже как PDF.
    """
    import fitz

    if portrait_first_page:
        data = normalize_passport_to_pdf(data, filename)
        filename = "passport.pdf"
    ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
    if ext == "pdf" or data[:4] == b"%PDF":
        src = fitz.open(stream=data, filetype="pdf")
        try:
            out_doc.insert_pdf(src)
        finally:
            src.close()
        return
    if ext in _IMAGE_EXTS or ext == "jpg":
        pdf_bytes = _image_bytes_to_pdf(data, filename)
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            out_doc.insert_pdf(src)
        finally:
            src.close()
        return
    raise PackageDataError(f"Неподдерживаемый формат файла: {filename or ext or '?'}")


def build_car_package_pdf(transport_request, car, only_types=None) -> bytes | None:
    """Собрать единый PDF по авто: документы заявки + тайтл из админки.

    Порядок страниц — как в ``TRANSPORT_DOCUMENT_TYPES`` (без подписи).
    Скан ``Car.title_scan`` добавляем в конец только если тайтла нет среди
    документов заявки: обычно он прикрепляется в пакет автоматически
    (``transport_package_actions.sync_title_documents``), и второй раз в
    PDF он не нужен. Возвращает ``None``, если нечего положить в пакет.
    """
    import fitz

    from core.models.website import TRANSPORT_DOCUMENT_TYPES

    type_order = {code: idx for idx, (code, _) in enumerate(TRANSPORT_DOCUMENT_TYPES)}
    docs = [d for d in transport_request.documents.filter(car=car) if d.doc_type not in _ARCHIVE_SKIP_DOC_TYPES]
    if only_types is not None:
        docs = [d for d in docs if d.doc_type in only_types]
    docs.sort(key=lambda d: (type_order.get(d.doc_type, 99), d.created_at, d.pk))

    out = fitz.open()
    try:
        for doc in docs:
            data = _read_storage_file(doc.file)
            if not data:
                continue
            try:
                _append_file_bytes(
                    out,
                    data,
                    doc.filename,
                    portrait_first_page=doc.doc_type == "PASSPORT",
                )
            except Exception as exc:
                logger.warning(
                    "archive: skip doc id=%s (%s): %s",
                    doc.pk,
                    doc.filename,
                    exc,
                )

        has_title_doc = any(doc.doc_type == "TITLE" for doc in docs)
        include_title = only_types is None or "TITLE" in only_types
        title_data = None
        if include_title and not has_title_doc:
            title_data = _read_storage_file(getattr(car, "title_scan", None))
        if title_data:
            title_name = os.path.basename(car.title_scan.name) if car.title_scan.name else "title.pdf"
            try:
                _append_file_bytes(out, title_data, title_name)
            except Exception as exc:
                logger.warning("archive: skip title for car %s: %s", car.vin, exc)

        if out.page_count == 0:
            return None
        return out.tobytes()
    finally:
        out.close()


def build_request_packages_zip(transport_request, only_types=None) -> tuple[str, bytes]:
    """ZIP с PDF-пакетами по каждому VIN заявки.

    Возвращает ``(имя_архива, байты)``. Пустой архив → ``PackageDataError``.
    """
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for car in transport_request.cars.all().order_by("id"):
            pdf_bytes = build_car_package_pdf(transport_request, car, only_types=only_types)
            if not pdf_bytes:
                continue
            zf.writestr(package_pdf_filename(car), pdf_bytes)
            added += 1
    if added == 0:
        raise PackageDataError("Нет документов для скачивания. Загрузите или сформируйте документы по автомобилям.")
    return f"{transport_request.number}_packages.zip", buf.getvalue()
