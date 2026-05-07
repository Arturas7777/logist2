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
    actions = (
        'apply_jobs_action',
        'apply_jobs_force_action',
        'ignore_jobs_action',
        'reprocess_jobs_action',
    )

    change_form_template = 'admin/scan_processing_job/change_form.html'

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
            flags.append(format_html(
                '<span style="background:#0d6efd;color:#fff;padding:2px 6px;'
                'border-radius:4px;font-size:11px;">🆕 Car</span>'
            ))
        if obj.created_new_container:
            flags.append(format_html(
                '<span style="background:#198754;color:#fff;padding:2px 6px;'
                'border-radius:4px;font-size:11px;">🆕 Container</span>'
            ))
        # Подозрение на VIN-mismatch (OCR-ошибка): показываем заметный бейдж.
        if (obj.extracted_data or {}).get('vin_mismatch_review'):
            flags.append(format_html(
                '<span style="background:#ffc107;color:#212529;padding:2px 6px;'
                'border-radius:4px;font-size:11px;font-weight:bold;" '
                'title="AI извлёк VIN, но в БД есть очень похожий — '
                'возможно ошибка OCR">⚠ VIN ?</span>'
            ))
        if not flags:
            return '—'
        return format_html(' '.join(['{}'] * len(flags)), *flags)
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

    def apply_jobs_force_action(self, request, queryset):
        """Применить, игнорируя проверку на похожие VIN — создаст новый Car
        даже если в БД есть очень похожий VIN (разница 1-2 символа).

        Использовать ТОЛЬКО если уверены, что AI прочитал VIN правильно
        и совпадение с существующим — случайно.
        """
        from core.services.scan_applier import apply_job
        applied, errors = 0, 0
        for job in queryset:
            if not job.can_apply:
                continue
            data = job.extracted_data or {}
            data['skip_vin_check'] = True
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])
            try:
                apply_job(job, applied_by=request.user)
                applied += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to force-apply scan job #%s", job.pk)
                errors += 1
        if applied:
            self.message_user(
                request,
                f"Применено (force, без VIN-проверки): {applied}",
                messages.SUCCESS,
            )
        if errors:
            self.message_user(request, f"Ошибок: {errors} (см. логи)", messages.ERROR)
    apply_jobs_force_action.short_description = (
        "⚠ Применить как НОВЫЙ Car (без проверки похожих VIN)"
    )

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
            path(
                '<int:job_id>/resolve-vin/',
                self.admin_site.admin_view(self.resolve_vin_view),
                name='core_scanprocessingjob_resolve_vin',
            ),
        ]
        return custom + urls

    def resolve_vin_view(self, request, job_id: int):
        """Принимает решение пользователя по VIN-mismatch.

        POST-параметры:
          * action='attach', chosen_vin=<VIN> — подменить VIN на выбранный
            из кандидатов, чтобы apply сматчил на существующий Car.
          * action='force_new' — выставить флаг ``skip_vin_check`` и
            принудительно создать новый Car, игнорируя похожие.
        После любого решения немедленно применяет job.
        """
        from core.services.scan_applier import apply_job

        if request.method != 'POST':
            return redirect('admin:core_scanprocessingjob_change', job_id)

        try:
            job = ScanProcessingJob.objects.get(pk=job_id)
        except ScanProcessingJob.DoesNotExist:
            messages.error(request, "Job не найден.")
            return redirect('admin:core_scanprocessingjob_changelist')

        action = request.POST.get('action', '')
        data = job.extracted_data or {}
        mismatch = data.get('vin_mismatch_review') or {}
        candidate_vins = {c.get('vin') for c in (mismatch.get('candidates') or [])}

        if action == 'attach':
            # ВАРИАНТ 1: AI ошибся в тайтле, dock receipt прав.
            # Подменяем VIN из тайтла на VIN из БД (кандидата).
            chosen_vin = (request.POST.get('chosen_vin') or '').strip().upper()
            if chosen_vin not in candidate_vins:
                messages.error(request, "Выбранный VIN не из списка кандидатов.")
                return redirect('admin:core_scanprocessingjob_change', job_id)
            vins = data.get('vins') or []
            if vins:
                vins[0] = chosen_vin
            else:
                vins = [chosen_vin]
            data['vins'] = vins
            data.pop('vin_mismatch_review', None)
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])
            try:
                apply_job(job, applied_by=request.user)
                messages.success(
                    request,
                    f"Тайтл прикреплён к существующему авто (VIN={chosen_vin}).",
                )
            except Exception:
                logger.exception("Failed to apply after attach: job #%s", job.pk)
                messages.error(request, "Ошибка при применении (см. логи).")
            return redirect('admin:core_scanprocessingjob_change', job_id)

        if action == 'fix_existing_car_vin':
            # ВАРИАНТ 2: AI ошибся в dock receipt, тайтл прав.
            # Обновляем VIN существующей карточки Car на VIN из тайтла,
            # затем applier найдёт её и прикрепит тайтл.
            from core.models import Car
            try:
                car_id = int(request.POST.get('car_id') or 0)
            except (TypeError, ValueError):
                car_id = 0
            if not any(c.get('car_id') == car_id for c in (mismatch.get('candidates') or [])):
                messages.error(request, "Car не из списка кандидатов.")
                return redirect('admin:core_scanprocessingjob_change', job_id)
            extracted_vin = (mismatch.get('extracted_vin') or '').strip().upper()
            if not extracted_vin:
                messages.error(request, "В job отсутствует исходный VIN.")
                return redirect('admin:core_scanprocessingjob_change', job_id)
            # Защита от дубликата: вдруг в БД уже есть Car с extracted_vin.
            collision = Car.objects.filter(vin=extracted_vin).exclude(pk=car_id).first()
            if collision:
                messages.error(
                    request,
                    f"VIN {extracted_vin} уже занят другой карточкой "
                    f"(Car #{collision.id}). Конфликт нужно решить вручную.",
                )
                return redirect('admin:core_scanprocessingjob_change', job_id)
            try:
                car = Car.objects.get(pk=car_id)
            except Car.DoesNotExist:
                messages.error(request, "Car не найден.")
                return redirect('admin:core_scanprocessingjob_change', job_id)
            old_vin = car.vin
            car.vin = extracted_vin
            car.save(update_fields=['vin'])
            data.pop('vin_mismatch_review', None)
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])
            try:
                apply_job(job, applied_by=request.user)
                messages.success(
                    request,
                    f"VIN в карточке Car #{car.id} исправлен: "
                    f"{old_vin} → {extracted_vin}. Тайтл прикреплён.",
                )
            except Exception:
                logger.exception("Failed to apply after VIN-fix: job #%s", job.pk)
                messages.error(request, "Ошибка при применении (см. логи).")
            return redirect('admin:core_scanprocessingjob_change', job_id)

        if action == 'force_new':
            data['skip_vin_check'] = True
            data.pop('vin_mismatch_review', None)
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])
            try:
                apply_job(job, applied_by=request.user)
                messages.success(request, "Создана новая карточка Car (force).")
            except Exception:
                logger.exception("Failed to force-apply: job #%s", job.pk)
                messages.error(request, "Ошибка при применении (см. логи).")
            return redirect('admin:core_scanprocessingjob_change', job_id)

        messages.warning(request, f"Неизвестное действие: {action}")
        return redirect('admin:core_scanprocessingjob_change', job_id)

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
