import logging
import re
from decimal import Decimal
from typing import Dict, Optional

from django.conf import settings

from core.models import Car, Carrier, Company, Container, Line, Warehouse, WarehouseService, CarService
from core.models_billing import NewInvoice
from core.models_website import ContainerPhoto, CarPhoto
from core.services.ai_chat_service import _call_ai_api, AIServiceError
from core.services.ai_rag import build_rag_snippets

logger = logging.getLogger(__name__)


def _extract_identifiers(message: str) -> Dict[str, list]:
    vin_pattern = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
    container_pattern = re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE)
    invoice_pattern = re.compile(r"\bINV-\d{6}-\d{4}\b", re.IGNORECASE)

    vins = list({m.group(0).upper() for m in vin_pattern.finditer(message or "")})
    containers = list({m.group(0).upper() for m in container_pattern.finditer(message or "")})
    invoices = list({m.group(0).upper() for m in invoice_pattern.finditer(message or "")})
    return {"vins": vins, "containers": containers, "invoices": invoices}


def _summarize_container(container: Container) -> str:
    car_count = container.container_cars.count()
    return (
        f"Контейнер {container.number}: статус {container.get_status_display()}, "
        f"линия {container.line.name if container.line else '—'}, "
        f"склад {container.warehouse.name if container.warehouse else '—'}, "
        f"THS {container.ths or '—'} ({container.ths_payer}), "
        f"ETA {container.eta or '—'}, выгрузка {container.unload_date or '—'}, "
        f"ТС в контейнере: {car_count}."
    )


def _summarize_car(car: Car) -> str:
    return (
        f"Авто VIN {car.vin}: статус {car.get_status_display()}, "
        f"контейнер {car.container.number if car.container else '—'}, "
        f"склад {car.warehouse.name if car.warehouse else '—'}, "
        f"линия {car.line.name if car.line else '—'}, "
        f"перевозчик {car.carrier.name if car.carrier else '—'}, "
        f"платные дни {car.days or 0}, хранение {car.storage_cost or 0}, "
        f"итоговая цена {car.total_price or 0}."
    )


def _summarize_invoice(invoice: NewInvoice) -> str:
    issuer = (
        invoice.issuer_company
        or invoice.issuer_warehouse
        or invoice.issuer_line
        or invoice.issuer_carrier
    )
    recipient = (
        invoice.recipient_client
        or invoice.recipient_company
        or invoice.recipient_warehouse
        or invoice.recipient_line
        or invoice.recipient_carrier
    )
    return (
        f"Инвойс {invoice.number}: статус {invoice.get_status_display()}, "
        f"выставитель {issuer or '—'}, получатель {recipient or '—'}, "
        f"итого {invoice.total or 0}, оплачено {invoice.paid_amount or 0}."
    )


def _summarize_photos_for_car(car: Car) -> str:
    car_photos = CarPhoto.objects.filter(car=car)
    container_photos = ContainerPhoto.objects.filter(container=car.container) if car.container else None
    container_count = container_photos.count() if container_photos is not None else 0
    return (
        f"Фото авто: {car_photos.count()} шт., "
        f"фото контейнера: {container_count} шт."
    )

def _format_money(value: Optional[Decimal]) -> str:
    try:
        return f"{Decimal(value or 0):.2f}"
    except Exception:
        return "0.00"


def _build_price_context(car: Car) -> str:
    services = CarService.objects.filter(car=car)
    services_total = Decimal("0")
    markup_total = Decimal("0")
    for service in services:
        services_total += service.final_price or Decimal("0")
        markup_total += (service.markup_amount or Decimal("0")) * (service.quantity or 1)

    total_price = car.total_price or (services_total + markup_total)
    storage_cost = car.storage_cost or Decimal("0")

    return (
        f"Цена авто (итого): {_format_money(total_price)} EUR. "
        f"Услуги: {_format_money(services_total)} EUR. "
        f"Скрытая наценка: {_format_money(markup_total)} EUR. "
        f"Хранение: {_format_money(storage_cost)} EUR."
    )


def _summarize_current_object(page_context: Dict) -> str:
    if not page_context:
        return ""
    model_name = (page_context.get("model_name") or "").lower()
    object_id = page_context.get("object_id")
    if not model_name or not object_id or not str(object_id).isdigit():
        return ""

    model_map = {
        "container": Container,
        "car": Car,
        "newinvoice": NewInvoice,
        "line": Line,
        "warehouse": Warehouse,
        "carrier": Carrier,
        "company": Company,
    }
    model = model_map.get(model_name)
    if not model:
        return ""

    obj = model.objects.filter(pk=object_id).first()
    if not obj:
        return ""

    if model is Container:
        return _summarize_container(obj)
    if model is Car:
        return _summarize_car(obj)
    if model is NewInvoice:
        return _summarize_invoice(obj)
    if model in (Line, Warehouse, Carrier, Company):
        return f"{model.__name__}: {getattr(obj, 'name', str(obj))}."
    return ""


def _build_db_context(message: str, page_context: Dict) -> str:
    parts = []
    current_summary = _summarize_current_object(page_context)
    if current_summary:
        parts.append(f"Открытая страница: {current_summary}")

    identifiers = _extract_identifiers(message)

    for vin in identifiers["vins"]:
        car = Car.objects.select_related("container", "warehouse", "line", "carrier").filter(vin__iexact=vin).first()
        if car:
            parts.append(_summarize_car(car))
            parts.append(_summarize_photos_for_car(car))
            if _wants_price(message):
                parts.append(_build_price_context(car))
        else:
            parts.append(f"VIN {vin}: авто не найдено.")

    for number in identifiers["containers"]:
        container = Container.objects.select_related("line", "warehouse").filter(number__iexact=number).first()
        if container:
            parts.append(_summarize_container(container))
        else:
            parts.append(f"Контейнер {number} не найден.")

    for inv_number in identifiers["invoices"]:
        invoice = NewInvoice.objects.filter(number__iexact=inv_number).first()
        if invoice:
            parts.append(_summarize_invoice(invoice))
        else:
            parts.append(f"Инвойс {inv_number} не найден.")

    return " ".join(parts)


def _build_ui_guidance(message: str, page_context: Dict) -> str:
    message_lower = (message or "").lower()
    model_name = (page_context.get("model_name") or "").lower() if page_context else ""

    if "пересчитать ths" in message_lower or "пересчет ths" in message_lower:
        if model_name == "line":
            return (
                "Кнопка пересчета THS находится в карточке линии: "
                "откройте линию, сохраните коэффициенты, нажмите \"Пересчитать THS\"."
            )
        return (
            "THS пересчитывается при сохранении контейнера, если изменились line/ths/ths_payer/warehouse. "
            "Для массового пересчета по линии используйте кнопку в карточке линии."
        )

    if "хранение" in message_lower and "цена" in message_lower:
        return (
            "Цена хранения рассчитывается динамически: платные дни × ставка из услуги \"Хранение\". "
            "Проверьте дни, дату разгрузки и ставку услуги склада."
        )

    if "инвойс" in message_lower and "не обнов" in message_lower:
        return (
            "Инвойсы пересчитываются сигналами при изменении услуг/дней/статуса ТС. "
            "Проверьте, что изменения сохранены и нет ошибок сигналов."
        )

    return ""

def _wants_price(message: str) -> bool:
    message_lower = (message or "").lower()
    keywords = ["цена", "стоимость", "сколько стоит", "итоговая цена", "итого", "total price"]
    return any(keyword in message_lower for keyword in keywords)


def _wants_diagnostics(message: str) -> bool:
    message_lower = (message or "").lower()
    triggers = [
        "диагност", "debug", "ошибка", "почему", "не работает", "не обнов",
        "не считается", "не создается", "не создает", "проверить",
    ]
    return any(trigger in message_lower for trigger in triggers)


def _diagnose_car(car: Car) -> list:
    issues = []
    if car.status == "UNLOADED" and not car.unload_date:
        issues.append("Статус 'Разгружен' без даты разгрузки.")
    if car.status == "TRANSFERRED" and not car.transfer_date:
        issues.append("Статус 'Передан' без даты передачи.")
    if car.container and car.container.unload_date and not car.unload_date:
        issues.append("Дата разгрузки контейнера есть, а у авто нет (ожидалось наследование).")
    if car.container and car.container.line and car.line and car.container.line_id != car.line_id:
        issues.append("Линия авто отличается от линии контейнера.")
    if car.container and car.container.warehouse and car.warehouse and car.container.warehouse_id != car.warehouse_id:
        issues.append("Склад авто отличается от склада контейнера.")
    if car.days and car.days > 0:
        if not car.warehouse:
            issues.append("Есть платные дни, но склад не указан.")
        else:
            storage_service = WarehouseService.objects.filter(
                warehouse=car.warehouse, name__iexact="Хранение"
            ).first()
            if not storage_service:
                issues.append("На складе нет услуги 'Хранение' (нужна ставка по дням).")
            else:
                has_storage_service = CarService.objects.filter(
                    car=car, service_type="WAREHOUSE", service_id=storage_service.id
                ).exists()
                if not has_storage_service:
                    issues.append("У авто нет услуги 'Хранение' (CarService).")
            if not car.storage_cost or car.storage_cost == 0:
                issues.append("Стоимость хранения = 0 при наличии платных дней.")
    return issues


def _diagnose_container(container: Container) -> list:
    issues = []
    if container.status == "UNLOADED":
        if not container.unload_date:
            issues.append("Статус 'Разгружен' без даты разгрузки.")
        if not container.warehouse:
            issues.append("Статус 'Разгружен' без склада.")
    if container.ths and not container.line:
        issues.append("THS указано, но линия не выбрана.")
    if container.ths_payer == "WAREHOUSE" and not container.warehouse:
        issues.append("THS через склад, но склад не указан.")
    if container.container_cars.count() == 0:
        issues.append("Контейнер без ТС.")
    return issues


def _diagnose_invoice(invoice: NewInvoice) -> list:
    issues = []
    if invoice.cars.count() == 0:
        issues.append("Инвойс без выбранных ТС.")
    if invoice.total is None:
        issues.append("Итоговая сумма не рассчитана.")
    if invoice.status in {"ISSUED", "PARTIALLY_PAID", "PAID"} and invoice.total in (0, None):
        issues.append("Инвойс выставлен, но сумма 0/пустая.")
    return issues


def _run_diagnostics(page_context: Dict) -> str:
    if not page_context:
        return ""
    model_name = (page_context.get("model_name") or "").lower()
    object_id = page_context.get("object_id")
    if not object_id or not str(object_id).isdigit():
        return "Откройте карточку объекта в админке, чтобы запустить диагностику."

    diagnostics = []

    if model_name == "car":
        car = Car.objects.select_related("container", "warehouse", "line", "carrier").filter(pk=object_id).first()
        if car:
            diagnostics.extend(_diagnose_car(car))
    elif model_name == "container":
        container = Container.objects.select_related("line", "warehouse").filter(pk=object_id).first()
        if container:
            diagnostics.extend(_diagnose_container(container))
    elif model_name == "newinvoice":
        invoice = NewInvoice.objects.filter(pk=object_id).first()
        if invoice:
            diagnostics.extend(_diagnose_invoice(invoice))
    else:
        return "Диагностика поддерживается для контейнеров, ТС и инвойсов."

    if not diagnostics:
        return "Критичных проблем не найдено."
    return "Потенциальные проблемы: " + " ".join(diagnostics)


def generate_admin_ai_response(
    message: str,
    user,
    page_context: Optional[Dict] = None,
    session_id: Optional[str] = None,
    language_code: str = "ru",
) -> Dict[str, Optional[str]]:
    page_context = page_context or {}
    db_context = _build_db_context(message, page_context)
    rag_context = build_rag_snippets(message, top_k=getattr(settings, "AI_RAG_TOP_K", 4))
    ui_guidance = _build_ui_guidance(message, page_context)
    diagnostics = _run_diagnostics(page_context) if _wants_diagnostics(message) else ""
    price_context = ""
    if _wants_price(message):
        model_name = (page_context.get("model_name") or "").lower()
        object_id = page_context.get("object_id")
        if model_name == "car" and object_id and str(object_id).isdigit():
            car = Car.objects.filter(pk=object_id).first()
            if car:
                price_context = _build_price_context(car)

    system_prompt = (
        "Ты AI-ассистент администратора проекта Logist2. "
        "Ты знаешь бизнес-логику, модели и правила админки. "
        "Отвечай кратко и по делу. Если данных недостаточно — уточни. "
        "Финансовые вопросы в админке разрешены. "
        "Если спрашивают про действие в админке — дай точные шаги."
    )

    language_map = {"ru": "Русский", "en": "English", "lt": "Lietuvių"}
    language_name = language_map.get(language_code, "Русский")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Язык ответа: {language_name}."},
    ]
    if page_context:
        messages.append({"role": "system", "content": f"Контекст страницы: {page_context}"})
    if db_context:
        messages.append({"role": "system", "content": f"Данные из БД: {db_context}"})
    if ui_guidance:
        messages.append({"role": "system", "content": f"Подсказка действий: {ui_guidance}"})
    if diagnostics:
        messages.append({"role": "system", "content": f"Диагностика: {diagnostics}"})
    if price_context:
        messages.append({"role": "system", "content": f"Цена по открытому авто: {price_context}"})
    if rag_context:
        messages.append({"role": "system", "content": f"Контекст проекта:\n{rag_context}"})
    messages.append({"role": "user", "content": message})

    try:
        response_text = _call_ai_api(messages)
        return {"response": response_text, "used_fallback": False, "fallback_reason": None}
    except AIServiceError as exc:
        logger.warning("Admin AI failed: %s", exc)
        fallback = ui_guidance or "Не хватает данных. Уточните VIN, номер контейнера или ссылку на страницу."
        return {"response": fallback, "used_fallback": True, "fallback_reason": str(exc)}
    except Exception as exc:
        logger.warning("Admin AI failed: %s", exc)
        fallback = ui_guidance or "Не хватает данных. Уточните VIN, номер контейнера или ссылку на страницу."
        return {"response": fallback, "used_fallback": True, "fallback_reason": "unknown_error"}
