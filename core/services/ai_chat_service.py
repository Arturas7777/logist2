import logging
import re
from typing import List, Optional

import requests
from django.conf import settings

from core.models import Car, Container
from core.models_website import AIChat, CarPhoto, ContainerPhoto

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    pass


def _get_recent_messages(session_id: Optional[str], user) -> List[dict]:
    if not session_id:
        return []

    chats = AIChat.objects.filter(session_id=session_id)
    if user:
        chats = chats.filter(user=user)

    chats = chats.order_by("-created_at")[:6]
    messages = []
    for chat in reversed(list(chats)):
        messages.append({"role": "user", "content": chat.message})
        messages.append({"role": "assistant", "content": chat.response})
    return messages


def _build_company_context() -> str:
    return (
        "Вы — ИИ-помощник логистической компании Caromoto Lithuania. "
        "Компания специализируется на доставке автомобилей из США, "
        "контейнерных перевозках, таможенном оформлении и складском хранении. "
        "Отвечайте вежливо, профессионально и по существу. "
        "Не отвечайте на финансовые вопросы, цены, платежи, балансы, инвойсы или стоимость услуг. "
        "Если спрашивают про финансы — вежливо направьте к менеджеру. "
        "Если вопрос требует персональных данных, отвечайте только на основе контекста. "
        "Если данных недостаточно — уточните у клиента VIN или номер контейнера."
    )


def _build_client_context(client) -> str:
    if not client:
        return ""

    cars = (
        Car.objects.filter(client=client)
        .select_related("container", "warehouse")
        .order_by("-id")[:5]
    )
    containers = (
        Container.objects.filter(client=client)
        .select_related("line", "warehouse")
        .order_by("-id")[:5]
    )

    cars_info = []
    for car in cars:
        cars_info.append(
            f"VIN: {car.vin}, статус: {car.get_status_display()}, "
            f"контейнер: {car.container.number if car.container else '—'}"
        )

    containers_info = []
    for container in containers:
        containers_info.append(
            f"Контейнер: {container.number}, статус: {container.get_status_display()}, "
            f"ETA: {container.eta or '—'}, выгрузка: {container.unload_date or '—'}"
        )

    return (
        f"Информация о клиенте: {client.name}. "
        f"Последние автомобили: {', '.join(cars_info) if cars_info else 'нет данных'}. "
        f"Последние контейнеры: {', '.join(containers_info) if containers_info else 'нет данных'}."
    )


def _build_system_prompt(language_code: str) -> str:
    language_map = {
        "ru": "Русский",
        "en": "English",
        "lt": "Lietuvių",
    }
    language_name = language_map.get(language_code, "Русский")
    return (
        "Отвечай кратко и по делу. "
        "Не придумывай данные, которых нет в контексте. "
        f"Язык ответа: {language_name}."
    )


def _find_identifiers(message: str) -> dict:
    vin_pattern = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
    container_pattern = re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE)

    vins = list({m.group(0).upper() for m in vin_pattern.finditer(message or "")})
    containers = list({m.group(0).upper() for m in container_pattern.finditer(message or "")})
    return {"vins": vins, "containers": containers}


def _build_tracking_context(message: str, user=None, client=None) -> str:
    identifiers = _find_identifiers(message)
    vins = identifiers["vins"]
    containers = identifiers["containers"]
    if not vins and not containers:
        return ""

    is_staff = bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))

    parts = []

    for vin in vins:
        car_qs = Car.objects.select_related("container", "warehouse").filter(vin__iexact=vin)
        if client:
            car_qs = car_qs.filter(client=client)
        car = car_qs.first()

        if not car:
            parts.append(f"По VIN {vin} автомобиль не найден.")
            continue

        unload_date = car.unload_date or (car.container.unload_date if car.container else None)
        transfer_date = car.transfer_date
        events = []
        if unload_date:
            events.append(f"Разгружен: {unload_date}")
        if transfer_date:
            events.append(f"Передан: {transfer_date}")
        history_text = "; ".join(events) if events else "История статусов: нет дат."

        photos_qs = CarPhoto.objects.filter(car=car)
        if not is_staff:
            photos_qs = photos_qs.filter(is_public=True)
        photos_count = photos_qs.count()
        last_photo = photos_qs.order_by("-uploaded_at").first()
        if last_photo:
            photos_text = (
                f"Фото авто: {photos_count} шт., последняя загрузка: "
                f"{last_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            container_photos_text = ""
            if car.container:
                container_photos = ContainerPhoto.objects.filter(container=car.container)
                if not is_staff:
                    container_photos = container_photos.filter(is_public=True)
                container_count = container_photos.count()
                last_container_photo = container_photos.order_by("-uploaded_at").first()
                if container_count:
                    if last_container_photo:
                        container_photos_text = (
                            f"Фото контейнера: {container_count} шт., последняя загрузка: "
                            f"{last_container_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}"
                        )
                    else:
                        container_photos_text = f"Фото контейнера: {container_count} шт."
            photos_text = "Фото авто: 0 шт." + (f" {container_photos_text}" if container_photos_text else "")

        parts.append(
            "Статус автомобиля по VIN {vin}: {status}. Контейнер: {container}. "
            "ETA: {eta}. Дата выгрузки: {unload}. Склад: {warehouse}. {history} {photos}".format(
                vin=vin,
                status=car.get_status_display(),
                container=car.container.number if car.container else "—",
                eta=car.container.eta if car.container and car.container.eta else "—",
                unload=unload_date or "—",
                warehouse=car.warehouse.name if car.warehouse else "—",
                history=history_text,
                photos=photos_text,
            )
        )

    for number in containers:
        container_qs = Container.objects.select_related("warehouse").filter(number__iexact=number)
        if client:
            container_qs = container_qs.filter(client=client)
        container = container_qs.first()

        if not container:
            parts.append(f"Контейнер {number} не найден.")
            continue

        events = []
        if container.planned_unload_date:
            events.append(f"План разгрузки: {container.planned_unload_date}")
        if container.unload_date:
            events.append(f"Разгружен: {container.unload_date}")
        if container.unloaded_status_at:
            events.append(f"Статус 'Разгружен' с: {container.unloaded_status_at}")
        transfer_date = None
        if container.status == "TRANSFERRED":
            transfer_date = (
                Car.objects.filter(container=container, transfer_date__isnull=False)
                .order_by("-transfer_date")
                .values_list("transfer_date", flat=True)
                .first()
            )
            if transfer_date:
                events.append(f"Передан: {transfer_date}")
            else:
                events.append("Передан: дата не указана")
        history_text = "; ".join(events) if events else "История статусов: нет дат."

        photos_qs = ContainerPhoto.objects.filter(container=container)
        if not is_staff:
            photos_qs = photos_qs.filter(is_public=True)
        photos_count = photos_qs.count()
        last_photo = photos_qs.order_by("-uploaded_at").first()
        photos_text = (
            f"Фото: {photos_count} шт., последняя загрузка: {last_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}"
            if last_photo else f"Фото: {photos_count} шт."
        )

        parts.append(
            "Статус контейнера {number}: {status}. ETA: {eta}. Дата выгрузки: {unload}. "
            "Склад: {warehouse}. {history} {photos}".format(
                number=number,
                status=container.get_status_display(),
                eta=container.eta or "—",
                unload=container.unload_date or "—",
                warehouse=container.warehouse.name if container.warehouse else "—",
                history=history_text,
                photos=photos_text,
            )
        )

    return " ".join(parts)


def _call_ai_api(messages: List[dict]) -> str:
    if not settings.AI_CHAT_ENABLED:
        raise AIServiceError("AI chat is disabled")

    api_key = settings.AI_API_KEY
    if not api_key:
        raise AIServiceError("AI API key is missing")

    base_url = settings.AI_API_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": settings.AI_MODEL,
        "messages": messages,
        "temperature": settings.AI_TEMPERATURE,
        "max_tokens": settings.AI_MAX_TOKENS,
    }

    try:
        session = requests.Session()
        session.trust_env = False
        response = session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.AI_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("AI API request failed")
        raise AIServiceError(f"AI API request failed: {exc.__class__.__name__}: {exc}") from exc

    if not response.ok:
        error_text = response.text[:500] if response.text else ""
        logger.error("AI API error: %s - %s", response.status_code, error_text)
        raise AIServiceError(f"AI API returned error ({response.status_code}): {error_text}")

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("AI API response parsing error: %s", data)
        raise AIServiceError("AI API response parsing error") from exc


def generate_ai_response(message: str, user=None, client=None, session_id: Optional[str] = None,
                         language_code: str = "ru") -> str:
    system_prompt = _build_system_prompt(language_code)
    company_context = _build_company_context()
    client_context = _build_client_context(client)
    tracking_context = _build_tracking_context(message, user=user, client=client)
    history_messages = _get_recent_messages(session_id, user)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": company_context},
    ]
    if client_context:
        messages.append({"role": "system", "content": client_context})
    if tracking_context:
        messages.append({"role": "system", "content": tracking_context})

    messages.extend(history_messages)
    messages.append({"role": "user", "content": message})

    return _call_ai_api(messages)
