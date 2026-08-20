"""Письмо-заявка складу на литовском: впустить автовоз и оформить декларацию.

Декларации оформляет склад, а не мы. Сотрудник проверяет заявку клиента на
доске заявок и отправляет складу письмо на литовском: просьба впустить
автовоз на территорию для загрузки перечисленных авто и оформить декларацию
нужного типа. Вложения — склеенные PDF-пакеты документов по каждому авто
(те же, что клиент скачивает из кабинета).

Отправка идёт через существующий Gmail-путь
(``email_compose.compose_new_email_from_transport_request``), поэтому ответ
склада попадает в тот же тред и автоматически появляется в карточке заявки
(см. ``TransportRequestEmailLink`` и ``email_matcher``).

Если машины заявки лежат на разных складах, письмо формируется по одному
складу за раз — сотрудник выбирает получателя, а состав авто ограничивается
машинами этого склада.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)

# Тип декларации → литовское название в винительном падеже (для «prašome
# įforminti …»). Терминология согласована с двуязычным договором автовоза
# (``transport_docs_pdf``).
DECLARATION_LT = {
    "TRANSIT": "tranzito deklaraciją (T1)",
    "EXPORT": "eksporto deklaraciją",
    "IMPORT": "importo deklaraciją",
    "REEXPORT": "reeksporto deklaraciją",
}

# Тот же тип в именительном падеже — для перечня «Reikalingos deklaracijos».
DECLARATION_LT_NOMINATIVE = {
    "TRANSIT": "tranzito deklaracija (T1)",
    "EXPORT": "eksporto deklaracija",
    "IMPORT": "importo deklaracija",
    "REEXPORT": "reeksporto deklaracija",
}

# Страна назначения по-литовски: складу важно, куда пойдёт автовоз.
COUNTRY_LT = {
    "BY": "Baltarusija",
    "MD": "Moldova",
    "UA": "Ukraina",
}


class WarehouseLetterError(Exception):
    """Невозможно собрать письмо складу (нет получателей, нет авто и т.п.)."""


@dataclass
class LetterCar:
    """Строка таблицы авто в письме складу."""

    car: object
    declaration_lt: str
    declaration_index: int = 0
    site_address: str = ""
    container_number: str = ""
    package_available: bool = False
    package_filename: str = ""


@dataclass
class LetterDeclaration:
    """Одна декларация в перечне письма: тип и авто, которые в неё входят."""

    index: int
    type_lt: str
    cars: list = field(default_factory=list)

    @property
    def vins(self) -> str:
        return ", ".join(str(getattr(car, "vin", "") or car.pk) for car in self.cars)


@dataclass
class WarehouseLetterDraft:
    """Черновик письма складу: получатели, тема, текст, доступные вложения."""

    transport_request: object
    warehouse: object
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    cars: list[LetterCar] = field(default_factory=list)
    declarations: list[LetterDeclaration] = field(default_factory=list)

    @property
    def car_ids(self) -> list[int]:
        return [row.car.pk for row in self.cars]


def resolve_warehouse_recipients(warehouse) -> list[str]:
    """Email-адреса склада: общая почта + email контактов, без дублей.

    Общая почта склада (``Warehouse.general_email``) сигналом
    ``core.signals.partners`` синкается в ``Contact``/``ContactEmail``,
    поэтому обычно достаточно контактов; общая почта остаётся фолбэком для
    складов, заведённых до синка. Порядок: сначала «основные» адреса.
    """
    if warehouse is None:
        return []

    from core.models.contact import ContactEmail
    from core.models.warehouses import Warehouse

    result: list[str] = []
    seen: set[str] = set()

    def _add(addr: str | None) -> None:
        addr = (addr or "").strip()
        if not addr or addr.lower() in seen:
            return
        seen.add(addr.lower())
        result.append(addr)

    content_type = ContentType.objects.get_for_model(Warehouse)
    rows = (
        ContactEmail.objects.filter(
            contact__content_type=content_type,
            contact__object_id=warehouse.pk,
            contact__is_orphan=False,
        )
        .order_by("-contact__is_primary", "-is_primary", "position", "email")
        .values_list("email", flat=True)
    )
    for email in rows:
        _add(email)

    _add(getattr(warehouse, "general_email", ""))
    return result


def warehouses_of_request(transport_request) -> list[object]:
    """Склады машин заявки (уникальные, в порядке названия)."""
    from core.models.warehouses import Warehouse

    ids = {car.warehouse_id for car in transport_request.cars.all() if car.warehouse_id}
    if not ids:
        return []
    return list(Warehouse.objects.filter(pk__in=ids).order_by("name"))


def build_letter_draft(transport_request, warehouse=None) -> WarehouseLetterDraft:
    """Собирает черновик письма складу: тема, литовский текст, вложения.

    ``warehouse`` не задан — берётся единственный склад машин заявки. Если
    складов несколько, вызывающий обязан указать конкретный.
    """
    from core.services import transport_docs

    if warehouse is None:
        warehouse = transport_request.warehouse or transport_request.default_warehouse()
    if warehouse is None:
        raise WarehouseLetterError(
            "Автомобили заявки относятся к разным складам (или склад не указан) — выберите получателя."
        )

    from core.services.transport_declarations import declaration_plan

    types_by_car = transport_request.declaration_types_by_car()
    cars = [car for car in transport_request.cars.all().order_by("id") if car.warehouse_id == warehouse.pk]
    if not cars:
        raise WarehouseLetterError(f"В заявке нет автомобилей на складе «{warehouse.name}».")

    # Разбивка заявки на отдельные декларации: складу важно, сколько
    # деклараций оформлять и какие машины идут в каждую из них.
    plan = declaration_plan(transport_request, cars=cars, include_empty=False)
    declarations = [
        LetterDeclaration(
            index=index,
            type_lt=DECLARATION_LT_NOMINATIVE.get(line.declaration_type, ""),
            cars=line.cars,
        )
        for index, line in enumerate(plan, start=1)
    ]
    declaration_index_by_car = {car.pk: item.index for item in declarations for car in item.cars}

    rows: list[LetterCar] = []
    for car in cars:
        declaration_type = types_by_car.get(car.pk) or ""
        _site_name, site_address = car.get_unload_address()
        rows.append(
            LetterCar(
                car=car,
                declaration_lt=DECLARATION_LT.get(declaration_type, ""),
                declaration_index=declaration_index_by_car.get(car.pk, 0),
                site_address=site_address or "",
                container_number=car.container.number if car.container_id else "",
                package_available=transport_request.documents.filter(car=car).exists(),
                package_filename=transport_docs.package_pdf_filename(car),
            )
        )

    context = {
        "request": transport_request,
        "warehouse": warehouse,
        "cars": rows,
        "declarations": declarations,
        "country_lt": COUNTRY_LT.get(transport_request.destination_country, ""),
        "declaration_labels": sorted({row.declaration_lt for row in rows if row.declaration_lt}),
    }
    body_text = render_to_string("email/warehouse_transport_request_lt.txt", context).strip()
    subject = f"Autovežio pakrovimas ir deklaracija — {transport_request.number}"

    return WarehouseLetterDraft(
        transport_request=transport_request,
        warehouse=warehouse,
        recipients=resolve_warehouse_recipients(warehouse),
        subject=subject,
        body_text=body_text,
        cars=rows,
        declarations=declarations,
    )


def build_attachments(transport_request, car_ids) -> list[tuple[str, bytes, str]]:
    """PDF-пакеты выбранных авто как кортежи для ``email_compose``.

    Пакет собирается тем же ``build_car_package_pdf``, что и ZIP в кабинете
    клиента: порядок документов из ``TRANSPORT_DOCUMENT_TYPES`` без подписи,
    в конце — скан тайтла.
    """
    from core.services import transport_docs

    if not car_ids:
        return []

    attachments: list[tuple[str, bytes, str]] = []
    for car in transport_request.cars.filter(pk__in=car_ids).order_by("id"):
        try:
            pdf_bytes = transport_docs.build_car_package_pdf(transport_request, car)
        except Exception as exc:
            logger.warning("[warehouse_letter] пакет для %s не собран: %s", car.vin, exc)
            continue
        if not pdf_bytes:
            continue
        attachments.append(
            (
                transport_docs.package_pdf_filename(car),
                pdf_bytes,
                "application/pdf",
            )
        )
    return attachments


def send_letter(
    *,
    transport_request,
    warehouse,
    user,
    to,
    cc="",
    bcc="",
    subject="",
    body_text="",
    car_ids=None,
):
    """Отправляет письмо складу и переводит заявку в состояние «отправлена».

    Возвращает созданный ``ContainerEmail``. Ошибки отправки (``SendError``,
    ``ComposeError``) пробрасываются наружу — вьюха показывает их сотруднику;
    отметки на заявке в этом случае не ставятся.
    """
    from core.services.email_compose import compose_new_email_from_transport_request

    attachments = build_attachments(transport_request, car_ids or [])

    email = compose_new_email_from_transport_request(
        transport_request=transport_request,
        user=user,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_text=body_text,
        attachments=attachments,
    )

    transport_request.warehouse = warehouse
    transport_request.warehouse_state = transport_request.WAREHOUSE_SENT
    transport_request.sent_to_warehouse_at = timezone.now()
    if transport_request.status in ("DRAFT", "SUBMITTED", "ACCEPTED"):
        transport_request.status = "IN_PROGRESS"
    transport_request.save(
        update_fields=[
            "warehouse",
            "warehouse_state",
            "sent_to_warehouse_at",
            "status",
            "updated_at",
        ]
    )
    logger.info(
        "[warehouse_letter] заявка %s отправлена складу %s (email pk=%s, вложений %d)",
        transport_request.number,
        warehouse.name,
        email.pk,
        len(attachments),
    )
    return email
