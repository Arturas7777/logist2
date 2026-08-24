/**
 * logist2_htmx.js — обработка сбоев HTMX-запросов.
 *
 * Без этого неудавшийся запрос выглядит как «ничего не произошло»:
 * HTMX по умолчанию молча оставляет страницу как есть.
 */
(function () {
    'use strict';

    function notify(text) {
        if (window.cmToast) {
            window.cmToast(text, {kind: 'error'});
        }
    }

    document.addEventListener('htmx:responseError', function (event) {
        var status = 0;
        try {
            status = event.detail.xhr.status;
            console.error('HTMX response error:', status, event.detail.xhr.response);
        } catch (e) {}

        if (status === 403) {
            notify('Недостаточно прав или истекла сессия. Обновите страницу.');
        } else if (status === 404) {
            notify('Данные не найдены — возможно, запись удалена.');
        } else if (status >= 500) {
            notify('Ошибка на сервере. Изменения не сохранены.');
        } else {
            notify('Запрос отклонён (код ' + (status || '—') + '). Изменения не сохранены.');
        }
    });

    document.addEventListener('htmx:sendError', function () {
        notify('Нет связи с сервером. Проверьте подключение.');
    });

    document.addEventListener('htmx:timeout', function () {
        notify('Сервер не ответил вовремя. Повторите попытку.');
    });
})();
