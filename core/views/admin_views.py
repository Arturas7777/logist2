"""Staff-only admin views: dashboards, payments, photos, GDrive sync."""
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required

from core.models import Car, Container, Client, Company
from core.models_billing import NewInvoice as Invoice, Transaction as Payment
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
