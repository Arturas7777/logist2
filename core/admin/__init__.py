# Import all admin modules to trigger registration
from core.admin.inlines import *  # noqa
from core.admin.container import *  # noqa
from core.admin.car import *  # noqa
from core.admin.partners import *  # noqa
from core.admin.email import *  # noqa
from core.admin.contacts import *  # noqa
from core.admin.tasks import *  # noqa

# Import website admin
from core.admin_website import (
    ClientUserAdmin, AIChatAdmin, NewsPostAdmin, ContactMessageAdmin, TrackingRequestAdmin
)

# Import billing admin (new system) — пакет core.admin.billing (H6b)
try:
    from core.admin.billing import (  # noqa: F401
        ExpenseCategoryAdmin,
        NewInvoiceAdmin,
        PersonalCardAdmin,
        PersonalTransferAdmin,
        TransactionAdmin,
    )
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not load new billing admin: {e}")
    logger.warning("Make sure core/admin/billing/ and models_billing.py exist")

# Import banking admin (Revolut и др.)
try:
    from core.admin_banking import BankConnectionAdmin, BankAccountAdmin, BankTransactionAdmin
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not load banking admin: {e}")

# Import accounting admin (site.pro / b1.lt)
try:
    from core.admin_accounting import SiteProConnectionAdmin, SiteProInvoiceSyncAdmin
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not load accounting admin: {e}")

# Scan processing (titles / dock receipts) — AI-обработка
try:
    from core.admin_scans import ScanProcessingJobAdmin  # noqa: F401
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not load scan processing admin: {e}")

# Invoice audit — managed via custom views at /admin/invoice-audit/
# No admin registration needed.
