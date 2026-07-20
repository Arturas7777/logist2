"""
Backward-compatible re-exports.

All views were split into submodules:
  - api.py          : JSON API endpoints
  - admin_views.py  : Staff dashboards, payments, photos
  - comparison.py   : Cost comparison dashboard & APIs
  - emails.py       : Email/Gmail UI views
  - health.py       : healthcheck endpoints
  - labels.py       : printable labels

TODO (структурная унификация P2 #15): постепенно перенести оставшиеся
файлы ``core/views_invoice_audit.py``, ``core/views_website.py``,
``core/views_autotransport.py`` в подмодули этого пакета. Сейчас
они импортируются напрямую из их прежних путей (logist2/urls.py),
поэтому ``__init__.py`` их не реэкспортирует. После переноса
обновить ``logist2/urls.py``.
"""

from .admin_views import (  # noqa: F401
    add_cash_expense,
    add_cash_income,
    cash_wallet_reset,
    company_dashboard,
    expense_analytics,
    get_container_photos_json,
    personal_card_add,
    personal_card_balance_reset,
    personal_card_deactivate,
    personal_card_delete,
    personal_card_expense,
    personal_card_income,
    personal_cards_page,
    personal_transfer,
    register_payment,
    sync_container_photos_from_gdrive,
    upload_expense_receipt,
)
from .api import (  # noqa: F401
    add_services,
    car_list_api,
    get_available_services,
    get_client_balance,
    get_companies,
    get_container_data,
    get_invoice_cars_api,
    get_invoice_total,
    get_payment_objects,
    get_warehouse_cars_api,
    get_warehouses,
    search_cars,
    search_counterparties,
    search_invoices,
    search_partners_api,
)
from .comparison import (  # noqa: F401
    compare_car_costs_api,
    compare_client_costs_api,
    compare_warehouse_costs_api,
    comparison_dashboard,
    get_discrepancies_api,
)
from .emails import (  # noqa: F401
    contacts_autocomplete,
    email_attachment,
    email_autotransport_updates,
    email_car_updates,
    email_compose_send,
    email_container_updates,
    email_detail,
    email_groups_list,
    email_mark_autotransport_read,
    email_mark_car_read,
    email_mark_container_read,
    email_mark_read,
    email_reply_draft,
    email_reply_send,
    email_set_needs_reply,
    email_trigger_sync,
)
from .global_search import (  # noqa: F401
    global_search,
)
from .health import (  # noqa: F401
    health,
    ready,
)
from .system_monitor import (  # noqa: F401
    system_monitor_history,
    system_monitor_page,
    system_monitor_snapshot,
)
