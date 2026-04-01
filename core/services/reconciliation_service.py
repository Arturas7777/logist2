"""
ReconciliationService
=====================
Расчёт прибыли per car/container, статус подтверждённости затрат, генерация подсказок.
Работает на основе SupplierCost (фактические затраты) и CarService (выручка).
"""
from decimal import Decimal
from collections import defaultdict
from django.db.models import Sum, Exists, OuterRef, Subquery, Count, Q

from core.models import Car, CarService
from core.models_invoice_audit import SupplierCost


def get_cost_confirmation_status(car_id):
    """
    Для одной машины: какие CarService подтверждены инвойсом/вручную, какие нет.
    Return: {
        'total_services': int,
        'confirmed_services': int,
        'unconfirmed_services': int,
        'status': 'full' | 'partial' | 'none',
        'confirmed': [{'service_name', 'service_type', 'amount', 'source'}],
        'unconfirmed': [{'service_name', 'service_type', 'service_id', 'client_price'}],
    }
    """
    services = CarService.objects.filter(car_id=car_id)
    total = services.count()
    if total == 0:
        return {
            'total_services': 0, 'confirmed_services': 0,
            'unconfirmed_services': 0, 'status': 'none',
            'confirmed': [], 'unconfirmed': [],
        }

    confirmed_ids = set(
        SupplierCost.objects.filter(
            car_id=car_id, car_service__isnull=False
        ).values_list('car_service_id', flat=True).distinct()
    )

    confirmed_list = []
    unconfirmed_list = []

    for svc in services:
        if svc.pk in confirmed_ids:
            costs = SupplierCost.objects.filter(car_service=svc)
            total_cost = costs.aggregate(t=Sum('amount'))['t'] or 0
            sources = list(costs.values_list('source', flat=True).distinct())
            confirmed_list.append({
                'car_service_id': svc.pk,
                'service_name':   svc.get_service_name(),
                'service_type':   svc.service_type,
                'actual_cost':    float(total_cost),
                'client_price':   float(svc.final_price),
                'sources':        sources,
            })
        else:
            unconfirmed_list.append({
                'car_service_id': svc.pk,
                'service_name':   svc.get_service_name(),
                'service_type':   svc.service_type,
                'service_id':     svc.service_id,
                'client_price':   float(svc.final_price),
            })

    confirmed_count = len(confirmed_list)
    if confirmed_count == total:
        status = 'full'
    elif confirmed_count > 0:
        status = 'partial'
    else:
        status = 'none'

    return {
        'total_services':      total,
        'confirmed_services':  confirmed_count,
        'unconfirmed_services': total - confirmed_count,
        'status':              status,
        'confirmed':           confirmed_list,
        'unconfirmed':         unconfirmed_list,
    }


def get_car_profitability(car_ids=None, container_ids=None, audit_ids=None):
    """
    Возвращает список dict-ов с расчётом прибыли per car.
    Включает статус подтверждённости затрат.
    """
    cost_qs = SupplierCost.objects.filter(car__isnull=False)
    if audit_ids:
        cost_qs = cost_qs.filter(audit_id__in=audit_ids)

    cost_by_car = defaultdict(lambda: {'total': Decimal('0'), 'breakdown': defaultdict(Decimal)})
    for sc in cost_qs.values('car_id', 'service_type').annotate(total=Sum('amount')):
        cid = sc['car_id']
        cost_by_car[cid]['total'] += sc['total']
        cost_by_car[cid]['breakdown'][sc['service_type']] += sc['total']

    if car_ids or container_ids:
        target_car_ids = None
        if car_ids:
            target_car_ids = set(car_ids)
        if container_ids:
            container_car_set = set(
                Car.objects.filter(container_id__in=container_ids).values_list('id', flat=True)
            )
            target_car_ids = container_car_set if target_car_ids is None else (target_car_ids & container_car_set)
    else:
        target_car_ids = set(cost_by_car.keys())

    if not target_car_ids:
        return []

    cars = Car.objects.filter(id__in=target_car_ids).select_related('client', 'container')

    # Batch: count total CarService and confirmed CarService per car
    total_svc_by_car = dict(
        CarService.objects.filter(car_id__in=target_car_ids)
        .values('car_id').annotate(cnt=Count('id')).values_list('car_id', 'cnt')
    )
    confirmed_svc_by_car = dict(
        SupplierCost.objects.filter(
            car_id__in=target_car_ids, car_service__isnull=False
        ).values('car_id').annotate(cnt=Count('car_service_id', distinct=True))
        .values_list('car_id', 'cnt')
    )
    # Unlinked SupplierCost per car (car_service=None, needs review)
    unlinked_by_car = dict(
        SupplierCost.objects.filter(
            car_id__in=target_car_ids, car_service__isnull=True
        ).values('car_id').annotate(cnt=Count('id')).values_list('car_id', 'cnt')
    )

    result = []
    for car in cars:
        costs = cost_by_car.get(car.pk, {'total': Decimal('0'), 'breakdown': {}})
        total_cost    = costs['total']
        total_revenue = car.total_price or Decimal('0')
        profit        = total_revenue - total_cost
        margin_pct    = (profit / total_revenue * 100) if total_revenue > 0 else Decimal('0')

        total_svc     = total_svc_by_car.get(car.pk, 0)
        confirmed_svc = confirmed_svc_by_car.get(car.pk, 0)
        unlinked      = unlinked_by_car.get(car.pk, 0)

        if total_svc == 0:
            cost_status = 'none'
        elif confirmed_svc >= total_svc:
            cost_status = 'full'
        elif confirmed_svc > 0:
            cost_status = 'partial'
        else:
            cost_status = 'none'

        is_final = cost_status == 'full' and unlinked == 0

        result.append({
            'car_id':           car.pk,
            'vin':              car.vin,
            'brand':            car.brand or '',
            'vehicle_type':     car.get_vehicle_type_display() if hasattr(car, 'get_vehicle_type_display') else car.vehicle_type,
            'client_name':      str(car.client) if car.client else '—',
            'client_id':        car.client_id,
            'container_number': car.container.number if car.container else '—',
            'container_id':     car.container_id,
            'total_cost':       float(total_cost),
            'total_revenue':    float(total_revenue),
            'profit':           float(profit),
            'margin_pct':       round(float(margin_pct), 1),
            'cost_breakdown':   dict(costs['breakdown']),
            'has_costs':        total_cost > 0,
            'cost_status':      cost_status,
            'total_services':   total_svc,
            'confirmed_services': confirmed_svc,
            'unlinked_costs':   unlinked,
            'is_final':         is_final,
        })

    result.sort(key=lambda x: x['profit'])
    return result


def get_container_profitability(audit_ids=None):
    """Группирует get_car_profitability по контейнерам."""
    cars = get_car_profitability(audit_ids=audit_ids)

    by_container = defaultdict(lambda: {
        'cars': [],
        'total_cost': 0.0,
        'total_revenue': 0.0,
    })

    for car in cars:
        key = car['container_number'] or '—'
        entry = by_container[key]
        entry['container_number'] = car['container_number']
        entry['container_id']     = car['container_id']
        entry['cars'].append(car)
        entry['total_cost']    += car['total_cost']
        entry['total_revenue'] += car['total_revenue']

    result = []
    for key, data in by_container.items():
        profit = data['total_revenue'] - data['total_cost']
        margin = (profit / data['total_revenue'] * 100) if data['total_revenue'] > 0 else 0
        all_final = all(c['is_final'] for c in data['cars'])
        result.append({
            'container_number': data['container_number'],
            'container_id':     data['container_id'],
            'cars_count':       len(data['cars']),
            'total_cost':       round(data['total_cost'], 2),
            'total_revenue':    round(data['total_revenue'], 2),
            'profit':           round(profit, 2),
            'margin_pct':       round(margin, 1),
            'is_final':         all_final,
            'cars':             data['cars'],
        })

    result.sort(key=lambda x: x['profit'])
    return result


def generate_hints(audit_ids=None):
    """Генерирует подсказки и замечания."""
    hints = []
    cost_qs = SupplierCost.objects.select_related('car', 'car__client', 'car__container', 'audit')
    if audit_ids:
        cost_qs = cost_qs.filter(audit_id__in=audit_ids)

    # 1. Машины не найдены в системе
    missing_cars = cost_qs.filter(car__isnull=True, vin__gt='')
    if missing_cars.exists():
        missing_vins = list(missing_cars.values_list('vin', flat=True).distinct())
        total_lost = missing_cars.aggregate(t=Sum('amount'))['t'] or 0
        hints.append({
            'severity': 'error',
            'title':    'Машины из счетов не найдены в системе',
            'message':  f'{len(missing_vins)} машин из входящих счетов отсутствуют в системе. '
                        f'Затраты на них: {float(total_lost):.2f} € — эти деньги не выставлены клиентам.',
            'related_vins': missing_vins[:20],
            'amount':   float(total_lost),
        })

    # 2. Непривязанные позиции (car_service=None, но car найден)
    unlinked = cost_qs.filter(car__isnull=False, car_service__isnull=True)
    if unlinked.exists():
        unlinked_count = unlinked.count()
        unlinked_amount = unlinked.aggregate(t=Sum('amount'))['t'] or 0
        unlinked_vins = list(unlinked.values_list('vin', flat=True).distinct()[:20])
        hints.append({
            'severity':      'warning',
            'title':         'Позиции инвойса не привязаны к услугам',
            'message':       f'{unlinked_count} позиций из инвойсов не привязаны к CarService ({float(unlinked_amount):.2f} €). '
                             'Возможно, нужно обновить маппинг или добавить услуги в карточки авто.',
            'related_vins':  unlinked_vins,
            'amount':        float(unlinked_amount),
        })

    # 3. THS убытки
    ths_costs = cost_qs.filter(service_type='THS', car__isnull=False, car_service__isnull=False)
    ths_loss_vins = []
    ths_total_loss = Decimal('0')
    for sc in ths_costs:
        if not sc.car_service:
            continue
        our_price = float(sc.car_service.custom_price or 0)
        diff = our_price - float(sc.amount)
        if diff < -5:
            ths_loss_vins.append(sc.vin)
            ths_total_loss += sc.amount - Decimal(str(our_price))

    if ths_loss_vins:
        hints.append({
            'severity':      'error',
            'title':         'THS: убытки по некоторым машинам',
            'message':       f'По {len(ths_loss_vins)} машинам THS от поставщика выше, чем выставлено клиенту. '
                             f'Общий убыток: {float(ths_total_loss):.2f} €.',
            'related_vins':  ths_loss_vins[:20],
            'amount':        float(ths_total_loss),
        })

    # 4. Компенсации
    compensations = cost_qs.filter(service_type='COMPENSATION')
    if compensations.exists():
        total_comp = compensations.aggregate(t=Sum('amount'))['t'] or 0
        hints.append({
            'severity':      'info',
            'title':         'Компенсации от поставщиков',
            'message':       f'Обнаружены компенсации на {float(total_comp):.2f} €. '
                             'Убедитесь, что они учтены в балансе поставщика.',
            'related_vins':  list(compensations.values_list('vin', flat=True).distinct()[:10]),
            'amount':        float(total_comp),
        })

    # 5. Машины с нулевой выручкой но есть затраты
    cars_with_costs = cost_qs.filter(car__isnull=False).values_list('car_id', flat=True).distinct()
    zero_revenue_list = list(
        Car.objects.filter(id__in=cars_with_costs, total_price__lte=0).values_list('vin', flat=True)
    )
    if zero_revenue_list:
        hints.append({
            'severity':      'warning',
            'title':         'Затраты без выручки',
            'message':       f'{len(zero_revenue_list)} машин имеют затраты от поставщиков, но total_price = 0. '
                             'Возможно, клиенту ещё не выставлен счёт.',
            'related_vins':  zero_revenue_list[:20],
            'amount':        0,
        })

    # 6. Контрагент без маппинга
    no_mapping = cost_qs.filter(car__isnull=False, car_service__isnull=True).values_list(
        'counterparty', flat=True
    ).distinct()
    # already covered by "unlinked" hint — skip duplicate

    hints.sort(key=lambda h: {'error': 0, 'warning': 1, 'info': 2}.get(h['severity'], 9))
    return hints


def get_unlinked_costs(audit_ids=None):
    """Возвращает список непривязанных SupplierCost для ручной привязки."""
    qs = SupplierCost.objects.filter(
        car__isnull=False, car_service__isnull=True
    ).select_related('car', 'audit')
    if audit_ids:
        qs = qs.filter(audit_id__in=audit_ids)
    return qs


def get_reconciliation_summary(audit_ids=None):
    """
    Полная сводка для Dashboard.
    """
    cars = get_car_profitability(audit_ids=audit_ids)
    containers = get_container_profitability(audit_ids=audit_ids)
    hints = generate_hints(audit_ids=audit_ids)
    unlinked = get_unlinked_costs(audit_ids=audit_ids)

    total_cost    = sum(c['total_cost'] for c in cars)
    total_revenue = sum(c['total_revenue'] for c in cars)
    total_profit  = total_revenue - total_cost
    avg_margin    = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

    loss_cars  = [c for c in cars if c['profit'] < 0]
    final_cars = [c for c in cars if c['is_final']]

    return {
        'totals': {
            'total_cost':       round(total_cost, 2),
            'total_revenue':    round(total_revenue, 2),
            'total_profit':     round(total_profit, 2),
            'avg_margin':       round(avg_margin, 1),
            'cars_count':       len(cars),
            'containers_count': len(containers),
            'loss_cars_count':  len(loss_cars),
            'final_cars_count': len(final_cars),
            'hints_count':      len(hints),
            'unlinked_count':   unlinked.count(),
        },
        'cars':       cars,
        'containers': containers,
        'hints':      hints,
        'unlinked':   list(unlinked.values(
            'id', 'vin', 'counterparty', 'service_type', 'amount', 'description',
            'car_id', 'car__vin', 'audit_id',
        )[:100]),
    }
