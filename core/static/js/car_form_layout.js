// Layout-fix for the Car change form (Django Admin).
// Sets inline-style with !important on flex-wrappers of the first
// row (year/brand/vin/vehicle_type/weight_kg) and rows
// has_title/title_link_display/title_notes and is_important/notes.
//
// Reason: dashboard_admin.css is loaded twice + responsive.css media
// queries reset our widths. Inline style with !important wins.
//
// ВАЖНО: на узких экранах (<=1024px) form-multiline переводится в колонку
// (dashboard_admin.css), и фиксированные flex-basis (это ШИРИНЫ колонок)
// начали бы работать как ВЫСОТА — огромные вертикальные пробелы. Поэтому
// на мобильной ширине инлайн-стили снимаем и перевешиваем при ресайзе.

(function() {
    'use strict';

    var mobileMq = window.matchMedia('(max-width: 1024px)');

    var LAYOUT_PROPS = ['flex', 'min-width', 'max-width', 'width', 'display',
                        'flex-wrap', 'gap', 'align-items'];

    function setStyle(el, css) {
        if (!el) return;
        Object.keys(css).forEach(function(k) {
            el.style.setProperty(k, css[k], 'important');
        });
    }

    function clearStyle(el) {
        if (!el) return;
        LAYOUT_PROPS.forEach(function(k) {
            el.style.removeProperty(k);
        });
    }

    function applyCarFormLayout() {
        var mobile = mobileMq.matches;
        // На мобильной ширине только очищаем ранее навешанные стили —
        // раскладкой управляет CSS (колонка, поля во всю ширину).
        var apply = mobile
            ? function(el) { clearStyle(el); }
            : function(el, css) { clearStyle(el); setStyle(el, css); };

        // Row 1: year / brand / vin / vehicle_type / weight_kg / status
        var row1 = document.querySelector(
            '.cm-form-main .form-row.field-year.field-brand.field-vin'
        );
        if (row1) {
            var ml = row1.querySelector(':scope > .form-multiline');
            if (ml) {
                // NOTE: do NOT set width:100% here. The form-multiline is
                // a flex BFC and must NOT overlap the floated car photo.
                // Letting width be auto allows the row to shrink to the
                // space available next to the float.
                apply(ml, {
                    'display': 'flex', 'flex-wrap': 'wrap', 'gap': '12px',
                    'align-items': 'flex-end'
                });
                var k = ml.children;
                apply(k[0], {'flex':'0 0 110px','min-width':'110px','max-width':'110px'});
                apply(k[1], {'flex':'2 1 280px','min-width':'280px','max-width':'320px'});
                apply(k[2], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                apply(k[3], {'flex':'0 0 140px','min-width':'140px','max-width':'140px'});
                apply(k[4], {'flex':'0 0 130px','min-width':'130px','max-width':'130px'});
                apply(k[5], {'flex':'0 0 140px','min-width':'140px','max-width':'140px'});
            }
        }
        // Row 2: client / warehouse / unload_site
        // Client wrapper: same as brand (<=35 chars, 280..320px)
        var row2 = document.querySelector(
            '.cm-form-main .form-row.field-client.field-warehouse'
        );
        if (row2) {
            var ml2 = row2.querySelector(':scope > .form-multiline');
            if (ml2 && ml2.children[0]) {
                apply(ml2.children[0], {
                    'flex':'2 1 280px','min-width':'280px','max-width':'320px'
                });
            }
        }
        // Row "has_title | title_link_display | title_notes"
        var rowTitle = document.querySelector(
            '.cm-form-main .form-row.field-has_title.field-title_link_display.field-title_notes'
        );
        if (rowTitle) {
            var mlT = rowTitle.querySelector(':scope > .form-multiline');
            if (mlT) {
                // flex-start — единообразно с рядом «Важно | Примечания»,
                // см. комментарий там же ниже.
                apply(mlT, {
                    'display':'flex','flex-wrap':'wrap','gap':'12px',
                    'align-items':'flex-start'
                });
                var c = mlT.children;
                apply(c[0], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                if (c[1]) {
                    var hasLink = c[1].querySelector('a');
                    if (!hasLink) {
                        c[1].style.setProperty('display','none','important');
                    } else {
                        apply(c[1], {'flex':'0 0 auto','min-width':'0'});
                    }
                }
                apply(c[2], {'flex':'1 1 220px','min-width':'220px'});
            }
        }
        // Row "is_important | notes"
        var rowImp = document.querySelector(
            '.cm-form-main .form-row.field-is_important.field-notes'
        );
        if (rowImp) {
            var mlI = rowImp.querySelector(':scope > .form-multiline');
            if (mlI) {
                // flex-start, а не center: обёртки чекбокса и textarea имеют
                // разную служебную высоту (скрытые help/label), center даёт
                // вертикальный сдвиг. Высоты самих элементов зафиксированы
                // в change_form.html (40px).
                apply(mlI, {
                    'display':'flex','flex-wrap':'wrap','gap':'12px',
                    'align-items':'flex-start'
                });
                var ic = mlI.children;
                apply(ic[0], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                apply(ic[1], {'flex':'1 1 220px','min-width':'220px'});
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyCarFormLayout);
    } else {
        applyCarFormLayout();
    }
    window.addEventListener('load', applyCarFormLayout);
    setTimeout(applyCarFormLayout, 200);
    setTimeout(applyCarFormLayout, 800);
    // Перевешиваем раскладку при смене ширины (десктоп <-> мобильный).
    if (mobileMq.addEventListener) {
        mobileMq.addEventListener('change', applyCarFormLayout);
    } else if (mobileMq.addListener) {
        mobileMq.addListener(applyCarFormLayout);
    }
})();
