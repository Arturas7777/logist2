/**
 * Форма инвойса (templates/admin/core/newinvoice/change_form.html).
 *
 * Перенесено из inline <script> шаблона (U8, план 2026-10) без изменения
 * поведения. Всё, что зависело от контекста шаблона (URL, pk, флаги,
 * предзагруженные ручные позиции), приходит через window.INVOICE_FORM_CONFIG:
 *
 *   invoiceId          — pk инвойса ('' для новой формы)
 *   carsAutocompleteUrl, calcCarsTotalUrl, auditPollUrl
 *   hideAutoItemsCard  — прятать карточку «позиции из авто» в ручном режиме
 *   startAuditPoll     — входящий инвойс с незавершённым AI-анализом
 *   manualItems        — [{description, quantity, unit_price}] для предзагрузки
 *   manualTotal        — строка с total для ручного режима (или null)
 */
(function ($) {
    if (!$) {
        console.error('jQuery not found!');
        return;
    }
    var cfg = window.INVOICE_FORM_CONFIG || {};

    $(document).ready(function () {
        // AJAX поиск для выставителя
        $('#issuer_select').select2({
            placeholder: 'Начните вводить название...',
            allowClear: true,
            minimumInputLength: 1,
            ajax: {
                url: '/core/api/search-counterparties/',
                dataType: 'json',
                delay: 250,
                data: function (params) {
                    return { q: params.term };
                },
                processResults: function (data) {
                    return { results: data.results };
                },
                cache: true
            }
        });

        // AJAX поиск для получателя
        $('#recipient_select').select2({
            placeholder: 'Начните вводить название...',
            allowClear: true,
            minimumInputLength: 1,
            ajax: {
                url: '/core/api/search-counterparties/',
                dataType: 'json',
                delay: 250,
                data: function (params) {
                    return { q: params.term };
                },
                processResults: function (data) {
                    return { results: data.results };
                },
                cache: true
            }
        });

        // AJAX поиск для связанного счёта
        $('#linked_invoice_select').select2({
            placeholder: 'Начните вводить номер или контрагента...',
            allowClear: true,
            minimumInputLength: 2,
            ajax: {
                url: '/core/api/search-invoices/',
                dataType: 'json',
                delay: 300,
                data: function (params) {
                    return {
                        q: params.term,
                        exclude: cfg.invoiceId || ''
                    };
                },
                processResults: function (data) {
                    return { results: data.results };
                },
                cache: true
            },
            templateResult: function (item) {
                if (!item.id) return item.text;
                var statusColors = {
                    'DRAFT': '#6c757d', 'ISSUED': '#007bff', 'PARTIALLY_PAID': '#ffc107',
                    'PAID': '#28a745', 'OVERDUE': '#dc3545', 'CANCELLED': '#6c757d'
                };
                var color = statusColors[item.status] || '#6c757d';
                return $('<span>' + item.text +
                    ' <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
                    color + ';margin-left:4px;"></span></span>');
            }
        });

        // Инициализация Select2 для автомобилей с цветами по статусу
        var carAdminBaseUrl = '/admin/core/car/';

        // Server-side AJAX поиск машин (post-M5): без ограничения top-N
        // (с лимитом 20 на запрос).
        $('#cars_select').select2({
            placeholder: 'Введите VIN или марку для поиска...',
            allowClear: true,
            minimumInputLength: 1,
            ajax: {
                url: cfg.carsAutocompleteUrl,
                dataType: 'json',
                delay: 200,
                data: function (params) { return { term: params.term || '' }; },
                processResults: function (data) {
                    return {
                        results: (data.results || []).map(function (c) {
                            return { id: c.id, text: c.text, status: c.status };
                        })
                    };
                },
                cache: true
            },
            templateResult: function (state) {
                if (!state.id) return state.text;
                // status может прийти и из <option data-status="">, и из AJAX
                var status = state.status || ($(state.element).data('status') || '');
                var statusLabels = {
                    'FLOATING': '🚢 В пути',
                    'IN_PORT': '⚓ В порту',
                    'UNLOADED': '✅ Разгружен',
                    'TRANSFERRED': '📦 Передан'
                };
                var statusLabel = statusLabels[status] || '';
                return $('<span>' + state.text + (statusLabel ? ' <small style="opacity:0.7;">' + statusLabel + '</small>' : '') + '</span>');
            },
            templateSelection: function (state) {
                if (!state.id) return state.text;
                var status = state.status || ($(state.element).data('status') || '');
                var link = carAdminBaseUrl + state.id + '/change/';
                return $('<a href="' + link + '" target="_blank" data-status="' + status + '" ' +
                    'onclick="event.stopPropagation();" ' +
                    'style="color:inherit;text-decoration:none;cursor:pointer;">' + state.text + '</a>');
            }
        });

        // Добавляем data-status к созданным тегам
        function applyStatusColors() {
            $('.select2-selection__choice').each(function () {
                var $choice = $(this);
                var $el = $choice.find('[data-status]');
                if ($el.length) {
                    $choice.attr('data-status', $el.data('status'));
                }
            });
        }

        $('#cars_select').on('select2:select select2:unselect', function () {
            setTimeout(applyStatusColors, 10);
        });

        // Инициализируем статусы для уже выбранных тегов
        setTimeout(applyStatusColors, 100);

        // ====================================================================
        // Ручной ввод суммы и позиций
        // ====================================================================

        var manualItemCounter = 0;

        function hasCarsSelected() {
            var vals = $('#cars_select').val();
            return vals && vals.length > 0;
        }

        function updateManualMode() {
            var carsSelected = hasCarsSelected();
            if (carsSelected) {
                // Режим "авто" — позиции из автомобилей
                $('#auto_mode_badge').show();
                $('#manual_mode_badge').hide();
                $('#auto_hint').show();
                $('#manual_hint').hide();
                $('#manual_total_input').prop('readonly', true).css('opacity', '0.6');
                $('#total_hint').text('Сумма будет пересчитана из позиций автомобилей при сохранении.');
                $('#manual_amount_card').hide();
                $('#auto_items_card').show();
            } else {
                // Режим "ручной" — ввод суммы вручную
                $('#manual_mode_badge').show();
                $('#auto_mode_badge').hide();
                $('#manual_hint').show();
                $('#auto_hint').hide();
                $('#manual_total_input').prop('readonly', false).css('opacity', '1');
                $('#total_hint').text('Общая сумма. Пересчитывается из позиций автоматически.');
                $('#manual_amount_card').show();
                // Скрываем auto items card если нет сохранённых позиций из авто
                if (cfg.hideAutoItemsCard) {
                    $('#auto_items_card').hide();
                }
            }
        }

        // Пересчитать сумму из ручных позиций
        function recalcManualTotal() {
            var rows = $('#manual_items_body tr');
            if (rows.length === 0) {
                $('#manual_items_footer').hide();
                return;
            }
            $('#manual_items_footer').show();
            var total = 0;
            rows.each(function () {
                var qty = parseFloat($(this).find('.mi-qty').val()) || 0;
                var price = parseFloat($(this).find('.mi-price').val()) || 0;
                var rowTotal = qty * price;
                $(this).find('.mi-row-total').text(rowTotal.toFixed(2) + ' €');
                total += rowTotal;
            });
            $('#manual_items_total').text(total.toFixed(2) + ' €');
            // Обновляем поле суммы
            if (!hasCarsSelected()) {
                $('#manual_total_input').val(total.toFixed(2));
            }
            // Обновляем JSON
            updateManualItemsJSON();
        }

        function updateManualItemsJSON() {
            var items = [];
            $('#manual_items_body tr').each(function () {
                var desc = $(this).find('.mi-desc').val().trim();
                var qty = parseFloat($(this).find('.mi-qty').val()) || 0;
                var price = parseFloat($(this).find('.mi-price').val()) || 0;
                if (desc && (qty > 0 || price > 0)) {
                    items.push({ description: desc, quantity: qty, unit_price: price });
                }
            });
            $('#manual_items_json').val(JSON.stringify(items));
        }

        function addManualItemRow(desc, qty, price) {
            manualItemCounter++;
            desc = desc || '';
            qty = qty || 1;
            price = price || 0;
            var rowTotal = (qty * price).toFixed(2);
            var html = '<tr id="mi_row_' + manualItemCounter + '">' +
                '<td><input type="text" class="mi-desc" value="' + desc.replace(/"/g, '&quot;') + '" placeholder="Описание услуги/товара" style="width:100%;"></td>' +
                '<td><input type="number" class="mi-qty" value="' + qty + '" min="0" step="1" style="width:70px;text-align:center;"></td>' +
                '<td><input type="number" class="mi-price" value="' + price + '" min="0" step="0.01" style="width:90px;text-align:right;"></td>' +
                '<td class="mi-row-total" style="text-align:right;font-weight:600;">' + rowTotal + ' €</td>' +
                '<td style="text-align:center;"><button type="button" class="mi-remove" data-row="mi_row_' + manualItemCounter + '" style="background:var(--danger);color:white;border:none;border-radius:6px;padding:6px 10px;cursor:pointer;font-size:14px;">✕</button></td>' +
                '</tr>';
            $('#manual_items_body').append(html);
            $('#manual_items_table').show();
            recalcManualTotal();
        }

        // Кнопка "Добавить позицию"
        $('#add_manual_item_btn').on('click', function () {
            addManualItemRow('', 1, 0);
        });

        // Удаление строки
        $(document).on('click', '.mi-remove', function () {
            var rowId = $(this).data('row');
            $('#' + rowId).remove();
            if ($('#manual_items_body tr').length === 0) {
                $('#manual_items_table').hide();
            }
            recalcManualTotal();
        });

        // Пересчёт при изменении
        $(document).on('input change', '.mi-qty, .mi-price', function () {
            recalcManualTotal();
        });

        // Обновляем JSON при потере фокуса описания
        $(document).on('blur', '.mi-desc', function () {
            updateManualItemsJSON();
        });

        // AJAX: пересчёт суммы при изменении автомобилей или выставителя
        var calcXhr = null;
        function fetchCarsTotal() {
            var carIds = $('#cars_select').val();
            if (!carIds || carIds.length === 0) {
                updateManualMode();
                return;
            }
            var issuerVal = $('#issuer_select').val() || '';

            // Отменяем предыдущий запрос
            if (calcXhr) calcXhr.abort();

            calcXhr = $.ajax({
                url: cfg.calcCarsTotalUrl,
                data: { 'car_ids': carIds, 'issuer': issuerVal },
                dataType: 'json',
                success: function (data) {
                    if (data.total) {
                        $('#manual_total_input').val(data.total);
                        $('#total_hint').text('Расчёт из ' + data.count + ' авто. Точная сумма — после сохранения.');
                    }
                }
            });
            updateManualMode();
        }

        // Реакция на изменение выбора автомобилей
        $('#cars_select').on('change', function () {
            fetchCarsTotal();
        });

        // Реакция на изменение выставителя (влияет на набор услуг)
        $('#issuer_select').on('change', function () {
            if (hasCarsSelected()) {
                fetchCarsTotal();
            }
        });

        // Загрузка существующих ручных позиций (инвойс без авто, но с позициями).
        // Для входящих инвойсов с AI-аудитом позиции отображаются в read-only таблице.
        (cfg.manualItems || []).forEach(function (item) {
            addManualItemRow(item.description, item.quantity, item.unit_price);
        });
        if (cfg.manualTotal !== null && cfg.manualTotal !== undefined) {
            $('#manual_total_input').val(cfg.manualTotal);
        }

        // Инициализация режима
        updateManualMode();

        // Auto-poll: для входящих инвойсов с незавершённым AI-анализом — обновить страницу
        if (cfg.startAuditPoll && cfg.auditPollUrl) {
            (function () {
                var pollUrl = cfg.auditPollUrl;
                var attempts = 0;
                var maxAttempts = 30;

                function poll() {
                    attempts++;
                    if (attempts > maxAttempts) return;
                    $.getJSON(pollUrl, function (data) {
                        if (data.ready) {
                            location.reload();
                        } else if (data.status === 'PENDING' || data.status === 'PROCESSING') {
                            setTimeout(poll, 3000);
                        }
                    });
                }
                // Проверяем, нужен ли polling (audit в процессе)
                $.getJSON(pollUrl, function (data) {
                    if (!data.ready && (data.status === 'PENDING' || data.status === 'PROCESSING')) {
                        $('#manual_total_input').css('opacity', '0.5');
                        $('#total_hint').html('⏳ AI-анализ PDF в процессе... страница обновится автоматически');
                        setTimeout(poll, 3000);
                    }
                });
            })();
        }

        // Кнопка "Перезапустить AI-анализ" — отправка через AJAX + polling
        $('#reanalyze-btn').on('click', function () {
            if (!confirm('Запустить AI-анализ заново? Текущие позиции будут пересозданы.')) return;
            var btn = $(this);
            var url = btn.data('url');
            var csrf = btn.data('csrf');
            btn.prop('disabled', true).text('⏳ Анализ...');
            $('#manual_total_input').css('opacity', '0.5');
            $('#total_hint').html('⏳ AI-анализ PDF в процессе... страница обновится автоматически');

            $.ajax({
                url: url,
                type: 'POST',
                data: { csrfmiddlewaretoken: csrf },
                success: function () {
                    if (cfg.auditPollUrl) {
                        var pollUrl = cfg.auditPollUrl;
                        var pollAfterReanalyze = function () {
                            $.getJSON(pollUrl, function (data) {
                                if (data.ready) {
                                    location.reload();
                                } else {
                                    setTimeout(pollAfterReanalyze, 3000);
                                }
                            });
                        };
                        setTimeout(pollAfterReanalyze, 3000);
                    }
                },
                error: function () {
                    btn.prop('disabled', false).text('🔄 Перезапустить AI-анализ');
                    alert('Ошибка при запуске анализа');
                }
            });
        });

        // Кнопка «Связать» из баннера кандидатов
        $(document).on('click', '.link-candidate-btn', function () {
            var number = $(this).data('number');
            var $sel = $('#linked_invoice_select');
            var option = new Option(number, number, true, true);
            $sel.append(option).trigger('change');
            $('#link-candidates-banner').slideUp(200);
        });
    });
})(window.django && window.django.jQuery || window.jQuery || window.$);
