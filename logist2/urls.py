from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from core.views import car_list_api, get_invoice_total, get_container_data, register_payment, get_client_balance, company_dashboard, get_payment_objects, search_partners_api, get_warehouse_cars_api, get_invoice_cars_api, comparison_dashboard, compare_car_costs_api, compare_client_costs_api, compare_warehouse_costs_api, get_discrepancies_api, get_available_services, add_services, get_warehouses, get_companies, add_cash_expense, add_cash_income, cash_wallet_reset, expense_analytics, upload_expense_receipt, personal_cards_page, personal_card_add, personal_transfer, personal_card_expense, personal_card_income, personal_card_deactivate, personal_card_delete, personal_card_balance_reset, health, ready
from core.routing import websocket_urlpatterns
from core.api import CarViewSet, InvoiceViewSet
from core.views_invoice_audit import (
    invoice_audit_list, invoice_audit_upload, invoice_audit_detail,
    invoice_audit_status, invoice_audit_delete, invoice_audit_reprocess,
    reconciliation_dashboard, reconciliation_fix_ths, reconciliation_mark_reviewed,
    manual_confirm_cost, reanalyze_newinvoice, newinvoice_audit_poll,
    supplier_cost_confirm, supplier_cost_confirm_all,
    supplier_cost_link, supplier_cost_car_services,
)

router = DefaultRouter()
router.register(r'cars', CarViewSet, basename='car')
router.register(r'invoices', InvoiceViewSet, basename='invoice')

# ============================================================================
# Версионированные API эндпоинты (api/v1/)
# ============================================================================
api_v1_patterns = [
    # DRF ViewSets (JSON API)
    path('', include(router.urls)),

    # Кастомные API эндпоинты
    path('cars/select/', car_list_api, name='car_list_api'),
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

# Legacy /api/ -> forwards to same views (deprecated, remove when all clients migrate)
api_legacy_patterns = api_v1_patterns

urlpatterns = [
    # ========== HEALTH / READINESS PROBES ==========
    path('health/', health, name='health'),
    path('ready/', ready, name='ready'),

    # ========== МЕЖДУНАРОДНАЯ ПОДДЕРЖКА ==========
    path('i18n/', include('django.conf.urls.i18n')),

    # ========== API v1 (основной) ==========
    path('api/v1/', include(api_v1_patterns)),

    # ========== API legacy (обратная совместимость, deprecated) ==========
    path('api/', include(api_legacy_patterns)),

    # ========== АДМИН-СПЕЦИФИЧНЫЕ ЭНДПОИНТЫ ==========
    path('admin/register-payment/', register_payment, name='register_payment'),
    path('admin/dashboard/', company_dashboard, name='company_dashboard'),
    path('admin/cash-expense/', add_cash_expense, name='add_cash_expense'),
    path('admin/cash-income/', add_cash_income, name='add_cash_income'),
    path('admin/cash-wallet/reset/', cash_wallet_reset, name='cash_wallet_reset'),
    path('admin/expense-analytics/', expense_analytics, name='expense_analytics'),
    path('admin/expense-receipt/<int:tx_id>/', upload_expense_receipt, name='upload_expense_receipt'),
    path('admin/personal-cards/', personal_cards_page, name='personal_cards_page'),
    path('admin/personal-cards/add/', personal_card_add, name='personal_card_add'),
    path('admin/personal-cards/<int:card_id>/edit/', personal_card_add, name='personal_card_edit'),
    path('admin/personal-cards/<int:card_id>/deactivate/', personal_card_deactivate, name='personal_card_deactivate'),
    path('admin/personal-cards/<int:card_id>/delete/', personal_card_delete, name='personal_card_delete'),
    path('admin/personal-transfer/', personal_transfer, name='personal_transfer'),
    path('admin/personal-card-expense/', personal_card_expense, name='personal_card_expense'),
    path('admin/personal-card-income/', personal_card_income, name='personal_card_income'),
    path('admin/personal-cards/<int:card_id>/balance-reset/', personal_card_balance_reset, name='personal_card_balance_reset'),
    path('comparison-dashboard/', comparison_dashboard, name='comparison_dashboard'),

    # ── Проверка счетов ──────────────────────────────────────────────────────
    path('admin/invoice-audit/', invoice_audit_list,   name='invoice_audit_list'),
    path('admin/invoice-audit/upload/', invoice_audit_upload,  name='invoice_audit_upload'),
    path('admin/invoice-audit/<int:pk>/', invoice_audit_detail, name='invoice_audit_detail'),
    path('admin/invoice-audit/<int:pk>/status/', invoice_audit_status,  name='invoice_audit_status'),
    path('admin/invoice-audit/<int:pk>/delete/', invoice_audit_delete,  name='invoice_audit_delete'),
    path('admin/invoice-audit/<int:pk>/reprocess/', invoice_audit_reprocess, name='invoice_audit_reprocess'),

    # ── Сверка счетов ─────────────────────────────────────────────────────────
    path('admin/reconciliation/', reconciliation_dashboard, name='reconciliation_dashboard'),
    path('admin/reconciliation/fix-ths/', reconciliation_fix_ths, name='reconciliation_fix_ths'),
    path('admin/reconciliation/mark-reviewed/', reconciliation_mark_reviewed, name='reconciliation_mark_reviewed'),
    path('admin/reconciliation/manual-confirm-cost/', manual_confirm_cost, name='manual_confirm_cost'),
    path('admin/reconciliation/supplier-cost/confirm/', supplier_cost_confirm, name='supplier_cost_confirm'),
    path('admin/reconciliation/supplier-cost/confirm-all/', supplier_cost_confirm_all, name='supplier_cost_confirm_all'),
    path('admin/reconciliation/supplier-cost/link/', supplier_cost_link, name='supplier_cost_link'),
    path('admin/reconciliation/supplier-cost/<int:sc_id>/services/', supplier_cost_car_services, name='supplier_cost_car_services'),
    path('admin/newinvoice/<int:pk>/reanalyze/', reanalyze_newinvoice, name='reanalyze_newinvoice'),
    path('admin/newinvoice/<int:pk>/audit-poll/', newinvoice_audit_poll, name='newinvoice_audit_poll'),

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

# Медиа-файлы обслуживаются Django только в DEBUG-режиме.
# В production используйте Nginx для отдачи /media/.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
