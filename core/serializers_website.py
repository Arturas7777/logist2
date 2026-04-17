"""
Сериализаторы для клиентского сайта
"""
from rest_framework import serializers

from .models import Car, Container
from .models_website import AIChat, CarPhoto, ClientUser, ContactMessage, ContainerPhoto, NewsPost, TrackingRequest


class ClientUserSerializer(serializers.ModelSerializer):
    """Сериализатор для клиентского пользователя"""
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    client_name = serializers.CharField(source='client.name', read_only=True)

    class Meta:
        model = ClientUser
        fields = ['id', 'username', 'email', 'client_name', 'phone',
                  'language', 'is_verified', 'created_at', 'last_login']
        read_only_fields = ['is_verified', 'created_at', 'last_login']


class CarPhotoSerializer(serializers.ModelSerializer):
    """Сериализатор для фотографий автомобилей"""
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = CarPhoto
        fields = ['id', 'car', 'photo', 'photo_url', 'photo_type',
                  'description', 'uploaded_at', 'filename']
        read_only_fields = ['uploaded_at', 'filename']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        if obj.photo and hasattr(obj.photo, 'url'):
            if request:
                return request.build_absolute_uri(obj.photo.url)
            return obj.photo.url
        return None


class ContainerPhotoSerializer(serializers.ModelSerializer):
    """Сериализатор для фотографий контейнеров"""
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = ContainerPhoto
        fields = ['id', 'container', 'photo', 'photo_url', 'photo_type',
                  'description', 'uploaded_at', 'filename']
        read_only_fields = ['uploaded_at', 'filename']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        if obj.photo and hasattr(obj.photo, 'url'):
            if request:
                return request.build_absolute_uri(obj.photo.url)
            return obj.photo.url
        return None


class ClientCarSerializer(serializers.ModelSerializer):
    """Сериализатор для автомобилей клиента.

    Expects queryset with prefetch:
        Prefetch('photos', queryset=CarPhoto.objects.filter(is_public=True))
        Prefetch('container__photos', queryset=ContainerPhoto.objects.filter(is_public=True))
    """
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, allow_null=True)
    warehouse_address = serializers.CharField(source='warehouse.address', read_only=True, allow_null=True)
    container_number = serializers.CharField(source='container.number', read_only=True, allow_null=True)
    container_unload_date = serializers.DateField(source='container.unload_date', read_only=True, allow_null=True)
    photos = CarPhotoSerializer(many=True, read_only=True)
    photos_count = serializers.SerializerMethodField()
    container_photos = serializers.SerializerMethodField()
    container_photos_count = serializers.SerializerMethodField()

    class Meta:
        model = Car
        fields = [
            'id', 'vin', 'brand', 'year', 'status', 'status_display',
            'warehouse_name', 'warehouse_address', 'container_number',
            'container_unload_date', 'unload_date', 'transfer_date',
            'total_price', 'storage_cost', 'days',
            'photos', 'photos_count', 'container_photos', 'container_photos_count'
        ]

    def get_photos_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'photos' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['photos'])
        return obj.photos.filter(is_public=True).count()

    def get_container_photos(self, obj):
        if not obj.container:
            return []
        if (hasattr(obj.container, '_prefetched_objects_cache')
                and 'photos' in obj.container._prefetched_objects_cache):
            photos = obj.container._prefetched_objects_cache['photos']
        else:
            photos = obj.container.photos.filter(is_public=True)
        return ContainerPhotoSerializer(photos, many=True, context=self.context).data

    def get_container_photos_count(self, obj):
        if not obj.container:
            return 0
        if (hasattr(obj.container, '_prefetched_objects_cache')
                and 'photos' in obj.container._prefetched_objects_cache):
            return len(obj.container._prefetched_objects_cache['photos'])
        return obj.container.photos.filter(is_public=True).count()


class ClientContainerSerializer(serializers.ModelSerializer):
    """Сериализатор для контейнеров клиента.

    Expects queryset with prefetch:
        Prefetch('photos', queryset=ContainerPhoto.objects.filter(is_public=True))
        Prefetch('container_cars')
    """
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_name = serializers.CharField(source='line.name', read_only=True, allow_null=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, allow_null=True)
    warehouse_address = serializers.CharField(source='warehouse.address', read_only=True, allow_null=True)
    cars_count = serializers.SerializerMethodField()
    photos = ContainerPhotoSerializer(many=True, read_only=True)
    photos_count = serializers.SerializerMethodField()

    class Meta:
        model = Container
        fields = [
            'id', 'number', 'status', 'status_display', 'line_name',
            'warehouse_name', 'warehouse_address', 'eta', 'unload_date', 'cars_count',
            'photos', 'photos_count'
        ]

    def get_cars_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'container_cars' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['container_cars'])
        return obj.container_cars.count()

    def get_photos_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'photos' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['photos'])
        return obj.photos.filter(is_public=True).count()


class AIChatSerializer(serializers.ModelSerializer):
    """Сериализатор для чата с ИИ"""

    class Meta:
        model = AIChat
        fields = ['id', 'session_id', 'message', 'response', 'created_at',
                  'processing_time', 'was_helpful']
        read_only_fields = ['response', 'created_at', 'processing_time']


class NewsPostSerializer(serializers.ModelSerializer):
    """Сериализатор для новостей"""
    author_name = serializers.CharField(source='author.username', read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsPost
        fields = ['id', 'title', 'slug', 'content', 'excerpt', 'image',
                  'image_url', 'author_name', 'published_at', 'views']

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and hasattr(obj.image, 'url'):
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


class ContactMessageSerializer(serializers.ModelSerializer):
    """Сериализатор для сообщений обратной связи"""

    class Meta:
        model = ContactMessage
        fields = ['id', 'name', 'email', 'phone', 'subject', 'message', 'created_at']
        read_only_fields = ['created_at']


class TrackingRequestSerializer(serializers.ModelSerializer):
    """Сериализатор для запросов отслеживания"""
    car_info = ClientCarSerializer(source='car', read_only=True, allow_null=True)
    container_info = ClientContainerSerializer(source='container', read_only=True, allow_null=True)

    class Meta:
        model = TrackingRequest
        fields = ['id', 'tracking_number', 'email', 'car_info',
                  'container_info', 'created_at']
        read_only_fields = ['created_at', 'car_info', 'container_info']


