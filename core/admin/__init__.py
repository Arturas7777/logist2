# Import all admin modules to trigger registration
from core.admin.inlines import *  # noqa
from core.admin.container import *  # noqa
from core.admin.car import *  # noqa
from core.admin.partners import *  # noqa

# Import website admin
from core.admin_website import (
    ClientUserAdmin, AIChatAdmin, NewsPostAdmin, ContactMessageAdmin, TrackingRequestAdmin
)

# Import billing admin (new system)
try:
    from core.admin_billing import NewInvoiceAdmin, TransactionAdmin
except ImportError as e:
    import logging
    logger = logging.getLogger('django')
    logger.warning(f"Could not load new billing admin: {e}")
    logger.warning("Make sure admin_billing.py and models_billing.py exist")

# Import banking admin (Revolut и др.)
try:
    from core.admin_banking import BankConnectionAdmin, BankAccountAdmin, BankTransactionAdmin
except ImportError as e:
    import logging
    logger = logging.getLogger('django')
    logger.warning(f"Could not load banking admin: {e}")
