/*
 * Просмотр скана в панели «Документы и сверка» карточки контейнера
 * и на странице задачи обработки скана.
 *
 * Зачем свой просмотрщик: окно предпросмотра узкое, а сверять нужно
 * мелкие детали — VIN, номер тайтла, вес. Встроенный в браузер вьюер PDF
 * внутри iframe не даёт ни зума к точке под курсором, ни перетаскивания,
 * поэтому страница рисуется через pdf.js на canvas: колесо — масштаб к
 * курсору, зажатая мышь — сдвиг, двойной клик — быстрый зум участка.
 *
 * PDF перерисовывается на canvas под текущий масштаб (не растягивается),
 * поэтому текст остаётся резким при любом увеличении.
 *
 * Если pdf.js не загрузился (нет сети до CDN), показываем прежний iframe —
 * предпросмотр деградирует до штатного вьюера браузера, а не пропадает.
 */
(function () {
    'use strict';

    var PDFJS_BASE = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/';
    var MIN_ZOOM = 0.6;
    var MAX_ZOOM = 10;
    // Больше 8192 px по стороне canvas браузеры не гарантируют — на таком
    // зуме дальше растягиваем уже отрисованное.
    var MAX_CANVAS_SIDE = 8192;
    var RENDER_DELAY = 130;

    var libPromise = null;

    function loadPdfLib() {
        if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
        if (libPromise) return libPromise;
        libPromise = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = PDFJS_BASE + 'pdf.min.js';
            script.onload = function () {
                if (!window.pdfjsLib) {
                    reject(new Error('pdf.js загрузился без pdfjsLib'));
                    return;
                }
                window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_BASE + 'pdf.worker.min.js';
                resolve(window.pdfjsLib);
            };
            script.onerror = function () { reject(new Error('pdf.js недоступен')); };
            document.head.appendChild(script);
        });
        return libPromise;
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function iconButton(icon, title) {
        var btn = el('button', 'cm-sr-doc-btn');
        // type=button обязателен: панель живёт внутри формы карточки
        // контейнера, иначе клик по кнопке отправит форму.
        btn.type = 'button';
        btn.title = title;
        btn.appendChild(el('i', 'bi ' + icon));
        return btn;
    }

    function showFallbackFrame(host, url) {
        host.innerHTML = '';
        var frame = el('iframe', 'cm-sr-doc-frame');
        frame.src = url;
        frame.title = 'Скан документа';
        host.appendChild(frame);
    }

    function setupViewer(host, url, kind, pdfDoc) {
        var stage = el('div', 'cm-sr-doc-stage');
        var layer = el('div', 'cm-sr-doc-layer');
        stage.appendChild(layer);
        stage.appendChild(el('div', 'cm-sr-doc-hint', 'Колесо — масштаб, перетащить — сдвиг'));
        host.innerHTML = '';
        host.appendChild(stage);

        // Логический размер содержимого при zoom = 1 (страница по ширине окна).
        var baseW = 0;
        var baseH = 0;
        var zoom = 1;
        var drawnZoom = 1;   // масштаб, под который отрисован canvas
        var panX = 0;
        var panY = 0;
        var page = 1;
        var pages = pdfDoc ? pdfDoc.numPages : 1;

        var renderTask = null;
        var renderTimer = null;

        var bar = host.parentNode ? host.parentNode.querySelector('.cm-sr-doc-bar') : null;
        var tools = el('div', 'cm-sr-doc-tools');
        var pageBox = el('span', 'cm-sr-doc-pages');
        var prevBtn = iconButton('bi-chevron-left', 'Предыдущая страница');
        var nextBtn = iconButton('bi-chevron-right', 'Следующая страница');
        var pageLabel = el('span', 'cm-sr-doc-page-label');
        var outBtn = iconButton('bi-dash-lg', 'Уменьшить');
        var inBtn = iconButton('bi-plus-lg', 'Увеличить');
        var resetBtn = iconButton('bi-arrows-angle-contract', 'Показать страницу целиком');
        var zoomLabel = el('span', 'cm-sr-doc-zoom', '100%');

        if (pages > 1) {
            pageBox.appendChild(prevBtn);
            pageBox.appendChild(pageLabel);
            pageBox.appendChild(nextBtn);
            tools.appendChild(pageBox);
        }
        tools.appendChild(outBtn);
        tools.appendChild(zoomLabel);
        tools.appendChild(inBtn);
        tools.appendChild(resetBtn);
        if (bar) bar.insertBefore(tools, bar.lastElementChild);

        function stageSize() {
            return { w: stage.clientWidth || 1, h: stage.clientHeight || 1 };
        }

        function clampPan() {
            var size = stageSize();
            var w = baseW * zoom;
            var h = baseH * zoom;
            panX = w <= size.w ? (size.w - w) / 2 : Math.min(0, Math.max(size.w - w, panX));
            panY = h <= size.h ? (size.h - h) / 2 : Math.min(0, Math.max(size.h - h, panY));
        }

        function applyTransform() {
            layer.style.transform = 'translate(' + panX.toFixed(2) + 'px,' + panY.toFixed(2) + 'px)' +
                ' scale(' + (zoom / drawnZoom).toFixed(4) + ')';
        }

        function updateLabels() {
            zoomLabel.textContent = Math.round(zoom * 100) + '%';
            pageLabel.textContent = page + ' / ' + pages;
            prevBtn.disabled = page <= 1;
            nextBtn.disabled = page >= pages;
        }

        function scheduleRender() {
            if (!pdfDoc) return;
            clearTimeout(renderTimer);
            renderTimer = setTimeout(renderPage, RENDER_DELAY);
        }

        function renderPage() {
            if (!pdfDoc) return;
            var targetZoom = zoom;
            var wanted = page;
            pdfDoc.getPage(wanted).then(function (pdfPage) {
                var unit = pdfPage.getViewport({ scale: 1 });
                var size = stageSize();
                var fit = size.w / unit.width;
                baseW = size.w;
                baseH = unit.height * fit;

                var dpr = Math.min(window.devicePixelRatio || 1, 2);
                var scale = fit * targetZoom * dpr;
                var limit = MAX_CANVAS_SIDE / Math.max(unit.width, unit.height);
                if (scale > limit) scale = limit;

                var viewport = pdfPage.getViewport({ scale: scale });
                var canvas = el('canvas', 'cm-sr-doc-canvas');
                canvas.width = Math.max(1, Math.floor(viewport.width));
                canvas.height = Math.max(1, Math.floor(viewport.height));
                canvas.style.width = (baseW * targetZoom) + 'px';
                canvas.style.height = (baseH * targetZoom) + 'px';

                if (renderTask) renderTask.cancel();
                renderTask = pdfPage.render({
                    canvasContext: canvas.getContext('2d'),
                    viewport: viewport
                });
                return renderTask.promise.then(function () {
                    renderTask = null;
                    layer.innerHTML = '';
                    layer.appendChild(canvas);
                    drawnZoom = targetZoom;
                    clampPan();
                    applyTransform();
                    updateLabels();
                });
            }).catch(function () {
                // Отменённый рендер — норма: пользователь крутит колесо
                // быстрее, чем страница успевает перерисоваться.
            });
        }

        function setZoom(next, anchorX, anchorY) {
            next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, next));
            if (Math.abs(next - zoom) < 0.0005) return;
            var size = stageSize();
            if (anchorX === undefined) {
                anchorX = size.w / 2;
                anchorY = size.h / 2;
            }
            // Точка документа под курсором должна остаться под курсором.
            var k = next / zoom;
            panX = anchorX - k * (anchorX - panX);
            panY = anchorY - k * (anchorY - panY);
            zoom = next;
            clampPan();
            applyTransform();
            updateLabels();
            scheduleRender();
        }

        function fitPage() {
            var size = stageSize();
            zoom = baseH > 0 ? Math.max(MIN_ZOOM, Math.min(1, size.h / baseH)) : 1;
            panX = 0;
            panY = 0;
            clampPan();
            applyTransform();
            updateLabels();
            scheduleRender();
        }

        function goToPage(next) {
            if (!pdfDoc || next < 1 || next > pages || next === page) return;
            page = next;
            panY = 0;
            renderPage();
        }

        stage.addEventListener('wheel', function (event) {
            event.preventDefault();
            // deltaMode=1 — прокрутка строками (Firefox), шаг там крупнее.
            var step = event.deltaMode === 1 ? 0.05 : 0.0018;
            setZoom(zoom * Math.exp(-event.deltaY * step),
                event.clientX - stage.getBoundingClientRect().left,
                event.clientY - stage.getBoundingClientRect().top);
        }, { passive: false });

        var dragging = false;
        var lastX = 0;
        var lastY = 0;

        stage.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            dragging = true;
            lastX = event.clientX;
            lastY = event.clientY;
            stage.classList.add('is-dragging');
            if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
        });

        stage.addEventListener('pointermove', function (event) {
            if (!dragging) return;
            panX += event.clientX - lastX;
            panY += event.clientY - lastY;
            lastX = event.clientX;
            lastY = event.clientY;
            clampPan();
            applyTransform();
        });

        ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (name) {
            stage.addEventListener(name, function (event) {
                if (!dragging) return;
                dragging = false;
                stage.classList.remove('is-dragging');
                if (stage.releasePointerCapture && stage.hasPointerCapture &&
                    stage.hasPointerCapture(event.pointerId)) {
                    stage.releasePointerCapture(event.pointerId);
                }
            });
        });

        stage.addEventListener('dblclick', function (event) {
            var rect = stage.getBoundingClientRect();
            setZoom(zoom < 3 ? 3 : 1, event.clientX - rect.left, event.clientY - rect.top);
        });

        outBtn.addEventListener('click', function () { setZoom(zoom / 1.4); });
        inBtn.addEventListener('click', function () { setZoom(zoom * 1.4); });
        resetBtn.addEventListener('click', fitPage);
        prevBtn.addEventListener('click', function () { goToPage(page - 1); });
        nextBtn.addEventListener('click', function () { goToPage(page + 1); });

        var resizeTimer = null;
        window.addEventListener('resize', function () {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(function () {
                if (!host.isConnected) return;
                if (pdfDoc) renderPage();
                else { clampPan(); applyTransform(); }
            }, 200);
        });

        if (pdfDoc) {
            renderPage();
            return;
        }

        // Картинка: перерисовывать нечего, растягиваем сам <img>.
        var img = el('img', 'cm-sr-doc-image');
        img.alt = 'Скан документа';
        img.addEventListener('load', function () {
            var size = stageSize();
            baseW = size.w;
            baseH = img.naturalWidth ? size.w * (img.naturalHeight / img.naturalWidth) : size.h;
            img.style.width = baseW + 'px';
            img.style.height = baseH + 'px';
            clampPan();
            applyTransform();
            updateLabels();
        });
        img.addEventListener('error', function () { showFallbackFrame(host, url); });
        layer.appendChild(img);
        img.src = url;
    }

    function start(host) {
        var url = host.getAttribute('data-file-url');
        var kind = host.getAttribute('data-file-kind') || 'other';
        if (!url) return;

        if (kind === 'image') {
            setupViewer(host, url, kind, null);
            return;
        }
        if (kind !== 'pdf') {
            showFallbackFrame(host, url);
            return;
        }
        loadPdfLib()
            .then(function (lib) { return lib.getDocument({ url: url }).promise; })
            .then(function (doc) { setupViewer(host, url, kind, doc); })
            .catch(function () { showFallbackFrame(host, url); });
    }

    function init(root) {
        var scope = root || document;
        var nodes = scope.querySelectorAll('.js-sr-doc');
        Array.prototype.forEach.call(nodes, function (host) {
            if (host.dataset.viewerReady === '1') return;
            host.dataset.viewerReady = '1';
            start(host);
        });
    }

    window.cmScanDocViewer = { init: init };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(); });
    } else {
        init();
    }
})();
