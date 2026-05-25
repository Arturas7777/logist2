"""DRF ViewSet'ы для клиентского портала + permission ``IsClientUser``."""

from django.db.models import Prefetch
from rest_framework import permissions, viewsets
from rest_framework.permissions import AllowAny

from core.models import Car, Container
from core.models_website import CarPhoto, ContactMessage, ContainerPhoto, NewsPost
from core.serializers_website import (
    ClientCarSerializer,
    ClientContainerSerializer,
    ContactMessageSerializer,
    NewsPostSerializer,
)


class IsClientUser(permissions.BasePermission):
    """Разрешает доступ только аутентифицированным пользователям-клиентам."""

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and hasattr(request.user, "clientuser")


class ClientCarViewSet(viewsets.ReadOnlyModelViewSet):
    """API для просмотра автомобилей клиента."""

    serializer_class = ClientCarSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        client = self.request.user.clientuser.client
        return (
            Car.objects.filter(client=client)
            .select_related("warehouse", "container")
            .prefetch_related(
                Prefetch("photos", queryset=CarPhoto.objects.filter(is_public=True)),
                Prefetch("container__photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
            )
            .order_by("-id")
        )


class ClientContainerViewSet(viewsets.ReadOnlyModelViewSet):
    """API для просмотра контейнеров клиента."""

    serializer_class = ClientContainerSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        client = self.request.user.clientuser.client
        return (
            Container.objects.filter(client=client)
            .select_related("line", "warehouse")
            .prefetch_related(
                Prefetch("photos", queryset=ContainerPhoto.objects.filter(is_public=True)),
                "container_cars",
            )
            .order_by("-id")
        )


class NewsViewSet(viewsets.ReadOnlyModelViewSet):
    """Публичный API для новостей."""

    serializer_class = NewsPostSerializer
    permission_classes = [AllowAny]
    queryset = NewsPost.objects.filter(published=True).order_by("-published_at")
    lookup_field = "slug"


class ContactMessageViewSet(viewsets.ModelViewSet):
    """API для сообщений обратной связи (только POST)."""

    serializer_class = ContactMessageSerializer
    permission_classes = [AllowAny]
    queryset = ContactMessage.objects.all()
    http_method_names = ["post"]
