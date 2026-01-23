"""
Views для клиентского сайта Caromoto Lithuania
"""
import os
import time
import zipfile
from io import BytesIO
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse, FileResponse, Http404, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, action, authentication_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
import re

from .models import Car, Container, Client
from .models_website import (
    ClientUser, CarPhoto, ContainerPhoto, ContainerPhotoArchive, AIChat,
    NewsPost, ContactMessage, TrackingRequest
)
from .services.ai_chat_service import generate_ai_response, AIServiceError
from .serializers_website import (
    ClientUserSerializer, CarPhotoSerializer, ContainerPhotoSerializer,
    ClientCarSerializer, ClientContainerSerializer, AIChatSerializer,
    NewsPostSerializer, ContactMessageSerializer, TrackingRequestSerializer
)


# ============================================================================
# Главная страница и информационные страницы
# ============================================================================

def website_home(request):
    """Главная страница сайта"""
    latest_news = NewsPost.objects.filter(published=True).order_by('-published_at')[:3]
    
    context = {
        'latest_news': latest_news,
        'company_name': 'Caromoto Lithuania',
    }
    return render(request, 'website/home.html', context)


def about_page(request):
    """Страница о компании"""
    context = {
        'company_name': 'Caromoto Lithuania',
    }
    return render(request, 'website/about.html', context)


def services_page(request):
    """Страница услуг"""
    return render(request, 'website/services.html')


def contact_page(request):
    """Страница контактов"""
    return render(request, 'website/contact.html')


def news_list(request):
    """Список новостей"""
    news = NewsPost.objects.filter(published=True).order_by('-published_at')
    return render(request, 'website/news_list.html', {'news': news})


def news_detail(request, slug):
    """Детальная страница новости"""
    post = get_object_or_404(NewsPost, slug=slug, published=True)
    post.views += 1
    post.save(update_fields=['views'])
    return render(request, 'website/news_detail.html', {'post': post})


# ============================================================================
# Личный кабинет клиента
# ============================================================================

@login_required
def client_dashboard(request):
    """Личный кабинет клиента"""
    try:
        client_user = request.user.clientuser
        client = client_user.client
        
        # Получаем автомобили клиента
        cars = Car.objects.filter(client=client).select_related(
            'warehouse', 'container'
        ).prefetch_related(
            Prefetch('photos', queryset=CarPhoto.objects.filter(is_public=True))
        ).order_by('-id')
        
        # Получаем контейнеры клиента
        containers = Container.objects.filter(client=client).select_related(
            'line', 'warehouse'
        ).prefetch_related(
            Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True))
        ).order_by('-id')
        
        context = {
            'client': client,
            'cars': cars,
            'containers': containers,
            'cars_count': cars.count(),
            'containers_count': containers.count(),
        }
        
        return render(request, 'website/client_dashboard.html', context)
    except ClientUser.DoesNotExist:
        return render(request, 'website/not_authorized.html', status=403)


@login_required
def car_detail(request, car_id):
    """Детальная информация об автомобиле"""
    try:
        client_user = request.user.clientuser
        car = get_object_or_404(
            Car.objects.select_related('warehouse', 'container', 'line', 'carrier')
                       .prefetch_related(
                           Prefetch('photos', queryset=CarPhoto.objects.filter(is_public=True))
                       ),
            id=car_id,
            client=client_user.client
        )
        
        return render(request, 'website/car_detail.html', {'car': car})
    except ClientUser.DoesNotExist:
        return render(request, 'website/not_authorized.html', status=403)


@login_required
def container_detail(request, container_id):
    """Детальная информация о контейнере"""
    try:
        client_user = request.user.clientuser
        container = get_object_or_404(
            Container.objects.select_related('line', 'warehouse')
                             .prefetch_related(
                                 Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True)),
                                 Prefetch('container_cars', queryset=Car.objects.all())
                             ),
            id=container_id,
            client=client_user.client
        )
        
        return render(request, 'website/container_detail.html', {'container': container})
    except ClientUser.DoesNotExist:
        return render(request, 'website/not_authorized.html', status=403)


# ============================================================================
# API для клиентского портала
# ============================================================================

class IsClientUser(permissions.BasePermission):
    """Проверяет, что пользователь является клиентом"""
    
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            hasattr(request.user, 'clientuser')
        )


class ClientCarViewSet(viewsets.ReadOnlyModelViewSet):
    """API для просмотра автомобилей клиента"""
    serializer_class = ClientCarSerializer
    permission_classes = [IsClientUser]
    
    def get_queryset(self):
        client = self.request.user.clientuser.client
        return Car.objects.filter(client=client).select_related(
            'warehouse', 'container'
        ).prefetch_related(
            Prefetch('photos', queryset=CarPhoto.objects.filter(is_public=True))
        ).order_by('-id')


class ClientContainerViewSet(viewsets.ReadOnlyModelViewSet):
    """API для просмотра контейнеров клиента"""
    serializer_class = ClientContainerSerializer
    permission_classes = [IsClientUser]
    
    def get_queryset(self):
        client = self.request.user.clientuser.client
        return Container.objects.filter(client=client).select_related(
            'line', 'warehouse'
        ).prefetch_related(
            Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True)),
            Prefetch('container_cars', queryset=Car.objects.all())
        ).order_by('-id')


class NewsViewSet(viewsets.ReadOnlyModelViewSet):
    """API для новостей"""
    serializer_class = NewsPostSerializer
    permission_classes = [AllowAny]
    queryset = NewsPost.objects.filter(published=True).order_by('-published_at')
    lookup_field = 'slug'


class ContactMessageViewSet(viewsets.ModelViewSet):
    """API для сообщений обратной связи"""
    serializer_class = ContactMessageSerializer
    permission_classes = [AllowAny]
    queryset = ContactMessage.objects.all()
    http_method_names = ['post']  # Только создание


# ============================================================================
# Отслеживание груза
# ============================================================================

@api_view(['POST'])
@permission_classes([AllowAny])
def track_shipment(request):
    """Отследить груз по номеру VIN или контейнера"""
    import logging
    logger = logging.getLogger('django')
    
    try:
        tracking_number = request.data.get('tracking_number', '').strip()
        email = request.data.get('email', '').strip()
        
        logger.info(f"[TRACK] Поиск груза: '{tracking_number}'")
        
        if not tracking_number:
            return Response(
                {'error': 'Пожалуйста, укажите номер для отслеживания'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Нормализуем номер: убираем пробелы, тире, переводим в верхний регистр
        normalized_number = tracking_number.upper().replace(' ', '').replace('-', '')
        logger.info(f"[TRACK] Нормализованный номер: '{normalized_number}'")
        
        # Ищем по VIN (с загрузкой контейнера, склада и фотографий)
        # Пробуем сначала точное совпадение, потом по нормализованному
        car = Car.objects.filter(vin__iexact=tracking_number).select_related(
            'container', 'container__warehouse', 'warehouse'
        ).prefetch_related(
            Prefetch('container__photos', queryset=ContainerPhoto.objects.filter(is_public=True))
        ).first()
        
        # Если не нашли - пробуем по нормализованному VIN
        if not car and normalized_number != tracking_number.upper():
            car = Car.objects.filter(vin__iexact=normalized_number).select_related(
                'container', 'container__warehouse', 'warehouse'
            ).prefetch_related(
                Prefetch('container__photos', queryset=ContainerPhoto.objects.filter(is_public=True))
            ).first()
        
        # Если не нашли - ищем по частичному совпадению VIN (содержит)
        if not car:
            car = Car.objects.filter(vin__icontains=normalized_number).select_related(
                'container', 'container__warehouse', 'warehouse'
            ).prefetch_related(
                Prefetch('container__photos', queryset=ContainerPhoto.objects.filter(is_public=True))
            ).first()
        
        container = None
        
        if not car:
            # Ищем по номеру контейнера (с загрузкой склада)
            container = Container.objects.filter(number__iexact=tracking_number).select_related(
                'warehouse'
            ).prefetch_related(
                Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True))
            ).first()
            
            # Если не нашли - пробуем по нормализованному
            if not container and normalized_number != tracking_number.upper():
                container = Container.objects.filter(number__iexact=normalized_number).select_related(
                    'warehouse'
                ).prefetch_related(
                    Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True))
                ).first()
        
        # Сохраняем запрос
        try:
            TrackingRequest.objects.create(
                tracking_number=tracking_number,
                email=email,
                car=car,
                container=container,
                ip_address=request.META.get('REMOTE_ADDR')
            )
        except Exception as e:
            logger.warning(f"[TRACK] Не удалось сохранить TrackingRequest: {e}")
        
        if car:
            logger.info(f"[TRACK] Найден автомобиль: {car.vin}")
            serializer = ClientCarSerializer(car, context={'request': request})
            return Response({
                'type': 'car',
                'data': serializer.data
            })
        elif container:
            logger.info(f"[TRACK] Найден контейнер: {container.number}")
            serializer = ClientContainerSerializer(container, context={'request': request})
            return Response({
                'type': 'container',
                'data': serializer.data
            })
        else:
            logger.info(f"[TRACK] Груз не найден: '{tracking_number}'")
            return Response(
                {'error': 'Груз не найден. Проверьте правильность номера.'},
                status=status.HTTP_404_NOT_FOUND
            )
    except Exception as e:
        logger.error(f"[TRACK] Ошибка при поиске груза: {e}", exc_info=True)
        return Response(
            {'error': f'Ошибка сервера: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# Скачивание фотографий
# ============================================================================

@login_required
def download_car_photo(request, photo_id):
    """Скачать фотографию автомобиля"""
    try:
        client_user = request.user.clientuser
        photo = get_object_or_404(
            CarPhoto.objects.select_related('car'),
            id=photo_id,
            car__client=client_user.client,
            is_public=True
        )
        
        if photo.photo and os.path.exists(photo.photo.path):
            response = FileResponse(photo.photo.open('rb'))
            response['Content-Disposition'] = f'attachment; filename="{photo.filename}"'
            return response
        else:
            raise Http404("Фото не найдено")
    except ClientUser.DoesNotExist:
        raise Http404("Доступ запрещен")


@login_required
def download_container_photo(request, photo_id):
    """Скачать фотографию контейнера"""
    try:
        client_user = request.user.clientuser
        photo = get_object_or_404(
            ContainerPhoto.objects.select_related('container'),
            id=photo_id,
            container__client=client_user.client,
            is_public=True
        )
        
        if photo.photo and os.path.exists(photo.photo.path):
            response = FileResponse(photo.photo.open('rb'))
            response['Content-Disposition'] = f'attachment; filename="{photo.filename}"'
            return response
        else:
            raise Http404("Фото не найдено")
    except ClientUser.DoesNotExist:
        raise Http404("Доступ запрещен")


@login_required
@api_view(['GET'])
def download_all_car_photos(request, car_id):
    """Скачать все фотографии автомобиля как ZIP архив"""
    import zipfile
    from io import BytesIO
    
    try:
        client_user = request.user.clientuser
        car = get_object_or_404(Car, id=car_id, client=client_user.client)
        photos = CarPhoto.objects.filter(car=car, is_public=True)
        
        if not photos.exists():
            return Response(
                {'error': 'Фотографии не найдены'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Создаем ZIP архив в памяти
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for photo in photos:
                if photo.photo and os.path.exists(photo.photo.path):
                    zip_file.write(
                        photo.photo.path,
                        arcname=f"{photo.get_photo_type_display()}_{photo.filename}"
                    )
        
        zip_buffer.seek(0)
        response = FileResponse(zip_buffer, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{car.vin}_photos.zip"'
        return response
        
    except ClientUser.DoesNotExist:
        return Response(
            {'error': 'Доступ запрещен'},
            status=status.HTTP_403_FORBIDDEN
        )


# ============================================================================
# ИИ-помощник
# ============================================================================

def get_ai_response(message, user=None, client=None):
    """
    Генерирует ответ ИИ-помощника на основе сообщения пользователя
    
    Здесь можно интегрировать:
    - OpenAI API (GPT-4)
    - Anthropic Claude
    - Local LLM (llama.cpp, ollama)
    - Свой обученный model
    """
    
    # Контекст о компании для ИИ
    company_context = """
    Вы - ИИ-помощник логистической компании Caromoto Lithuania.
    
    Компания специализируется на:
    - Доставке автомобилей из США в Казахстан и страны СНГ
    - Контейнерных перевозках
    - Таможенном оформлении
    - Хранении на складах
    
    Ваша задача - помогать клиентам с информацией о их грузах, отвечать на вопросы
    о процессе доставки, статусах заказов и услугах компании.
    
    Отвечайте вежливо, профессионально и по существу.
    """
    
    # Получаем информацию о клиенте, если доступна
    client_context = ""
    if client:
        cars_count = Car.objects.filter(client=client).count()
        active_cars = Car.objects.filter(
            client=client,
            status__in=['FLOATING', 'IN_PORT', 'UNLOADED']
        ).count()
        
        client_context = f"""
        Информация о клиенте:
        - Имя: {client.name}
        - Всего автомобилей: {cars_count}
        - Активных заказов: {active_cars}
        """
    
    # Простая логика ответов (можно заменить на вызов OpenAI API)
    message_lower = message.lower()
    
    # Определяем тип вопроса и даем соответствующий ответ
    if any(word in message_lower for word in ['статус', 'где', 'находится', 'местоположение']):
        response = "Чтобы узнать статус вашего груза, пожалуйста, укажите VIN автомобиля или номер контейнера. Вы также можете воспользоваться функцией отслеживания на главной странице."
    
    elif any(word in message_lower for word in ['сколько стоит', 'цена', 'стоимость', 'тариф']):
        response = "Стоимость доставки зависит от многих факторов: маршрута, типа автомобиля, дополнительных услуг. Для получения точного расчета, пожалуйста, свяжитесь с нашими менеджерами через форму обратной связи или по телефону."
    
    elif any(word in message_lower for word in ['срок', 'сколько времени', 'как долго', 'когда']):
        response = "Средний срок доставки автомобиля из США составляет:\n- Морская перевозка: 30-45 дней\n- Таможенное оформление: 3-7 дней\n- Доставка до вашего города: 2-5 дней\n\nТочные сроки зависят от конкретного маршрута и текущей ситуации."
    
    elif any(word in message_lower for word in ['документы', 'нужно', 'требуется']):
        response = "Для оформления доставки автомобиля вам понадобятся:\n- Копия паспорта\n- Договор купли-продажи (Bill of Sale)\n- Титул автомобиля (Title)\n- Экспортная декларация\n\nНаши специалисты помогут вам с подготовкой всех необходимых документов."
    
    elif any(word in message_lower for word in ['контакт', 'связаться', 'телефон', 'email']):
        response = "Вы можете связаться с нами:\n📞 Телефон: +370 XXX XXXXX\n📧 Email: info@caromoto-lt.com\n🏢 Офис: Вильнюс, Литва\n\nТакже вы можете оставить сообщение через форму обратной связи на сайте."
    
    elif any(word in message_lower for word in ['фото', 'фотографии', 'картинки', 'снимки']):
        response = "Фотографии вашего автомобиля доступны в личном кабинете. После разгрузки мы делаем детальную фотофиксацию состояния автомобиля. Вы можете просмотреть и скачать все фотографии в разделе 'Мои автомобили'."
    
    elif any(word in message_lower for word in ['оплата', 'платеж', 'как оплатить', 'способы оплаты']):
        response = "Мы принимаем оплату:\n- Банковским переводом\n- Наличными в офисе\n- Картой\n\nВы можете оплатить полную стоимость сразу или частями согласно договору. Все инвойсы доступны в вашем личном кабинете."
    
    elif any(word in message_lower for word in ['склад', 'хранение', 'хранить']):
        response = "Мы предоставляем услуги хранения на наших складах в Литве и Казахстане. Первые 3-7 дней хранения (в зависимости от тарифа) бесплатно. Далее стоимость хранения рассчитывается посуточно. Подробнее о тарифах вы можете узнать у вашего менеджера."
    
    elif any(word in message_lower for word in ['спасибо', 'благодарю', 'thanks']):
        response = "Пожалуйста! Рады помочь. Если у вас есть еще вопросы - обращайтесь! 😊"
    
    elif any(word in message_lower for word in ['привет', 'здравствуй', 'добрый день', 'hello', 'hi']):
        response = f"Здравствуйте! Я ИИ-помощник Caromoto Lithuania. Чем могу помочь?\n\nЯ могу ответить на вопросы о:\n• Статусе вашего груза\n• Стоимости и сроках доставки\n• Необходимых документах\n• Услугах компании\n\n{client_context if client_context else ''}"
    
    else:
        response = "Спасибо за ваш вопрос! Я постараюсь помочь, но для более точного ответа рекомендую связаться с нашим менеджером.\n\nВы можете:\n• Написать в форму обратной связи\n• Позвонить по телефону: +370 XXX XXXXX\n• Написать на email: info@caromoto-lt.com\n\nЧем еще я могу помочь?"
    
    return response


# ====== ИНТЕГРАЦИЯ С OPENAI (опционально) ======
# Раскомментируйте и настройте, если хотите использовать GPT-4
"""
import openai
from django.conf import settings

def get_ai_response_openai(message, user=None, client=None):
    openai.api_key = settings.OPENAI_API_KEY
    
    company_context = "..."  # Контекст о компании
    client_context = "..."   # Контекст о клиенте
    
    messages = [
        {"role": "system", "content": company_context + client_context},
        {"role": "user", "content": message}
    ]
    
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
        temperature=0.7,
        max_tokens=500
    )
    
    return response.choices[0].message.content
"""


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def ai_chat(request):
    """Эндпоинт для чата с ИИ-помощником"""
    message = request.data.get('message', '').strip()
    session_id = request.data.get('session_id', '')
    
    if not message:
        return Response(
            {'error': 'Сообщение не может быть пустым'},
            status=status.HTTP_400_BAD_REQUEST
        )

    photo_keywords = [
        'фото', 'фотографии', 'фотография', 'фотки', 'фотка', 'фоточку',
        'снимки', 'картинки', 'изображения', 'галерея', 'gallery', 'photo', 'photos'
    ]
    vin_pattern = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
    message_lower = message.lower()
    if any(keyword in message_lower for keyword in photo_keywords) or re.search(r"\bфот", message_lower):
        vin_match = vin_pattern.search(message)
        if vin_match:
            vin = vin_match.group(0).upper()
            car_qs = Car.objects.select_related('container').filter(vin__iexact=vin)
            if request.user.is_authenticated and hasattr(request.user, 'clientuser'):
                car_qs = car_qs.filter(client=request.user.clientuser.client)
            car = car_qs.first()

            if car:
                is_staff = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
                car_photos = CarPhoto.objects.filter(car=car)
                if not is_staff:
                    car_photos = car_photos.filter(is_public=True)
                car_count = car_photos.count()
                last_car_photo = car_photos.order_by('-uploaded_at').first()

                container_link_text = ""
                if car.container:
                    gallery_link = request.build_absolute_uri(
                        f"/?track={car.container.number}&photos=1"
                    )
                    container_link_text = f" Ссылка на галерею фото контейнера: {gallery_link}"

                if car_count:
                    response_text = (
                        f"Фото автомобиля по VIN {vin} доступны в личном кабинете. "
                        f"Количество: {car_count}. "
                        + (
                            f"Последняя загрузка: {last_car_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}."
                            if last_car_photo else ""
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
                        last_container_photo = container_photos.order_by('-uploaded_at').first()
                        if container_count:
                            container_photos_text = (
                                f"Есть фото контейнера {car.container.number}: {container_count} шт."
                                + (
                                    f" Последняя загрузка: {last_container_photo.uploaded_at.strftime('%Y-%m-%d %H:%M')}."
                                    if last_container_photo else ""
                                )
                            )
                    if container_photos_text:
                        response_text = (
                            f"Фото автомобиля по VIN {vin} отсутствуют. {container_photos_text} "
                            "Посмотреть можно по ссылке."
                            + container_link_text
                        )
                    else:
                        response_text = (
                            f"Фото автомобиля по VIN {vin} пока не загружены. "
                            "Если нужно — уточните у менеджера сроки загрузки."
                        )

                chat = AIChat.objects.create(
                    session_id=session_id,
                    user=request.user if request.user.is_authenticated else None,
                    client=getattr(request.user, 'clientuser', None).client if request.user.is_authenticated and hasattr(request.user, 'clientuser') else None,
                    message=message,
                    response=response_text,
                    processing_time=0,
                )
                serializer = AIChatSerializer(chat)
                payload = serializer.data
                if settings.DEBUG:
                    payload["meta"] = {"used_fallback": False, "fallback_reason": "photo_lookup"}
                return Response(payload)

    financial_keywords = [
        'цена', 'стоимость', 'сколько стоит', 'тариф', 'оплата', 'платеж', 'платёж',
        'счет', 'счёт', 'инвойс', 'invoice', 'payment', 'balance', 'баланс', 'долг',
        'mark up', 'markup', 'наценка', 'комиссия'
    ]
    message_lower = message.lower()
    if any(keyword in message_lower for keyword in financial_keywords):
        response_text = (
            "По финансовым вопросам, ценам и оплатам я не консультирую. "
            "Пожалуйста, обратитесь к вашему менеджеру или в службу поддержки."
        )
        chat = AIChat.objects.create(
            session_id=session_id,
            user=request.user if request.user.is_authenticated else None,
            client=getattr(request.user, 'clientuser', None).client if request.user.is_authenticated and hasattr(request.user, 'clientuser') else None,
            message=message,
            response=response_text,
            processing_time=0,
        )
        serializer = AIChatSerializer(chat)
        payload = serializer.data
        if settings.DEBUG:
            payload["meta"] = {"used_fallback": False, "fallback_reason": "financial_block"}
        return Response(payload)
    
    # Получаем информацию о пользователе, если авторизован
    user = request.user if request.user.is_authenticated else None
    client = None
    
    if user and hasattr(user, 'clientuser'):
        client = user.clientuser.client
    
    # Засекаем время обработки
    start_time = time.time()
    
    # Получаем ответ от ИИ
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
        import logging
        logger = logging.getLogger(__name__)
        fallback_reason = str(exc)
        logger.warning("AI service failed, fallback to local rules: %s", fallback_reason)

    if not response_text:
        used_fallback = True
        response_text = get_ai_response(message, user=user, client=client)
    
    processing_time = time.time() - start_time
    
    # Сохраняем в историю чата
    chat = AIChat.objects.create(
        session_id=session_id,
        user=user,
        client=client,
        message=message,
        response=response_text,
        processing_time=processing_time
    )
    
    serializer = AIChatSerializer(chat)
    payload = serializer.data
    if settings.DEBUG:
        payload["meta"] = {
            "used_fallback": used_fallback,
            "fallback_reason": fallback_reason,
        }
    return Response(payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ai_chat_feedback(request, chat_id):
    """Отметить, был ли полезен ответ ИИ"""
    was_helpful = request.data.get('was_helpful', None)
    
    if was_helpful is None:
        return Response(
            {'error': 'Параметр was_helpful обязателен'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    chat = get_object_or_404(AIChat, id=chat_id, user=request.user)
    chat.was_helpful = was_helpful
    chat.save()
    
    return Response({'success': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_chat_history(request):
    """Получить историю чата пользователя"""
    session_id = request.query_params.get('session_id')
    
    chats = AIChat.objects.filter(user=request.user)
    
    if session_id:
        chats = chats.filter(session_id=session_id)
    
    chats = chats.order_by('-created_at')[:50]
    
    serializer = AIChatSerializer(chats, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_container_photos(request, container_number):
    """Получить фотографии контейнера с разделением по типам"""
    try:
        container = Container.objects.get(number=container_number)
        photos = ContainerPhoto.objects.filter(
            container=container, 
            is_public=True
        )
        
        # Сортируем по имени файла для сохранения последовательности из архива
        photos_list = list(photos)
        photos_list.sort(key=lambda p: p.photo.name if p.photo else '')
        
        photos_data = []
        type_counts = {'IN_CONTAINER': 0, 'UNLOADING': 0, 'GENERAL': 0}
        
        for photo in photos_list:
            photo_type = photo.photo_type or 'GENERAL'
            type_counts[photo_type] = type_counts.get(photo_type, 0) + 1
            
            # Ensure URLs have /media/ prefix
            photo_url = photo.photo.url
            if not photo_url.startswith('/media/') and not photo_url.startswith('http'):
                photo_url = '/media/' + photo_url.lstrip('/')
            
            thumb_url = photo.thumbnail.url if photo.thumbnail else photo_url
            if not thumb_url.startswith('/media/') and not thumb_url.startswith('http'):
                thumb_url = '/media/' + thumb_url.lstrip('/')
            
            photos_data.append({
                'id': photo.id,
                'url': photo_url,
                'thumbnail_url': thumb_url,
                'description': photo.description,
                'photo_type': photo.get_photo_type_display(),
                'photo_type_code': photo_type,  # Сырой код для фильтрации
                'uploaded_at': photo.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                'filename': photo.filename
            })
        
        return Response({
            'success': True,
            'container_number': container.number,
            'photos': photos_data,
            'photos_count': len(photos_data),
            'type_counts': type_counts  # Количество фото каждого типа
        })
        
    except Container.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Контейнер не найден'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def download_photos_archive(request):
    """Скачать архив выбранных фотографий"""
    try:
        photo_ids = request.data.get('photo_ids', [])
        if not photo_ids:
            return Response({
                'success': False,
                'error': 'Не выбраны фотографии'
            }, status=400)
        
        photos = ContainerPhoto.objects.filter(
            id__in=photo_ids,
            is_public=True
        )
        
        if not photos.exists():
            return Response({
                'success': False,
                'error': 'Фотографии не найдены'
            }, status=404)
        
        # Создаем ZIP архив
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for photo in photos:
                if photo.photo and os.path.exists(photo.photo.path):
                    zip_file.write(
                        photo.photo.path, 
                        f"{photo.container.number}_{photo.filename}"
                    )
        
        zip_buffer.seek(0)
        
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="container_photos_{photos.first().container.number}.zip"'
        
        return response
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=500)

