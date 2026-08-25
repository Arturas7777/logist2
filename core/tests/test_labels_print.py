"""Печать наклеек: позиции ячеек Forpus и изоляция листа от экранной панели."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Container
from core.views.labels import PAGE_HEIGHT_MM, PAGE_WIDTH_MM, _cell_positions, _fmt_spec

pytestmark = pytest.mark.django_db


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


def test_print_sheet_keeps_toolbar_out_of_page_flow(client):
    user = User.objects.create_user(username="labels-staff", password="x", is_staff=True)
    client.force_login(user)
    Container.objects.create(number="MRSU0000001", status="FLOATING")
    container = Container.objects.get(number="MRSU0000001")

    url = reverse("labels_print_sheet")
    response = client.get(
        url,
        {"container_ids": str(container.id), "format": "41531", "auto_print": "0"},
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "position: fixed" in html
    assert "size: A4 portrait" in html
    assert "padding: 0 !important" in html
    assert re.search(r"top:\s*6\.000mm", html)
    assert "6,000mm" not in html
    assert "не Letter" in html
