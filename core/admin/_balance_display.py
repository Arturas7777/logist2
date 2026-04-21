"""Унифицированное отображение total_balance для контрагентов.

Используется в WarehouseAdmin, CompanyAdmin, LineAdmin, CarrierAdmin —
показывает итоговый баланс с учётом открытых FACT/PARDP и breakdown
в tooltip (касса/залог, «должны нам», «мы должны»).
"""
from decimal import Decimal

from django.utils.html import format_html


def render_total_balance(obj):
    """HTML-рендер total_balance контрагента для колонок admin-а.

    Зелёный = контрагент «в плюсе» (нам должны / у нас их залог).
    Красный = мы им должны.
    Серый  = всё сведено (0.00).

    Tooltip показывает разложение: касса/залог + открытые инвойсы.
    """
    try:
        balance = obj.balance or Decimal('0.00')
        total = obj.total_balance or Decimal('0.00')
        fact_debt = obj.open_fact_debt or Decimal('0.00')
        pardp_rec = obj.open_pardp_receivable or Decimal('0.00')

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
