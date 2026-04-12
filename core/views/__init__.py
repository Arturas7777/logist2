"""
Backward-compatible re-exports.

All views were split into submodules:
  - api.py          : JSON API endpoints
  - admin_views.py  : Staff dashboards, payments, photos
  - comparison.py   : Cost comparison dashboard & APIs
"""
from .api import (  # noqa: F401
    car_list_api,
    get_invoice_total,
    get_container_data,
    get_client_balance,
    get_payment_objects,
    search_partners_api,
    get_invoice_cars_api,
    get_warehouse_cars_api,
    get_available_services,
    add_services,
    get_warehouses,
    get_companies,
    search_counterparties,
    search_cars,
    search_invoices,
)

from .admin_views import (  # noqa: F401
    register_payment,
    company_dashboard,
    get_container_photos_json,
    sync_container_photos_from_gdrive,
    add_cash_expense,
    add_cash_income,
    cash_wallet_reset,
    expense_analytics,
    upload_expense_receipt,
)

from .comparison import (  # noqa: F401
    comparison_dashboard,
    compare_car_costs_api,
    compare_client_costs_api,
    compare_warehouse_costs_api,
    get_discrepancies_api,
)
