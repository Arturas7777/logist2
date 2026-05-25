"""Пакет ``core.admin.billing`` — админка биллинга разнесена по модулям.

Раньше всё лежало в одном файле ``core/admin_billing.py`` (~1860 строк).
В рамках H6b (см. ``docs/ROADMAP_2026-05_high_medium.md``) он распилен
на отдельные подмодули по ответственности.

Подмодули:

* :mod:`.expense_category`  — :class:`ExpenseCategoryAdmin`.
* :mod:`.filters`           — :class:`InvoiceDirectionFilter`.
* :mod:`.inlines`           — :class:`InvoiceItemInline`.
* :mod:`.invoice_display`   — :class:`NewInvoiceDisplayMixin`
  (колонки и readonly).
* :mod:`.invoice_forms`     — :class:`NewInvoiceFormHandlerMixin`
  (``add_view``/``save_*`` lifecycle, обработчик кастомной формы).
* :mod:`.invoice_actions`   — :class:`NewInvoiceActionsMixin`
  (массовые admin actions).
* :mod:`.invoice_urls`      — :class:`NewInvoiceUrlsMixin`
  (кастомные URL: оплата, расчёт суммы).
* :mod:`.invoice`           — :class:`NewInvoiceAdmin` (сборка из миксинов).
* :mod:`.transaction`       — :class:`TransactionAdmin`.
* :mod:`.personal`          — :class:`PersonalCardAdmin`,
  :class:`PersonalTransferAdmin`.

Импорт подмодулей ниже регистрирует все ``@admin.register(...)``-декораторы
в ``django.contrib.admin.site`` — порядок важен только тем, что ``invoice``
обязан импортироваться после своих миксинов (это гарантирует Python: имя
``invoice`` ссылается на остальные модули пакета).
"""

from .expense_category import ExpenseCategoryAdmin
from .filters import InvoiceDirectionFilter
from .inlines import InvoiceItemInline
from .invoice import NewInvoiceAdmin
from .personal import PersonalCardAdmin, PersonalTransferAdmin
from .transaction import TransactionAdmin

__all__ = [
    'ExpenseCategoryAdmin',
    'InvoiceDirectionFilter',
    'InvoiceItemInline',
    'NewInvoiceAdmin',
    'PersonalCardAdmin',
    'PersonalTransferAdmin',
    'TransactionAdmin',
]
