// Layout-fix for the Car change form (Django Admin).
// Sets inline-style with !important on flex-wrappers of the first
// row (year/brand/vin/vehicle_type/weight_kg) and rows
// has_title/title_link_display/title_notes and is_important/notes.
//
// Reason: dashboard_admin.css is loaded twice + responsive.css media
// queries reset our widths. Inline style with !important wins.

(function() {
    'use strict';

    function setStyle(el, css) {
        if (!el) return;
        Object.keys(css).forEach(function(k) {
            el.style.setProperty(k, css[k], 'important');
        });
    }

    function applyCarFormLayout() {
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
                setStyle(ml, {
                    'display': 'flex', 'flex-wrap': 'wrap', 'gap': '12px',
                    'align-items': 'flex-end'
                });
                var k = ml.children;
                if (k[0]) setStyle(k[0], {'flex':'0 0 110px','min-width':'110px','max-width':'110px'});
                if (k[1]) setStyle(k[1], {'flex':'2 1 280px','min-width':'280px','max-width':'320px'});
                if (k[2]) setStyle(k[2], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                if (k[3]) setStyle(k[3], {'flex':'0 0 140px','min-width':'140px','max-width':'140px'});
                if (k[4]) setStyle(k[4], {'flex':'0 0 130px','min-width':'130px','max-width':'130px'});
                if (k[5]) setStyle(k[5], {'flex':'0 0 140px','min-width':'140px','max-width':'140px'});
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
                setStyle(ml2.children[0], {
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
                setStyle(mlT, {
                    'display':'flex','flex-wrap':'wrap','gap':'12px',
                    'align-items':'center'
                });
                var c = mlT.children;
                if (c[0]) setStyle(c[0], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                if (c[1]) {
                    var hasLink = c[1].querySelector('a');
                    if (!hasLink) {
                        c[1].style.setProperty('display','none','important');
                    } else {
                        setStyle(c[1], {'flex':'0 0 auto','min-width':'0'});
                    }
                }
                if (c[2]) setStyle(c[2], {'flex':'1 1 220px','min-width':'220px'});
            }
        }
        // Row "is_important | notes"
        var rowImp = document.querySelector(
            '.cm-form-main .form-row.field-is_important.field-notes'
        );
        if (rowImp) {
            var mlI = rowImp.querySelector(':scope > .form-multiline');
            if (mlI) {
                setStyle(mlI, {
                    'display':'flex','flex-wrap':'wrap','gap':'12px',
                    'align-items':'center'
                });
                var ic = mlI.children;
                if (ic[0]) setStyle(ic[0], {'flex':'0 0 200px','min-width':'200px','max-width':'200px'});
                if (ic[1]) setStyle(ic[1], {'flex':'1 1 220px','min-width':'220px'});
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
})();
