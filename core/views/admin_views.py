"""Staff-only admin views: dashboards, payments, photos, GDrive sync, personal cards."""
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages, admin
from django.utils import timezone

from core.models import Car, Container, Client, Company
from core.models_billing import (
    NewInvoice as Invoice, Transaction as Payment,
    PersonalCard, PersonalTransfer, ExpenseCategory,
)
from core.services.billing_service import BillingService

logger = logging.getLogger(__name__)


@staff_member_required
def register_payment(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

    invoice_id: Optional[str] = request.POST.get('invoice_id')
    amount_raw = request.POST.get('amount', '0')
    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            return JsonResponse({'status': 'error', 'message': 'Сумма должна быть положительной'}, status=400)
    except (InvalidOperation, Exception):
        return JsonResponse({'status': 'error', 'message': 'Некорректная сумма'}, status=400)

    payment_method: Optional[str] = request.POST.get('payment_type', 'TRANSFER')
    from_balance: bool = request.POST.get('from_balance') == 'on'
    description: str = request.POST.get('description', '')
    payer_id: Optional[str] = request.POST.get('payer_id')

    try:
        with db_transaction.atomic():
            invoice = Invoice.objects.get(id=invoice_id) if invoice_id else None
            payer = None

            if payer_id:
                payer = Client.objects.select_for_update().get(id=payer_id)

            if from_balance and not payer:
                return JsonResponse({'status': 'error', 'message': 'Плательщик обязателен для оплаты с баланса'}, status=400)

            if from_balance and payer and payer.balance < amount:
                return JsonResponse({'status': 'error', 'message': 'Недостаточно средств на балансе'}, status=400)

            method = 'BALANCE' if from_balance else (payment_method or 'TRANSFER')

            if invoice:
                if not payer:
                    payer = invoice.recipient
                result = BillingService.pay_invoice(
                    invoice=invoice,
                    amount=amount,
                    method=method,
                    payer=payer,
                    description=description or f'Платёж на сумму {amount}',
                    created_by=request.user if request.user.is_authenticated else None,
                )
            else:
                payment = Payment(
                    type='PAYMENT',
                    method=method,
                    status='COMPLETED',
                    amount=amount,
                    description=description or f'Платёж на сумму {amount}',
                    from_client=payer,
                    to_company=Company.get_default(),
                    created_by=request.user if request.user.is_authenticated else None,
                )
                payment.save()

            if payer:
                payer.refresh_from_db()

        return JsonResponse({
            'status': 'success',
            'message': f'Платеж на сумму {amount} зарегистрирован',
            'client_balance': str(payer.balance) if payer else None,
        })
    except (Invoice.DoesNotExist, Client.DoesNotExist) as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=404)
    except ValueError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    except Exception as e:
        logger.error("Unexpected error registering payment: %s", e, exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def company_dashboard(request):
    from django.contrib import admin
    from core.services.dashboard_service import DashboardService

    service = DashboardService()
    dashboard_data = service.get_full_dashboard_context()

    context = admin.site.each_context(request)
    context.update(dashboard_data)

    context['revenue_expenses_chart_json'] = context['revenue_expenses_chart']
    context['invoices_by_status_json'] = context['invoices_by_status']
    context['cars_by_status_json'] = {
        k: v for k, v in context['cars_by_status'].items() if k != 'total'
    }
    context['expenses_by_category_json'] = context.get('expenses_by_category', [])
    context['income_by_category_json'] = context.get('income_by_category', [])

    cash_wallet = context.get('cash_wallet', {})
    context['personal_category_breakdown_json'] = cash_wallet.get('category_breakdown', [])

    return render(request, 'admin/company_dashboard.html', context)


@staff_member_required
def get_container_photos_json(request, container_id):
    try:
        from core.models_website import ContainerPhoto

        container = Container.objects.get(id=container_id)

        photos_data = []
        for photo in container.photos.only('id', 'photo', 'thumbnail', 'photo_type').all():
            photo_url = photo.photo.url if photo.photo else ''
            if photo_url and not photo_url.startswith('/media/') and not photo_url.startswith('http'):
                photo_url = '/media/' + photo_url.lstrip('/')

            thumb_url = photo.thumbnail.url if photo.thumbnail else photo_url
            if thumb_url and not thumb_url.startswith('/media/') and not thumb_url.startswith('http'):
                thumb_url = '/media/' + thumb_url.lstrip('/')

            photos_data.append({
                'id': photo.id,
                'url': photo_url,
                'thumbnail': thumb_url,
                'type': photo.photo_type or 'GENERAL',
            })

        return JsonResponse({
            'success': True,
            'photos': photos_data,
            'count': len(photos_data),
        })
    except Container.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Container not found'}, status=404)
    except Exception as e:
        logger.error("Error in get_container_photos_json: %s", e, exc_info=True)
        return JsonResponse({'success': False, 'error': 'Внутренняя ошибка сервера'}, status=500)


@staff_member_required
def sync_container_photos_from_gdrive(request, container_id):
    try:
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'Only POST method allowed'}, status=405)

        container = Container.objects.get(id=container_id)
        folder_url = container.google_drive_folder_url

        from core.tasks import sync_container_photos_gdrive_task
        sync_container_photos_gdrive_task.delay(container_id, folder_url or None)

        message = 'Загрузка фотографий начата. '
        if folder_url:
            message += 'Используется указанная ссылка на папку.'
        else:
            message += 'Ищем папку по номеру контейнера.'
        message += ' Обновите страницу через 1-2 минуты.'

        return JsonResponse({
            'success': True,
            'message': message,
            'photos_count': 0,
        })
    except Container.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Контейнер не найден'}, status=404)
    except Exception as e:
        logger.error("Error syncing Google Drive photos: %s", e, exc_info=True)
        return JsonResponse({'success': False, 'error': 'Внутренняя ошибка сервера'}, status=500)


def _ensure_personal_categories():
    """Create default PERSONAL categories if none exist."""
    from core.models_billing import ExpenseCategory
    if ExpenseCategory.objects.filter(category_type='PERSONAL').exists():
        return
    defaults = [
        ('Личные расходы', 'ЛИЧН', 100),
        ('Продукты', 'ПРОД', 101),
        ('Транспорт (личный)', 'ТРАНС', 102),
        ('Развлечения', 'РАЗВЛ', 103),
        ('Здоровье', 'ЗДОР', 104),
        ('Одежда', 'ОДЕЖ', 105),
    ]
    for name, short, order in defaults:
        ExpenseCategory.objects.get_or_create(
            name=name,
            defaults={'short_name': short, 'category_type': 'PERSONAL', 'order': order, 'is_active': True},
        )


@staff_member_required
def add_cash_expense(request):
    """Quick form to record a personal cash expense."""
    from core.models_billing import Transaction, ExpenseCategory

    _ensure_personal_categories()

    company = Company.objects.filter(name__icontains='Caromoto').first()
    personal_categories = ExpenseCategory.objects.filter(
        category_type='PERSONAL', is_active=True
    ).order_by('order')

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0').replace(',', '.'))
            if amount <= 0:
                raise ValueError('Сумма должна быть больше 0')
            category_id = request.POST.get('category')
            description = request.POST.get('description', '').strip()

            category = ExpenseCategory.objects.get(id=category_id, category_type='PERSONAL')

            tx = Transaction.objects.create(
                type='ADJUSTMENT',
                method='CASH',
                amount=amount,
                currency='EUR',
                from_company=company,
                category=category,
                description=description or f'Личный расход: {category.name}',
                status='COMPLETED',
                date=timezone.now(),
            )

            receipt_file = request.FILES.get('receipt')
            if receipt_file:
                tx.attachment = receipt_file
                tx.save(update_fields=['attachment'])
                try:
                    from core.tasks import parse_receipt_task
                    parse_receipt_task.delay(tx.id)
                except Exception:
                    pass

            messages.success(request, f'Расход {amount:.2f} € записан ({category.name})')
            return redirect('company_dashboard')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')
        except ExpenseCategory.DoesNotExist:
            messages.error(request, 'Выберите категорию')

    context = admin.site.each_context(request)
    context['personal_categories'] = personal_categories

    if company:
        breakdown = company.get_balance_breakdown()
        context['current_cash'] = breakdown.get('cash', Decimal('0'))
    else:
        context['current_cash'] = Decimal('0')

    return render(request, 'admin/cash_expense.html', context)


@staff_member_required
def add_cash_income(request):
    """Quick form to top up the cash wallet from external sources."""
    from core.models_billing import Transaction

    company = Company.objects.filter(name__icontains='Caromoto').first()

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0').replace(',', '.'))
            if amount <= 0:
                raise ValueError('Сумма должна быть больше 0')
            description = request.POST.get('description', '').strip()
            if not description:
                raise ValueError('Укажите источник поступления')

            Transaction.objects.create(
                type='ADJUSTMENT',
                method='CASH',
                amount=amount,
                currency='EUR',
                to_company=company,
                description=description,
                status='COMPLETED',
                date=timezone.now(),
            )
            messages.success(request, f'Пополнение {amount:.2f} € записано')
            return redirect('company_dashboard')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')

    context = admin.site.each_context(request)
    if company:
        breakdown = company.get_balance_breakdown()
        context['current_cash'] = breakdown.get('cash', Decimal('0'))
    else:
        context['current_cash'] = Decimal('0')

    return render(request, 'admin/cash_income.html', context)


@staff_member_required
def cash_wallet_reset(request):
    """Reconcile the cash wallet with the real amount in pocket."""
    from core.models_billing import Transaction

    company = Company.objects.filter(name__icontains='Caromoto').first()
    breakdown = company.get_balance_breakdown() if company else {}
    current_cash = breakdown.get('cash', Decimal('0'))

    if request.method == 'POST':
        try:
            real_amount = Decimal(request.POST.get('real_amount', '0').replace(',', '.'))
            if real_amount < 0:
                raise ValueError('Сумма не может быть отрицательной')

            diff = real_amount - current_cash
            if abs(diff) < Decimal('0.01'):
                messages.info(request, 'Баланс совпадает, корректировка не нужна')
                return redirect('company_dashboard')

            if diff > 0:
                tx = Transaction.objects.create(
                    type='ADJUSTMENT', method='CASH', amount=abs(diff),
                    currency='EUR', to_company=company,
                    description='Сверка кошелька — корректировка вверх',
                    status='COMPLETED', date=timezone.now(),
                )
            else:
                tx = Transaction.objects.create(
                    type='ADJUSTMENT', method='CASH', amount=abs(diff),
                    currency='EUR', from_company=company,
                    description='Сверка кошелька — корректировка вниз',
                    status='COMPLETED', date=timezone.now(),
                )

            messages.success(
                request,
                f'Баланс кошелька установлен: {real_amount:.2f} € '
                f'(корректировка {"+" if diff > 0 else ""}{diff:.2f} €)'
            )
            return redirect('company_dashboard')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')

    context = admin.site.each_context(request)
    context['current_cash'] = current_cash
    return render(request, 'admin/cash_wallet_reset.html', context)


@staff_member_required
def expense_analytics(request):
    """Personal expense analytics page with charts and AI insights."""
    import json
    from core.models_billing import Transaction, ExpenseCategory
    from core.services.expense_analytics_service import ExpenseAnalyticsService

    period = request.GET.get('period', '3m')
    if period not in ('1m', '3m', '6m', '1y', 'all'):
        period = '3m'

    svc = ExpenseAnalyticsService()
    breakdown = svc.get_category_breakdown(period)
    trend = svc.get_monthly_trend(
        months={'1m': 1, '3m': 3, '6m': 6, '1y': 12, 'all': 24}.get(period, 3)
    )
    top_items = svc.get_top_items(period)

    generate_ai = request.GET.get('ai') == '1'
    ai_insights = None
    if generate_ai:
        ai_insights = svc.get_ai_insights(period)

    total_spent = sum(b['total'] for b in breakdown)
    top_category = breakdown[0]['category'] if breakdown else '—'

    personal_cats = list(
        ExpenseCategory.objects.filter(category_type='PERSONAL')
        .values_list('id', flat=True)
    )
    no_receipt = list(
        Transaction.objects.filter(
            status='COMPLETED', category_id__in=personal_cats,
        ).filter(attachment='').select_related('category')
        .order_by('-date')[:20]
    ) if personal_cats else []

    context = admin.site.each_context(request)
    context.update({
        'period': period,
        'breakdown': breakdown,
        'breakdown_json': json.dumps(breakdown, ensure_ascii=False),
        'trend': trend,
        'trend_json': json.dumps(trend, ensure_ascii=False),
        'top_items': top_items,
        'ai_insights': ai_insights,
        'total_spent': total_spent,
        'top_category': top_category,
        'generate_ai': generate_ai,
        'no_receipt_expenses': no_receipt,
    })
    return render(request, 'admin/expense_analytics.html', context)


@staff_member_required
def upload_expense_receipt(request, tx_id):
    """Upload a receipt photo for an existing personal expense transaction."""
    from core.models_billing import Transaction, ExpenseCategory

    personal_cats = list(
        ExpenseCategory.objects.filter(category_type='PERSONAL')
        .values_list('id', flat=True)
    )

    try:
        tx = Transaction.objects.get(
            id=tx_id, status='COMPLETED', category_id__in=personal_cats,
        )
    except Transaction.DoesNotExist:
        messages.error(request, 'Транзакция не найдена')
        return redirect('company_dashboard')

    if request.method == 'POST':
        receipt_file = request.FILES.get('receipt')
        if not receipt_file:
            messages.error(request, 'Файл не выбран')
            return redirect('company_dashboard')

        tx.attachment = receipt_file
        tx.receipt_data = None
        tx.save(update_fields=['attachment', 'receipt_data'])

        try:
            from core.tasks import parse_receipt_task
            parse_receipt_task.delay(tx.id)
        except Exception:
            pass

        messages.success(
            request,
            f'Чек загружен для расхода {tx.amount:.2f} € ({tx.category.name})',
        )
        return redirect(request.POST.get('next', 'company_dashboard'))

    return redirect('company_dashboard')


# ============================================================================
# ЛИЧНЫЕ КАРТЫ
# ============================================================================

def _get_company():
    return Company.objects.filter(name__icontains='Caromoto').first()


@staff_member_required
def personal_cards_page(request):
    """Main page: cards list, cash balance, recent transfers."""
    company = _get_company()
    cards = PersonalCard.objects.filter(is_active=True).order_by('order', 'name')

    breakdown = company.get_balance_breakdown() if company else {}
    current_cash = breakdown.get('cash', Decimal('0'))

    recent_transfers = PersonalTransfer.objects.select_related(
        'from_card', 'to_card', 'category',
    ).order_by('-date')[:30]

    total_cards = sum(c.balance for c in cards)
    total_all = current_cash + total_cards

    context = admin.site.each_context(request)
    context.update({
        'cards': cards,
        'current_cash': current_cash,
        'total_cards': total_cards,
        'total_all': total_all,
        'recent_transfers': recent_transfers,
    })
    return render(request, 'admin/personal_cards.html', context)


@staff_member_required
def personal_card_add(request, card_id=None):
    """Add or edit a personal card."""
    card = get_object_or_404(PersonalCard, pk=card_id) if card_id else None

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        last_four = request.POST.get('last_four', '').strip()
        color = request.POST.get('color', '#6366f1').strip()
        initial_balance = request.POST.get('initial_balance', '0').replace(',', '.')

        if not name:
            messages.error(request, 'Укажите название карты')
        else:
            if card:
                card.name = name
                card.last_four = last_four
                card.color = color
                card.save(update_fields=['name', 'last_four', 'color'])
                messages.success(request, f'Карта «{card}» обновлена')
            else:
                try:
                    bal = Decimal(initial_balance) if initial_balance else Decimal('0')
                except (InvalidOperation, ValueError):
                    bal = Decimal('0')
                card = PersonalCard.objects.create(
                    name=name,
                    last_four=last_four,
                    color=color,
                    balance=bal,
                )
                messages.success(request, f'Карта «{card}» создана')
            return redirect('personal_cards_page')

    context = admin.site.each_context(request)
    context['card'] = card
    if card:
        has_transfers = card.transfers_in.exists() or card.transfers_out.exists()
        context['can_delete'] = not has_transfers
    return render(request, 'admin/personal_card_form.html', context)


@staff_member_required
def personal_card_deactivate(request, card_id):
    """Toggle active/inactive state of a card."""
    card = get_object_or_404(PersonalCard, pk=card_id)
    if request.method == 'POST':
        card.is_active = not card.is_active
        card.save(update_fields=['is_active'])
        state = 'активирована' if card.is_active else 'деактивирована'
        messages.success(request, f'Карта «{card}» {state}')
    return redirect('personal_card_edit', card_id=card.pk)


@staff_member_required
def personal_card_delete(request, card_id):
    """Permanently delete a card (only if no transfer history)."""
    card = get_object_or_404(PersonalCard, pk=card_id)
    if request.method == 'POST':
        has_transfers = card.transfers_in.exists() or card.transfers_out.exists()
        if has_transfers:
            messages.error(request, 'Невозможно удалить карту с историей операций. Деактивируйте её.')
        else:
            name = str(card)
            card.delete()
            messages.success(request, f'Карта «{name}» удалена')
            return redirect('personal_cards_page')
    return redirect('personal_card_edit', card_id=card.pk)


@staff_member_required
def personal_transfer(request):
    """Transfer between cash wallet and cards, or card-to-card."""
    company = _get_company()
    cards = PersonalCard.objects.filter(is_active=True).order_by('order', 'name')
    breakdown = company.get_balance_breakdown() if company else {}
    current_cash = breakdown.get('cash', Decimal('0'))

    if request.method == 'POST':
        try:
            transfer_type = request.POST.get('transfer_type', '')
            amount = Decimal(request.POST.get('amount', '0').replace(',', '.'))
            if amount <= 0:
                raise ValueError('Сумма должна быть больше 0')

            from_card_id = request.POST.get('from_card') or None
            to_card_id = request.POST.get('to_card') or None
            description = request.POST.get('description', '').strip()

            pt = PersonalTransfer(
                transfer_type=transfer_type,
                from_card_id=from_card_id,
                to_card_id=to_card_id,
                amount=amount,
                description=description,
                date=timezone.now(),
                created_by=request.user if request.user.is_authenticated else None,
            )
            pt.full_clean()
            pt.save()
            pt.execute(company=company)

            messages.success(request, f'{pt.get_transfer_type_display()}: {amount:.2f} €')
            return redirect('personal_cards_page')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')
        except Exception as e:
            logger.error("Personal transfer error: %s", e, exc_info=True)
            messages.error(request, f'Ошибка: {e}')

    context = admin.site.each_context(request)
    context.update({
        'cards': cards,
        'current_cash': current_cash,
    })
    return render(request, 'admin/personal_transfer.html', context)


@staff_member_required
def personal_card_expense(request):
    """Record an expense from a personal card."""
    _ensure_personal_categories()
    cards = PersonalCard.objects.filter(is_active=True).order_by('order', 'name')
    personal_categories = ExpenseCategory.objects.filter(
        category_type='PERSONAL', is_active=True,
    ).order_by('order')
    company = _get_company()

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0').replace(',', '.'))
            if amount <= 0:
                raise ValueError('Сумма должна быть больше 0')

            card_id = request.POST.get('card')
            category_id = request.POST.get('category')
            description = request.POST.get('description', '').strip()

            card = PersonalCard.objects.get(pk=card_id, is_active=True)
            category = ExpenseCategory.objects.get(pk=category_id, category_type='PERSONAL')

            pt = PersonalTransfer(
                transfer_type='CARD_EXPENSE',
                from_card=card,
                amount=amount,
                description=description or f'Расход с карты: {category.name}',
                category=category,
                date=timezone.now(),
                created_by=request.user if request.user.is_authenticated else None,
            )
            pt.full_clean()
            pt.save()
            pt.execute(company=company)

            messages.success(request, f'Расход {amount:.2f} € с карты «{card}» ({category.name})')
            return redirect('personal_cards_page')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')
        except (PersonalCard.DoesNotExist, ExpenseCategory.DoesNotExist):
            messages.error(request, 'Выберите карту и категорию')

    context = admin.site.each_context(request)
    context.update({
        'cards': cards,
        'personal_categories': personal_categories,
    })
    return render(request, 'admin/personal_card_expense.html', context)


@staff_member_required
def personal_card_income(request):
    """Record external income to a personal card (child benefit, refund, etc.)."""
    cards = PersonalCard.objects.filter(is_active=True).order_by('order', 'name')
    company = _get_company()

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0').replace(',', '.'))
            if amount <= 0:
                raise ValueError('Сумма должна быть больше 0')

            card_id = request.POST.get('card')
            description = request.POST.get('description', '').strip()
            if not description:
                raise ValueError('Укажите источник поступления')

            card = PersonalCard.objects.get(pk=card_id, is_active=True)

            pt = PersonalTransfer(
                transfer_type='CARD_INCOME',
                to_card=card,
                amount=amount,
                description=description,
                date=timezone.now(),
                created_by=request.user if request.user.is_authenticated else None,
            )
            pt.full_clean()
            pt.save()
            pt.execute(company=company)

            messages.success(request, f'Поступление {amount:.2f} € на карту «{card}»')
            return redirect('personal_cards_page')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')
        except PersonalCard.DoesNotExist:
            messages.error(request, 'Выберите карту')

    context = admin.site.each_context(request)
    context['cards'] = cards
    return render(request, 'admin/personal_card_income.html', context)


@staff_member_required
def personal_card_balance_reset(request, card_id):
    """Reconcile a personal card balance with the real amount."""
    card = get_object_or_404(PersonalCard, pk=card_id, is_active=True)

    if request.method == 'POST':
        try:
            real_amount = Decimal(request.POST.get('real_amount', '0').replace(',', '.'))
            if real_amount < 0:
                raise ValueError('Сумма не может быть отрицательной')

            diff = real_amount - card.balance
            if abs(diff) < Decimal('0.01'):
                messages.info(request, f'Баланс карты «{card}» совпадает, корректировка не нужна')
                return redirect('personal_cards_page')

            company = _get_company()
            if diff > 0:
                pt = PersonalTransfer(
                    transfer_type='CARD_INCOME',
                    to_card=card,
                    amount=abs(diff),
                    description=f'Сверка баланса — корректировка вверх',
                    date=timezone.now(),
                    created_by=request.user if request.user.is_authenticated else None,
                )
            else:
                pt = PersonalTransfer(
                    transfer_type='CARD_EXPENSE',
                    from_card=card,
                    amount=abs(diff),
                    description=f'Сверка баланса — корректировка вниз',
                    date=timezone.now(),
                    created_by=request.user if request.user.is_authenticated else None,
                )
            pt.full_clean()
            pt.save()
            pt.execute(company=company)

            messages.success(
                request,
                f'Баланс карты «{card}» установлен: {real_amount:.2f} € '
                f'(корректировка {"+" if diff > 0 else ""}{diff:.2f} €)'
            )
            return redirect('personal_cards_page')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'Ошибка: {e}')

    context = admin.site.each_context(request)
    context.update({
        'card': card,
    })
    return render(request, 'admin/personal_card_balance_reset.html', context)
