"""
Reusable model mixins.

These mixins provide shared behaviour without defining database fields,
so they can be added to existing models without generating migrations.
"""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


# ============================================================================
# СТАТУСЫ ИНВОЙСОВ — единый источник правды
# ============================================================================
# Раньше эти кортежи дублировались в виде хардкодных списков ['ISSUED',
# 'PARTIALLY_PAID', ...] в десятках мест (admin, services, tasks, managers,
# management commands). Это приводило к тому, что добавление нового статуса
# или переименование требовало точечной правки по проекту, и где-то его всё
# равно забывали обновить (например, `LINKED_PAID` отсутствует во всех
# фильтрах ниже намеренно — он считается «оплачен», но через связку).

# Открытые: счёт выставлен, но ещё не оплачен полностью. Используется в
# балансовых property `open_fact_debt` / `open_pardp_receivable`, в
# dashboard'е, в auto-reconciliation.
OPEN_INVOICE_STATUSES = ('ISSUED', 'OVERDUE', 'PARTIALLY_PAID')

# Активные: открытые + PAID. Используется в SupplierCost-аналитике
# (`core/models.py` ~1385) и в admin-фильтрах, где нужно отделить «реальные»
# инвойсы от черновиков и отменённых.
ACTIVE_INVOICE_STATUSES = (*OPEN_INVOICE_STATUSES, 'PAID')

# Кандидаты на перевод в OVERDUE: статусы, где due_date вообще имеет смысл.
# Используется в `check_overdue_invoices` Celery-задаче и в dashboard'е.
OVERDUE_CANDIDATE_STATUSES = ('ISSUED', 'PARTIALLY_PAID')

# Статусы, при которых имеет смысл регенерировать позиции из CarService
# (после изменения цены/услуги машины). PAID — нет (уже оплачен,
# регенерация нарушит баланс), CANCELLED/LINKED_PAID — тоже нет.
REGENERATABLE_INVOICE_STATUSES = ('DRAFT',) + OPEN_INVOICE_STATUSES

# Обратная совместимость для уже импортированного приватного имени.
_OPEN_INVOICE_STATUSES = OPEN_INVOICE_STATUSES


class BalanceMethodsMixin:
    """Balance helpers for entities that can act as invoice issuer/recipient.

    Provides:
        * ``balance`` (DB field on concrete model) — чистое сальдо Tx без инвойсов (залоги/авансы/возвраты);
        * ``open_fact_debt`` — сколько мы должны этому контрагенту по открытым FACT (они issuer, мы recipient);
        * ``open_pardp_receivable`` — сколько этот контрагент должен нам по открытым PARDP (мы issuer, они recipient);
        * ``total_balance`` — итог с учётом открытых инвойсов:
            ``total_balance = balance + open_pardp_receivable − open_fact_debt``
            **+ = контрагент нам должен / у нас его залог; − = мы ему должны.**

    Не объявляет полей БД — они остаются на конкретных моделях.
    """

    def _has_invoice_field(self, field_name):
        from core.models_billing import NewInvoice
        try:
            NewInvoice._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def get_balance_breakdown(self):
        from django.db.models import Case, DecimalField, Q, Sum, Value, When
        from django.db.models.functions import Coalesce

        model_name = self.__class__.__name__.lower()
        from core.models_billing import Transaction

        incoming_filter = Q(**{f'to_{model_name}': self})
        outgoing_filter = Q(**{f'from_{model_name}': self})
        zero = Value(Decimal('0.00'), output_field=DecimalField(max_digits=15, decimal_places=2))

        rows = (
            Transaction.objects
            .filter((incoming_filter | outgoing_filter), status='COMPLETED')
            .values('method')
            .annotate(
                incoming=Coalesce(
                    Sum(Case(When(incoming_filter, then='amount'))),
                    zero,
                    output_field=DecimalField(max_digits=15, decimal_places=2),
                ),
                outgoing=Coalesce(
                    Sum(Case(When(outgoing_filter, then='amount'))),
                    zero,
                    output_field=DecimalField(max_digits=15, decimal_places=2),
                ),
            )
        )

        breakdown = {m.lower(): Decimal('0.00') for m in ('CASH', 'CARD', 'TRANSFER')}
        for row in rows:
            key = row['method'].lower()
            if key in breakdown:
                breakdown[key] = row['incoming'] - row['outgoing']

        breakdown['total'] = self.balance
        return breakdown

    @property
    def open_fact_debt(self):
        """Сумма открытых FACT, выписанных этим контрагентом на нас. Мы им должны."""
        from django.db.models import F, Sum

        from core.models_billing import NewInvoice

        model_name = self.__class__.__name__.lower()
        field = f'issuer_{model_name}'
        if not self._has_invoice_field(field):
            return Decimal('0.00')
        total = NewInvoice.objects.filter(
            **{field: self},
            document_type='INVOICE_FACT',
            status__in=_OPEN_INVOICE_STATUSES,
        ).aggregate(s=Sum(F('total') - F('paid_amount')))['s'] or Decimal('0.00')
        return total

    @property
    def open_pardp_receivable(self):
        """Сумма открытых PARDP, выставленных нами этому контрагенту. Они нам должны."""
        from django.db.models import F, Sum

        from core.models_billing import NewInvoice

        model_name = self.__class__.__name__.lower()
        field = f'recipient_{model_name}'
        if not self._has_invoice_field(field):
            return Decimal('0.00')
        total = NewInvoice.objects.filter(
            **{field: self},
            document_type='INVOICE',
            status__in=_OPEN_INVOICE_STATUSES,
        ).aggregate(s=Sum(F('total') - F('paid_amount')))['s'] or Decimal('0.00')
        return total

    @property
    def total_balance(self):
        """Итоговый баланс с учётом открытых инвойсов.

        + = контрагент «в плюсе» с нашей точки зрения (нам должны / у нас их залог);
        − = мы должны контрагенту (дебет);
        0  = всё сведено.
        """
        return (self.balance or Decimal('0.00')) + self.open_pardp_receivable - self.open_fact_debt

    @property
    def total_balance_status(self):
        """Статус total_balance («нам должны» / «мы должны» / «баланс»)."""
        tb = self.total_balance
        if tb > 0:
            return 'НАМ ДОЛЖНЫ'
        elif tb < 0:
            return 'МЫ ДОЛЖНЫ'
        return 'БАЛАНС'

    @property
    def total_balance_color(self):
        tb = self.total_balance
        if tb > 0:
            return '#28a745'
        elif tb < 0:
            return '#dc3545'
        return '#6c757d'

    def get_balance_info(self):
        balance = self.balance
        if balance > 0:
            status, color = 'ПЕРЕПЛАТА', '#28a745'
            description = f'Переплата {balance:.2f}'
        elif balance < 0:
            status, color = 'ДОЛГ', '#dc3545'
            description = f'Долг {abs(balance):.2f}'
        else:
            status, color = 'БАЛАНС', '#6c757d'
            description = 'Баланс нулевой'

        return {
            'balance': balance,
            'status': status,
            'color': color,
            'description': description,
            'breakdown': self.get_balance_breakdown(),
            'open_fact_debt': self.open_fact_debt,
            'open_pardp_receivable': self.open_pardp_receivable,
            'total_balance': self.total_balance,
            'total_balance_status': self.total_balance_status,
            'total_balance_color': self.total_balance_color,
        }
