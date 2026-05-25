"""Публичный эндпоинт ``/api/track/`` — отслеживание груза по VIN/контейнеру.

Throttle (`TrackShipmentThrottle`) ограничивает частоту запросов на IP,
чтобы исключить перебор номеров. Найденный груз пишется в
``TrackingRequest`` для аналитики и аудита.
"""

import logging

from django.db.models import Prefetch
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import Car, Container
from core.models_website import ContainerPhoto, TrackingRequest
from core.serializers_website import ClientCarSerializer, ClientContainerSerializer
from core.throttles import TrackShipmentThrottle

logger = logging.getLogger(__name__)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([TrackShipmentThrottle])
def track_shipment(request):
    """Отследить груз по номеру VIN или контейнера."""
    try:
        tracking_number = request.data.get("tracking_number", "").strip()
        email = request.data.get("email", "").strip()

        logger.info("[TRACK] Поиск груза: '%s'", tracking_number)

        if not tracking_number:
            return Response(
                {"error": "Пожалуйста, укажите номер для отслеживания"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Нормализуем: убираем пробелы/тире, переводим в верхний регистр.
        normalized_number = tracking_number.upper().replace(" ", "").replace("-", "")
        logger.info("[TRACK] Нормализованный номер: '%s'", normalized_number)

        # Безопасность: VIN — 17 символов, номер контейнера ~11. Слишком короткий
        # ввод однозначно не валидный, но при этом провоцирует широкий поиск.
        # Минимум 8 — компромисс между опечатками и защитой от перебора/утечки.
        if len(normalized_number) < 8:
            return Response(
                {"error": "Номер слишком короткий. Введите полный VIN (17 символов) или номер контейнера."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        car_qs = Car.objects.select_related("container", "container__warehouse", "warehouse").prefetch_related(
            Prefetch("container__photos", queryset=ContainerPhoto.objects.filter(is_public=True))
        )

        # Только точное совпадение VIN. Раньше тут был vin__icontains в качестве
        # fallback — это давало возможность по частичному совпадению получать
        # данные чужих автомобилей (security leak).
        car = car_qs.filter(vin__iexact=tracking_number).first()
        if not car and normalized_number != tracking_number.upper():
            car = car_qs.filter(vin__iexact=normalized_number).first()

        container = None

        if not car:
            container_qs = Container.objects.select_related("warehouse").prefetch_related(
                Prefetch("photos", queryset=ContainerPhoto.objects.filter(is_public=True))
            )
            container = container_qs.filter(number__iexact=tracking_number).first()
            if not container and normalized_number != tracking_number.upper():
                container = container_qs.filter(number__iexact=normalized_number).first()

        # Логируем сам запрос для аналитики (не блокирующее).
        try:
            TrackingRequest.objects.create(
                tracking_number=tracking_number,
                email=email,
                car=car,
                container=container,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        except Exception as e:
            logger.warning("[TRACK] Не удалось сохранить TrackingRequest: %s", e)

        if car:
            logger.info("[TRACK] Найден автомобиль: %s", car.vin)
            serializer = ClientCarSerializer(car, context={"request": request})
            return Response({"type": "car", "data": serializer.data})
        elif container:
            logger.info("[TRACK] Найден контейнер: %s", container.number)
            serializer = ClientContainerSerializer(container, context={"request": request})
            return Response({"type": "container", "data": serializer.data})
        else:
            logger.info("[TRACK] Груз не найден: '%s'", tracking_number)
            return Response(
                {"error": "Груз не найден. Проверьте правильность номера."},
                status=status.HTTP_404_NOT_FOUND,
            )
    except Exception as e:
        logger.error("[TRACK] Ошибка при поиске груза: %s", e, exc_info=True)
        # Не возвращаем str(e) клиенту: stacktrace в JSON-ответе — утечка
        # деталей реализации. Сообщение в логах + Sentry достаточно.
        return Response(
            {"error": "Внутренняя ошибка сервера. Попробуйте позже."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
