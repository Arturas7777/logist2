from django.contrib import admin
from django.urls import path, include
from core.views import car_list_api, get_invoice_total, get_container_data, register_payment, get_client_balance
from core.routing import websocket_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/cars/', car_list_api, name='car_list_api'),
    path('api/invoice-total/', get_invoice_total, name='get_invoice_total'),
    path('api/container/<int:container_id>/', get_container_data, name='get_container_data'),
    path('api/client-balance/', get_client_balance, name='get_client_balance'),
    path('admin/register-payment/', register_payment, name='register_payment'),
    path('ws/', include(websocket_urlpatterns)),
]