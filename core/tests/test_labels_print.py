"""Печать наклеек: позиции ячеек Forpus и PDF 1:1 без масштаба браузера."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Car, Container
from core.services.labels_pdf import compact_level, render_labels_pdf
from core.views.labels import PAGE_HEIGHT_MM, PAGE_WIDTH_MM, _cell_positions, _fmt_spec

pytestmark = pytest.mark.django_db


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        parts: list[str] = []
        for page in doc:
            textpage = page.get_textpage()
            try:
                parts.append(textpage.get_text_bounded())
            finally:
                textpage.close()
        return "\n".join(parts)
    finally:
        doc.close()


def test_format_41531_has_even_vertical_margins():
    fmt = _fmt_spec("41531")
    assert fmt["cols"] == 2
    assert fmt["rows"] == 5
    assert fmt["w"] == 105.0
    assert fmt["h"] == 57.0
    assert fmt["margin_x"] == 0.0
    assert fmt["margin_y"] == pytest.approx((PAGE_HEIGHT_MM - 5 * 57.0) / 2.0)


def test_cell_positions_row_major_without_extra_gap():
    fmt = _fmt_spec("41531")
    positions = _cell_positions(fmt)
    assert len(positions) == 10
    first = positions[0]
    assert first["left"] == pytest.approx(fmt["margin_x"])
    assert first["top"] == pytest.approx(fmt["margin_y"])
    assert first["width"] == 105.0
    assert first["height"] == 57.0
    second_row = positions[2]
    assert second_row["top"] == pytest.approx(fmt["margin_y"] + 57.0)
    last = positions[-1]
    assert last["top"] + last["height"] == pytest.approx(PAGE_HEIGHT_MM - fmt["margin_y"])
    assert last["left"] + last["width"] == pytest.approx(PAGE_WIDTH_MM - fmt["margin_x"])


def test_compact_level_matches_former_html_breakpoints():
    assert compact_level({"w": 105.0, "h": 57.0}) == 0
    assert compact_level({"w": 52.5, "h": 29.7}) == 1
    assert compact_level({"w": 38.0, "h": 21.2}) == 2


def test_labels_pdf_is_a4_and_disables_print_scaling():
    fmt = _fmt_spec("41531")
    pos = _cell_positions(fmt)[0]
    pages = [
        [
            {
                "label": {
                    "number": "MRSU3691469",
                    "eta": "01.09.2026",
                    "lines": "MSC",
                    "cars": [
                        {
                            "brand": "TOYOTA CAMRY",
                            "vin_tail": "123456",
                            "client": "Test Client",
                            "has_title": True,
                        }
                    ],
                },
                "box": {
                    "left": pos["left"],
                    "top": pos["top"],
                    "width": pos["width"],
                    "height": pos["height"],
                    "pad_left": 3.2,
                    "pad_right": 3.2,
                    "pad_top": 1.2,
                    "pad_bottom": 1.2,
                },
            }
        ]
    ]
    pdf = render_labels_pdf(pages, fmt)
    assert pdf.startswith(b"%PDF")
    assert b"/PrintScaling /None" in pdf
    assert b"/MediaBox" in pdf
    text = _pdf_text(pdf)
    assert "MRSU3691469" in text
    assert "ETA 01.09.2026" in text
    assert "TOYOTA CAMRY" in text


def test_print_sheet_returns_pdf(client):
    user = User.objects.create_user(username="labels-staff", password="x", is_staff=True)
    client.force_login(user)
    container = Container.objects.create(number="MRSU0000001", status="FLOATING")
    Car.objects.create(
        year=2020,
        brand="BMW X5",
        vin="WBAZZZ00000000001",
        status="FLOATING",
        container=container,
    )

    url = reverse("labels_print_sheet")
    response = client.get(
        url,
        {"container_ids": str(container.id), "format": "41531", "auto_print": "0"},
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "text/html" not in response["Content-Type"]
    body = response.content.lstrip()
    assert body.startswith(b"%PDF")
    assert not body.startswith(b"<!DOCTYPE")
    assert not body.startswith(b"<html")
    assert b"/PrintScaling /None" in response.content
    assert "MRSU0000001" in _pdf_text(response.content)
    container.refresh_from_db()
    assert container.labels_printed_at is not None


def test_labels_print_view_stays_on_pdf_path():
    """Регрессия: нельзя снова отдать HTML-лист в Chrome.print()."""
    source = Path("core/views/labels.py").read_text(encoding="utf-8")
    assert "render_labels_pdf" in source
    assert "print_sheet.html" not in source
    assert "window.print" not in source
    templates_dir = Path("templates/admin/labels")
    assert not (templates_dir / "print_sheet.html").exists()
