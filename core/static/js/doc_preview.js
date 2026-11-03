/* Предпросмотр документа и кнопка «Скачать».

PDF во фрейме открываем по обычному адресу файла. Просмотрщик Chrome
не сохраняет документ с адреса blob: — его кнопка «Скачать» молчит.
Если заголовок X-Frame-Options не пускает файл во фрейм, показываем blob.

Кнопка внизу окна качает файл сама: у ссылки на PDF Chrome часто
игнорирует атрибут download и не сохраняет файл.
*/
(function () {
    if (window.DocPreview) return;

    function basename(name) {
        var clean = String(name || "").split("?")[0].split("#")[0];
        var parts = clean.split(/[/\\]/);
        return parts[parts.length - 1] || "document";
    }

    function isImageName(name) {
        return /\.(jpe?g|png|gif|webp)$/i.test(String(name || "").split("?")[0]);
    }

    var states = new WeakMap();

    function stateOf(modal) {
        var current = states.get(modal);
        if (!current) {
            current = { objectUrl: null, blob: null, filename: "", directUrl: "" };
            states.set(modal, current);
        }
        return current;
    }

    function fetchBlob(url) {
        return fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (resp) {
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            return resp.blob();
        });
    }

    function saveBlob(blob, filename) {
        var objectUrl = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = objectUrl;
        link.download = basename(filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
        }, 2000);
    }

    function revoke(modal) {
        var current = stateOf(modal);
        if (current.objectUrl) {
            URL.revokeObjectURL(current.objectUrl);
            current.objectUrl = null;
        }
        current.blob = null;
        current.directUrl = "";
    }

    function bindDownload(modal) {
        if (modal.dataset.docPreviewBound) return;
        modal.dataset.docPreviewBound = "1";
        modal.addEventListener("click", function (event) {
            var link = event.target.closest(".doc-preview-download, .rc-doc-preview-download");
            if (!link || !modal.contains(link)) return;
            event.preventDefault();
            var current = stateOf(modal);
            var filename = current.filename || link.getAttribute("download");
            if (current.blob) {
                saveBlob(current.blob, filename);
                return;
            }
            var url = current.directUrl || link.getAttribute("href");
            if (!url || url === "#") return;
            fetchBlob(url)
                .then(function (blob) {
                    if (current.directUrl && current.directUrl !== url) return;
                    current.blob = blob;
                    saveBlob(blob, filename);
                })
                .catch(function () {});
        });
    }

    function prepare(modal, url, filename) {
        var current = stateOf(modal);
        revoke(modal);
        current.directUrl = url;
        current.filename = basename(filename || url);
        modal.querySelectorAll(".doc-preview-download, .rc-doc-preview-download").forEach(function (link) {
            link.href = url;
            link.setAttribute("download", current.filename);
        });
        fetchBlob(url)
            .then(function (blob) {
                if (current.directUrl !== url) return;
                current.blob = blob;
            })
            .catch(function () {});
    }

    function showPdf(frame, modal, url, onBlocked) {
        var current = stateOf(modal);
        frame.onload = function () {
            frame.onload = null;
            var blocked = false;
            try {
                var doc = frame.contentDocument;
                blocked = !!(doc && doc.URL === "about:blank");
            } catch (err) {
                blocked = false;
            }
            if (!blocked) return;
            var blob = current.blob;
            var apply = function (fileBlob) {
                if (current.directUrl !== url) return;
                var pdfBlob =
                    fileBlob.type === "application/pdf"
                        ? fileBlob
                        : new Blob([fileBlob], { type: "application/pdf" });
                current.blob = pdfBlob;
                if (current.objectUrl) URL.revokeObjectURL(current.objectUrl);
                current.objectUrl = URL.createObjectURL(pdfBlob);
                frame.src = current.objectUrl;
            };
            if (blob) {
                apply(blob);
                return;
            }
            fetchBlob(url).then(apply).catch(function () {
                if (current.directUrl === url && onBlocked) onBlocked();
            });
        };
        frame.src = url;
    }

    document.addEventListener("click", function (event) {
        var link = event.target.closest(".rc-file-act");
        if (!link) return;
        var url = link.getAttribute("href");
        if (!url || url === "#") return;
        event.preventDefault();
        fetchBlob(url)
            .then(function (blob) {
                saveBlob(blob, link.getAttribute("download") || url);
            })
            .catch(function () {});
    });

    window.DocPreview = {
        isImageName: isImageName,
        basename: basename,
        revoke: revoke,
        bindDownload: bindDownload,
        prepare: prepare,
        showPdf: showPdf,
    };
})();
