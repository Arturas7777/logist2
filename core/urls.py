from django.urls import path
from . import views
from . import views_autotransport

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
    # API для автовозов
    path('api/carrier/<int:carrier_id>/info/', views_autotransport.get_carrier_info, name='get_carrier_info'),
    path('api/driver/<int:driver_id>/phone/', views_autotransport.get_driver_phone, name='get_driver_phone'),
    path('api/driver/update-phone/', views_autotransport.update_driver_phone, name='update_driver_phone'),
    path('api/border-crossings/', views_autotransport.get_border_crossings, name='get_border_crossings'),
    path('api/carrier/create-truck/', views_autotransport.create_carrier_truck, name='create_carrier_truck'),
    path('api/carrier/create-driver/', views_autotransport.create_carrier_driver, name='create_carrier_driver'),
]

