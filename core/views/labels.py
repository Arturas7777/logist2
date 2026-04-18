"""
Печать самоклеющихся наклеек (self-adhesive labels) для контейнеров.

Поддерживаются форматы листов Forpus (A4: 210 × 297 мм).
Пользователь выбирает формат и отмечает уже использованные ячейки
на листе — оставшиеся пустые позиции заполняются наклейками
выбранных контейнеров.
"""
from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.models import Container

# A4 размеры (мм)
PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0

# «Безопасный отступ» по умолчанию (мм) — у лазерных принтеров есть
# физическая непечатаемая зона по краям листа (~3–5 мм). Ячейкам,
# соприкасающимся с краями A4, мы прижимаем содержимое внутрь на
# эту величину, иначе края наклеек срежутся.
DEFAULT_SAFE_MARGIN_MM = 4.0

# Справочник форматов Forpus (арт. → параметры)
# cols × rows раскладка, w × h размер одной наклейки в мм.
# Поля (margins) вычисляются автоматически равномерно по ширине и высоте.
LABEL_FORMATS: dict[str, dict[str, Any]] = {
    '41501': {'name': '41501 · 65 шт · 38 × 21.2 мм', 'cols': 5,  'rows': 13, 'w': 38.0,  'h': 21.2},
    '41503': {'name': '41503 · 44 шт · 48.5 × 25.4 мм', 'cols': 4, 'rows': 11, 'w': 48.5, 'h': 25.4},
    '41504': {'name': '41504 · 56 шт · 52.5 × 21.2 мм', 'cols': 4, 'rows': 14, 'w': 52.5, 'h': 21.2},
    '41505': {'name': '41505 · 40 шт · 52.5 × 29.7 мм', 'cols': 4, 'rows': 10, 'w': 52.5, 'h': 29.7},
    '41509': {'name': '41509 · 24 шт · 66 × 33.8 мм',   'cols': 3, 'rows': 8,  'w': 66.0, 'h': 33.8},
    '41514': {'name': '41514 · 24 шт · 70 × 35 мм',     'cols': 3, 'rows': 8,  'w': 70.0, 'h': 35.0},
    '41515': {'name': '41515 · 24 шт · 70 × 36 мм',     'cols': 3, 'rows': 8,  'w': 70.0, 'h': 36.0},
    '41516': {'name': '41516 · 24 шт · 70 × 37 мм',     'cols': 3, 'rows': 8,  'w': 70.0, 'h': 37.0},
    '41518': {'name': '41518 · 21 шт · 70 × 42.4 мм',   'cols': 3, 'rows': 7,  'w': 70.0, 'h': 42.4},
    '41526': {'name': '41526 · 16 шт · 105 × 35 мм',    'cols': 2, 'rows': 8,  'w': 105.0, 'h': 35.0},
    '41530': {'name': '41530 · 12 шт · 105 × 48 мм',    'cols': 2, 'rows': 6,  'w': 105.0, 'h': 48.0},
    '41531': {'name': '41531 · 10 шт · 105 × 57 мм',    'cols': 2, 'rows': 5,  'w': 105.0, 'h': 57.0},
    '41533': {'name': '41533 · 8 шт · 105 × 74 мм',     'cols': 2, 'rows': 4,  'w': 105.0, 'h': 74.0},
    '41534': {'name': '41534 · 4 шт · 105 × 148 мм',    'cols': 2, 'rows': 2,  'w': 105.0, 'h': 148.0},
    '41535': {'name': '41535 · 2 шт · 210 × 148 мм',    'cols': 1, 'rows': 2,  'w': 210.0, 'h': 148.0},
    '41536': {'name': '41536 · 1 шт · 210 × 297 мм',    'cols': 1, 'rows': 1,  'w': 210.0, 'h': 297.0},
}


def _parse_container_ids(value: str | None) -> list[int]:
    if not value:
        return []
    result: list[int] = []
    for token in value.split(','):
        token = token.strip()
        if token.isdigit():
            result.append(int(token))
    return result


def _fmt_spec(format_code: str) -> dict[str, Any]:
    spec = LABEL_FORMATS[format_code]
    cols = spec['cols']
    rows = spec['rows']
    w = spec['w']
    h = spec['h']
    margin_x = max(0.0, (PAGE_WIDTH_MM - cols * w) / 2.0)
    margin_y = max(0.0, (PAGE_HEIGHT_MM - rows * h) / 2.0)
    return {
        'code': format_code,
        'name': spec['name'],
        'cols': cols,
        'rows': rows,
        'w': w,
        'h': h,
        'count': cols * rows,
        'margin_x': round(margin_x, 2),
        'margin_y': round(margin_y, 2),
    }


def _cell_positions(
    fmt: dict[str, Any],
    row_gap: float = 0.0,
    col_gap: float = 0.0,
) -> list[dict[str, float]]:
    """
    Возвращает список абсолютных позиций ячеек на листе (мм).
    Порядок — row-major (слева направо, сверху вниз).

    row_gap / col_gap — дополнительный шаг между рядами/колонками.
    Используется для компенсации случаев, когда принтер механически
    «сжимает» отпечаток по вертикали или когда физические ячейки
    листа Forpus идут с зазором.
    """
    positions: list[dict[str, float]] = []
    step_x = fmt['w'] + col_gap
    step_y = fmt['h'] + row_gap
    for row in range(fmt['rows']):
        for col in range(fmt['cols']):
            positions.append({
                'left': round(fmt['margin_x'] + col * step_x, 3),
                'top': round(fmt['margin_y'] + row * step_y, 3),
                'width': fmt['w'],
                'height': fmt['h'],
            })
    return positions


def _load_containers(ids: list[int]) -> list[Container]:
    """
    Возвращает контейнеры в том же порядке, в котором переданы id,
    с предзагруженными авто и линией.
    """
    if not ids:
        return []
    qs = Container.objects.filter(id__in=ids).select_related('line').prefetch_related(
        'container_cars__client', 'container_cars__line'
    )
    by_id = {c.id: c for c in qs}
    return [by_id[i] for i in ids if i in by_id]


def _build_label_data(container: Container) -> dict[str, Any]:
    """Собирает данные одной наклейки для шаблона."""
    cars_qs = container.container_cars.all().order_by('brand', 'vin')
    cars: list[dict[str, Any]] = []
    for car in cars_qs:
        vin = car.vin or ''
        cars.append({
            'brand': car.brand or '',
            'vin_tail': vin[-6:] if vin else '',
            'client': car.client.name if car.client_id else '',
            'has_title': bool(car.has_title),
        })

    # Линии, которыми плывёт контейнер: линия самого контейнера + все
    # уникальные линии его авто (на случай, если отличаются).
    lines: list[str] = []
    if container.line_id and container.line:
        lines.append(container.line.name)
    seen = set(lines)
    for car in cars_qs:
        if car.line_id and car.line and car.line.name not in seen:
            lines.append(car.line.name)
            seen.add(car.line.name)

    eta_str = container.eta.strftime('%d.%m.%Y') if container.eta else ''
    printed_str = (
        timezone.localtime(container.labels_printed_at).strftime('%d.%m.%Y %H:%M')
        if container.labels_printed_at else ''
    )

    return {
        'id': container.id,
        'number': container.number,
        'eta': eta_str,
        'lines': ', '.join(lines),
        'cars': cars,
        'cars_count': len(cars),
        'labels_printed_at': printed_str,
    }


@staff_member_required
def print_labels_settings(request) -> HttpResponse:
    """
    Страница настройки печати наклеек.
    Принимает container_ids (CSV) через GET/POST.
    Отдаёт форму выбора формата листа и отметки использованных ячеек.
    """
    raw = request.GET.get('container_ids') or request.POST.get('container_ids') or ''
    container_ids = _parse_container_ids(raw)

    default_format = request.GET.get('format') or '41531'
    if default_format not in LABEL_FORMATS:
        default_format = '41531'

    containers = _load_containers(container_ids)
    labels_preview = [_build_label_data(c) for c in containers]

    formats_list = [_fmt_spec(code) for code in LABEL_FORMATS.keys()]

    context = {
        **admin.site.each_context(request),
        'title': 'Печать наклеек',
        'container_ids_csv': ','.join(str(i) for i in container_ids),
        'containers': containers,
        'labels_preview': labels_preview,
        'labels_count': len(labels_preview),
        'formats_list': formats_list,
        'default_format': default_format,
        'default_safe_margin': DEFAULT_SAFE_MARGIN_MM,
        'print_url': reverse('labels_print_sheet'),
    }
    return render(request, 'admin/labels/print_settings.html', context)


@staff_member_required
def print_labels_sheet(request) -> HttpResponse:
    """
    Печатная страница: раскладывает наклейки на листе согласно формату
    и списку пропущенных ячеек.
    """
    if request.method == 'POST':
        src = request.POST
    else:
        src = request.GET

    container_ids = _parse_container_ids(src.get('container_ids'))
    format_code = src.get('format') or '41531'
    if format_code not in LABEL_FORMATS:
        format_code = '41531'

    # Уже использованные ячейки на ПЕРВОМ листе — нумерация с 0
    skipped_raw = src.get('skipped_cells') or ''
    skipped: set[int] = set()
    for token in skipped_raw.split(','):
        token = token.strip()
        if token.isdigit():
            skipped.add(int(token))

    auto_print = src.get('auto_print') != '0'

    # Безопасный отступ от края листа (мм). Пользователь может подстроить.
    try:
        safe_margin = float(src.get('safe_margin', DEFAULT_SAFE_MARGIN_MM))
    except (TypeError, ValueError):
        safe_margin = DEFAULT_SAFE_MARGIN_MM
    safe_margin = max(0.0, min(safe_margin, 15.0))

    # Калибровка положения отпечатка относительно листа (мм).
    # Нужна если принтер механически подаёт лист со смещением.
    # Положительные значения сдвигают содержимое вправо/вниз,
    # отрицательные — влево/вверх.
    def _parse_offset(raw: str | None) -> float:
        try:
            v = float(raw) if raw not in (None, '') else 0.0
        except (TypeError, ValueError):
            v = 0.0
        return max(-15.0, min(v, 15.0))

    offset_x = _parse_offset(src.get('offset_x'))
    offset_y = _parse_offset(src.get('offset_y'))

    # Сжатие содержимого каждой наклейки (мм на сторону).
    # Применяется ко ВСЕМ ячейкам одновременно — уменьшает
    # эффективную ширину/высоту отпечатка и создаёт запас под
    # механическое смещение при ручной подаче листа.
    def _parse_inset(raw: str | None) -> float:
        try:
            v = float(raw) if raw not in (None, '') else 0.0
        except (TypeError, ValueError):
            v = 0.0
        return max(0.0, min(v, 10.0))

    inset_h = _parse_inset(src.get('inset_h'))
    inset_v = _parse_inset(src.get('inset_v'))

    # Дополнительный шаг между рядами/колонками (мм).
    # Помогает когда принтер «сжимает» отпечаток по вертикали
    # и последние наклейки ползут вверх / накладываются друг на друга.
    def _parse_gap(raw: str | None) -> float:
        try:
            v = float(raw) if raw not in (None, '') else 0.0
        except (TypeError, ValueError):
            v = 0.0
        return max(-5.0, min(v, 10.0))

    row_gap = _parse_gap(src.get('row_gap'))
    col_gap = _parse_gap(src.get('col_gap'))

    fmt = _fmt_spec(format_code)
    per_page = fmt['count']

    containers = _load_containers(container_ids)
    labels = [_build_label_data(c) for c in containers]

    # Отмечаем все распечатанные контейнеры как «напечатанные» — проставляем
    # текущее время. Так пользователь в админке видит, для каких контейнеров
    # наклейки уже сделаны, и когда именно это было.
    if containers:
        printed_ids = [c.id for c in containers]
        Container.objects.filter(id__in=printed_ids).update(
            labels_printed_at=timezone.now()
        )

    # Раскладываем наклейки по ячейкам с учётом пропущенных
    pages: list[list[dict[str, Any] | None]] = []
    label_idx = 0
    page_num = 0

    while label_idx < len(labels):
        page: list[dict[str, Any] | None] = [None] * per_page
        for cell in range(per_page):
            # На первой странице пропускаем отмеченные ячейки
            if page_num == 0 and cell in skipped:
                continue
            if label_idx >= len(labels):
                break
            page[cell] = labels[label_idx]
            label_idx += 1
        pages.append(page)
        page_num += 1

    # Если наклеек нет — всё равно показываем пустой лист для проверки вёрстки
    if not pages:
        pages = [[None] * per_page]

    # Собираем список ячеек с абсолютными координатами для печати.
    # Координаты форматируем строками с точкой в качестве десятичного
    # разделителя, чтобы CSS не ломался из-за локализации (ru → "0,0mm").
    positions = _cell_positions(fmt, row_gap=row_gap, col_gap=col_gap)
    eps = 0.1  # мм, погрешность для сравнения с краями листа
    pages_positioned: list[list[dict[str, Any]]] = []
    for page in pages:
        items: list[dict[str, Any]] = []
        for idx, label in enumerate(page):
            if label is None:
                continue
            pos = positions[idx]
            left = pos['left'] + offset_x
            top = pos['top'] + offset_y
            width = pos['width']
            height = pos['height']
            right = left + width
            bottom = top + height

            style_parts = [
                f"left: {left:.3f}mm",
                f"top: {top:.3f}mm",
                f"width: {width:.3f}mm",
                f"height: {height:.3f}mm",
            ]

            # Базовый padding задаётся CSS (padding: 1.2mm 3.2mm).
            # Считаем эффективный padding на каждую сторону с учётом:
            #   - inset_h / inset_v применяются ко всем ячейкам;
            #   - safe_margin применяется только к ячейкам у внешнего края листа.
            base_h = 3.2  # мм, синхронно с CSS .cell
            base_v = 1.2
            pad_left = base_h + inset_h
            pad_right = base_h + inset_h
            pad_top = base_v + inset_v
            pad_bottom = base_v + inset_v

            if safe_margin > 0:
                if left < eps:
                    pad_left = max(pad_left, safe_margin)
                if right > PAGE_WIDTH_MM - eps:
                    pad_right = max(pad_right, safe_margin)
                if top < eps:
                    pad_top = max(pad_top, safe_margin)
                if bottom > PAGE_HEIGHT_MM - eps:
                    pad_bottom = max(pad_bottom, safe_margin)

            style_parts.append(
                f"padding: {pad_top:.2f}mm {pad_right:.2f}mm {pad_bottom:.2f}mm {pad_left:.2f}mm"
            )

            items.append({
                'label': label,
                'style': '; '.join(style_parts) + ';',
            })
        pages_positioned.append(items)

    context = {
        'title': 'Наклейки — печать',
        'fmt': fmt,
        'pages_positioned': pages_positioned,
        'auto_print': auto_print,
        'labels_count': len(labels),
        'safe_margin': safe_margin,
        'offset_x': offset_x,
        'offset_y': offset_y,
        'inset_h': inset_h,
        'inset_v': inset_v,
        'row_gap': row_gap,
        'col_gap': col_gap,
    }
    return render(request, 'admin/labels/print_sheet.html', context)


def redirect_to_print_settings(container_ids: list[int]) -> HttpResponseRedirect:
    """Хелпер для admin action: редирект на страницу настройки печати."""
    qs = ','.join(str(i) for i in container_ids)
    url = reverse('labels_print_settings') + f'?container_ids={qs}'
    return HttpResponseRedirect(url)
