"""Автоподстановка и слияние граф литовского бланка CMR.

Один бланк — одна машина заявки. Ключи JSON совпадают с ``name`` полей
в шаблоне редактора. ``AUTO_KEYS`` заново заполняются кнопкой
«Подставить из заявки»; остальные графы сотрудник вводит вручную и
повторный префилл их не затирает.
"""

from __future__ import annotations

from decimal import Decimal

from core.services.transport_request_autotransport import match_carrier

# Графы, которые система умеет взять из заявки / машины / справочников.
AUTO_KEYS = (
    "sender",
    "consignee",
    "delivery_place",
    "delivery_country",
    "takeover_place",
    "takeover_country",
    "takeover_date",
    "annexed_docs",
    "marks",
    "packages",
    "packing",
    "goods_nature",
    "weight_kg",
    "sender_instructions",
    "carrier",
    "drivers",
    "truck_reg",
    "trailer_reg",
    "established_place",
    "established_date",
)

# Строки таблицы графы 19 «Apmokėjimui / To be paid by». В бланке каждая
# денежная колонка разделена вертикальной чертой на две клетки, поэтому у
# строки шесть полей ввода; ``strong`` — утолщённая линия под графой: ею
# в бланке отбит блок вычитания «провозная плата − скидка = разница».
PAY_ROWS = (
    {"key": "carriage", "lt": "Pervežimo kaina", "en": "Carriage charges", "sign": "", "strong": False},
    {"key": "reductions", "lt": "Nuolaida", "en": "Deductions", "sign": "–", "strong": True},
    {"key": "balance", "lt": "Skirtumas", "en": "Balance", "sign": "", "strong": False},
    {"key": "supplement", "lt": "Priedas", "en": "Supplem. charges", "sign": "", "strong": False},
    {"key": "other", "lt": "Papildoma rinkliava", "en": "Additional charges", "sign": "", "strong": False},
    {"key": "misc", "lt": "Kiti", "en": "Other", "sign": "+", "strong": False},
)

PAY_CELLS = ("sender", "sender_c", "currency", "currency_2", "consignee", "consignee_c")

_PAY_KEYS = tuple(f"pay_{row['key']}_{cell}" for row in PAY_ROWS for cell in PAY_CELLS)

# Все поля бланка — чтобы POST не тащил лишнее и пустые ключи были стабильны.
CMR_KEYS = (
    *AUTO_KEYS,
    "delivery_extra",
    "stat_no",
    "volume_m3",
    "adr_class",
    "adr_number",
    "adr_letter",
    "adr_agreement",
    "declared_value",
    "cod",
    "freight_payment",
    "following_carrier",
    "carrier_reservations",
    "special_agreements",
    *_PAY_KEYS,
    "pay_total_sender",
    "pay_total_currency",
    "pay_total_consignee",
    "load_arrival_h",
    "load_arrival_m",
    "load_departure_h",
    "load_departure_m",
    "goods_received_place",
    "goods_received_date",
    "goods_received_on",
    "goods_received_year",
    "unload_arrival_h",
    "unload_arrival_m",
    "unload_departure_h",
    "unload_departure_m",
    "journey_sheet",
    "journey_sheet_year",
    "drivers_2",
    "truck_type",
    "trailer_type",
    "t1_rate_km",
    "t1_distance",
    "t1_usage_pct",
    "t1_zone",
    "t1_other",
    "t1_total",
    "t2_distance",
    "t2_schema",
    "t2_weight",
    "t2_rate",
    "t2_surcharge",
    "t2_discount",
    "t2_other",
    "t2_payable",
    "t2_paid_customer",
    "t2_deductions",
    "t3_distance",
    "t3_schema",
    "t3_weight",
    "t3_rate",
    "t3_surcharge",
    "t3_discount",
    "t3_other",
    "t3_payable",
    "currency_code",
    "payer_code",
)

# Названия стран на бланке (литовский CMR). Коды — из Client / заявки.
_COUNTRY_LT = {
    "BY": "Baltarusija",
    "MD": "Moldova",
    "UA": "Ukraina",
    "LT": "Lietuva",
    "LV": "Latvija",
    "PL": "Lenkija",
    "KZ": "Kazachstanas",
    "KG": "Kirgizija",
    "UZ": "Uzbekistanas",
    "AM": "Armėnija",
    "AZ": "Azerbaidžanas",
    "GE": "Gruzija",
}

_DECL_DOCS = {
    "TRANSIT": "T1",
    "EXPORT": "Eksporto deklaracija",
    "IMPORT": "Importo deklaracija",
    "REEXPORT": "Reeksporto deklaracija",
}


def empty_cmr() -> dict:
    return {key: "" for key in CMR_KEYS}


def pay_rows(data: dict) -> list[dict]:
    """Строки графы 19 с уже подставленными значениями шести клеток."""
    rows = []
    for row in PAY_ROWS:
        cells = {cell: (data or {}).get(f"pay_{row['key']}_{cell}", "") or "" for cell in PAY_CELLS}
        rows.append({**row, **cells})
    return rows


def parse_cmr_post(post) -> dict:
    """Собирает JSON граф из POST редактора. Неизвестные ключи отбрасываются."""
    data = empty_cmr()
    for key in CMR_KEYS:
        data[key] = (post.get(key) or "").strip()
    return data


def apply_prefill(existing: dict, fresh: dict) -> dict:
    """Перезаписывает только автополя; ручные графы из ``existing`` сохраняются."""
    merged = empty_cmr()
    merged.update(existing or {})
    for key in AUTO_KEYS:
        merged[key] = fresh.get(key, "") or ""
    return merged


def prefill_cmr(transport_request, car) -> dict:
    """Значения граф из заявки, машины, компании, склада и перевозчика."""
    data = empty_cmr()
    data["sender"] = _sender_block()
    data["consignee"] = _consignee_block(transport_request, car)
    delivery_place, delivery_country = _delivery(transport_request, car)
    data["delivery_place"] = delivery_place
    data["delivery_country"] = delivery_country
    takeover_place, takeover_country, takeover_date = _takeover(transport_request, car)
    data["takeover_place"] = takeover_place
    data["takeover_country"] = takeover_country
    data["takeover_date"] = takeover_date
    data["annexed_docs"] = _annexed_docs(transport_request, car)
    data["marks"] = car.vin or ""
    data["packages"] = "1"
    data["packing"] = ""
    data["goods_nature"] = _goods_nature(car)
    data["weight_kg"] = _weight(car)
    data["sender_instructions"] = _sender_instructions(transport_request, car)
    data["carrier"] = _carrier_block(transport_request)
    data["drivers"] = (transport_request.driver_name or "").strip()
    data["truck_reg"] = (transport_request.truck_number or "").strip()
    data["trailer_reg"] = (transport_request.trailer_number or "").strip()
    data["established_place"] = takeover_place
    data["established_date"] = takeover_date
    return data


def _join(*parts: str) -> str:
    return "\n".join(p.strip() for p in parts if p and str(p).strip())


def _country_name(code: str, fallback: str = "") -> str:
    code = (code or "").strip()
    if not code:
        return fallback
    if code in _COUNTRY_LT:
        return _COUNTRY_LT[code]
    return fallback or code


def _sender_block() -> str:
    from core.models.company import Company

    company = Company.get_default()
    if company is None:
        return ""
    extras = []
    if company.imones_kodas:
        extras.append(f"Įm.k. {company.imones_kodas}")
    if company.vat_code:
        extras.append(f"PVM {company.vat_code}")
    country = (company.registration_country or "").strip() or "Lietuva"
    return _join(company.name, company.physical_address, country, ", ".join(extras))


def _consignee_block(transport_request, car) -> str:
    declaration = _declaration_for_car(transport_request, car)
    if declaration is not None:
        return _join(
            declaration.buyer_name,
            declaration.buyer_address,
            declaration.buyer_country,
            f"Kodas {declaration.buyer_code}" if declaration.buyer_code else "",
        )
    client = transport_request.client
    country = _country_name(client.country, client.get_country_display() if client.country else "")
    if not country:
        country = (client.registration_country or "").strip()
    extras = []
    if client.imones_kodas:
        extras.append(f"Įm.k. {client.imones_kodas}")
    return _join(client.name, client.physical_address, country, ", ".join(extras))


def _declaration_for_car(transport_request, car):
    qs = getattr(transport_request, "declaration_requests", None)
    if qs is None:
        return None
    return qs.filter(car=car).order_by("-created_at").first()


def _delivery(transport_request, car) -> tuple[str, str]:
    declaration = _declaration_for_car(transport_request, car)
    if declaration is not None:
        place = (declaration.destination_city or "").strip()
        country = (declaration.destination_country or "").strip()
        if not country:
            country = _country_name(transport_request.destination_country)
        return place, country
    country = _country_name(
        transport_request.destination_country,
        transport_request.get_destination_country_display() if transport_request.destination_country else "",
    )
    return "", country


def _takeover(transport_request, car) -> tuple[str, str, str]:
    warehouse = car.warehouse or transport_request.warehouse
    place = ""
    if warehouse is not None:
        site_name, site_address = warehouse.get_site_address(getattr(car, "unload_site", 1) or 1)
        place = _join(warehouse.name, site_name, site_address)
    date = ""
    if transport_request.planned_loading_date:
        date = transport_request.planned_loading_date.strftime("%d.%m.%Y")
    return place, "Lietuva", date


def _annexed_docs(transport_request, car) -> str:
    parts = []
    decl_type = transport_request.declaration_types_by_car().get(car.pk) or transport_request.declaration_type
    label = _DECL_DOCS.get(decl_type, "")
    if label:
        parts.append(label)
    if getattr(car, "has_title", False):
        parts.append("Title")
    return ", ".join(parts)


def _goods_nature(car) -> str:
    bits = ["NAUDOTAS AUTOMOBILIS / USED VEHICLE"]
    title = " ".join(p for p in (car.brand, str(car.year or "")) if p).strip()
    if title:
        bits.append(title)
    type_label = ""
    if getattr(car, "vehicle_type", None):
        type_label = car.get_vehicle_type_display()
    if type_label:
        bits.append(type_label)
    if car.vin:
        bits.append(f"VIN {car.vin}")
    return "\n".join(bits)


def _weight(car) -> str:
    weight = getattr(car, "weight_kg", None)
    if weight is None:
        return ""
    value = Decimal(weight)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _sender_instructions(transport_request, car) -> str:
    from core.models.website import TRANSPORT_DECLARATION_TYPES

    parts = []
    if transport_request.border_crossing:
        parts.append(f"Siena / Border: {transport_request.border_crossing}")
    decl_type = transport_request.declaration_types_by_car().get(car.pk) or transport_request.declaration_type
    if decl_type:
        labels = dict(TRANSPORT_DECLARATION_TYPES)
        parts.append(labels.get(decl_type, decl_type))
    return "\n".join(parts)


def _carrier_block(transport_request) -> str:
    match = match_carrier(transport_request)
    carrier = match.carrier
    if carrier is not None:
        country = (carrier.registration_country or "").strip()
        extras = []
        if carrier.imones_kodas:
            extras.append(f"Įm.k. {carrier.imones_kodas}")
        if carrier.vat_code:
            extras.append(f"PVM {carrier.vat_code}")
        return _join(carrier.name, carrier.physical_address, country, ", ".join(extras))
    return (transport_request.carrier_name or "").strip()
