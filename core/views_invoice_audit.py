"""
Вьюхи для раздела "Проверка счетов" (InvoiceAudit).
"""

import threading
import logging
import os
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

from core.models_invoice_audit import InvoiceAudit, SupplierCost
from core.models import CarService

logger = logging.getLogger('django')


@staff_member_required
def invoice_audit_list(request):
    """Список всех проверок счетов + форма загрузки нового."""
    from logist2.admin_site import admin_site

    audits = InvoiceAudit.objects.select_related('created_by').order_by('-created_at')[:50]

    context = admin_site.each_context(request)
    context.update({
        'title': 'Проверка счетов',
        'audits': audits,
    })
    return render(request, 'admin/invoice_audit_list.html', context)


@staff_member_required
@require_POST
def invoice_audit_upload(request):
    """Принимает PDF, создаёт InvoiceAudit и запускает обработку в фоне."""
    pdf_file = request.FILES.get('pdf_file')

    if not pdf_file:
        messages.error(request, 'Выберите PDF файл для загрузки.')
        return redirect('invoice_audit_list')

    if not pdf_file.name.lower().endswith('.pdf'):
        messages.error(request, 'Только PDF файлы поддерживаются.')
        return redirect('invoice_audit_list')

    if pdf_file.size > 20 * 1024 * 1024:  # 20 MB
        messages.error(request, 'Файл слишком большой (максимум 20 МБ).')
        return redirect('invoice_audit_list')

    audit = InvoiceAudit.objects.create(
        pdf_file=pdf_file,
        original_filename=pdf_file.name,
        created_by=request.user,
    )

    # Запускаем обработку в отдельном потоке (не блокируем запрос)
    def _process():
        from core.services.invoice_audit_service import process_invoice_audit
        process_invoice_audit(audit.pk)

    thread = threading.Thread(target=_process, daemon=True)
    thread.start()

    messages.success(
        request,
        f'Счёт «{pdf_file.name}» загружен и отправлен на обработку. '
        f'Обычно это занимает 10–30 секунд.'
    )
    return redirect('invoice_audit_detail', pk=audit.pk)


@staff_member_required
def invoice_audit_detail(request, pk):
    """Детальный отчёт по одному счёту."""
    from logist2.admin_site import admin_site

    audit = get_object_or_404(InvoiceAudit, pk=pk)

    # Группируем расхождения по типу серьёзности
    errors   = [d for d in audit.discrepancies if d.get('severity') == 'error']
    warnings = [d for d in audit.discrepancies if d.get('severity') == 'warning']
    infos    = [d for d in audit.discrepancies if d.get('severity') == 'info']

    # Статистика из raw_extracted
    items = audit.raw_extracted.get('items', []) if audit.raw_extracted else []
    total_items = len(items)

    # Привязка позиций к CarService
    supplier_costs = SupplierCost.objects.filter(audit=audit).select_related(
        'car', 'car_service'
    ).order_by('vin', 'service_type')
    linked_count   = supplier_costs.filter(car_service__isnull=False).count()
    unlinked_count = supplier_costs.filter(car__isnull=False, car_service__isnull=True).count()

    context = admin_site.each_context(request)
    context.update({
        'title':          f'Проверка: {audit}',
        'audit':          audit,
        'errors':         errors,
        'warnings':       warnings,
        'infos':          infos,
        'total_items':    total_items,
        'supplier_costs': supplier_costs,
        'linked_count':   linked_count,
        'unlinked_count': unlinked_count,
    })
    return render(request, 'admin/invoice_audit_detail.html', context)


@staff_member_required
@require_GET
def invoice_audit_status(request, pk):
    """API: возвращает текущий статус обработки (для polling из JS)."""
    audit = get_object_or_404(InvoiceAudit, pk=pk)
    return JsonResponse({
        'status':       audit.status,
        'status_label': audit.get_status_display(),
        'issues_count': audit.issues_count,
        'cars_found':   audit.cars_found,
        'cars_missing': audit.cars_missing,
        'counterparty': audit.counterparty_detected,
        'invoice_date': audit.invoice_date.strftime('%d.%m.%Y') if audit.invoice_date else None,
        'total_amount': float(audit.total_amount) if audit.total_amount else None,
    })


@staff_member_required
@require_GET
def newinvoice_audit_poll(request, pk):
    """API: poll audit status for a NewInvoice (used for auto-refresh after PDF upload)."""
    from core.models_billing import NewInvoice
    invoice = get_object_or_404(NewInvoice, pk=pk)
    try:
        audit = invoice.audit
    except Exception:
        return JsonResponse({'ready': False, 'status': 'NO_AUDIT'})

    if not audit:
        return JsonResponse({'ready': False, 'status': 'NO_AUDIT'})

    done = audit.status in ('OK', 'HAS_ISSUES', 'ERROR')
    return JsonResponse({
        'ready': done,
        'status': audit.status,
        'total': float(invoice.total) if done else None,
        'items_count': invoice.items.count() if done else 0,
        'cars_count': invoice.cars.count() if done else 0,
    })


@staff_member_required
@require_POST
def invoice_audit_delete(request, pk):
    """Удалить запись проверки."""
    audit = get_object_or_404(InvoiceAudit, pk=pk)
    name = str(audit)
    audit.delete()
    messages.success(request, f'Проверка «{name}» удалена.')
    return redirect('invoice_audit_list')


@staff_member_required
@require_POST
def invoice_audit_reprocess(request, pk):
    """Перезапустить обработку счёта."""
    audit = get_object_or_404(InvoiceAudit, pk=pk)
    audit.status        = InvoiceAudit.STATUS_PENDING
    audit.error_message = ''
    audit.discrepancies = []
    audit.raw_extracted = {}
    audit.save()

    def _process():
        from core.services.invoice_audit_service import process_invoice_audit
        process_invoice_audit(audit.pk)

    thread = threading.Thread(target=_process, daemon=True)
    thread.start()

    messages.info(request, 'Счёт отправлен на повторную обработку.')
    return redirect('invoice_audit_detail', pk=audit.pk)


@staff_member_required
@require_POST
def reanalyze_newinvoice(request, pk):
    """Re-trigger AI analysis for a NewInvoice. Clears old audit data and re-runs."""
    from core.models_billing import NewInvoice
    invoice = get_object_or_404(NewInvoice, pk=pk)

    if not invoice.attachment:
        messages.error(request, 'У инвойса нет PDF-файла для анализа.')
        return redirect('admin:core_newinvoice_change', pk)

    audit = getattr(invoice, 'audit', None)
    if audit:
        SupplierCost.objects.filter(audit=audit).delete()
        audit.status = InvoiceAudit.STATUS_PENDING
        audit.error_message = ''
        audit.discrepancies = []
        audit.raw_extracted = {}
        audit.save()
    else:
        import shutil
        audit = InvoiceAudit.objects.create(
            invoice=invoice,
            status=InvoiceAudit.STATUS_PENDING,
        )
        if invoice.attachment:
            src = invoice.attachment.path
            dst_dir = os.path.join('media', 'invoice_audits')
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, os.path.basename(src))
            shutil.copy2(src, dst)
            audit.pdf_file = os.path.join('invoice_audits', os.path.basename(src))
            audit.save(update_fields=['pdf_file'])

    invoice.items.all().delete()

    def _process():
        from core.services.invoice_audit_service import process_invoice_audit
        process_invoice_audit(audit.pk)

    thread = threading.Thread(target=_process, daemon=True)
    thread.start()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'audit_id': audit.pk})

    messages.info(request, 'AI-анализ запущен заново. Обновите страницу через несколько секунд.')
    return redirect('admin:core_newinvoice_change', pk)


# ═══════════════════════════════════════════════════════════════════════════════
# RECONCILIATION DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@staff_member_required
def reconciliation_dashboard(request):
    """Dashboard сверки: прибыль по машинам, контейнерам, подсказки."""
    from logist2.admin_site import admin_site
    from core.services.reconciliation_service import get_reconciliation_summary

    # Фильтр по конкретным счетам (опционально)
    audit_ids = request.GET.getlist('audit')
    audit_ids = [int(x) for x in audit_ids if x.isdigit()] or None

    # Все загруженные счета для фильтра
    all_audits = InvoiceAudit.objects.filter(
        status__in=[InvoiceAudit.STATUS_OK, InvoiceAudit.STATUS_HAS_ISSUES]
    ).order_by('-invoice_date')

    data = get_reconciliation_summary(audit_ids=audit_ids)

    context = admin_site.each_context(request)
    context.update({
        'title':        'Сверка счетов',
        'data':         data,
        'totals':       data['totals'],
        'cars':         data['cars'],
        'containers':   data['containers'],
        'hints':        data['hints'],
        'unlinked':     data.get('unlinked', []),
        'all_audits':   all_audits,
        'selected_audits': audit_ids or [],
        'active_tab':   request.GET.get('tab', 'cars'),
    })
    return render(request, 'admin/reconciliation_dashboard.html', context)


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK ACTIONS API
# ═══════════════════════════════════════════════════════════════════════════════

@staff_member_required
@require_POST
def reconciliation_fix_ths(request):
    """Обновить THS в CarService на основе фактической стоимости из SupplierCost."""
    from django.utils import timezone

    sc_id = request.POST.get('supplier_cost_id')
    if not sc_id:
        return JsonResponse({'error': 'supplier_cost_id required'}, status=400)

    try:
        sc = SupplierCost.objects.select_related('car').get(pk=sc_id, service_type='THS')
    except SupplierCost.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    if not sc.car:
        return JsonResponse({'error': 'No car linked'}, status=400)

    THS_SERVICE_IDS = [46, 47]
    ths_service = CarService.objects.filter(car=sc.car, service_id__in=THS_SERVICE_IDS).first()

    if ths_service:
        ths_service.custom_price = sc.amount
        ths_service.save()
        action = 'updated'
    else:
        return JsonResponse({'error': 'THS CarService not found for this car'}, status=404)

    sc.reviewed = True
    sc.reviewed_at = timezone.now()
    sc.save(update_fields=['reviewed', 'reviewed_at'])

    return JsonResponse({
        'ok': True,
        'action': action,
        'vin': sc.vin,
        'new_amount': float(sc.amount),
    })


@staff_member_required
@require_POST
def reconciliation_mark_reviewed(request):
    """Отметить SupplierCost как проверенный."""
    from django.utils import timezone

    sc_id = request.POST.get('supplier_cost_id')
    if not sc_id:
        return JsonResponse({'error': 'supplier_cost_id required'}, status=400)

    try:
        sc = SupplierCost.objects.get(pk=sc_id)
    except SupplierCost.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    sc.reviewed = True
    sc.reviewed_at = timezone.now()
    sc.save(update_fields=['reviewed', 'reviewed_at'])

    return JsonResponse({'ok': True, 'vin': sc.vin})


@staff_member_required
@require_POST
def manual_confirm_cost(request):
    """Ручное подтверждение затраты для CarService (без PDF)."""
    from django.utils import timezone

    car_service_id = request.POST.get('car_service_id')
    amount         = request.POST.get('amount')
    counterparty   = request.POST.get('counterparty', '')
    description    = request.POST.get('description', '')

    if not car_service_id or amount is None:
        return JsonResponse({'error': 'car_service_id and amount required'}, status=400)

    try:
        cs = CarService.objects.select_related('car').get(pk=car_service_id)
    except CarService.DoesNotExist:
        return JsonResponse({'error': 'CarService not found'}, status=404)

    try:
        amount_dec = Decimal(str(amount))
    except Exception:
        return JsonResponse({'error': 'Invalid amount'}, status=400)

    from core.models_invoice_audit import SupplierCost
    SERVICE_TYPE_MAP = {
        'WAREHOUSE': 'OTHER',
        'LINE':      'OTHER',
        'CARRIER':   'TRANSPORT',
        'COMPANY':   'OTHER',
    }
    svc_name = cs.get_service_name().upper()
    if 'THS' in svc_name:
        stype = 'THS'
    elif any(k in svc_name for k in ['РАЗГРУЗК', 'ПОГРУЗК', 'UNLOAD', 'HANDLING']):
        stype = 'UNLOADING'
    elif any(k in svc_name for k in ['ХРАНЕН', 'STORAGE', 'SANDEL']):
        stype = 'STORAGE'
    elif any(k in svc_name for k in ['ПЕРЕВОЗК', 'TRANSPORT', 'ДОСТАВК']):
        stype = 'TRANSPORT'
    elif any(k in svc_name for k in ['ДЕКЛАР', 'DECLARATION']):
        stype = 'DECLARATION'
    elif 'BDK' in svc_name:
        stype = 'BDK'
    elif any(k in svc_name for k in ['ДОКУМЕНТ', 'DOCS', 'СПРАВК']):
        stype = 'DOCS'
    else:
        stype = SERVICE_TYPE_MAP.get(cs.service_type, 'OTHER')

    sc = SupplierCost.objects.create(
        car=cs.car,
        car_service=cs,
        audit=None,
        source='MANUAL',
        counterparty=counterparty or 'Ручной ввод',
        service_type=stype,
        amount=amount_dec,
        vin=cs.car.vin if cs.car else '',
        description=description or f'Ручное подтверждение: {cs.get_service_name()}',
        reviewed=True,
        reviewed_at=timezone.now(),
    )

    return JsonResponse({
        'ok': True,
        'supplier_cost_id': sc.pk,
        'service_name': cs.get_service_name(),
        'amount': float(amount_dec),
    })
