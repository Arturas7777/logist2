/**
 * live_updates.js — WebSocket-клиент live-обновлений админки.
 *
 * Подключается к ws/updates/ (DataUpdateConsumer, только staff) и:
 *   • на changelist Car/Container подсвечивает изменённые строки
 *     и обновляет ячейки дней/цены без перезагрузки;
 *   • на дашборде (/admin/dashboard/) обновляет KPI-счётчики
 *     (debounce-перезагрузка фрагмента страницы);
 *   • показывает toast-уведомление о событии (в стиле DS).
 *
 * Формат событий (см. core/consumers.py, core/utils.py WebSocketBatcher,
 * core/services/car_lifecycle_service.py send_car_ws_notification):
 *   одиночное: {"model": "Car", "id": 1, "status": "...", "days": 3, ...}
 *   пакет:     [{"model": ..., "id": ..., ...}, ...]
 */
(function () {
    'use strict';

    if (window.__cmLiveUpdates) return; // защита от двойного подключения
    window.__cmLiveUpdates = true;

    var RECONNECT_BASE_MS = 2000;
    var RECONNECT_MAX_MS = 60000;
    var TOAST_LIFETIME_MS = 5000;
    var DASHBOARD_REFRESH_DEBOUNCE_MS = 4000;

    var reconnectAttempt = 0;
    var socket = null;

    var MODEL_LABELS = {
        Car: 'Авто',
        Container: 'Контейнер',
        AutoTransport: 'Автовоз',
        NewInvoice: 'Инвойс',
        Transaction: 'Транзакция'
    };

    // ── Контекст страницы ───────────────────────────────────────────────
    var ctx = window.__adminPageContext || {};
    var isDashboard = /^\/admin\/dashboard\//.test(window.location.pathname);

    function currentChangelistModel() {
        // На changelist model_name заполнен, а object_id пуст и это не форма
        if (!ctx.model_name || ctx.add || ctx.change) return null;
        return ctx.model_name; // 'car', 'container', ...
    }

    // ── Toast-уведомления ───────────────────────────────────────────────
    // Реализация общая для всей админки — cm_toast.js.
    function showToast(text) {
        if (window.cmToast) {
            window.cmToast(text, {icon: 'bi-arrow-repeat', timeout: TOAST_LIFETIME_MS});
        }
    }

    function describeEvents(events) {
        // Группируем по модели: «Авто: обновлено 3»
        var byModel = {};
        events.forEach(function (ev) {
            var label = MODEL_LABELS[ev.model] || ev.model || 'Объект';
            byModel[label] = (byModel[label] || 0) + 1;
        });
        return Object.keys(byModel)
            .map(function (label) {
                var n = byModel[label];
                return n === 1 ? label + ': обновлён' : label + ': обновлено ' + n;
            })
            .join(', ');
    }

    // ── Подсветка строк changelist ──────────────────────────────────────
    function injectHighlightStyle() {
        if (document.getElementById('cm-live-style')) return;
        var style = document.createElement('style');
        style.id = 'cm-live-style';
        style.textContent =
            '@keyframes cm-live-pulse {' +
            '  0% { background-color: #f0edff; }' +
            '  60% { background-color: #f0edff; }' +
            '  100% { background-color: transparent; }' +
            '}' +
            '#result_list tbody tr.cm-live-updated > td {' +
            '  animation: cm-live-pulse 2.5s ease-out;' +
            '}';
        document.head.appendChild(style);
    }

    function findRow(modelLower, objId) {
        var table = document.getElementById('result_list');
        if (!table) return null;
        // Строка содержит ссылку вида /admin/core/car/123/change/
        var sel = 'a[href*="/' + modelLower + '/' + objId + '/change/"]';
        var link = table.querySelector(sel);
        return link ? link.closest('tr') : null;
    }

    function highlightRow(row) {
        injectHighlightStyle();
        row.classList.remove('cm-live-updated');
        void row.offsetWidth; // рестарт CSS-анимации
        row.classList.add('cm-live-updated');
    }

    function applyChangelistUpdates(events) {
        var model = currentChangelistModel();
        if (!model) return 0;
        var touched = 0;
        events.forEach(function (ev) {
            if (!ev.model || String(ev.model).toLowerCase() !== model) return;
            var row = findRow(model, ev.id);
            if (row) {
                highlightRow(row);
                touched += 1;
            }
        });
        return touched;
    }

    // ── Обновление KPI дашборда ─────────────────────────────────────────
    var dashboardRefreshTimer = null;

    function scheduleDashboardRefresh() {
        if (!isDashboard) return;
        if (dashboardRefreshTimer) return;
        dashboardRefreshTimer = setTimeout(function () {
            dashboardRefreshTimer = null;
            fetch(window.location.href, { credentials: 'same-origin' })
                .then(function (resp) { return resp.text(); })
                .then(function (html) {
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    var fresh = doc.querySelectorAll('.kpi-value');
                    var current = document.querySelectorAll('.kpi-value');
                    if (fresh.length !== current.length) return;
                    current.forEach(function (el, i) {
                        if (el.textContent !== fresh[i].textContent) {
                            el.textContent = fresh[i].textContent;
                            el.style.transition = 'color .3s';
                            el.style.color = '#6c5ce7';
                            setTimeout(function () { el.style.color = ''; }, 1500);
                        }
                    });
                })
                .catch(function () { /* сеть/парсинг — молча пропускаем */ });
        }, DASHBOARD_REFRESH_DEBOUNCE_MS);
    }

    // ── Обработка входящих событий ──────────────────────────────────────
    function handleMessage(raw) {
        var data;
        try {
            data = JSON.parse(raw);
        } catch (e) {
            return;
        }
        var events = Array.isArray(data) ? data : [data];
        // Отбрасываем служебные ответы ({"message": "Update received"})
        events = events.filter(function (ev) { return ev && ev.model; });
        if (!events.length) return;

        applyChangelistUpdates(events);
        scheduleDashboardRefresh();
        showToast(describeEvents(events));
    }

    // ── WebSocket с reconnect ───────────────────────────────────────────
    function connect() {
        var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        var url = proto + window.location.host + '/ws/updates/';
        try {
            socket = new WebSocket(url);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        socket.addEventListener('open', function () {
            reconnectAttempt = 0;
        });
        socket.addEventListener('message', function (event) {
            handleMessage(event.data);
        });
        socket.addEventListener('close', function (event) {
            socket = null;
            // 4401/4403 — отказ по правам, не переподключаемся
            if (event.code === 4401 || event.code === 4403) return;
            scheduleReconnect();
        });
        socket.addEventListener('error', function () {
            // close придёт следом — reconnect там
        });
    }

    function scheduleReconnect() {
        reconnectAttempt += 1;
        var delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt - 1), RECONNECT_MAX_MS);
        setTimeout(function () {
            if (document.visibilityState === 'hidden') {
                // Вкладка в фоне — подключимся, когда пользователь вернётся
                var onVisible = function () {
                    document.removeEventListener('visibilitychange', onVisible);
                    connect();
                };
                document.addEventListener('visibilitychange', onVisible);
                return;
            }
            connect();
        }, delay);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', connect);
    } else {
        connect();
    }
})();
