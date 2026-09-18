"""PDF-печать наклеек Forpus: координаты в миллиметрах, без масштаба браузера.

HTML-печать в Chrome периодически сжимает лист (Letter / «вписать в область»),
из-за этого нижние ряды уезжают вверх, а слева появляется лишний отступ.
PDF с MediaBox = A4 и PrintScaling=None печатается 1:1.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0

FONTS_DIR = Path(__file__).resolve().parent.parent / "pdf_assets" / "fonts"
FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

INK_SOFT = HexColor("#333333")
INK_CLIENT = HexColor("#444444")
RULE = HexColor("#666666")

# Размеры как в templates/admin/labels/print_sheet.html (мм).
_SIZES = {
    0: {
        "num": 5.2,
        "eta": 3.9,
        "lines": 2.4,
        "car": 2.9,
        "client": 2.5,
        "checkbox": 3.5,
        "cars_gap": 0.8,
        "car_pad_bottom": 0.4,
    },
    1: {
        "num": 4.2,
        "eta": 3.2,
        "lines": 2.2,
        "car": 2.5,
        "client": 2.3,
        "checkbox": 2.8,
        "cars_gap": 1.1,
        "car_pad_bottom": 0.6,
    },
    2: {
        "num": 3.4,
        "eta": 2.7,
        "lines": 1.8,
        "car": 2.0,
        "client": 1.8,
        "checkbox": 2.2,
        "cars_gap": 0.6,
        "car_pad_bottom": 0.3,
    },
}


def compact_level(fmt: dict[str, Any]) -> int:
    if fmt["h"] <= 22 or fmt["w"] <= 40:
        return 2
    if fmt["h"] <= 30 or fmt["w"] <= 55:
        return 1
    return 0


def _register_fonts() -> None:
    registered = set(pdfmetrics.getRegisteredFontNames())
    if FONT not in registered:
        pdfmetrics.registerFont(TTFont(FONT, str(FONTS_DIR / "DejaVuSans.ttf")))
    if FONT_BOLD not in registered:
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONTS_DIR / "DejaVuSans-Bold.ttf")))


def _fit_text(text: str, font: str, size_pt: float, max_width: float) -> str:
    if not text or pdfmetrics.stringWidth(text, font, size_pt) <= max_width:
        return text
    ellipsis = "…"
    if pdfmetrics.stringWidth(ellipsis, font, size_pt) > max_width:
        return ""
    low, high = 0, len(text)
    best = ellipsis
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if pdfmetrics.stringWidth(candidate, font, size_pt) <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _draw_checkbox(c: canvas.Canvas, x: float, y: float, size: float, checked: bool) -> None:
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(0.4 * mm)
    c.setDash()
    c.rect(x, y, size, size, stroke=1, fill=0)
    if checked:
        pad = size * 0.18
        c.setLineWidth(0.5 * mm)
        c.line(x + pad, y + pad, x + size - pad, y + size - pad)
        c.line(x + pad, y + size - pad, x + size - pad, y + pad)


def _draw_cell(c: canvas.Canvas, item: dict[str, Any], sizes: dict[str, float]) -> None:
    box = item["box"]
    label = item["label"]

    left = box["left"] * mm
    width = box["width"] * mm
    height = box["height"] * mm
    top = (PAGE_HEIGHT_MM - box["top"]) * mm
    bottom = top - height

    inner_l = left + box["pad_left"] * mm
    inner_r = left + width - box["pad_right"] * mm
    inner_t = top - box["pad_top"] * mm
    inner_b = bottom + box["pad_bottom"] * mm
    inner_w = max(0.0, inner_r - inner_l)

    c.saveState()
    clip = c.beginPath()
    clip.rect(left, bottom, width, height)
    c.clipPath(clip, stroke=0, fill=0)

    num_pt = sizes["num"] * mm
    eta_pt = sizes["eta"] * mm
    lines_pt = sizes["lines"] * mm
    car_pt = sizes["car"] * mm
    client_pt = sizes["client"] * mm
    box_size = sizes["checkbox"] * mm
    cars_gap = sizes["cars_gap"] * mm
    car_pad = sizes["car_pad_bottom"] * mm

    cursor = inner_t
    baseline = cursor - num_pt * 0.82

    number = str(label.get("number") or "")
    eta = str(label.get("eta") or "")
    lines = str(label.get("lines") or "")

    num_w = pdfmetrics.stringWidth(number, FONT_BOLD, num_pt)
    eta_text = f"ETA {eta}" if eta else ""
    eta_w = pdfmetrics.stringWidth(eta_text, FONT, eta_pt) if eta_text else 0.0
    eta_gap = (2 * mm) if eta_text else 0.0
    left_block = num_w + eta_gap + eta_w

    lines_max = inner_w - left_block - (2 * mm if lines else 0.0)
    lines_draw = _fit_text(lines, FONT_BOLD, lines_pt, max(0.0, lines_max)) if lines else ""
    lines_w = pdfmetrics.stringWidth(lines_draw, FONT_BOLD, lines_pt) if lines_draw else 0.0

    if left_block + (2 * mm if lines_draw else 0.0) + lines_w > inner_w and eta_text:
        overflow = left_block + (2 * mm if lines_draw else 0.0) + lines_w - inner_w
        eta_budget = max(0.0, eta_w - overflow)
        eta_text = _fit_text(eta_text, FONT, eta_pt, eta_budget)
        eta_w = pdfmetrics.stringWidth(eta_text, FONT, eta_pt) if eta_text else 0.0
        left_block = num_w + ((2 * mm + eta_w) if eta_text else 0.0)

    c.setFillColor(black)
    c.setFont(FONT_BOLD, num_pt)
    number_draw = _fit_text(number, FONT_BOLD, num_pt, inner_w)
    c.drawString(inner_l, baseline, number_draw)
    num_w = pdfmetrics.stringWidth(number_draw, FONT_BOLD, num_pt)

    if eta_text:
        c.setFillColor(INK_SOFT)
        c.setFont(FONT, eta_pt)
        c.drawString(inner_l + num_w + 2 * mm, baseline, eta_text)

    if lines_draw:
        c.setFillColor(INK_SOFT)
        c.setFont(FONT_BOLD, lines_pt)
        c.drawRightString(inner_r, baseline + (num_pt - lines_pt) * 0.15, lines_draw)

    rule_y = cursor - num_pt - 0.3 * mm
    c.setStrokeColor(black)
    c.setLineWidth(0.25 * mm)
    c.setDash()
    c.line(inner_l, rule_y, inner_r, rule_y)

    cursor = rule_y - 0.4 * mm
    cars = label.get("cars") or []
    for idx, car in enumerate(cars):
        if cursor - (car_pt + client_pt + car_pad) < inner_b:
            break

        row_top = cursor
        brand = str(car.get("brand") or "")
        vin_tail = str(car.get("vin_tail") or "")
        client_name = str(car.get("client") or "")
        has_title = bool(car.get("has_title"))

        text_right = inner_r - box_size - 1.5 * mm
        text_w = max(0.0, text_right - inner_l)

        brand_line = brand
        if vin_tail:
            brand_line = f"{brand}  · ...{vin_tail}" if brand else f"· ...{vin_tail}"
        brand_line = _fit_text(brand_line, FONT_BOLD, car_pt, text_w)

        c.setFillColor(black)
        c.setFont(FONT_BOLD, car_pt)
        c.drawString(inner_l, row_top - car_pt * 0.85, brand_line)

        next_y = row_top - car_pt
        if client_name:
            client_draw = _fit_text(client_name, FONT, client_pt, text_w)
            c.setFillColor(INK_CLIENT)
            c.setFont(FONT, client_pt)
            c.drawString(inner_l, next_y - client_pt * 0.95, client_draw)
            next_y -= client_pt + 0.1 * mm

        box_y = row_top - (car_pt + (client_pt if client_name else 0.0)) / 2.0 - box_size / 2.0
        _draw_checkbox(c, inner_r - box_size, box_y, box_size, has_title)

        cursor = next_y - car_pad
        if idx != len(cars) - 1:
            c.setStrokeColor(RULE)
            c.setLineWidth(0.15 * mm)
            c.setDash(0.4 * mm, 0.4 * mm)
            c.line(inner_l, cursor, text_right, cursor)
            c.setDash()
            cursor -= cars_gap

    c.restoreState()


def render_labels_pdf(pages_positioned: list[list[dict[str, Any]]], fmt: dict[str, Any]) -> bytes:
    """Собирает многостраничный A4 PDF. Координаты ячеек — мм от левого верхнего угла."""
    _register_fonts()
    sizes = _SIZES[compact_level(fmt)]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Наклейки {fmt.get('name') or fmt.get('code') or ''}".strip())
    c.setViewerPreference("PrintScaling", "None")
    c.setViewerPreference("PrintArea", "MediaBox")
    c.setViewerPreference("PrintClip", "MediaBox")

    # На всякий случай фиксируем MediaBox явно — A4 = 210×297 мм.
    page_w, page_h = PAGE_WIDTH_MM * mm, PAGE_HEIGHT_MM * mm
    assert abs(page_w - A4[0]) < 0.2
    assert abs(page_h - A4[1]) < 0.2

    for page_index, page in enumerate(pages_positioned):
        if page_index:
            c.showPage()
        for item in page:
            _draw_cell(c, item, sizes)

    c.save()
    return buf.getvalue()
