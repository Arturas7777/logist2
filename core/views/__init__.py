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
    personal_cards_page,
    personal_card_add,
    personal_transfer,
    personal_card_expense,
    personal_card_income,
    personal_card_deactivate,
    personal_card_delete,
    personal_card_balance_reset,
)

from .comparison import (  # noqa: F401
    comparison_dashboard,
    compare_car_costs_api,
    compare_client_costs_api,
    compare_warehouse_costs_api,
    get_discrepancies_api,
)

from .health import (  # noqa: F401
    health,
    ready,
)

from .emails import (  # noqa: F401
    email_detail,
    email_attachment,
    email_mark_read,
    email_mark_container_read,
    email_mark_car_read,
    email_mark_autotransport_read,
    email_trigger_sync,
    email_reply_draft,
    email_reply_send,
    email_compose_send,
    email_groups_list,
    contacts_autocomplete,
    email_container_updates,
    email_car_updates,
    email_autotransport_updates,
)
