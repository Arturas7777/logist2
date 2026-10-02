"""Значок линии рядом с номером контейнера и контраст текста."""

import pytest
from django.contrib.auth import get_user_model

from core.line_marks import _ink_on, line_mark_html, line_short_code
from core.models import Container, Line

pytestmark = pytest.mark.django_db


def test_short_code_and_ink():
    assert line_short_code("MAERSK") == "MAE"
    assert line_short_code("HAPPAG") == "HPL"
    assert line_short_code("CMA CGM") == "CMA"
    assert _ink_on("#D9A404") == "#1a1a2e"  # MSC, светлый фон
    assert _ink_on("#E30613") == "#ffffff"  # CMA, тёмно-красный фон
    assert _ink_on("#4E8FBF") == "#ffffff"


def test_container_list_shows_line_mark(client):
    user = get_user_model().objects.create_user(
        username="line-mark", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    line = Line.objects.create(name="MAERSK")
    Container.objects.create(number="MSCU1234567", status="FLOATING", line=line)
    resp = client.get("/admin/core/container/?status_multi=FLOATING")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "MSCU1234567" in html
    assert "cm-line-mark" in html
    assert "vin-copy-btn" in html
    assert 'data-vin="MSCU1234567"' in html
    assert "cm-count-badge--ok" in html
    assert "MAE" in html
    assert "MSCU1234567" in html
    assert line_mark_html(line)


def test_photo_and_label_columns_share_badge(client):
    """Фото и наклейки — одна пилюля. Пустые ячейки тоже одинаковые."""
    from django.contrib import admin
    from django.utils import timezone

    user = get_user_model().objects.create_user(
        username="badge-cols", password="secret123", is_staff=True, is_superuser=True
    )
    client.force_login(user)
    container = Container.objects.create(number="MSCU7654321", status="FLOATING")
    container.labels_printed_at = timezone.now()
    container._photos_count = 4
    model_admin = admin.site._registry[Container]
    photos = str(model_admin.photos_count_display(container))
    labels = str(model_admin.labels_printed_display(container))
    assert "cm-count-badge cm-count-badge--info" in photos
    assert "bi-camera" in photos
    assert "cm-count-badge cm-count-badge--done" in labels
    assert "bi-tag-fill" in labels

    container.labels_printed_at = None
    container._photos_count = 0
    assert str(model_admin.photos_count_display(container)) == str(model_admin.labels_printed_display(container))
