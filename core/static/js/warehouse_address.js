(function() {
    'use strict';

    var API_BASE = '/admin/core/warehouse/';

    function fetchAddresses(warehouseId, callback) {
        if (!warehouseId) {
            callback([]);
            return;
        }
        fetch(API_BASE + warehouseId + '/addresses/')
            .then(function(r) { return r.json(); })
            .then(function(data) { callback(data.addresses || []); })
            .catch(function() { callback([]); });
    }

    function updateAddressSelect(addressSelect, addresses, currentValue) {
        addressSelect.innerHTML = '';

        if (addresses.length === 0) {
            var empty = document.createElement('option');
            empty.value = '1';
            empty.textContent = '\u2014';
            addressSelect.appendChild(empty);
            return;
        }

        addresses.forEach(function(addr) {
            var opt = document.createElement('option');
            opt.value = addr.value;
            opt.textContent = addr.label;
            if (String(addr.value) === String(currentValue)) {
                opt.selected = true;
            }
            addressSelect.appendChild(opt);
        });
    }

    function init() {
        var warehouseSelect = document.querySelector('#id_warehouse');
        var addressSelect = document.querySelector('#id_unload_site');

        if (!warehouseSelect || !addressSelect) return;

        var savedValue = addressSelect.value || '1';
        var lastWarehouseId = warehouseSelect.value || '';

        if (lastWarehouseId) {
            fetchAddresses(lastWarehouseId, function(addresses) {
                updateAddressSelect(addressSelect, addresses, savedValue);
            });
        } else {
            updateAddressSelect(addressSelect, [], '1');
        }

        function onWarehouseChange() {
            var newId = warehouseSelect.value || '';
            // Select2 при инициализации autocomplete шлёт jQuery `change`
            // на уже выбранный склад. Раньше это сбрасывало площадку на 1
            // (у ATLANTIC это Nevezio 5), и после сохранения Minijos 181
            // в карточке снова показывалась первая площадка.
            if (newId === lastWarehouseId) {
                return;
            }
            lastWarehouseId = newId;
            fetchAddresses(newId, function(addresses) {
                updateAddressSelect(addressSelect, addresses, '1');
            });
        }

        // M5 (autocomplete_fields для warehouse) ломал нативный change-event:
        // Django admin оборачивает <select> в Select2, а Select2 триггерит
        // change через jQuery.trigger('change') — это jQuery-only event,
        // нативный addEventListener его не ловит. Поэтому регистрируем
        // обработчик ОБОИМИ способами: через django.jQuery (поймает события
        // от Select2) и нативно (страховка на случай если autocomplete
        // отключён или jQuery отсутствует).
        var $ = window.django && window.django.jQuery;
        if ($) {
            $(warehouseSelect).on('change', onWarehouseChange);
        } else {
            warehouseSelect.addEventListener('change', onWarehouseChange);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
