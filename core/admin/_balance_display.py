"""Унифицированное отображение total_balance для контрагентов.

Используется в WarehouseAdmin, CompanyAdmin, LineAdmin, CarrierAdmin —
показывает итоговый баланс с учётом открытых FACT/PARDP и breakdown
в tooltip (касса/залог, «должны нам», «мы должны»).

OPTIMIZATION: когда на queryset навешены аннотации
``_ann_fact_debt``, ``_ann_pardp_rec``, ``_ann_total_balance``
(см. ``annotate_partner_balance``), рендер использует их вместо
property-вызовов, убирая N+1.
"""
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.html import format_html

_OPEN_STATUSES = ('ISSUED', 'OVERDUE', 'PARTIALLY_PAID')


def annotate_partner_balance(qs, model_name):
    """Add ``_ann_fact_debt``, ``_ann_pardp_rec``, ``_ann_total_balance``
    annotations to a partner queryset.

    ``model_name`` — lowercase model class name (warehouse / company / line / carrier).
    """
    from core.models_billing import NewInvoice

    dec = DecimalField(max_digits=15, decimal_places=2)
    zero = Value(Decimal('0'), output_field=dec)

    fact_sq = (
        NewInvoice.objects
        .filter(**{f'issuer_{model_name}': OuterRef('pk')},
                document_type='INVOICE_FACT',
                status__in=_OPEN_STATUSES)
        .values(f'issuer_{model_name}')
        .annotate(s=Sum(F('total') - F('paid_amount')))
        .values('s')[:1]
    )
    pardp_sq = (
        NewInvoice.objects
        .filter(**{f'recipient_{model_name}': OuterRef('pk')},
                document_type='INVOICE',
                status__in=_OPEN_STATUSES)
        .values(f'recipient_{model_name}')
        .annotate(s=Sum(F('total') - F('paid_amount')))
        .values('s')[:1]
    )

    return (
        qs
        .annotate(
            _ann_fact_debt=Coalesce(Subquery(fact_sq, output_field=dec), zero),
            _ann_pardp_rec=Coalesce(Subquery(pardp_sq, output_field=dec), zero),
        )
        .annotate(
            _ann_total_balance=F('balance') + F('_ann_pardp_rec') - F('_ann_fact_debt'),
        )
    )


def render_total_balance(obj):
    """HTML-рендер total_balance контрагента для колонок admin-а.

    Зелёный = контрагент «в плюсе» (нам должны / у нас их залог).
    Красный = мы им должны.
    Серый  = всё сведено (0.00).

    Tooltip показывает разложение: касса/залог + открытые инвойсы.
    """
    try:
        balance = obj.balance or Decimal('0.00')

        total = getattr(obj, '_ann_total_balance', None)
        fact_debt = getattr(obj, '_ann_fact_debt', None)
        pardp_rec = getattr(obj, '_ann_pardp_rec', None)

        if total is None:
            total = obj.total_balance or Decimal('0.00')
            fact_debt = obj.open_fact_debt or Decimal('0.00')
            pardp_rec = obj.open_pardp_receivable or Decimal('0.00')
        else:
            fact_debt = fact_debt or Decimal('0.00')
            pardp_rec = pardp_rec or Decimal('0.00')

        if total > 0:
            color = '#28a745'
        elif total < 0:
            color = '#dc3545'
        else:
            color = '#6c757d'

        parts = []
        if balance:
            label = 'Касса/залог' if obj.__class__.__name__ == 'Company' else 'Залог/аванс'
            parts.append(f'{label}: {balance:+.2f}')
        if pardp_rec:
            parts.append(f'Должны нам (PARDP): +{pardp_rec:.2f}')
        if fact_debt:
            parts.append(f'Мы должны (FACT): -{fact_debt:.2f}')
        tooltip = ' | '.join(parts) if parts else 'Всё сведено, 0.00'

        return format_html(
            '<span style="color:{}; font-weight:bold;" title="{}">{:+.2f}</span>',
            color, tooltip, float(total),
        )
    except Exception:
        return '-'
