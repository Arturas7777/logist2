from django.contrib import admin
from django.urls import path, include
from core.views import car_list_api, get_invoice_total  # Импорт из views.py
from core.routing import websocket_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/cars/', car_list_api, name='car_list_api'),
    path('api/invoice-total/', get_invoice_total, name='get_invoice_total'),  # Новый маршрут
    path('ws/', include(websocket_urlpatterns)),
]