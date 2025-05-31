console.log('Script loaded successfully - VERSION 42');

document.addEventListener('DOMContentLoaded', function() {
    console.log('Session ID from cookie:', document.cookie.match(/sessionid=([^;]+)/)?.[1] || 'Not found');
    console.log('CSRF Token from cookie:', document.cookie.match(/csrftoken=([^;]+)/)?.[1] || 'Not found');

    const clientSelect = document.querySelector('#id_client');
    const carsList = document.querySelector('#id_available_cars');

    console.log('Initial #id_client found:', !!clientSelect);
    console.log('Initial #id_cars found:', !!carsList);

    if (!clientSelect || !carsList) {
        console.error('Required elements not found: clientSelect=', !!clientSelect, 'carsList=', !!carsList);
        return;
    }

    console.log('Initial client value:', clientSelect.value || 'None');
    console.log('Initial selected car value:', carsList.value || 'None');
    console.log('Initial cars options:', carsList.innerHTML || 'None');

    if (!clientSelect.value) {
        console.log('No initial client, cars list initialized with "Select a client first"');
        carsList.innerHTML = '<option value="">Сначала выберите клиента</option>';
    }

    let initializeSelect2 = function() {
        if (window.jQuery && jQuery.fn.select2 && !jQuery('#id_client').data('select2')) {
            console.log('Select2 is initialized, binding handlers');
            jQuery('#id_client').select2({
                placeholder: "Выберите клиента",
                allowClear: true
            });
            jQuery('#id_client').on('select2:select', function() {
                console.log('Select2 selection triggered');
                clientSelect.dispatchEvent(new Event('change'));
            });
            jQuery('#id_client').on('select2:open', function() {
                console.log('Select2 dropdown opened');
            });
            jQuery('#id_client').on('select2:clear', function() {
                console.log('Select2 cleared, dispatching change event');
                clientSelect.value = '';
                clientSelect.dispatchEvent(new Event('change'));
            });
            console.log('Select2 and change handlers bound successfully');
        } else {
            console.log('Select2 not yet initialized, retrying in 500ms');
            setTimeout(initializeSelect2, 500);
        }
    };

    initializeSelect2();

    let lastClientValue = clientSelect.value;
    setInterval(function() {
        const currentClientValue = clientSelect.value;
        console.log('Periodic check - Current client value:', currentClientValue || 'None');
        if (currentClientValue !== lastClientValue) {
            console.log('Client value changed from', lastClientValue, 'to', currentClientValue);
            lastClientValue = currentClientValue;
        }
    }, 500);

    setTimeout(function() {
        console.log('After 2s, #id_client found:', !!clientSelect);
        console.log('After 2s, Select2 initialized:', !!jQuery('#id_client').data('select2'));
        console.log('After 2s, current client value:', clientSelect.value || 'None');
        console.log('After 2s, current cars options:', carsList.innerHTML || 'None');
    }, 2000);
});