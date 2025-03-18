document.addEventListener('DOMContentLoaded', function() {
    var $ = django.jQuery || window.jQuery;
    if (!$) {
        console.error("jQuery not found");
        return;
    }
    console.log("Script loaded successfully - VERSION 40");  // Новая версия

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    const sessionId = getCookie('sessionid');
    const csrfToken = getCookie('csrftoken');
    console.log("Session ID from cookie:", sessionId || "Not found");
    console.log("CSRF Token from cookie:", csrfToken || "Not found");

    const $clientSelect = $('#id_client');
    const $carsSelect = $('#id_cars');
    console.log("Initial #id_client found:", $clientSelect.length ? "Yes" : "No");
    console.log("Initial #id_cars found:", $carsSelect.length ? "Yes" : "No");

    // Проверяем начальное состояние
    const initialClientId = $clientSelect.val();
    const initialCarId = $carsSelect.val();
    console.log("Initial client value:", initialClientId || "None");
    console.log("Initial selected car value:", initialCarId || "None");
    console.log("Initial cars options:", $carsSelect.find('option').length > 0 ? $carsSelect.html() : "None");

    // Сохраняем начальные опции "Cars" для редактирования
    const initialCarsHtml = $carsSelect.html();

    // Инициализируем "Cars" в зависимости от начального состояния
    if (!initialClientId) {
        $carsSelect.empty();
        $carsSelect.append($('<option></option>').val('').text('Select a client first'));
        console.log("No initial client, cars list initialized with 'Select a client first'");
    } else {
        console.log("Initial client detected, loading cars for clientId:", initialClientId);
        updateCars(initialClientId, initialCarId);
    }

    // Функция для привязки обработчиков Select2
    function bindSelect2Handler() {
        if ($clientSelect.hasClass('select2-hidden-accessible')) {
            console.log("Select2 is initialized, binding handlers");
            $clientSelect.off('select2:select change');  // Убираем старые обработчики
            $clientSelect.on('select2:select', function(e) {
                console.log("Select2:select event triggered on #id_client");
                var clientId = e.params.data.id;
                console.log("Select2 Client selected (val):", clientId || "None");
                console.log("Select2 Selected option text:", e.params.data.text || "None");
                console.log("Select2 Raw element value:", $(this).val() || "None");
                console.log("Select2 Full select HTML:", $(this).prop('outerHTML'));
                updateCars(clientId);
            });
            $clientSelect.on('change', function(e) {
                console.log("jQuery Change event triggered on #id_client");
                var clientId = $(this).val();
                console.log("jQuery Client selected (val):", clientId || "None");
                console.log("jQuery Selected option text:", $(this).find('option:selected').text() || "None");
                console.log("jQuery Raw element value:", this.value || "None");
                console.log("jQuery Full select HTML:", $(this).prop('outerHTML'));
                updateCars(clientId);
            });
            console.log("Select2 and change handlers bound successfully");
        } else {
            console.log("Select2 not yet initialized, retrying in 500ms");
            setTimeout(bindSelect2Handler, 500);
        }
    }

    // Запускаем привязку обработчиков
    bindSelect2Handler();

    // Обновление списка машин
    function updateCars(clientId, selectedCarId = null) {
        console.log("updateCars called with clientId:", clientId, "and selectedCarId:", selectedCarId);
        if (clientId && clientId !== "") {
            $.ajax({
                url: '/get_cars/',
                method: 'GET',
                data: { 'client_id': clientId },
                xhrFields: { withCredentials: true },
                beforeSend: function(xhr) {
                    xhr.setRequestHeader('X-CSRFToken', csrfToken);
                    xhr.setRequestHeader('X-Session-ID', sessionId);
                    console.log("Sending AJAX with session ID:", sessionId || "Not found");
                    console.log("Sending AJAX with CSRF token:", csrfToken || "Not found");
                    console.log("Sending AJAX with client_id:", clientId);
                },
                success: function(data) {
                    console.log("AJAX success, raw data:", data);
                    $carsSelect.empty();
                    if (!data.cars || data.cars.length === 0) {
                        console.log("No cars available for client:", clientId);
                        $carsSelect.append($('<option></option>').val('').text('No cars available'));
                        if (selectedCarId && initialCarsHtml) {
                            console.log("Restoring initial car due to no cars from AJAX:", selectedCarId);
                            $carsSelect.html(initialCarsHtml);
                        }
                    } else {
                        $.each(data.cars, function(index, car) {
                            console.log("Adding car:", car.id, car.text);
                            var $option = $('<option></option>').val(car.id).text(car.text);
                            if (selectedCarId && car.id == selectedCarId) {
                                $option.prop('selected', true);
                                console.log("Setting car", car.id, "as selected");
                            }
                            $carsSelect.append($option);
                        });
                    }
                },
                error: function(xhr, status, error) {
                    console.error("AJAX error:", status, error);
                    console.error("Response:", xhr.responseText);
                    if (initialCarsHtml) {
                        console.log("Restoring initial cars due to AJAX error");
                        $carsSelect.html(initialCarsHtml);
                    }
                }
            });
        } else {
            console.log("No client selected, resetting cars list");
            $carsSelect.empty();
            $carsSelect.append($('<option></option>').val('').text('Select a client first'));
        }
    }

    // Периодическая проверка для отладки
    let lastClientId = initialClientId;
    setInterval(function() {
        var currentClientId = $clientSelect.val();
        console.log("Periodic check - Current client value:", currentClientId || "None");
        if (currentClientId && currentClientId !== "" && currentClientId !== lastClientId) {
            console.log("Client value changed from", lastClientId, "to", currentClientId, "- triggering updateCars");
            updateCars(currentClientId);
            lastClientId = currentClientId;
        }
    }, 1000);

    setTimeout(function() {
        console.log("After 2s, #id_client found:", $clientSelect.length ? "Yes" : "No");
        console.log("After 2s, Select2 initialized:", $clientSelect.hasClass('select2-hidden-accessible') ? "Yes" : "No");
        console.log("After 2s, current client value:", $clientSelect.val() || "None");
        console.log("After 2s, current cars options:", $carsSelect.find('option').length > 0 ? $carsSelect.html() : "None");
    }, 2000);
});