"""Декларации внутри заявки на автовоз: план и ручная группировка авто.

Клиент выбирает один тип декларации на заявку — этого достаточно в 90%
случаев. Дальше сотрудник в карточке заявки при необходимости собирает
разбивку вручную: например одна транзитная T1 на две машины, вторая
отдельная T1 на третью, а ещё три машины — каждая по своей экспортной.
Каждая такая группа (``TransportDeclarationGroup``) = одна декларация;
авто, не попавшие ни в одну группу, идут одной декларацией типа заявки.

План деклараций используется в трёх местах: панель «Декларации» в карточке,
сводка на доске заявок и перечень деклараций в письме складу.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from core.models.website import TRANSPORT_DECLARATION_TYPES, TransportDeclarationGroup

DECLARATION_LABELS = dict(TRANSPORT_DECLARATION_TYPES)

# Ключ строки плана для авто без отдельной декларации.
DEFAULT_KEY = "default"


class DeclarationError(Exception):
    """Некорректная операция с декларациями заявки."""


@dataclass
class DeclarationLine:
    """Одна декларация плана: тип + авто, которые в неё входят."""

    key: str
    declaration_type: str
    cars: list = field(default_factory=list)
    note: str = ""
    group: object | None = None

    @property
    def is_default(self) -> bool:
        """Строка «по умолчанию» — авто без отдельной декларации."""
        return self.group is None

    @property
    def type_display(self) -> str:
        return DECLARATION_LABELS.get(self.declaration_type, "")

    @property
    def cars_count(self) -> int:
        return len(self.cars)


def declaration_plan(transport_request, cars=None, *, include_empty=True) -> list[DeclarationLine]:
    """Список деклараций заявки: отдельные группы + остаток одной строкой.

    ``cars`` ограничивает план подмножеством машин заявки (например только
    машинами одного склада — для письма). ``include_empty=False`` убирает
    группы, в которых после такого ограничения не осталось авто.
    """
    request_cars = list(cars) if cars is not None else list(transport_request.cars.all().order_by("id"))
    allowed = {car.pk: car for car in request_cars}

    lines: list[DeclarationLine] = []
    grouped: set[int] = set()
    for group in transport_request.declaration_groups.prefetch_related("cars"):
        group_cars = [allowed[car.pk] for car in group.cars.all() if car.pk in allowed]
        grouped.update(car.pk for car in group_cars)
        if not group_cars and not include_empty:
            continue
        lines.append(
            DeclarationLine(
                key=f"group-{group.pk}",
                declaration_type=group.declaration_type,
                cars=sorted(group_cars, key=lambda car: car.pk),
                note=group.note,
                group=group,
            )
        )

    rest = [car for car in request_cars if car.pk not in grouped]
    if rest:
        lines.append(
            DeclarationLine(
                key=DEFAULT_KEY,
                declaration_type=transport_request.declaration_type,
                cars=rest,
            )
        )
    return lines


def _validate_type(declaration_type: str) -> str:
    declaration_type = (declaration_type or "").strip()
    if declaration_type not in DECLARATION_LABELS:
        raise DeclarationError("Неизвестный тип декларации.")
    return declaration_type


def _cars_of_request(transport_request, car_ids):
    ids = {int(cid) for cid in car_ids if str(cid).isdigit()}
    if not ids:
        return []
    return list(transport_request.cars.filter(pk__in=ids))


@transaction.atomic
def create_group(transport_request, declaration_type: str, car_ids=(), note: str = "") -> TransportDeclarationGroup:
    """Создаёт отдельную декларацию и переносит в неё указанные авто."""
    declaration_type = _validate_type(declaration_type)
    last = transport_request.declaration_groups.order_by("-position").first()
    group = TransportDeclarationGroup.objects.create(
        request=transport_request,
        declaration_type=declaration_type,
        note=(note or "").strip()[:255],
        position=(last.position + 1) if last else 1,
    )
    cars = _cars_of_request(transport_request, car_ids)
    if cars:
        _detach_from_other_groups(transport_request, cars, keep=group)
        group.cars.set(cars)
    return group


@transaction.atomic
def update_group(group, *, declaration_type=None, note=None, car_ids=None) -> TransportDeclarationGroup:
    """Обновляет тип/примечание/состав авто отдельной декларации.

    Авто может входить только в одну декларацию заявки, поэтому при
    добавлении оно снимается с остальных.
    """
    fields = []
    if declaration_type is not None:
        group.declaration_type = _validate_type(declaration_type)
        fields.append("declaration_type")
    if note is not None:
        group.note = (note or "").strip()[:255]
        fields.append("note")
    if fields:
        group.save(update_fields=fields)

    if car_ids is not None:
        cars = _cars_of_request(group.request, car_ids)
        _detach_from_other_groups(group.request, cars, keep=group)
        group.cars.set(cars)
    return group


def delete_group(group) -> None:
    """Удаляет отдельную декларацию: её авто возвращаются к типу заявки."""
    group.delete()


def _detach_from_other_groups(transport_request, cars, keep=None) -> None:
    if not cars:
        return
    others = transport_request.declaration_groups.exclude(pk=keep.pk) if keep else transport_request.declaration_groups
    for other in others:
        other.cars.remove(*cars)


def sync_group_cars(transport_request) -> None:
    """Убирает из деклараций авто, которых больше нет в заявке."""
    car_ids = set(transport_request.cars.values_list("pk", flat=True))
    for group in transport_request.declaration_groups.prefetch_related("cars"):
        extra = [car for car in group.cars.all() if car.pk not in car_ids]
        if extra:
            group.cars.remove(*extra)
