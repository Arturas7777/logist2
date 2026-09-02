/**
 * Живая проверка VIN в формах админки.
 *
 * Работает и в карточке авто, и в табличном инлайне машин контейнера:
 * ловит любое поле с data-vin-guard, при выходе из него спрашивает сервер
 * и показывает результат рядом с полем.
 *
 * Что даёт оператору:
 *   - расшифровку VIN («TOYOTA CAMRY 2021») до сохранения, чтобы опечатка
 *     всплывала сразу, а не через неделю при разборе Dock Receipt;
 *   - автозаполнение марки и года, если они ещё пустые;
 *   - галочку подтверждения при спорном VIN — она проставляет скрытое
 *     поле vin_confirmed, без которого форма не сохранится.
 *
 * Панель живёт в body и позиционируется фиксированно: ячейки табличного
 * инлайна и его контейнеры режут всё, что выходит за их границы
 * (overflow: hidden/clip), поэтому вложенная подсказка была бы не видна.
 * Само поле при этом помечается рамкой — она остаётся видимой и после того,
 * как панель уехала к следующей строке.
 *
 * Серверная проверка обязательна и работает независимо: этот скрипт лишь
 * переносит её ближе к моменту ввода.
 */
(function () {
  'use strict';

  var ENDPOINT = '/admin/core/car/vin-check/';
  var PANEL_WIDTH = 280;
  var cache = new Map();
  var panel = null;
  var anchorInput = null;
  var hideTimer = null;

  function rowOf(input) {
    return input.closest('tr') || input.closest('fieldset') || input.form || document;
  }

  function siblingField(input, name) {
    // В инлайне поля одной машины лежат в одной строке и различаются
    // префиксом (container_cars-0-brand); в карточке — просто по id.
    var scope = rowOf(input);
    return scope.querySelector('[name$="-' + name + '"], [name="' + name + '"]');
  }

  function confirmField(input) {
    return siblingField(input, 'vin_confirmed');
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement('div');
    panel.className = 'cm-vin-panel';
    panel.hidden = true;
    document.body.appendChild(panel);
    return panel;
  }

  function place() {
    if (!anchorInput || !panel || panel.hidden) return;
    if (!anchorInput.isConnected) return hide();
    var rect = anchorInput.getBoundingClientRect();
    if (!rect.width && !rect.height) return hide();
    var left = Math.min(rect.left, window.innerWidth - PANEL_WIDTH - 8);
    panel.style.left = Math.max(8, left) + 'px';
    // Если снизу не помещается — показываем над полем.
    var below = window.innerHeight - rect.bottom;
    if (below < panel.offsetHeight + 12 && rect.top > panel.offsetHeight + 12) {
      panel.style.top = rect.top - panel.offsetHeight - 4 + 'px';
    } else {
      panel.style.top = rect.bottom + 4 + 'px';
    }
  }

  function hide() {
    clearTimeout(hideTimer);
    if (panel) {
      panel.hidden = true;
      panel.innerHTML = '';
    }
    anchorInput = null;
  }

  function markField(input, hasIssues, confirmed) {
    input.classList.toggle('cm-vin-flag', Boolean(hasIssues) && !confirmed);
  }

  function render(input, data) {
    var box = ensurePanel();
    clearTimeout(hideTimer);
    box.innerHTML = '';
    if (!data) return hide();

    var issues = data.issues || [];
    var confirm = confirmField(input);
    var confirmed = Boolean(confirm) && (confirm.value === 'on' || confirm.value === 'true');
    markField(input, issues.length, confirmed);

    if (data.nhtsa && data.nhtsa.summary) {
      var badge = document.createElement('div');
      badge.className = 'cm-vin-badge' + (data.nhtsa.ok ? ' is-ok' : '');
      badge.textContent = 'NHTSA: ' + data.nhtsa.summary;
      box.appendChild(badge);
    }

    issues.forEach(function (issue) {
      var row = document.createElement('div');
      row.className = 'cm-vin-issue';
      row.textContent = issue.message;
      if (issue.suggestion) {
        var fix = document.createElement('button');
        fix.type = 'button';
        fix.className = 'cm-vin-fix';
        fix.textContent = 'Подставить ' + issue.suggestion;
        fix.addEventListener('click', function () {
          input.value = issue.suggestion;
          check(input);
        });
        row.appendChild(fix);
      }
      box.appendChild(row);
    });

    if (confirm && issues.length) {
      var label = document.createElement('label');
      label.className = 'cm-vin-confirm';
      var checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = confirmed;
      checkbox.addEventListener('change', function () {
        confirm.value = checkbox.checked ? 'on' : '';
        markField(input, true, checkbox.checked);
      });
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(' Я сверил VIN с документом, он верный'));
      box.appendChild(label);
    } else if (confirm) {
      confirm.value = '';
    }

    if (!box.childNodes.length) return hide();

    anchorInput = input;
    box.hidden = false;
    place();

    // Подсказка без замечаний — просто подтверждение, что VIN распознан:
    // висеть поверх формы ей незачем.
    if (!issues.length) hideTimer = setTimeout(hide, 5000);
  }

  function fillEmpty(input, data) {
    if (!data.nhtsa || !data.nhtsa.ok) return;
    var brand = siblingField(input, 'brand');
    if (brand && !brand.value.trim() && data.nhtsa.make) {
      brand.value = [data.nhtsa.make, data.nhtsa.model].filter(Boolean).join(' ');
    }
    var year = siblingField(input, 'year');
    if (year && !year.value.trim() && data.nhtsa.year) {
      year.value = data.nhtsa.year;
    }
    var typeField = siblingField(input, 'vehicle_type');
    if (typeField && data.nhtsa.vehicle_type) {
      var current = (typeField.value || 'SEDAN').trim() || 'SEDAN';
      if (current === 'SEDAN') typeField.value = data.nhtsa.vehicle_type;
    }
  }

  function check(input) {
    var vin = (input.value || '').replace(/\s+/g, '').toUpperCase();
    if (input.value !== vin) input.value = vin;
    if (!vin) {
      markField(input, false, false);
      if (anchorInput === input) hide();
      return;
    }
    if (vin.length !== 17) {
      render(input, { issues: [{ message: 'VIN содержит ' + vin.length + ' символов вместо 17.' }] });
      return;
    }

    if (cache.has(vin)) {
      var cached = cache.get(vin);
      render(input, cached);
      fillEmpty(input, cached);
      return;
    }

    var params = new URLSearchParams({ vin: vin });
    var brand = siblingField(input, 'brand');
    var year = siblingField(input, 'year');
    if (brand && brand.value) params.set('brand', brand.value);
    if (year && year.value) params.set('year', year.value);
    var match = window.location.pathname.match(/\/car\/(\d+)\/change\//);
    if (match) params.set('car_id', match[1]);

    fetch(ENDPOINT + '?' + params.toString(), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        cache.set(vin, data);
        render(input, data);
        fillEmpty(input, data);
      })
      .catch(function () {
        // Проверка недоступна — молчим: серверная валидация при сохранении
        // всё равно отработает, пугать оператора сетевой ошибкой незачем.
      });
  }

  function isVinField(node) {
    return node && node.matches && node.matches('[data-vin-guard]');
  }

  document.addEventListener('blur', function (event) {
    // Уход в саму панель (клик по галочке или кнопке подстановки) закрывать
    // подсказку не должен.
    if (panel && panel.contains(event.relatedTarget)) return;
    if (isVinField(event.target)) check(event.target);
  }, true);

  document.addEventListener('mousedown', function (event) {
    if (!panel || panel.hidden) return;
    if (panel.contains(event.target) || event.target === anchorInput) return;
    if (isVinField(event.target)) return;
    hide();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') hide();
  });

  window.addEventListener('scroll', place, true);
  window.addEventListener('resize', place);
})();
