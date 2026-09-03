"""Создание рейса ``AutoTransport`` из заявки клиента на автовоз.

Заявка хранит перевозчика ТЕКСТОМ (клиент вписывает название и EORI), а рейс
требует FK на ``Carrier``. Поэтому перед созданием рейса сотруднику
показывается результат сопоставления (``match_carrier``): найденный
перевозчик или предупреждение, что будет создан новый. Молча дубликаты не
плодим — создание нового перевозчика выполняется только по явному
подтверждению из формы.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class AutoTransportBuildError(Exception):
    """Из заявки нельзя собрать рейс (нет машин, не определён перевозчик)."""


@dataclass
class CarrierMatch:
    """Результат сопоставления текстового перевозчика заявки со справочником."""

    carrier: object | None
    matched_by: str = ""
    will_create: bool = False
    candidates: list = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


def match_carrier(transport_request) -> CarrierMatch:
    """Ищет перевозчика заявки в справочнике: сначала по EORI, потом по названию.

    EORI надёжнее названия («MAXER TRANSPORT Sp. z.o.o.» пишут по-разному),
    поэтому проверяется первым. Если совпадений нет — возвращается
    ``will_create=True``: сотрудник увидит, что будет создан новый перевозчик.
    """
    from core.models.carriers import Carrier

    eori = (transport_request.carrier_eori or "").strip()
    name = (transport_request.carrier_name or "").strip()

    if eori:
        carrier = Carrier.objects.filter(eori_code__iexact=eori).first()
        if carrier:
            return CarrierMatch(carrier=carrier, matched_by="EORI")

    if name:
        carrier = Carrier.objects.filter(name__iexact=name).first()
        if carrier:
            return CarrierMatch(carrier=carrier, matched_by="NAME")
        candidates = list(Carrier.objects.filter(name__icontains=name[:12]).order_by("name")[:5])
        return CarrierMatch(carrier=None, will_create=True, candidates=candidates)

    return CarrierMatch(carrier=None, will_create=False)


def create_autotransport(transport_request, *, user=None, carrier=None, create_carrier=False):
    """Создаёт рейс по заявке и связывает его через ``TransportRequest.auto_transport``.

    Переносит перевозчика, EORI, номера тягача/прицепа, водителя с телефоном,
    границу, дату загрузки и все машины заявки. Номера и ФИО пишутся в
    ``*_manual``-поля рейса: справочники ``CarrierTruck``/``CarrierDriver``
    заполняются вручную и заводить их из клиентской заявки не стоит.

    ``carrier`` — выбранный сотрудником перевозчик; если не передан и
    ``create_carrier=True``, создаётся новый по названию и EORI из заявки.
    Повторный вызов возвращает уже созданный рейс.
    """
    from core.models.auto_transport import AutoTransport
    from core.models.carriers import Carrier

    if transport_request.auto_transport_id:
        return transport_request.auto_transport

    cars = list(transport_request.cars.all())
    if not cars:
        raise AutoTransportBuildError("В заявке нет автомобилей — рейс создавать не из чего.")

    if carrier is None:
        match = match_carrier(transport_request)
        carrier = match.carrier
    if carrier is None:
        if not create_carrier:
            raise AutoTransportBuildError(
                "Перевозчик заявки не найден в справочнике. Выберите существующего или подтвердите создание нового."
            )
        name = (transport_request.carrier_name or "").strip()
        if not name:
            raise AutoTransportBuildError("В заявке не указан перевозчик.")
        carrier = Carrier.objects.create(
            name=name[:100],
            eori_code=(transport_request.carrier_eori or "").strip()[:50] or None,
        )
        logger.info(
            "[autotransport] создан перевозчик «%s» по заявке %s",
            carrier.name,
            transport_request.number,
        )

    auto_transport = AutoTransport.objects.create(
        carrier=carrier,
        eori_code=(transport_request.carrier_eori or "").strip()[:50],
        truck_number_manual=(transport_request.truck_number or "")[:20],
        trailer_number_manual=(transport_request.trailer_number or "")[:20],
        driver_name_manual=(transport_request.driver_name or "")[:100],
        driver_phone=(transport_request.driver_phone or "")[:20],
        border_crossing=(transport_request.border_crossing or "")[:100],
        loading_date=transport_request.planned_loading_date,
        notes=f"Создан по заявке клиента {transport_request.number}",
        created_by=(getattr(user, "username", "") or "")[:100],
    )
    auto_transport.cars.set(cars)

    transport_request.auto_transport = auto_transport
    transport_request.save(update_fields=["auto_transport", "updated_at"])
    logger.info(
        "[autotransport] рейс %s создан по заявке %s (%d авто)",
        auto_transport.number,
        transport_request.number,
        len(cars),
    )
    return auto_transport


class RevertToDraftError(Exception):
    """Заявку нельзя вернуть в черновик: рейс уже оформлен."""


# Рейс «оформлен», когда вышел из черновика. CANCELLED не блокирует возврат —
# отменённый рейс заявку не держит.
_FORMED_TRIP_STATUSES = frozenset({"FORMED", "LOADED", "IN_TRANSIT", "DELIVERED"})


def can_revert_to_draft(transport_request) -> bool:
    """Можно ли кнопкой «корзина» вернуть заявку в черновик."""
    if transport_request.status in ("COMPLETED", "CANCELLED"):
        return False
    auto = transport_request.auto_transport
    if auto is not None and auto.status in _FORMED_TRIP_STATUSES:
        return False
    return True


def revert_to_draft(transport_request) -> bool:
    """Мягкое «удаление» с доски: заявка снова черновик, запись не стираем.

    Если связанный рейс ещё черновик — отвязываем его и снимаем машины заявки.
    Оформленный рейс (сформирован и дальше) или заявка «Оформлена» — ошибка.

    Возвращает ``False``, если заявка уже была черновиком без рейса.
    """
    from core.models.website import TransportRequest

    if not can_revert_to_draft(transport_request):
        raise RevertToDraftError(
            "Нельзя вернуть в черновик: автовоз уже оформлен."
        )

    already_draft = transport_request.status == "DRAFT" and not transport_request.auto_transport_id
    if already_draft and transport_request.warehouse_state == TransportRequest.WAREHOUSE_NOT_SENT:
        return False

    fields = ["status"]
    transport_request.status = "DRAFT"

    auto = transport_request.auto_transport
    if auto is not None and auto.status not in _FORMED_TRIP_STATUSES:
        if auto.status == "DRAFT":
            car_ids = list(transport_request.cars.values_list("pk", flat=True))
            if car_ids:
                auto.cars.remove(*car_ids)
        transport_request.auto_transport = None
        fields.append("auto_transport")

    if transport_request.warehouse_state != TransportRequest.WAREHOUSE_NOT_SENT:
        transport_request.warehouse_state = TransportRequest.WAREHOUSE_NOT_SENT
        fields.append("warehouse_state")

    if transport_request.awaiting_client_docs:
        transport_request.awaiting_client_docs = False
        fields.append("awaiting_client_docs")

    transport_request.save(update_fields=[*fields, "updated_at"])
    logger.info(
        "[autotransport] заявка %s возвращена в черновик",
        transport_request.number,
    )
    return True
