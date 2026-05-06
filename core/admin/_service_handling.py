"""Shared helper for processing service-related POST fields in admin change_view.

Used by WarehouseAdmin, LineAdmin, CarrierAdmin (and similar) — they all share
the same pattern of POST keys: ``service_name_<id>``, ``service_price_<id>``,
``delete_service_<id>``, ``new_service_name_<idx>`` / ``new_service_price_<idx>``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def process_service_fields(request, parent_obj, service_model, fk_field: str) -> None:
    """Process service POST fields for a given parent admin object.

    Args:
        request: The Django HttpRequest with POST data.
        parent_obj: The parent model instance (Warehouse/Line/Carrier/...).
        service_model: The related service model class
            (WarehouseService/LineService/CarrierService/...).
        fk_field: Name of the FK on ``service_model`` pointing to ``parent_obj``
            ('warehouse', 'line', 'carrier', ...).
    """
    if request.method != 'POST' or parent_obj is None:
        return

    fk_filter: dict[str, Any] = {fk_field: parent_obj}

    for key, value in request.POST.items():
        if key.startswith('service_name_'):
            service_id = key.replace('service_name_', '')
            if not service_id.isdigit():
                continue
            try:
                service = service_model.objects.get(id=service_id, **fk_filter)
                service.name = value
                service.save()
            except service_model.DoesNotExist:
                logger.debug(
                    "Service %s id=%s not found for %s, skipping rename",
                    service_model.__name__, service_id, parent_obj,
                )
        elif key.startswith('service_price_'):
            service_id = key.replace('service_price_', '')
            if not service_id.isdigit():
                continue
            try:
                service = service_model.objects.get(id=service_id, **fk_filter)
                service.default_price = float(value) if value else 0
                service.save()
            except service_model.DoesNotExist:
                logger.debug(
                    "Service %s id=%s not found for %s, skipping price update",
                    service_model.__name__, service_id, parent_obj,
                )
            except ValueError:
                logger.warning(
                    "Invalid price '%s' for %s id=%s",
                    value, service_model.__name__, service_id,
                )
        elif key.startswith('delete_service_'):
            service_id = key.replace('delete_service_', '')
            if not service_id.isdigit():
                continue
            try:
                service = service_model.objects.get(id=service_id, **fk_filter)
                service.delete()
            except service_model.DoesNotExist:
                logger.debug(
                    "Service %s id=%s not found for %s, skipping delete",
                    service_model.__name__, service_id, parent_obj,
                )

    for key, value in request.POST.items():
        if not key.startswith('new_service_name_'):
            continue
        index = key.replace('new_service_name_', '')
        name = value
        if not name:
            continue
        price_raw = request.POST.get(f'new_service_price_{index}', 0)
        try:
            price = float(price_raw) if price_raw else 0
        except ValueError:
            logger.warning(
                "Invalid new service price '%s' (idx=%s) for %s",
                price_raw, index, parent_obj,
            )
            continue
        try:
            service_model.objects.create(
                name=name, default_price=price, **fk_filter,
            )
        except Exception:
            logger.exception(
                "Failed to create new %s for %s",
                service_model.__name__, parent_obj,
            )
