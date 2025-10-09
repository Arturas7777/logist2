"""
URL маршруты для клиентского сайта
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views_website

# API Router
router = DefaultRouter()
router.register(r'cars', views_website.ClientCarViewSet, basename='client-car')
router.register(r'containers', views_website.ClientContainerViewSet, basename='client-container')
router.register(r'news', views_website.NewsViewSet, basename='news')
router.register(r'contact', views_website.ContactMessageViewSet, basename='contact')

app_name = 'website'

urlpatterns = [
    # ========== Информационные страницы ==========
    path('', views_website.website_home, name='home'),
    path('about/', views_website.about_page, name='about'),
    path('services/', views_website.services_page, name='services'),
    path('contact/', views_website.contact_page, name='contact'),
    
    # ========== Новости ==========
    path('news/', views_website.news_list, name='news_list'),
    path('news/<slug:slug>/', views_website.news_detail, name='news_detail'),
    
    # ========== Личный кабинет ==========
    path('dashboard/', views_website.client_dashboard, name='dashboard'),
    path('car/<int:car_id>/', views_website.car_detail, name='car_detail'),
    path('container/<int:container_id>/', views_website.container_detail, name='container_detail'),
    
    # ========== Скачивание фотографий ==========
    path('photo/car/<int:photo_id>/download/', views_website.download_car_photo, name='download_car_photo'),
    path('photo/container/<int:photo_id>/download/', views_website.download_container_photo, name='download_container_photo'),
    path('car/<int:car_id>/download-photos/', views_website.download_all_car_photos, name='download_all_car_photos'),
    
    # ========== API ==========
    path('api/', include(router.urls)),
    path('api/track/', views_website.track_shipment, name='track_shipment'),
    path('api/ai-chat/', views_website.ai_chat, name='ai_chat'),
    path('api/ai-chat/<int:chat_id>/feedback/', views_website.ai_chat_feedback, name='ai_chat_feedback'),
    path('api/ai-chat/history/', views_website.ai_chat_history, name='ai_chat_history'),
    
    # Container Photos API
    path('api/container-photos/<str:container_number>/', views_website.get_container_photos, name='get_container_photos'),
    path('api/download-photos-archive/', views_website.download_photos_archive, name='download_photos_archive'),
]

