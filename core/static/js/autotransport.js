/* ============================================
   🚛 АВТОВОЗЫ - ДИНАМИЧЕСКИЕ ФУНКЦИИ
   ============================================ */

(function($) {
    'use strict';
    
    $(document).ready(function() {

        // ============================================
        // 1. АВТОЗАПОЛНЕНИЕ EORI КОДА ИЗ ПЕРЕВОЗЧИКА
        // ============================================
        const carrierSelect = $('#id_carrier');
        const eoriInput = $('#id_eori_code');

        if (carrierSelect.length && eoriInput.length) {
            carrierSelect.on('change', function() {
                const carrierId = $(this).val();
                if (!carrierId) {
                    eoriInput.val('');
                    return;
                }

                $.ajax({
                    url: '/core/api/carrier/' + carrierId + '/info/',
                    method: 'GET',
                    success: function(data) {
                        if (data.eori_code) {
                            eoriInput.val(data.eori_code);
                        }
                        
                        // Обновляем списки автовозов и водителей
                        updateTrucksList(data.trucks);
                        updateDriversList(data.drivers);
                    },
                    error: function(xhr, status, error) {
                        console.error('Ошибка загрузки данных перевозчика:', error);
                    }
                });
            });
        }

        // ============================================
        // 2. ОБНОВЛЕНИЕ СПИСКА АВТОВОЗОВ
        // ============================================
        function updateTrucksList(trucks) {
            const truckSelect = $('#id_truck');
            if (!truckSelect.length) return;

            const currentValue = truckSelect.val();
            truckSelect.empty();
            truckSelect.append('<option value="">---------</option>');

            trucks.forEach(function(truck) {
                const option = $('<option></option>')
                    .val(truck.id)
                    .text(truck.full_number);
                truckSelect.append(option);
            });

            if (currentValue) {
                truckSelect.val(currentValue);
            }
        }

        // ============================================
        // 3. ОБНОВЛЕНИЕ СПИСКА ВОДИТЕЛЕЙ
        // ============================================
        function updateDriversList(drivers) {
            const driverSelect = $('#id_driver');
            if (!driverSelect.length) return;

            const currentValue = driverSelect.val();
            driverSelect.empty();
            driverSelect.append('<option value="">---------</option>');

            drivers.forEach(function(driver) {
                const option = $('<option></option>')
                    .val(driver.id)
                    .text(driver.full_name);
                driverSelect.append(option);
            });

            if (currentValue) {
                driverSelect.val(currentValue);
            }
        }

        // ============================================
        // 4. АВТОЗАПОЛНЕНИЕ ТЕЛЕФОНА ВОДИТЕЛЯ
        // ============================================
        const driverSelect = $('#id_driver');
        const driverPhoneInput = $('#id_driver_phone');

        if (driverSelect.length && driverPhoneInput.length) {
            driverSelect.on('change', function() {
                const driverId = $(this).val();
                if (!driverId) {
                    return;
                }

                $.ajax({
                    url: '/core/api/driver/' + driverId + '/phone/',
                    method: 'GET',
                    success: function(data) {
                        if (data.phone) {
                            driverPhoneInput.val(data.phone);
                        }
                    },
                    error: function(xhr, status, error) {
                        console.error('Ошибка загрузки телефона водителя:', error);
                    }
                });
            });

            // Обновление телефона водителя при ручном вводе
            let phoneUpdateTimeout;
            driverPhoneInput.on('input', function() {
                clearTimeout(phoneUpdateTimeout);
                const newPhone = $(this).val();
                const driverId = driverSelect.val();

                if (!driverId || !newPhone) return;

                phoneUpdateTimeout = setTimeout(function() {
                    $.ajax({
                        url: '/core/api/driver/update-phone/',
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': $('[name=csrfmiddlewaretoken]').val()
                        },
                        data: {
                            driver_id: driverId,
                            phone: newPhone
                        },
                        success: function(data) {
                        },
                        error: function(xhr, status, error) {
                            console.error('Ошибка обновления телефона:', error);
                        }
                    });
                }, 1000); // Задержка 1 секунда после ввода
            });
        }

        // ============================================
        // 5. АВТОЗАПОЛНЕНИЕ ГРАНИЦЫ ПЕРЕСЕЧЕНИЯ
        // ============================================
        const borderInput = $('#id_border_crossing');
        
        if (borderInput.length) {
            let borderTimeout;
            let bordersCache = null;

            // Загружаем список границ при фокусе
            borderInput.on('focus', function() {
                if (bordersCache !== null) {
                    showBorderSuggestions(bordersCache);
                    return;
                }

                $.ajax({
                    url: '/core/api/border-crossings/',
                    method: 'GET',
                    success: function(data) {
                        bordersCache = data.borders;
                        showBorderSuggestions(bordersCache);
                    },
                    error: function(xhr, status, error) {
                        console.error('Ошибка загрузки границ:', error);
                    }
                });
            });

            // Фильтрация при вводе
            borderInput.on('input', function() {
                clearTimeout(borderTimeout);
                const query = $(this).val().toLowerCase();

                if (!bordersCache) return;

                borderTimeout = setTimeout(function() {
                    const filtered = bordersCache.filter(function(border) {
                        return border.toLowerCase().includes(query);
                    });
                    showBorderSuggestions(filtered);
                }, 300);
            });

            function showBorderSuggestions(borders) {
                // Удаляем старый список
                $('.border-suggestions').remove();

                if (!borders || borders.length === 0) return;

                const suggestions = $('<div class="border-suggestions"></div>');
                borders.forEach(function(border) {
                    const item = $('<div class="border-suggestion-item"></div>')
                        .text(border)
                        .on('click', function() {
                            borderInput.val(border);
                            suggestions.remove();
                        });
                    suggestions.append(item);
                });

                borderInput.after(suggestions);
            }

            // Скрываем подсказки при клике вне поля
            $(document).on('click', function(e) {
                if (!$(e.target).closest('.form-group').length) {
                    $('.border-suggestions').remove();
                }
            });
        }

        // ============================================
        // 6. СОЗДАНИЕ НОВОГО АВТОВОЗА/ВОДИТЕЛЯ
        // ============================================
        
        // Создание нового автовоза при вводе в ручное поле
        const truckManualInput = $('#id_truck_number_manual');
        const trailerManualInput = $('#id_trailer_number_manual');
        
        if (truckManualInput.length && carrierSelect.length) {
            function createNewTruck() {
                const carrierId = carrierSelect.val();
                const truckNumber = truckManualInput.val();
                const trailerNumber = trailerManualInput.val();

                if (!carrierId || !truckNumber) return;

                $.ajax({
                    url: '/core/api/carrier/create-truck/',
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': $('[name=csrfmiddlewaretoken]').val()
                    },
                    data: {
                        carrier_id: carrierId,
                        truck_number: truckNumber,
                        trailer_number: trailerNumber
                    },
                    success: function(data) {
                        // Обновляем список
                        const truckSelect = $('#id_truck');
                        const option = $('<option></option>')
                            .val(data.truck.id)
                            .text(data.truck.full_number)
                            .prop('selected', true);
                        truckSelect.append(option);
                    },
                    error: function(xhr, status, error) {
                        console.error('Ошибка создания автовоза:', error);
                    }
                });
            }

            truckManualInput.on('blur', createNewTruck);
            trailerManualInput.on('blur', createNewTruck);
        }

        // Создание нового водителя при вводе в ручное поле
        const driverManualInput = $('#id_driver_name_manual');
        
        if (driverManualInput.length && carrierSelect.length) {
            driverManualInput.on('blur', function() {
                const carrierId = carrierSelect.val();
                const fullName = $(this).val().trim();

                if (!carrierId || !fullName) return;

                // Разделяем на имя и фамилию
                const nameParts = fullName.split(' ');
                const firstName = nameParts[0] || '';
                const lastName = nameParts.slice(1).join(' ') || '';

                if (!firstName || !lastName) {
                    console.warn('⚠️ Введите полное имя (Имя Фамилия)');
                    return;
                }

                const phone = driverPhoneInput.val() || '';

                $.ajax({
                    url: '/core/api/carrier/create-driver/',
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': $('[name=csrfmiddlewaretoken]').val()
                    },
                    data: {
                        carrier_id: carrierId,
                        first_name: firstName,
                        last_name: lastName,
                        phone: phone
                    },
                    success: function(data) {
                        // Обновляем список
                        const driverSelect = $('#id_driver');
                        const option = $('<option></option>')
                            .val(data.driver.id)
                            .text(data.driver.full_name)
                            .prop('selected', true);
                        driverSelect.append(option);
                    },
                    error: function(xhr, status, error) {
                        console.error('Ошибка создания водителя:', error);
                    }
                });
            });
        }

        // ============================================
        // 7. ИНИЦИАЛИЗАЦИЯ SELECT2 ДЛЯ АВТОМОБИЛЕЙ С AJAX
        // ============================================
        const carsSelect = $('#cars_select');
        
        if (carsSelect.length && typeof $.fn.select2 === 'function') {
            // Получаем уже выбранные ID
            function getSelectedIds() {
                var selected = [];
                carsSelect.find('option:selected').each(function() {
                    selected.push($(this).val());
                });
                return selected;
            }

            carsSelect.select2({
                placeholder: 'Введите VIN или марку для поиска...',
                allowClear: true,
                width: '100%',
                minimumInputLength: 2,
                ajax: {
                    url: '/core/api/search-cars/',
                    dataType: 'json',
                    delay: 250,
                    data: function(params) {
                        return {
                            q: params.term,
                            selected: getSelectedIds()
                        };
                    },
                    processResults: function(data) {
                        return {
                            results: data.results.map(function(car) {
                                return {
                                    id: car.id,
                                    text: car.text,
                                    vin: car.vin,
                                    status: car.status || 'UNKNOWN'
                                };
                            })
                        };
                    },
                    cache: true
                },
                templateResult: function(state) {
                    if (!state.id) return state.text;
                    var statusLabels = {
                        'FLOATING': '🚢 В пути',
                        'IN_PORT': '⚓ В порту',
                        'UNLOADED': '✅ Разгружен',
                        'TRANSFERRED': '📦 Передан'
                    };
                    var statusLabel = statusLabels[state.status] || '';
                    return $('<span>' + state.text + (statusLabel ? ' <small style="opacity:0.7;">' + statusLabel + '</small>' : '') + '</span>');
                },
                templateSelection: function(state) {
                    if (!state.id) return state.text;
                    return $('<span data-status="' + (state.status || 'UNKNOWN') + '">' + state.text + '</span>');
                }
            });

            // Добавляем data-status к созданным тегам
            carsSelect.on('select2:select', function(e) {
                var status = e.params.data.status || 'UNKNOWN';
                setTimeout(function() {
                    $('.select2-selection__choice').each(function() {
                        var $choice = $(this);
                        var $span = $choice.find('span[data-status]');
                        if ($span.length) {
                            $choice.attr('data-status', $span.data('status'));
                        }
                    });
                }, 10);
            });

            // Инициализируем статусы для уже выбранных тегов
            setTimeout(function() {
                $('.select2-selection__choice').each(function() {
                    var $choice = $(this);
                    var $span = $choice.find('span[data-status]');
                    if ($span.length) {
                        $choice.attr('data-status', $span.data('status'));
                    }
                });
            }, 100);
        }

        // ============================================
        // 8. ПРЕДУПРЕЖДЕНИЕ ПРИ ФОРМИРОВАНИИ
        // ============================================
        $('form').on('submit', function(e) {
            const status = $('#id_status').val();
            const carsCount = $('#id_cars option:selected').length;

            if (status === 'FORMED' && carsCount === 0) {
                if (!confirm('⚠️ Автовоз не содержит автомобилей. Инвойсы не будут созданы. Продолжить?')) {
                    e.preventDefault();
                    return false;
                }
            }

            if (status === 'FORMED' && carsCount > 0) {
                const message = '✅ После сохранения будут автоматически созданы инвойсы для ' + 
                                carsCount + ' авто. Продолжить?';
                if (!confirm(message)) {
                    e.preventDefault();
                    return false;
                }
            }
        });

    });

})(window.django && window.django.jQuery ? window.django.jQuery : window.jQuery);
