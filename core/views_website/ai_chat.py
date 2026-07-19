"""ИИ-помощник на сайте: чат, история, фидбек, локальные fallback-правила.

Эндпоинт ``/api/ai-chat/`` сначала пытается ответить через внешние сервисы
(:func:`generate_ai_response` для клиентов, :func:`generate_admin_ai_response`
для админов в контексте ``/admin/``), а при ошибке/таймауте откатывается
на локальные правила :func:`get_ai_response`.

Отдельно перехватываются типовые сценарии без обращения к LLM:

* запросы про **фото** автомобиля/контейнера — отвечаем готовым шаблоном
  со счётчиком фото и ссылкой на галерею;
* финансовые вопросы — отказ по политике (не консультируем по ценам).
"""

import logging
import re
import time

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import Car, Container
from core.models_website import AIChat, CarPhoto, ContainerPhoto
from core.serializers_website import AIChatSerializer
from core.services.admin_ai_agent import generate_admin_ai_response
from core.services.ai_chat_service import AIServiceError, generate_ai_response
from core.throttles import AIChatThrottle

logger = logging.getLogger(__name__)


def get_ai_response(message, user=None, client=None):
    """Локальный fallback: простые правила-ответы без обращения к LLM.

    Используется, когда :func:`generate_ai_response` упал (нет ключа,
    rate-limit, таймаут и т.п.). Стиль ответов согласован с маркетингом.
    """
    client_context = ""
    if client:
        cars_count = Car.objects.filter(client=client).count()
        active_cars = Car.objects.filter(
            client=client,
            status__in=["FLOATING", "IN_PORT", "UNLOADED"],
        ).count()

        client_context = f"""
        Информация о клиенте:
        - Имя: {client.name}
        - Всего автомобилей: {cars_count}
        - Активных заказов: {active_cars}
        """

    message_lower = message.lower()

    if any(word in message_lower for word in ["статус", "где", "находится", "местоположение"]):
        response = (
            "Чтобы узнать статус вашего груза, пожалуйста, укажите VIN автомобиля или номер контейнера. "
            "Вы также можете воспользоваться функцией отслеживания на главной странице."
        )

    elif any(word in message_lower for word in ["сколько стоит", "цена", "стоимость", "тариф"]):
        response = (
            "Стоимость доставки зависит от многих факторов: маршрута, типа автомобиля, "
            "дополнительных услуг. Для получения точного расчета, пожалуйста, "
            "свяжитесь с нашими менеджерами через форму обратной связи или по телефону."
        )

    elif any(word in message_lower for word in ["срок", "сколько времени", "как долго", "когда"]):
        response = (
            "Средний срок доставки автомобиля из США составляет:\n"
            "- Морская перевозка: 30-45 дней\n"
            "- Таможенное оформление: 3-7 дней\n"
            "- Доставка до вашего города: 2-5 дней\n\n"
            "Точные сроки зависят от конкретного маршрута и текущей ситуации."
        )

    elif any(word in message_lower for word in ["документы", "нужно", "требуется"]):
        response = (
            "Для оформления доставки автомобиля вам понадобятся:\n"
            "- Копия паспорта\n"
            "- Договор купли-продажи (Bill of Sale)\n"
            "- Титул автомобиля (Title)\n"
            "- Экспортная декларация\n\n"
            "Наши специалисты помогут вам с подготовкой всех необходимых документов."
        )

    elif any(word in message_lower for word in ["контакт", "связаться", "телефон", "email"]):
        response = (
            f"Вы можете связаться с нами:\n"
            f"📞 Телефон: {getattr(settings, 'COMPANY_PHONE', '+37068830450')}\n"
            f"📧 Email: {getattr(settings, 'COMPANY_EMAIL', 'info@caromoto-lt.com')}\n"
            f"🏢 Офис: Вильнюс, Литва\n\n"
            "Также вы можете оставить сообщение через форму обратной связи на сайте."
        )

    elif any(word in message_lower for word in ["фото", "фотографии", "картинки", "снимки"]):
        response = (
            "Фотографии вашего автомобиля доступны в личном кабинете. "
            "После разгрузки мы делаем детальную фотофиксацию состояния автомобиля. "
            "Вы можете просмотреть и скачать все фотографии в разделе 'Мои автомобили'."
        )

    elif any(word in message_lower for word in ["оплата", "платеж", "как оплатить", "способы оплаты"]):
        response = (
            "Мы принимаем оплату:\n"
            "- Банковским переводом\n"
            "- Наличными в офисе\n"
            "- Картой\n\n"
            "Вы можете оплатить полную стоимость сразу или частями согласно договору. "
            "Все инвойсы доступны в вашем личном кабинете."
        )

    elif any(word in message_lower for word in ["склад", "хранение", "хранить"]):
        response = (
            "Мы предоставляем услуги хранения на наших складах в Литве и Казахстане. "
            "Первые 3-7 дней хранения (в зависимости от тарифа) бесплатно. "
            "Далее стоимость хранения рассчитывается посуточно. "
            "Подробнее о тарифах вы можете узнать у вашего менеджера."
        )

    elif any(word in message_lower for word in ["спасибо", "благодарю", "thanks"]):
        response = "Пожалуйста! Рады помочь. Если у вас есть еще вопросы - обращайтесь! 😊"

    elif any(word in message_lower for word in ["привет", "здравствуй", "добрый день", "hello", "hi"]):
        response = (
            f"Здравствуйте! Я ИИ-помощник Caromoto Lithuania. Чем могу помочь?\n\n"
            "Я могу ответить на вопросы о:\n"
            "• Статусе вашего груза\n"
            "• Стоимости и сроках доставки\n"
            "• Необходимых документах\n"
            "• Услугах компании\n\n"
            f"{client_context if client_context else ''}"
        )

    else:
        response = (
            "Спасибо за ваш вопрос! Я постараюсь помочь, но для более точного ответа "
            "рекомендую связаться с нашим менеджером.\n\n"
            "Вы можете:\n"
            "• Написать в форму обратной связи\n"
            f"• Позвонить по телефону: {getattr(settings, 'COMPANY_PHONE', '+37068830450')}\n"
            f"• Написать на email: {getattr(settings, 'COMPANY_EMAIL', 'info@caromoto-lt.com')}\n\n"
            "Чем еще я могу помочь?"
        )

    return response


# ====== ИНТЕГРАЦИЯ С OPENAI (опционально) ======
# Раскомментируйте и настройте, если хотите использовать GPT-4 напрямую.
# В проде сейчас используется core.services.ai_chat_service.generate_ai_response —
# он сам выбирает провайдера и держит ретраи.
"""
import openai
from django.conf import settings


def get_ai_response_openai(message, user=None, client=None):
    openai.api_key = settings.OPENAI_API_KEY

    company_context = "..."  # Контекст о компании
    client_context = "..."   # Контекст о клиенте

    messages = [
        {"role": "system", "content": company_context + client_context},
        {"role": "user", "content": message},
    ]

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
        temperature=0.7,
        max_tokens=500,
    )

    return response.choices[0].message.content
"""


def _resolve_client(user):
    """Достать ``Client`` из User, если он клиентский. Иначе ``None``."""
    if user and user.is_authenticated and hasattr(user, "clientuser"):
        return user.clientuser.client
    return None


def _photo_response_for_car(request, message, vin):
    """Сценарий «фото по VIN» — без обращения к LLM."""
    car_qs = Car.objects.select_related("container").filter(vin__iexact=vin)
    if request.user.is_authenticated and hasattr(request.user, "clientuser"):
        car_qs = car_qs.filter(client=request.user.clientuser.client)
    car = car_qs.first()
    if not car:
        return None

    is_staff = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
    car_photos = CarPhoto.objects.filter(car=car)
    if not is_staff:
        car_photos = car_photos.filter(is_public=True)
    car_count = car_photos.count()
    last_car_photo = car_photos.order_by("-uploaded_at").first()

    container_link_text = ""
    if car.container:
        gallery_link = request.build_absolute_uri(f"/?track={car.container.number}&photos=1")
        container_link_text = f" Ссылка на галерею фото контейнера: {gallery_link}"

    if car_count:
        response_text = (
            f"Фото автомобиля по VIN {vin} доступны в личном кабинете. "
            f"Количество: {car_count}. "
            + (
                f"Последняя загрузка: {last_car_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}."
                if last_car_photo
                else ""
            )
            + container_link_text
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
                container_photos_text = f"Есть фото контейнера {car.container.number}: {container_count} шт." + (
                    f" Последняя загрузка: {last_container_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}."
                    if last_container_photo
                    else ""
                )
        if container_photos_text:
            response_text = (
                f"Фото автомобиля по VIN {vin} отсутствуют. {container_photos_text} "
                "Посмотреть можно по ссылке." + container_link_text
            )
        else:
            response_text = (
                f"Фото автомобиля по VIN {vin} пока не загружены. Если нужно — уточните у менеджера сроки загрузки."
            )

    return response_text


def _photo_response_for_container(request, number):
    """Сценарий «фото по номеру контейнера» — без обращения к LLM."""
    container = Container.objects.select_related("warehouse").filter(number__iexact=number).first()
    if not container:
        return None

    is_staff = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
    container_photos = ContainerPhoto.objects.filter(container=container)
    if not is_staff:
        container_photos = container_photos.filter(is_public=True)
    container_count = container_photos.count()
    last_container_photo = container_photos.order_by("-uploaded_at").first()
    gallery_link = request.build_absolute_uri(f"/?track={container.number}&photos=1")
    if container_count:
        response_text = (
            f"Фото контейнера {container.number}: {container_count} шт. "
            + (
                f"Последняя загрузка: {last_container_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}. "
                if last_container_photo
                else ""
            )
            + f"Ссылка на галерею: {gallery_link}"
        )
    else:
        response_text = f"Фото контейнера {container.number} пока не загружены. Ссылка на галерею: {gallery_link}"
    return response_text


def _save_chat_and_respond(session_id, user, client, message, response_text, *, processing_time=0, debug_meta=None):
    """Сохранить запись AIChat и вернуть DRF Response с сериализованной моделью."""
    chat = AIChat.objects.create(
        session_id=session_id,
        user=user,
        client=client,
        message=message,
        response=response_text,
        processing_time=processing_time,
    )
    serializer = AIChatSerializer(chat)
    payload = serializer.data
    if settings.DEBUG and debug_meta is not None:
        payload["meta"] = debug_meta
    return Response(payload)


@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
@throttle_classes([AIChatThrottle])
def ai_chat(request):
    """Основной эндпоинт ИИ-чата (анонимный + авторизованный + админский)."""
    message = request.data.get("message", "").strip()
    session_id = request.data.get("session_id", "")
    page_context = request.data.get("page_context") or {}
    if not isinstance(page_context, dict):
        page_context = {}

    if not message:
        return Response(
            {"error": "Сообщение не может быть пустым"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = request.user if request.user.is_authenticated else None
    is_staff = bool(user and (user.is_staff or user.is_superuser))
    referer = request.META.get("HTTP_REFERER", "")
    admin_header = request.META.get("HTTP_X_ADMIN_CHAT", "")
    logger.info(
        "AI chat request: user=%s staff=%s admin_header=%s referer=%s page_context=%s",
        getattr(user, "username", None),
        is_staff,
        admin_header,
        referer,
        page_context,
    )
    is_admin_context = bool(page_context.get("is_admin")) and is_staff
    if not is_admin_context and is_staff:
        if admin_header == "1" or "/admin/" in referer:
            page_context.setdefault("is_admin", True)
            is_admin_context = True

    try:
        if is_admin_context:
            return _handle_admin_chat(request, message, session_id, user, page_context)

        # --- Перехватываем «фото по VIN/номеру контейнера» без LLM ---
        photo_keywords = [
            "фото",
            "фотографии",
            "фотография",
            "фотки",
            "фотка",
            "фоточку",
            "снимки",
            "картинки",
            "изображения",
            "галерея",
            "gallery",
            "photo",
            "photos",
        ]
        vin_pattern = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
        container_pattern = re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE)
        message_lower = message.lower()
        if any(keyword in message_lower for keyword in photo_keywords) or re.search(r"\bфот", message_lower):
            vin_match = vin_pattern.search(message)
            if vin_match:
                vin = vin_match.group(0).upper()
                response_text = _photo_response_for_car(request, message, vin)
                if response_text is not None:
                    return _save_chat_and_respond(
                        session_id,
                        request.user if request.user.is_authenticated else None,
                        _resolve_client(request.user),
                        message,
                        response_text,
                        debug_meta={"used_fallback": False, "fallback_reason": "photo_lookup"},
                    )

            container_match = container_pattern.search(message)
            if container_match:
                number = container_match.group(0).upper()
                response_text = _photo_response_for_container(request, number)
                if response_text is not None:
                    return _save_chat_and_respond(
                        session_id,
                        request.user if request.user.is_authenticated else None,
                        _resolve_client(request.user),
                        message,
                        response_text,
                        debug_meta={"used_fallback": False, "fallback_reason": "container_photo_lookup"},
                    )

        # --- Финансовые вопросы блокируем по политике ---
        financial_keywords = [
            "цена",
            "стоимость",
            "сколько стоит",
            "тариф",
            "оплата",
            "платеж",
            "платёж",
            "счет",
            "счёт",
            "инвойс",
            "invoice",
            "payment",
            "balance",
            "баланс",
            "долг",
            "mark up",
            "markup",
            "наценка",
            "комиссия",
        ]
        if any(keyword in message_lower for keyword in financial_keywords):
            response_text = (
                "По финансовым вопросам, ценам и оплатам я не консультирую. "
                "Пожалуйста, обратитесь к вашему менеджеру или в службу поддержки."
            )
            return _save_chat_and_respond(
                session_id,
                request.user if request.user.is_authenticated else None,
                _resolve_client(request.user),
                message,
                response_text,
                debug_meta={"used_fallback": False, "fallback_reason": "financial_block"},
            )

        # --- Основной путь: внешний LLM с откатом на локальные правила ---
        client = _resolve_client(user)

        start_time = time.time()
        response_text = None
        used_fallback = False
        fallback_reason = None
        try:
            response_text = generate_ai_response(
                message=message,
                user=user,
                client=client,
                session_id=session_id,
                language_code=getattr(request, "LANGUAGE_CODE", "ru"),
            )
        except AIServiceError as exc:
            fallback_reason = str(exc)
            logger.warning("AI service failed, fallback to local rules: %s", fallback_reason)

        if not response_text:
            used_fallback = True
            response_text = get_ai_response(message, user=user, client=client)

        processing_time = time.time() - start_time
        logger.info(
            "AI chat client response: used_fallback=%s reason=%s",
            used_fallback,
            fallback_reason,
        )
        return _save_chat_and_respond(
            session_id,
            user,
            client,
            message,
            response_text,
            processing_time=processing_time,
            debug_meta={"used_fallback": used_fallback, "fallback_reason": fallback_reason},
        )
    except Exception:
        # Текст исключения наружу не отдаём: эндпоинт доступен анонимно,
        # а str(exc) может содержать внутренние детали (SQL, пути, ключи).
        # Полный traceback уходит в лог/Sentry через logger.exception.
        logger.exception("AI chat failed")
        return Response(
            {"error": "Внутренняя ошибка AI-чата. Попробуйте ещё раз позже."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _handle_admin_chat(request, message, session_id, user, page_context):
    """Ветка ИИ-чата для админки: использует ``generate_admin_ai_response``."""
    start_time = time.time()
    response_text = None
    fallback_reason = None
    used_fallback = False
    try:
        result = generate_admin_ai_response(
            message=message,
            user=user,
            page_context=page_context,
            session_id=session_id,
            language_code=getattr(request, "LANGUAGE_CODE", "ru"),
        )
        response_text = result.get("response")
        used_fallback = bool(result.get("used_fallback"))
        fallback_reason = result.get("fallback_reason")
    except Exception as exc:
        fallback_reason = str(exc)
        used_fallback = True
        response_text = "Не удалось обработать запрос. Попробуйте переформулировать вопрос."

    processing_time = time.time() - start_time
    chat = AIChat.objects.create(
        session_id=session_id,
        user=user,
        client=None,
        message=message,
        response=response_text,
        processing_time=processing_time,
        context_snapshot=page_context,
    )
    serializer = AIChatSerializer(chat)
    payload = serializer.data
    if settings.DEBUG:
        payload["meta"] = {
            "used_fallback": used_fallback,
            "fallback_reason": fallback_reason,
            "admin_context": True,
        }
    logger.info(
        "AI chat admin response: used_fallback=%s reason=%s",
        used_fallback,
        fallback_reason,
    )
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_feedback(request, chat_id):
    """Отметить, был ли полезен ответ ИИ."""
    was_helpful = request.data.get("was_helpful", None)

    if was_helpful is None:
        return Response(
            {"error": "Параметр was_helpful обязателен"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    chat = get_object_or_404(AIChat, id=chat_id, user=request.user)
    chat.was_helpful = was_helpful
    chat.save()

    return Response({"success": True})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_chat_history(request):
    """Получить историю чата пользователя (последние 50 сообщений)."""
    session_id = request.query_params.get("session_id")

    chats = AIChat.objects.filter(user=request.user)

    if session_id:
        chats = chats.filter(session_id=session_id)

    chats = chats.order_by("-created_at")[:50]

    serializer = AIChatSerializer(chats, many=True)
    return Response(serializer.data)
