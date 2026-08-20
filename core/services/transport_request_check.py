"""Проверка готовности заявки на автовоз к отправке складу.

Единственное место, где решается, какие документы обязательны в пакете на
авто. Питает три вещи:

* бейдж полноты пакета на карточке заявки в админ-доске;
* предзаполнение чекбоксов в форме «запросить документы у клиента»;
* блокирующие замечания перед отправкой письма складу.

Обязательность зависит от пары «страна назначения + таможенная процедура»:
в разные страны таможня требует разный набор документов. Наборы правит
сотрудник в админке (``TransportDocumentRule``); встроенные значения ниже —
фолбэк на случай, если для пары строки нет (в т.ч. когда страна не выбрана).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models.website import TRANSPORT_DOCUMENT_TYPES

_DOC_LABELS = dict(TRANSPORT_DOCUMENT_TYPES)

# Тайтл нужен в любом пакете: без документа о собственности таможня не
# оформит ни одну процедуру. Отключить его правилом в админке нельзя —
# требование добавляется к любому набору (обычно тайтл уже есть у нас и
# прикрепляется автоматически, см. ``transport_package_actions``).
_ALWAYS_REQUIRED = ("TITLE",)

# Базовый набор — нужен при любой процедуре и в любую страну.
_BASE_REQUIRED = ("PASSPORT", "INVOICE")

# Транзит: полный пакет, который склад просит для оформления T1.
_TRANSIT_REQUIRED = (
    "PASSPORT",
    "INVOICE",
    "PAYMENT_ORDER",
    "LETTER_USA",
    "OBLIGATION",
    "CONTRACT",
)

DEFAULT_REQUIRED_BY_PROCEDURE = {
    "TRANSIT": _TRANSIT_REQUIRED,
    "EXPORT": _BASE_REQUIRED,
    "IMPORT": _BASE_REQUIRED,
    "REEXPORT": _BASE_REQUIRED,
    # Процедура не выбрана — требуем хотя бы базу, чтобы бейдж не был «всё готово».
    "": _BASE_REQUIRED,
}


def required_doc_types(procedure: str | None, country: str | None = None) -> tuple[str, ...]:
    """Обязательные типы документов для пары «страна + процедура».

    Сначала ищем правило, заведённое сотрудником в админке; если его нет —
    берём встроенный набор по процедуре. К любому набору добавляется
    ``_ALWAYS_REQUIRED``. Порядок — как в ``TRANSPORT_DOCUMENT_TYPES``,
    чтобы списки везде выглядели одинаково.
    """
    procedure = procedure or ""
    country = country or ""
    codes = _rule_doc_types(country, procedure)
    if codes is None:
        codes = DEFAULT_REQUIRED_BY_PROCEDURE.get(procedure, _BASE_REQUIRED)
    wanted = set(codes) | set(_ALWAYS_REQUIRED)
    return tuple(code for code, _label in TRANSPORT_DOCUMENT_TYPES if code in wanted)


def _rule_doc_types(country: str, procedure: str) -> list[str] | None:
    """Список из правила админки или ``None``, если правила нет."""
    if not country or not procedure:
        return None
    from core.models.website import TransportDocumentRule

    rule = TransportDocumentRule.objects.filter(country=country, procedure=procedure, is_active=True).first()
    if rule is None:
        return None
    return list(rule.required_doc_types or [])


@dataclass
class CarDocStatus:
    """Полнота пакета по одному авто заявки."""

    car: object
    declaration_type: str
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def missing_labels(self) -> list[str]:
        return [_DOC_LABELS.get(code, code) for code in self.missing]


@dataclass
class RequestReadiness:
    """Сводка готовности заявки: полнота пакетов + блокеры отправки складу."""

    cars: list[CarDocStatus] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return bool(self.cars) and all(c.is_complete for c in self.cars)

    @property
    def missing_doc_types(self) -> list[str]:
        """Объединение недостающих типов по всем авто, в порядке ``TRANSPORT_DOCUMENT_TYPES``."""
        missing: set[str] = set()
        for status in self.cars:
            missing.update(status.missing)
        return [code for code, _label in TRANSPORT_DOCUMENT_TYPES if code in missing]

    @property
    def complete_cars_count(self) -> int:
        return sum(1 for c in self.cars if c.is_complete)

    @property
    def can_send_to_warehouse(self) -> bool:
        return not self.blockers


def check_request(transport_request) -> RequestReadiness:
    """Считает полноту пакетов документов и блокеры отправки складу.

    Блокеры — то, без чего письмо складу бессмысленно: нет машин, не выбрана
    страна или процедура, не заполнены данные автовоза. Недостающие
    документы блокером НЕ считаются: сотрудник может отправить заявку складу
    и параллельно добирать документы у клиента.
    """
    cars = list(transport_request.cars.all())
    types_by_car = transport_request.declaration_types_by_car()
    country = transport_request.destination_country

    docs_by_car: dict[int, set[str]] = {car.pk: set() for car in cars}
    for car_id, doc_type in transport_request.documents.values_list("car_id", "doc_type"):
        if car_id in docs_by_car:
            docs_by_car[car_id].add(doc_type)

    # Наборы кэшируем по процедуре: правило лежит в БД, а машин может быть
    # много, и все они обычно идут одной процедурой.
    required_by_procedure: dict[str, tuple[str, ...]] = {}

    statuses: list[CarDocStatus] = []
    for car in cars:
        declaration_type = types_by_car.get(car.pk) or ""
        present = docs_by_car[car.pk]
        if declaration_type not in required_by_procedure:
            required_by_procedure[declaration_type] = required_doc_types(declaration_type, country)
        required = required_by_procedure[declaration_type]
        statuses.append(
            CarDocStatus(
                car=car,
                declaration_type=declaration_type,
                present=[code for code in required if code in present],
                missing=[code for code in required if code not in present],
            )
        )

    blockers: list[str] = []
    if not cars:
        blockers.append("В заявке нет автомобилей.")
    if not transport_request.truck_number:
        blockers.append("Не указан номер тягача.")
    if not transport_request.driver_name:
        blockers.append("Не указан водитель.")
    if not country:
        blockers.append("Не выбрана страна назначения.")
    without_type = [s.car for s in statuses if not s.declaration_type]
    if without_type:
        vins = ", ".join(str(getattr(car, "vin", "") or car.pk) for car in without_type)
        blockers.append(f"Не выбран тип декларации: {vins}.")
    cars_without_warehouse = [car for car in cars if not car.warehouse_id]
    if cars_without_warehouse:
        vins = ", ".join(str(getattr(car, "vin", "") or car.pk) for car in cars_without_warehouse)
        blockers.append(f"У автомобилей не указан склад: {vins}.")

    return RequestReadiness(cars=statuses, blockers=blockers)
