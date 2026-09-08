"""Тесты авто-извлечения подписи из паспорта (core.services.passport_signature).

Покрытие:

* очистка фрагмента: тонированный фон и цветная гильошная сетка паспорта
  убираются, тёмные штрихи остаются;
* фильтр связных компонент: брызги и обрезанные рамкой чужие линии — долой;
* валидация рамки от Claude Vision (мусорные ответы не роняют пайплайн);
* конец-в-конец на синтетическом «паспорте» (Claude мокается);
* интеграция: генерация документов без загруженной подписи вытягивает её
  из паспорта и сохраняет в слот «Подпись» (is_generated=True), при неудаче
  «Сгенерировать всё» даёт понятную ошибку.
"""

from __future__ import annotations

import datetime
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Car, Client
from core.models.website import TransportRequest, TransportRequestDocument
from core.services import passport_signature
from core.services.passport_signature import (
    _clean_signature_crop,
    _filter_components,
    _otsu_threshold,
    _parse_locate_response,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Синтетика: фрагмент паспорта с подписью
# ---------------------------------------------------------------------------

_PAPER = (215, 205, 190)  # тонированная бумага паспорта
_INK = (30, 40, 90)  # тёмно-синие чернила подписи


def _draw_guilloche(img):
    """Цветная защитная сетка по всей площади (светлее бумаги хотя бы в 1 канале)."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(0, h, 9):
        draw.line([(0, y), (w, y + 6)], fill=(230, 170, 180), width=1)
    for x in range(0, w, 11):
        draw.line([(x, 0), (x + 8, h)], fill=(170, 210, 190), width=1)


def _draw_signature(draw, box):
    """Росчерк из дуг и линий внутри box=(left, top, right, bottom)."""
    left, top, right, bottom = box
    mid_y = (top + bottom) // 2
    draw.line([(left, mid_y), (right, mid_y)], fill=_INK, width=4)
    draw.arc([left, top, (left + right) // 2, bottom], 0, 360, fill=_INK, width=4)
    draw.line([(left, bottom), (right, top)], fill=_INK, width=3)


def _signature_crop(size=(600, 240)):
    """Фрагмент «паспорта»: бумага + сетка + подпись + брызги."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, _PAPER)
    _draw_guilloche(img)
    draw = ImageDraw.Draw(img)
    _draw_signature(draw, (80, 60, 520, 180))
    # Пара брызг — должны исчезнуть после фильтра компонент.
    draw.rectangle([30, 20, 32, 22], fill=(40, 40, 40))
    draw.rectangle([560, 210, 562, 212], fill=(40, 40, 40))
    return img


def test_clean_signature_crop_removes_passport_background():
    import numpy as np

    cleaned = _clean_signature_crop(_signature_crop())
    assert cleaned is not None
    arr = np.asarray(cleaned, dtype=np.uint8)
    # Углы (бумага + сетка) должны стать чисто белыми.
    for corner in (arr[:15, :15], arr[:15, -15:], arr[-15:, :15], arr[-15:, -15:]):
        assert corner.min() == 255
    # Штрихи остались тёмными, но не залили весь кадр.
    ink = (arr < 160).sum()
    assert 0 < ink < arr.size * 0.3
    # Центр горизонтального штриха на месте.
    assert arr[120, 300] < 160


def test_clean_signature_crop_without_ink_returns_none():
    from PIL import Image

    img = Image.new("RGB", (400, 200), _PAPER)
    _draw_guilloche(img)
    assert _clean_signature_crop(img) is None


def test_filter_components_drops_specks_and_border_lines():
    import numpy as np

    mask = np.zeros((100, 200), dtype=bool)
    mask[40:60, 40:160] = True  # «подпись»
    mask[10:12, 10:12] = True  # брызга (4 px)
    mask[0:2, 0:80] = True  # линия, разрезанная границей кадра
    out = _filter_components(mask)
    assert out is not None
    assert out[50, 100]
    assert not out[10, 10]
    assert not out[0, 40]


def test_otsu_threshold_separates_clusters():
    import numpy as np

    values = np.concatenate([np.full(500, 90.0), np.full(9500, 250.0)])
    threshold = _otsu_threshold(values)
    assert 90.0 < threshold < 250.0


def test_parse_locate_response_validates_garbage():
    ok = _parse_locate_response(
        {"found": True, "page_index": 0, "left": 0.5, "top": 0.6, "right": 0.9, "bottom": 0.75},
        page_count=1,
    )
    assert ok == (0, (0.5, 0.6, 0.9, 0.75))
    assert _parse_locate_response({"found": False}, page_count=1) is None
    assert _parse_locate_response({}, page_count=1) is None
    assert _parse_locate_response("не json", page_count=1) is None
    # Рамка вывернута / вне страницы / нереального размера.
    bad_boxes = [
        {"left": 0.9, "top": 0.6, "right": 0.5, "bottom": 0.75},
        {"left": -0.1, "top": 0.6, "right": 0.9, "bottom": 0.75},
        {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0},
        {"left": 0.5, "top": 0.6, "right": 0.505, "bottom": 0.75},
    ]
    for box in bad_boxes:
        assert _parse_locate_response({"found": True, "page_index": 0, **box}, page_count=1) is None
    # Страница за пределами присланных.
    assert (
        _parse_locate_response(
            {"found": True, "page_index": 3, "left": 0.5, "top": 0.6, "right": 0.9, "bottom": 0.75},
            page_count=1,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Конец-в-конец на синтетическом паспорте (Claude мокается)
# ---------------------------------------------------------------------------


def _passport_page_file(tmp_path):
    """Синтетическая «главная страница паспорта» 1200×800 c подписью справа внизу."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1200, 800), _PAPER)
    _draw_guilloche(img)
    draw = ImageDraw.Draw(img)
    # «Печатный текст» страницы — вне рамки подписи.
    for y in range(80, 400, 40):
        draw.line([(300, y), (900, y)], fill=(50, 50, 50), width=6)
    # Подпись в правом нижнем углу (рамка ~0.55..0.92 × 0.62..0.80).
    _draw_signature(draw, (700, 520, 1060, 610))
    path = tmp_path / "passport.jpg"
    img.save(path, format="JPEG", quality=92)
    return str(path)


def test_extract_signature_end_to_end(tmp_path, monkeypatch):
    from PIL import Image

    from core.services import scan_extractor

    monkeypatch.setattr(
        scan_extractor,
        "_call_claude_vision",
        lambda images, system_prompt, user_text: {
            "found": True,
            "page_index": 0,
            "left": 0.55,
            "top": 0.62,
            "right": 0.92,
            "bottom": 0.80,
        },
    )
    png = passport_signature.extract_signature_from_passport(_passport_page_file(tmp_path))
    assert png and png[:4] == b"\x89PNG"

    out = Image.open(io.BytesIO(png)).convert("RGBA")
    alphas = list(out.split()[-1].getdata())
    ink = [px for px in out.getdata() if px[3] > 200]
    # Фон прозрачный, штрихи есть и они синие.
    assert any(a == 0 for a in alphas)
    assert ink
    assert len(ink) < len(alphas) * 0.5
    r, g, b, _ = ink[len(ink) // 2]
    assert b > r and b > g


def test_extract_returns_none_when_not_found(tmp_path, monkeypatch):
    from core.services import scan_extractor

    monkeypatch.setattr(
        scan_extractor,
        "_call_claude_vision",
        lambda images, system_prompt, user_text: {"found": False},
    )
    assert passport_signature.extract_signature_from_passport(_passport_page_file(tmp_path)) is None


# ---------------------------------------------------------------------------
# Интеграция с генерацией документов
# ---------------------------------------------------------------------------


@pytest.fixture
def car(db):
    return Car.objects.create(year=2023, brand="Chevrolet Malibu", vin="1G1ZD5ST0PF171248", status="UNLOADED")


@pytest.fixture
def transport_request(car, db):
    client = Client.objects.create(name="Test Portal Client")
    tr = TransportRequest.objects.create(client=client, carrier_name="MAXER", status="DRAFT")
    tr.cars.add(car)
    return tr


def _fake_signature_png() -> bytes:
    from PIL import Image

    from core.services.signature_normalizer import normalize_signature_image

    img = Image.new("RGB", (300, 120), (255, 255, 255))
    for x in range(20, 280):
        for y in range(50, 62):
            img.putpixel((x, y), (15, 15, 15))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return normalize_signature_image(buf.getvalue())


def _add_passport(transport_request, car):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 60), _PAPER).save(buf, format="JPEG")
    return TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="PASSPORT",
        file=SimpleUploadedFile("passport.jpg", buf.getvalue(), content_type="image/jpeg"),
    )


def _post(**extra):
    from django.http import QueryDict

    qd = QueryDict("", mutable=True)
    qd.update(
        {
            "buyer_name": "ZIZIKA ULADZIMIR",
            "buyer_name_ru": "Зизико Владимир Константинович",
            "buyer_passport_number": "MC3902087",
            "buyer_address": "ul. Gaya 5, Belarus",
            "buyer_address_ru": "д. Большая Лысица, ул. Гая 5",
            "buyer_birth_date": "1967-01-29",
            "buyer_passport_issue_date": "2025-10-22",
            "invoice_number": "INV-1",
            # Письмо USA требует, чтобы с даты инвойса прошло ≥ 4 недель.
            "invoice_date": (datetime.date.today() - datetime.timedelta(days=45)).isoformat(),
            "invoice_amount": "2850",
        }
    )
    qd.update(extra)
    return qd


def test_generate_all_auto_extracts_signature(transport_request, car, settings, tmp_path, monkeypatch):
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    _add_passport(transport_request, car)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_png = _fake_signature_png()
    monkeypatch.setattr(passport_signature, "extract_signature_from_passport", lambda path: fake_png)

    notices = actions.generate_all_for_car(
        transport_request=transport_request,
        car=car,
        post=_post(),
        files={},
        user=None,
    )

    sig_doc = transport_request.documents.get(car=car, doc_type="SIGNATURE")
    assert sig_doc.is_generated
    with sig_doc.file.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"
    assert transport_request.documents.filter(car=car, doc_type="OBLIGATION", is_generated=True).exists()
    assert any("вытянута из паспорта" in text for _, text in notices)
    # Обязательство собрано С подписью — предупреждения «без подписи» нет.
    assert not any("без подписи" in text for _, text in notices)


def test_generate_all_fails_clearly_when_extraction_impossible(transport_request, car, settings, tmp_path, monkeypatch):
    from core.services import transport_package_actions as actions
    from core.services.transport_docs import PackageDataError

    settings.MEDIA_ROOT = str(tmp_path)
    _add_passport(transport_request, car)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(passport_signature, "extract_signature_from_passport", lambda path: None)

    with pytest.raises(PackageDataError, match="подпись не загружена"):
        actions.generate_all_for_car(
            transport_request=transport_request,
            car=car,
            post=_post(),
            files={},
            user=None,
        )
    assert not transport_request.documents.filter(car=car, doc_type="SIGNATURE").exists()


def test_generate_all_prefers_uploaded_signature(transport_request, car, settings, tmp_path, monkeypatch):
    """Загруженная вручную подпись используется как раньше — AI не вызывается."""
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    _add_passport(transport_request, car)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(path):
        raise AssertionError("авто-извлечение не должно вызываться при загруженной подписи")

    monkeypatch.setattr(passport_signature, "extract_signature_from_passport", _boom)
    TransportRequestDocument.objects.create(
        request=transport_request,
        car=car,
        doc_type="SIGNATURE",
        file=SimpleUploadedFile("sign.png", _fake_signature_png(), content_type="image/png"),
    )

    notices = actions.generate_all_for_car(
        transport_request=transport_request,
        car=car,
        post=_post(),
        files={},
        user=None,
    )
    assert any("Пакет сгенерирован" in text for _, text in notices)


def test_single_doc_generate_auto_extracts_signature(transport_request, car, settings, tmp_path, monkeypatch):
    """Генерация обязательства без подписи вытягивает её из паспорта."""
    from core.services import transport_package_actions as actions

    settings.MEDIA_ROOT = str(tmp_path)
    _add_passport(transport_request, car)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_png = _fake_signature_png()
    monkeypatch.setattr(passport_signature, "extract_signature_from_passport", lambda path: fake_png)

    captured = {}

    def _fake_generate(tr, c, data, doc_type, signature_bytes=None):
        captured["signature"] = signature_bytes
        return ("obligation.pdf", b"%PDF-fake", [])

    monkeypatch.setattr(actions.docs_service, "generate_document", _fake_generate)

    notices = actions.apply_doc_action(
        transport_request=transport_request,
        car=car,
        doc_type="OBLIGATION",
        post=_post(action="generate"),
        files=[],
        user=None,
    )

    assert captured["signature"] == fake_png
    assert transport_request.documents.filter(car=car, doc_type="SIGNATURE", is_generated=True).exists()
    assert any("вытянута из паспорта" in text for _, text in notices)
