from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from core.views import car_list_api, get_invoice_total, get_container_data, register_payment, get_client_balance, company_dashboard, get_payment_objects, search_partners_api, get_warehouse_cars_api, get_invoice_cars_api, comparison_dashboard, compare_car_costs_api, compare_client_costs_api, compare_warehouse_costs_api, get_discrepancies_api, get_available_services, add_services
from core.routing import websocket_urlpatterns
from core.api import CarViewSet, InvoiceViewSet

router = DefaultRouter()
router.register(r'cars', CarViewSet, basename='car')
router.register(r'invoices', InvoiceViewSet, basename='invoice')

urlpatterns = [
    # ========== МЕЖДУНАРОДНАЯ ПОДДЕРЖКА ==========
    path('i18n/', include('django.conf.urls.i18n')),
    
    # ========== API ДЛЯ АДМИНКИ ==========
    path('api/cars/', car_list_api, name='car_list_api'),
    path('api/invoice-total/', get_invoice_total, name='get_invoice_total'),
    path('api/container/<int:container_id>/', get_container_data, name='get_container_data'),
    path('api/client-balance/', get_client_balance, name='get_client_balance'),
    path('admin/register-payment/', register_payment, name='register_payment'),
    path('company-dashboard/', company_dashboard, name='company_dashboard'),
    path('api/payment-objects/', get_payment_objects, name='get_payment_objects'),
    path('api/search-partners/', search_partners_api, name='search_partners_api'),
    path('api/warehouse-cars/', get_warehouse_cars_api, name='get_warehouse_cars_api'),
    path('api/invoice-cars/', get_invoice_cars_api, name='get_invoice_cars_api'),
    path('comparison-dashboard/', comparison_dashboard, name='comparison_dashboard'),
    path('api/compare-car-costs/', compare_car_costs_api, name='compare_car_costs_api'),
    path('api/compare-client-costs/', compare_client_costs_api, name='compare_client_costs_api'),
    path('api/compare-warehouse-costs/', compare_warehouse_costs_api, name='compare_warehouse_costs_api'),
    path('api/discrepancies/', get_discrepancies_api, name='get_discrepancies_api'),
    path('api/car/<int:car_id>/get_available_services/', get_available_services, name='get_available_services'),
    path('api/car/<int:car_id>/add_services/', add_services, name='add_services'),
    path('api/v1/', include(router.urls)),
    path('ws/', include(websocket_urlpatterns)),
]

# Добавляем интернационализированные URL паттерны
urlpatterns += i18n_patterns(
    # ========== КЛИЕНТСКИЙ САЙТ (главная страница) ==========
    path('', include('core.urls_website')),
    
    # ========== АДМИН ПАНЕЛЬ ==========
    path('admin/', admin.site.urls),
    prefix_default_language=False,
)

# Добавляем поддержку медиа файлов в режиме разработки
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)