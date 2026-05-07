"""Админка для AI-обработки сканов титулов и Dock Receipts."""
from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from core.models_scans import ScanProcessingJob

logger = logging.getLogger(__name__)

# Multi-file upload идёт прямым POST'ом в ``upload_view``, без Django Form,
# потому что ``forms.ClearableFileInput`` в Django 5+ запрещает
# ``multiple=True`` на security-уровне (уязвимость с дублированием файла
# при ре-submit). Виджет в шаблоне — нативный <input type="file" multiple>,
# а на сервере мы используем ``request.FILES.getlist('files')``.


@admin.register(ScanProcessingJob)
class ScanProcessingJobAdmin(admin.ModelAdmin):
    """Список и review всех загруженных сканов.

    Workflow в одном экране:
      1. Загружаем сканы через action в Car/Container ИЛИ через
         ``Add scan`` в этой админке (multi-file).
      2. Celery process_scan_job извлекает данные → status=NEEDS_REVIEW.
      3. Открываем job → видим preview PDF + извлечённый JSON →
         жмём "Применить" (action ``apply_jobs``) или "Игнорировать".
    """

    list_display = (
        'id', 'scan_type_badge', 'status_badge', 'extracted_summary',
        'linked_car_link', 'linked_container_link',
        'created_new_flags',
        'created_by', 'created_at',
    )
    list_filter = ('scan_type', 'status', 'created_at')
    search_fields = (
        'extracted_data__container_number',
        'extracted_data__booking_number',
        'extracted_data__vins',
        'extracted_data__vehicles__vin',
        'linked_car__vin',
        'linked_container__number',
        'error_message',
    )
    readonly_fields = (
        'scan_type', 'status', 'original_file_preview',
        'extracted_data_pretty', 'applied_changes_pretty',
        'linked_car', 'linked_container',
        'created_new_car', 'created_new_container',
        'error_message', 'created_at', 'processed_at', 'applied_at',
        'created_by', 'applied_by',
    )
    fieldsets = (
        ('Скан', {
            'fields': ('scan_type', 'status', 'original_file_preview'),
        }),
        ('AI извлечение', {
            'fields': ('extracted_data_pretty', 'error_message'),
        }),
        ('Применение', {
            'fields': ('linked_car', 'linked_container',
                       'created_new_car', 'created_new_container',
                       'applied_changes_pretty'),
        }),
        ('Аудит', {
            'fields': ('created_by', 'created_at', 'processed_at',
                       'applied_by', 'applied_at'),
        }),
    )
    actions = ('apply_jobs_action', 'ignore_jobs_action', 'reprocess_jobs_action')

    # ── Список ────────────────────────────────────────────────────────────

    def scan_type_badge(self, obj):
        colors = {
            ScanProcessingJob.SCAN_TYPE_TITLE: '#0d6efd',
            ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT: '#198754',
        }
        color = colors.get(obj.scan_type, '#6c757d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_scan_type_display(),
        )
    scan_type_badge.short_description = 'Тип'

    def status_badge(self, obj):
        palette = {
            ScanProcessingJob.STATUS_PENDING: '#6c757d',
            ScanProcessingJob.STATUS_PROCESSING: '#0dcaf0',
            ScanProcessingJob.STATUS_NEEDS_REVIEW: '#fd7e14',
            ScanProcessingJob.STATUS_APPLIED: '#198754',
            ScanProcessingJob.STATUS_ERROR: '#dc3545',
            ScanProcessingJob.STATUS_IGNORED: '#adb5bd',
        }
        color = palette.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_status_display(),
        )
    status_badge.short_description = 'Статус'

    def extracted_summary(self, obj):
        d = obj.extracted_data or {}
        if obj.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
            vins = d.get('vins') or []
            year = d.get('year') or ''
            make = d.get('make') or ''
            return format_html(
                '<code>{}</code> {} {}',
                ', '.join(vins) or '—', year, make,
            )
        if obj.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
            cn = d.get('container_number') or '—'
            bn = d.get('booking_number') or ''
            n = len(d.get('vehicles') or [])
            return format_html(
                '<code>{}</code> {} <span style="color:#6c757d;">({} авто)</span>',
                cn, f'/{bn}' if bn else '', n,
            )
        return '—'
    extracted_summary.short_description = 'AI извлёк'

    def linked_car_link(self, obj):
        if not obj.linked_car_id:
            return '—'
        url = reverse('admin:core_car_change', args=[obj.linked_car_id])
        return format_html('<a href="{}">{}</a>', url, obj.linked_car.vin)
    linked_car_link.short_description = 'Car'

    def linked_container_link(self, obj):
        if not obj.linked_container_id:
            return '—'
        url = reverse('admin:core_container_change', args=[obj.linked_container_id])
        return format_html('<a href="{}">{}</a>', url, obj.linked_container.number)
    linked_container_link.short_description = 'Container'

    def created_new_flags(self, obj):
        flags = []
        if obj.created_new_car:
            flags.append('🆕 Car')
        if obj.created_new_container:
            flags.append('🆕 Container')
        return ' '.join(flags) or '—'
    created_new_flags.short_description = 'Создано'

    # ── Read-only детали ──────────────────────────────────────────────────

    def original_file_preview(self, obj):
        if not obj.original_file:
            return '—'
        url = obj.original_file.url
        return format_html(
            '<a href="{0}" target="_blank">📄 Открыть PDF</a><br>'
            '<iframe src="{0}" width="100%" height="600" style="border:1px solid #dee2e6;border-radius:4px;margin-top:8px;"></iframe>',
            url,
        )
    original_file_preview.short_description = 'Скан'

    def extracted_data_pretty(self, obj):
        return self._json_block(obj.extracted_data)
    extracted_data_pretty.short_description = 'Извлечённые данные'

    def applied_changes_pretty(self, obj):
        return self._json_block(obj.applied_changes)
    applied_changes_pretty.short_description = 'Применённые изменения'

    def _json_block(self, data):
        import json as _json
        if not data:
            return '—'
        formatted = _json.dumps(data, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="background:#f8f9fa;padding:12px;border-radius:4px;font-size:12px;'
            'max-height:400px;overflow:auto;">{}</pre>',
            formatted,
        )

    # ── Actions ───────────────────────────────────────────────────────────

    def apply_jobs_action(self, request, queryset):
        from core.services.scan_applier import apply_job
        applied, errors = 0, 0
        for job in queryset:
            if not job.can_apply:
                continue
            try:
                apply_job(job, applied_by=request.user)
                applied += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to apply scan job #%s", job.pk)
                errors += 1
        if applied:
            self.message_user(request, f"Применено: {applied}", messages.SUCCESS)
        if errors:
            self.message_user(request, f"Ошибок: {errors} (см. логи)", messages.ERROR)
        if not applied and not errors:
            self.message_user(
                request,
                "Нет задач в статусе 'Ожидает проверки' среди выбранных.",
                messages.WARNING,
            )
    apply_jobs_action.short_description = "✅ Применить (Car/Container будут обновлены)"

    def ignore_jobs_action(self, request, queryset):
        n = queryset.filter(status__in=[
            ScanProcessingJob.STATUS_NEEDS_REVIEW,
            ScanProcessingJob.STATUS_ERROR,
        ]).update(status=ScanProcessingJob.STATUS_IGNORED)
        self.message_user(request, f"Помечено как 'Проигнорировано': {n}", messages.INFO)
    ignore_jobs_action.short_description = "🚫 Игнорировать"

    def reprocess_jobs_action(self, request, queryset):
        from core.tasks import process_scan_job
        n = 0
        for job in queryset:
            if job.status in (ScanProcessingJob.STATUS_PROCESSING, ScanProcessingJob.STATUS_APPLIED):
                continue
            job.status = ScanProcessingJob.STATUS_PENDING
            job.error_message = ''
            job.save(update_fields=['status', 'error_message'])
            try:
                process_scan_job.delay(job.id)
            except Exception:
                # eager / no broker — выполняем синхронно
                process_scan_job(job.id)  # type: ignore[call-arg]
            n += 1
        self.message_user(request, f"Поставлено на повторную обработку: {n}", messages.INFO)
    reprocess_jobs_action.short_description = "🔁 Повторить AI-обработку"

    # ── Custom URL: multi-file upload ─────────────────────────────────────

    change_list_template = 'admin/scan_processing_job/change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'upload/',
                self.admin_site.admin_view(self.upload_view),
                name='core_scanprocessingjob_upload',
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['upload_title_url'] = reverse(
            'admin:core_scanprocessingjob_upload'
        ) + f'?type={ScanProcessingJob.SCAN_TYPE_TITLE}'
        extra_context['upload_dock_url'] = reverse(
            'admin:core_scanprocessingjob_upload'
        ) + f'?type={ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT}'
        # Счётчики, чтобы сразу было видно, сколько ждёт review.
        from django.db.models import Count, Q
        counts = ScanProcessingJob.objects.aggregate(
            review=Count('id', filter=Q(status=ScanProcessingJob.STATUS_NEEDS_REVIEW)),
            errors=Count('id', filter=Q(status=ScanProcessingJob.STATUS_ERROR)),
            processing=Count('id', filter=Q(status__in=[
                ScanProcessingJob.STATUS_PENDING, ScanProcessingJob.STATUS_PROCESSING,
            ])),
        )
        extra_context['scan_counts'] = counts
        return super().changelist_view(request, extra_context=extra_context)

    def upload_view(self, request):
        """Простая страница drag&drop multi-file upload.

        Доступна по ``/admin/core/scanprocessingjob/upload/?type=TITLE``
        или ``?type=DOCK_RECEIPT``.
        """
        from core.tasks import process_scan_job

        scan_type = request.GET.get('type', ScanProcessingJob.SCAN_TYPE_TITLE)
        if scan_type not in dict(ScanProcessingJob.SCAN_TYPE_CHOICES):
            scan_type = ScanProcessingJob.SCAN_TYPE_TITLE

        if request.method == 'POST':
            files = request.FILES.getlist('files')
            if not files:
                messages.warning(request, "Не выбрано ни одного файла.")
                return HttpResponseRedirect(request.get_full_path())
            created_ids = []
            for f in files:
                # Быстрая sanity-проверка: хотим PDF.
                if not f.name.lower().endswith('.pdf'):
                    messages.warning(request, f"Пропущен (не PDF): {f.name}")
                    continue
                job = ScanProcessingJob.objects.create(
                    scan_type=scan_type,
                    original_file=f,
                    created_by=request.user,
                )
                created_ids.append(job.id)
                # Бросаем в Celery; если broker недоступен — выполняется eager.
                try:
                    process_scan_job.delay(job.id)
                except Exception:
                    process_scan_job(job.id)  # type: ignore[call-arg]
            if created_ids:
                messages.success(
                    request,
                    f"Загружено {len(created_ids)} сканов. После обработки AI они "
                    f"появятся в списке со статусом 'Ожидает проверки'.",
                )
            return redirect('admin:core_scanprocessingjob_changelist')

        context = {
            **self.admin_site.each_context(request),
            'title': 'Загрузка сканов',
            'opts': self.model._meta,
            'scan_type': scan_type,
            'scan_type_label': dict(ScanProcessingJob.SCAN_TYPE_CHOICES)[scan_type],
            'scan_type_choices': ScanProcessingJob.SCAN_TYPE_CHOICES,
        }
        return render(request, 'admin/scan_processing_job/upload.html', context)
