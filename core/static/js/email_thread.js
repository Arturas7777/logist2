/**
 * Общая лента переписки админки (U1).
 *
 * Корень: элемент с data-email-thread="1" и
 *   data-scope, data-object-id, data-mark-url, data-updates-url,
 *   data-list, data-badge, data-status, data-sync, data-compose,
 *   data-compose-to, data-compose-subject, data-place="top" (необязательно).
 * Список писем — .cm-chat (или id из data-list).
 */
(function () {
    'use strict';

    function csrf() {
        var input = document.querySelector('[name=csrfmiddlewaretoken]');
        return (input && input.value) || '';
    }

    function init(section) {
        if (!section || section.dataset.emailThreadBound === '1') return;
        section.dataset.emailThreadBound = '1';

        if (section.dataset.place === 'top') {
            var formMain = document.querySelector('.cm-form-main');
            if (formMain) {
                var form = formMain.querySelector('form') || formMain;
                var firstBlock = null;
                var kids = form.children;
                for (var i = 0; i < kids.length; i++) {
                    var ch = kids[i];
                    if (ch === section) continue;
                    if (ch.tagName === 'FIELDSET' || ch.classList.contains('inline-group')) {
                        firstBlock = ch;
                        break;
                    }
                }
                if (firstBlock) form.insertBefore(section, firstBlock);
                else form.insertBefore(section, form.firstChild);
            }
        }

        function pick(sel, fallback) {
            var chosen = sel || fallback;
            if (!chosen) return null;
            return section.querySelector(chosen);
        }

        var details = section.querySelector(':scope > details') || section.querySelector('details');
        var list = pick(section.dataset.list, '.cm-chat');
        var badge = pick(section.dataset.badge, '.cm-email-badge');
        var status = pick(section.dataset.status, '.cm-emails-status');
        var syncBtn = pick(section.dataset.sync, '');
        var composeBtn = pick(section.dataset.compose, '');
        var scope = section.dataset.scope;
        var objectId = section.dataset.objectId;
        var markUrl = section.dataset.markUrl;
        var updatesUrl = section.dataset.updatesUrl;

        function refreshBadge() {
            if (!badge || !list) return;
            var unread = list.querySelectorAll('.cm-msg[data-unread="1"]').length;
            var total = list.querySelectorAll('.cm-msg').length;
            if (badge.classList.contains('rc-chip')) {
                badge.textContent = unread ? (unread + '/' + total) : String(total);
                badge.className = 'rc-chip ' + (unread ? 'alert' : 'muted');
                return;
            }
            badge.classList.toggle('has-unread', unread > 0);
            badge.textContent = unread > 0 ? (unread + '/' + total) : String(total);
            badge.title = unread > 0 ? (unread + ' непрочитанных из ' + total) : ('Всего писем: ' + total);
        }
        refreshBadge();

        function markAllRead() {
            if (!list || !markUrl) return;
            var unreadEls = list.querySelectorAll('.cm-msg[data-unread="1"]');
            if (!unreadEls.length) return;
            fetch(markUrl, {
                method: 'POST',
                headers: {'X-CSRFToken': csrf()},
                credentials: 'same-origin',
            }).then(function () {
                unreadEls.forEach(function (el) { el.setAttribute('data-unread', '0'); });
                refreshBadge();
            }).catch(function () {});
        }

        var firstOpen = true;
        if (details) {
            details.addEventListener('toggle', function () {
                if (!details.open || !firstOpen) return;
                firstOpen = false;
                if (list) list.scrollTop = 0;
                markAllRead();
            });
        }
        section.emailThreadMarkRead = function () {
            if (!firstOpen) return;
            firstOpen = false;
            markAllRead();
        };
        if (section.dataset.export === 'tr') {
            window.TRMailPanel = {markAllRead: section.emailThreadMarkRead};
        }

        function insertBubble(html) {
            if (!list || !html) return;
            var tmp = document.createElement('div');
            tmp.innerHTML = html;
            var node = tmp.firstElementChild;
            if (!node) return;
            list.insertBefore(node, list.firstChild);
            var empty = section.querySelector('[data-email-empty]');
            if (empty) empty.remove();
            refreshBadge();
        }

        if (syncBtn) {
            syncBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                syncBtn.disabled = true;
                if (status) status.textContent = '';
                fetch('/core/emails/sync/', {
                    method: 'POST',
                    headers: {'X-CSRFToken': csrf()},
                    credentials: 'same-origin',
                }).then(function (r) {
                    return r.json().then(function (d) { return {ok: r.ok, data: d}; });
                }).then(function (res) {
                    if (status) {
                        status.style.color = res.ok && res.data.ok ? '#16a34a' : '#b91c1c';
                        status.textContent = res.ok && res.data.ok
                            ? 'Синхронизация запущена'
                            : ('Ошибка: ' + ((res.data && res.data.error) || ''));
                    }
                    syncBtn.disabled = false;
                }).catch(function () {
                    syncBtn.disabled = false;
                });
            });
        }

        if (composeBtn && section.dataset.composer !== 'legacy') {
            composeBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (!window.CMComposer) return;
                window.CMComposer.open({
                    mode: 'compose',
                    scope: scope,
                    scopeId: objectId,
                    to: section.dataset.composeTo || '',
                    subject: section.dataset.composeSubject || '',
                    body: '',
                    maxMb: 25,
                    onSuccess: function (data) { insertBubble(data && data.html); },
                });
            });
        }

        if (list && section.dataset.composer !== 'legacy') {
            list.addEventListener('click', function (e) {
                var btn = e.target.closest('.cm-reply-btn');
                if (!btn || !window.CMComposer) return;
                e.preventDefault();
                e.stopPropagation();
                window.CMComposer.openReplyFor(btn.dataset.emailId, scope, objectId, function (data) {
                    insertBubble(data && data.html);
                });
            });
        }

        if (!updatesUrl) return;
        var inFlight = false;
        function latestId() {
            var maxId = 0;
            if (!list) return 0;
            list.querySelectorAll('.cm-msg[data-email-id]').forEach(function (el) {
                var id = parseInt(el.getAttribute('data-email-id'), 10) || 0;
                if (id > maxId) maxId = id;
            });
            return maxId;
        }
        function pollOnce() {
            if (inFlight || document.visibilityState !== 'visible') return;
            if (details && !details.open) return;
            inFlight = true;
            fetch(updatesUrl + (updatesUrl.indexOf('?') === -1 ? '?' : '&') + 'since_id=' + latestId(), {
                credentials: 'same-origin',
            }).then(function (r) {
                return r.ok ? r.json() : null;
            }).then(function (data) {
                if (!data || !data.ok || !Array.isArray(data.bubbles)) return;
                data.bubbles.slice().reverse().forEach(function (b) {
                    if (list && list.querySelector('.cm-msg[data-email-id="' + b.id + '"]')) return;
                    insertBubble(b.html);
                });
                if (details && details.open) markAllRead();
            }).catch(function () {}).finally(function () { inFlight = false; });
        }
        setInterval(pollOnce, 30000);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') pollOnce();
        });
    }

    function boot() {
        document.querySelectorAll('[data-email-thread="1"]').forEach(init);
    }

    window.EmailThread = {init: init, boot: boot};
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
