/**
 * cm_toast.js — единые всплывающие уведомления админки.
 *
 * Раньше тосты жили внутри live_updates.js и были недоступны остальному коду,
 * из-за чего, например, ошибки HTMX уходили только в консоль браузера.
 *
 * Использование:
 *   window.cmToast('Сохранено');
 *   window.cmToast('Не удалось обновить', {kind: 'error'});
 *
 * Виды (kind): 'info' (по умолчанию), 'success', 'error'.
 * Ошибки живут дольше и не вытесняются: их важно успеть прочитать.
 */
(function () {
    'use strict';

    if (window.cmToast) return;

    var LIFETIME_MS = {info: 4000, success: 4000, error: 9000};
    var MAX_VISIBLE = 4;

    var STYLES = {
        info: {accent: '#6c5ce7', border: '#e0dcf8', icon: 'bi-arrow-repeat'},
        success: {accent: '#28a745', border: '#cfe9d6', icon: 'bi-check-circle'},
        error: {accent: '#dc3545', border: '#f3cdd2', icon: 'bi-exclamation-triangle'}
    };

    var wrap = null;

    function ensureWrap() {
        if (wrap && wrap.parentNode) return wrap;
        wrap = document.createElement('div');
        wrap.id = 'cm-toasts';
        wrap.setAttribute('aria-live', 'polite');
        wrap.style.cssText =
            'position:fixed;right:20px;bottom:20px;z-index:10050;' +
            'display:flex;flex-direction:column;gap:8px;pointer-events:none;';
        document.body.appendChild(wrap);
        return wrap;
    }

    function dismiss(toast) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(8px)';
        setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    }

    window.cmToast = function (text, options) {
        if (!text) return;
        var opts = options || {};
        var kind = STYLES[opts.kind] ? opts.kind : 'info';
        var style = STYLES[kind];
        var host = ensureWrap();

        // Вытесняем только нейтральные тосты — ошибки остаются на экране.
        while (host.children.length >= MAX_VISIBLE) {
            var oldest = null;
            for (var i = 0; i < host.children.length; i++) {
                if (host.children[i].dataset.kind !== 'error') {
                    oldest = host.children[i];
                    break;
                }
            }
            host.removeChild(oldest || host.firstChild);
        }

        var toast = document.createElement('div');
        toast.dataset.kind = kind;
        toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        toast.style.cssText =
            'background:#fff;color:#1a1a2e;border:1px solid ' + style.border + ';' +
            'border-left:4px solid ' + style.accent + ';border-radius:10px;' +
            'box-shadow:0 6px 18px rgba(30,20,60,.12),0 2px 6px rgba(30,20,60,.08);' +
            'padding:10px 14px;font-size:13px;font-weight:500;max-width:360px;' +
            'display:flex;align-items:center;gap:8px;pointer-events:auto;cursor:pointer;' +
            'opacity:0;transform:translateY(8px);transition:opacity .25s,transform .25s;';

        var icon = document.createElement('i');
        icon.className = 'bi ' + (opts.icon || style.icon);
        icon.style.cssText = 'color:' + style.accent + ';font-size:15px;flex-shrink:0;';
        toast.appendChild(icon);
        toast.appendChild(document.createTextNode(text));
        toast.addEventListener('click', function () { dismiss(toast); });
        host.appendChild(toast);

        requestAnimationFrame(function () {
            toast.style.opacity = '1';
            toast.style.transform = 'translateY(0)';
        });
        setTimeout(function () { dismiss(toast); }, opts.timeout || LIFETIME_MS[kind]);
        return toast;
    };
})();
