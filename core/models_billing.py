"""Реэкспорт: модуль перенесён в ``core/models/billing.py`` (A1, AUDIT_ROUND3).

Старый путь ``core.models_billing`` сохранён для обратной совместимости.
Новый код должен импортировать из ``core.models.billing`` (или ``core.models``).
"""

from core.models.billing import *  # noqa: F403
from core.models.billing import ExpenseCategory, InvoiceItem, NewInvoice, Transaction  # noqa: F401
