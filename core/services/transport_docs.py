"""Пакет документов автовоза (оформление на Беларусь) — бизнес-логика.

Правила пакета (со слов владельца процесса):

* ключевые «реальные» файлы — паспорт, инвойс, подпись (их нельзя сгенерировать);
* платёжка и все остальные документы не могут быть датированы раньше инвойса;
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
import random
from decimal import Decimal, InvalidOperation

import holidays as holidays_lib

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
    day = requested or datetime.date.today()
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


def pick_payment_date(invoice_date: datetime.date) -> datetime.date:
    """Случайный рабочий день Беларуси в течение месяца после даты инвойса.

    Диапазон: от даты инвойса (включительно) до +30 дней. Если в диапазоне
    нет рабочих дней (крайне редко) — берётся ближайший рабочий день после инвойса.
    """
    countries = DOC_HOLIDAY_COUNTRIES["PAYMENT_ORDER"]
    end = invoice_date + datetime.timedelta(days=30)
    candidates = [
        day
        for offset in range((end - invoice_date).days + 1)
        if is_business_day(day := invoice_date + datetime.timedelta(days=offset), countries)
    ]
    if candidates:
        return random.choice(candidates)
    return shift_to_business_day(invoice_date, countries)


def pick_letter_usa_date(
    invoice_date: datetime.date,
    *,
    today: datetime.date | None = None,
) -> datetime.date:
    """Случайный рабочий день США для письма USA.

    Диапазон: не раньше чем через 3 недели после даты инвойса и не позже
    чем за неделю до даты генерации на сайте. Только будни без US-праздников.
    """
    today = today or datetime.date.today()
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
        raise PackageDataError(
            "В допустимом диапазоне нет рабочих дней США для письма USA. "
            "Проверьте дату инвойса."
        )
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
    today = today or datetime.date.today()
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
    return {
        "name": (data.get("buyer_name") or "").strip(),
        "name_ru": (data.get("buyer_name_ru") or "").strip(),
        "passport_number": (data.get("buyer_passport_number") or "").strip(),
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
            raise PackageDataError("Укажите сумму инвойса.")
        number = (data.get("invoice_number") or "").strip() or str(next_portal_number("PORTAL-USA-INVOICE", 32545))
        data["invoice_number"] = number
        date = resolve("invoice_date")
        pdf_bytes = pdf.generate_invoice_pdf(car, number=number, date=date, amount=amount, buyer=_buyer_from(data))

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
        amount = parse_amount(data.get("invoice_amount"))
        if amount is None:
            raise PackageDataError("Укажите сумму инвойса в окне «Инвойс».")
        require(data, "buyer_name", "Сначала заполните данные покупателя в окне «Паспорт».")
        date = parse_date(data.get("payment_date"))
        if date is None or date < invoice_date or date > invoice_date + datetime.timedelta(days=30):
            date = pick_payment_date(invoice_date)
        else:
            date = shift_to_business_day(date, DOC_HOLIDAY_COUNTRIES["PAYMENT_ORDER"])
        data["payment_date"] = date.isoformat()
        number = (data.get("payment_number") or "").strip()
        if not number:
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
        if signature_bytes is None:
            notices.append("Подпись не загружена — платёжка сформирована без подписи плательщика.")
        pdf_bytes = pdf.generate_payment_order_pdf(
            car,
            number=number,
            date=date,
            amount=amount,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            payer=payer,
            signature_bytes=signature_bytes,
        )

    elif doc_type == "LETTER_USA":
        invoice_date = get_invoice_date(data)
        if invoice_date is None:
            raise PackageDataError("Сначала укажите дату инвойса в окне «Инвойс».")
        date = parse_date(data.get("letter_date"))
        latest = datetime.date.today() - datetime.timedelta(weeks=1)
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
            "Сначала заполните данные покупателя в окне «Паспорт» (номер паспорта).",
        )
        if not (data.get("buyer_name_ru") or data.get("buyer_name")):
            raise PackageDataError("Сначала заполните ФИО покупателя в окне «Паспорт».")
        if not (data.get("buyer_address_ru") or data.get("buyer_address")):
            raise PackageDataError("Сначала укажите адрес покупателя в окне «Паспорт».")
        invoice_date = get_invoice_date(data)
        today = datetime.date.today()
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
