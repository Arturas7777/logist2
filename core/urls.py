from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('car/<int:car_id>/get_available_services/', views.get_available_services, name='get_available_services'),
    path('car/<int:car_id>/add_services/', views.add_services, name='add_services'),
    path('warehouses/', views.get_warehouses, name='get_warehouses'),
    path('container/<int:container_id>/sync-gdrive-photos/', views.sync_container_photos_from_gdrive, name='sync_container_photos_gdrive'),
    path('container/<int:container_id>/photos-json/', views.get_container_photos_json, name='get_container_photos_json'),
    # API для автокомплита в инвойсах
    path('api/search-counterparties/', views.search_counterparties, name='search_counterparties'),
    path('api/search-cars/', views.search_cars, name='search_cars'),
]

