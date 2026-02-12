from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from core.views import car_list_api, get_invoice_total, get_container_data, register_payment, get_client_balance, company_dashboard, get_payment_objects, search_partners_api, get_warehouse_cars_api, get_invoice_cars_api, comparison_dashboard, compare_car_costs_api, compare_client_costs_api, compare_warehouse_costs_api, get_discrepancies_api, get_available_services, add_services, get_warehouses, get_companies
from core.routing import websocket_urlpatterns
from core.api import CarViewSet, InvoiceViewSet

router = DefaultRouter()
router.register(r'cars', CarViewSet, basename='car')
router.register(r'invoices', InvoiceViewSet, basename='invoice')

# ============================================================================
# Версионированные API эндпоинты (api/v1/)
# ============================================================================
api_v1_patterns = [
    # DRF ViewSets
    path('', include(router.urls)),
    
    # Кастомные API эндпоинты
    path('cars/', car_list_api, name='car_list_api'),
    path('invoice-total/', get_invoice_total, name='get_invoice_total'),
    path('container/<int:container_id>/', get_container_data, name='get_container_data'),
    path('client-balance/', get_client_balance, name='get_client_balance'),
    path('payment-objects/', get_payment_objects, name='get_payment_objects'),
    path('search-partners/', search_partners_api, name='search_partners_api'),
    path('warehouse-cars/', get_warehouse_cars_api, name='get_warehouse_cars_api'),
    path('invoice-cars/', get_invoice_cars_api, name='get_invoice_cars_api'),
    path('compare-car-costs/', compare_car_costs_api, name='compare_car_costs_api'),
    path('compare-client-costs/', compare_client_costs_api, name='compare_client_costs_api'),
    path('compare-warehouse-costs/', compare_warehouse_costs_api, name='compare_warehouse_costs_api'),
    path('discrepancies/', get_discrepancies_api, name='get_discrepancies_api'),
    path('car/<int:car_id>/get_available_services/', get_available_services, name='get_available_services'),
    path('car/<int:car_id>/add_services/', add_services, name='add_services'),
    path('warehouses/', get_warehouses, name='get_warehouses'),
    path('companies/', get_companies, name='get_companies'),
]

# ============================================================================
# Обратная совместимость: старые /api/ пути (deprecated, удалить в будущем)
# ============================================================================
api_legacy_patterns = [
    path('cars/', car_list_api, name='car_list_api_legacy'),
    path('invoice-total/', get_invoice_total, name='get_invoice_total_legacy'),
    path('container/<int:container_id>/', get_container_data, name='get_container_data_legacy'),
    path('client-balance/', get_client_balance, name='get_client_balance_legacy'),
    path('payment-objects/', get_payment_objects, name='get_payment_objects_legacy'),
    path('search-partners/', search_partners_api, name='search_partners_api_legacy'),
    path('warehouse-cars/', get_warehouse_cars_api, name='get_warehouse_cars_api_legacy'),
    path('invoice-cars/', get_invoice_cars_api, name='get_invoice_cars_api_legacy'),
    path('compare-car-costs/', compare_car_costs_api, name='compare_car_costs_api_legacy'),
    path('compare-client-costs/', compare_client_costs_api, name='compare_client_costs_api_legacy'),
    path('compare-warehouse-costs/', compare_warehouse_costs_api, name='compare_warehouse_costs_api_legacy'),
    path('discrepancies/', get_discrepancies_api, name='get_discrepancies_api_legacy'),
    path('car/<int:car_id>/get_available_services/', get_available_services, name='get_available_services_legacy'),
    path('car/<int:car_id>/add_services/', add_services, name='add_services_legacy'),
    path('warehouses/', get_warehouses, name='get_warehouses_legacy'),
    path('companies/', get_companies, name='get_companies_legacy'),
]

urlpatterns = [
    # ========== МЕЖДУНАРОДНАЯ ПОДДЕРЖКА ==========
    path('i18n/', include('django.conf.urls.i18n')),
    
    # ========== API v1 (основной) ==========
    path('api/v1/', include(api_v1_patterns)),
    
    # ========== API legacy (обратная совместимость, deprecated) ==========
    path('api/', include(api_legacy_patterns)),
    
    # ========== АДМИН-СПЕЦИФИЧНЫЕ ЭНДПОИНТЫ ==========
    path('admin/register-payment/', register_payment, name='register_payment'),
    path('admin/dashboard/', company_dashboard, name='company_dashboard'),
    path('comparison-dashboard/', comparison_dashboard, name='comparison_dashboard'),
    
    path('ws/', include(websocket_urlpatterns)),
]

# Добавляем URL без языковых префиксов (язык определяется через cookie)
urlpatterns += [
    # ========== КЛИЕНТСКИЙ САЙТ (главная страница) ==========
    path('', include('core.urls_website')),
    
    # ========== CORE APP URLS ==========
    path('core/', include('core.urls')),
    
    # ========== АДМИН ПАНЕЛЬ ==========
    path('admin/', admin.site.urls),
]

# Добавляем поддержку медиа файлов в режиме разработки
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # В продакшне также добавляем обработку медиа файлов для админки
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)