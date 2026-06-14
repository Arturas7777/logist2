(function() {
    'use strict';

    // При выборе линии в форме контейнера подтягиваем её THS по умолчанию
    // (Line.ths_fee) в поле «Оплата линиям» (#id_ths). Значение можно
    // изменить вручную — повторный выбор линии НЕ перезаписывает уже
    // введённое ненулевое значение (подставляем только в пустое / 0).

    var API_BASE = '/admin/core/line/';

    function fillThsFromLine(lineId, thsInput) {
        if (!lineId) return;
        fetch(API_BASE + lineId + '/ths/')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var raw = data && data.ths_fee;
                if (raw === undefined || raw === null || raw === '') return;
                var num = parseFloat(String(raw).replace(',', '.'));
                if (isNaN(num) || num <= 0) return;

                var cur = (thsInput.value || '').trim();
                var curNum = parseFloat(cur.replace(',', '.'));
                if (cur === '' || isNaN(curNum) || curNum === 0) {
                    thsInput.value = num.toFixed(2);
                }
            })
            .catch(function() {});
    }

    function init() {
        var lineSelect = document.querySelector('#id_line');
        var thsInput = document.querySelector('#id_ths');
        if (!lineSelect || !thsInput) return;

        function onLineChange() {
            fillThsFromLine(lineSelect.value, thsInput);
        }

        // line — autocomplete_field (Select2): change приходит через
        // jQuery.trigger('change'), нативный addEventListener его не ловит.
        // Регистрируем обоими способами (как в warehouse_address.js).
        var $ = window.django && window.django.jQuery;
        if ($) {
            $(lineSelect).on('change', onLineChange);
        } else {
            lineSelect.addEventListener('change', onLineChange);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
