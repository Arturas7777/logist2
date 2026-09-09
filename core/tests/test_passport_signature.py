"""Тесты авто-извлечения подписи из паспорта (core.services.passport_signature).

Покрытие:

* очистка фрагмента: тонированный фон и цветная гильошная сетка паспорта
  убираются, тёмные и синие штрихи остаются; штамп в углу не перебивает
  бледную ручку;
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
    _mask_is_speckled,
    _otsu_threshold,
    _parse_locate_response,
    _parse_trace_response,
    _rasterize_trace,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Синтетика: фрагмент паспорта с подписью
# ---------------------------------------------------------------------------

_PAPER = (215, 205, 190)  # тонированная бумага паспорта
_INK = (30, 40, 90)  # тёмно-синие чернила подписи
_BLUE_PEN = (80, 120, 195)  # типичная шариковая ручка (ярко-синий канал)


def _draw_guilloche(img):
    """Цветная защитная сетка по всей площади (светлее бумаги хотя бы в 1 канале)."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(0, h, 9):
        draw.line([(0, y), (w, y + 6)], fill=(230, 170, 180), width=1)
    for x in range(0, w, 11):
        draw.line([(x, 0), (x + 8, h)], fill=(170, 210, 190), width=1)


def _draw_signature(draw, box, fill=_INK):
    """Росчерк из дуг и линий внутри box=(left, top, right, bottom)."""
    left, top, right, bottom = box
    mid_y = (top + bottom) // 2
    draw.line([(left, mid_y), (right, mid_y)], fill=fill, width=4)
    draw.arc([left, top, (left + right) // 2, bottom], 0, 360, fill=fill, width=4)
    draw.line([(left, bottom), (right, top)], fill=fill, width=3)


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

    cleaned = _clean_signature_crop(_signature_crop(size=(800, 320)))
    assert cleaned is not None
    arr = np.asarray(cleaned, dtype=np.uint8)
    # Углы (бумага + сетка) должны стать чисто белыми.
    for corner in (arr[:15, :15], arr[:15, -15:], arr[-15:, :15], arr[-15:, -15:]):
        assert corner.min() == 255
    # Штрихи остались тёмными, но не залили весь кадр.
    ink = (arr < 160).sum()
    assert 0 < ink < arr.size * 0.3


def test_clean_signature_crop_without_ink_returns_none():
    from PIL import Image

    img = Image.new("RGB", (400, 200), _PAPER)
    _draw_guilloche(img)
    assert _clean_signature_crop(img) is None


def test_clean_keeps_blue_ballpoint_and_drops_corner_stamp():
    """Синяя ручка не должна пропадать из-за яркого канала B; штамп — долой."""
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 240), _PAPER)
    _draw_guilloche(img)
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 54, 54], outline=(70, 40, 85), width=5)
    _draw_signature(draw, (80, 60, 520, 180), fill=_BLUE_PEN)
    cleaned = _clean_signature_crop(img)
    assert cleaned is not None
    arr = np.asarray(cleaned, dtype=np.uint8)
    assert arr[:18, :18].min() > 200
    assert (arr < 160).sum() > 150


def test_clean_recovers_faint_signature_despite_dark_stamp():
    """Бледный росчерк + тёмный штамп: растягиваем пиксели подписи, не рисуем новую."""
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 240), _PAPER)
    _draw_guilloche(img)
    draw = ImageDraw.Draw(img)
    draw.ellipse([6, 6, 50, 50], outline=(55, 40, 70), width=6)
    faint = (150, 158, 175)
    _draw_signature(draw, (90, 70, 510, 175), fill=faint)
    cleaned = _clean_signature_crop(img)
    assert cleaned is not None
    arr = np.asarray(cleaned, dtype=np.uint8)
    assert arr[:16, :16].min() > 200
    assert (arr < 180).sum() > 80


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


def test_filter_drops_bottom_rule_even_if_largest():
    import numpy as np

    mask = np.zeros((100, 200), dtype=bool)
    mask[40:55, 70:110] = True  # подпись меньше линейки
    mask[90:93, 5:195] = True  # линейка поля
    out = _filter_components(mask)
    assert out is not None
    assert out[48, 90]
    assert not out[91, 100]


def test_clean_drops_passport_field_line():
    """Черта под росчерком не должна стать частью подписи."""
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 240), _PAPER)
    _draw_guilloche(img)
    draw = ImageDraw.Draw(img)
    _draw_signature(draw, (80, 40, 520, 140))
    draw.line([(15, 222), (585, 222)], fill=(25, 25, 25), width=5)
    cleaned = _clean_signature_crop(img)
    assert cleaned is not None
    arr = np.asarray(cleaned, dtype=np.uint8)
    bottom = arr[int(arr.shape[0] * 0.88) :]
    assert (bottom < 160).sum() < bottom.size * 0.02
    assert (arr[: int(arr.shape[0] * 0.7)] < 160).sum() > 150


def test_rasterize_keeps_thin_continuous_stroke():
    import numpy as np

    dense_pts = [[10 + i * 8, 40 + (i % 5) * 2] for i in range(20)]
    parsed = _parse_trace_response(
        {
            "ok": True,
            "view_width": 200,
            "view_height": 80,
            "stroke_width": 10,
            "strokes": [{"points": dense_pts}],
        }
    )
    assert parsed is not None
    assert parsed["stroke_width"] <= 5
    img = _rasterize_trace(parsed, canvas_size=(200, 80))
    arr = np.asarray(img, dtype=np.uint8)
    ink = arr < 80
    col = ink[:, 100]
    thickness = int(col.sum())
    assert 2 <= thickness <= 10
    # линия не рвётся на середине
    assert ink[38:50, 40:160].any(axis=0).mean() > 0.9


def test_otsu_threshold_separates_clusters():
    import numpy as np

    values = np.concatenate([np.full(500, 90.0), np.full(9500, 250.0)])
    threshold = _otsu_threshold(values)
    assert 90.0 < threshold < 250.0


def test_mask_is_speckled_detects_dots_not_stroke():
    import numpy as np

    stroke = np.zeros((80, 200), dtype=bool)
    stroke[30:50, 20:180] = True
    assert _mask_is_speckled(stroke) is False
    dots = np.zeros((80, 200), dtype=bool)
    for x, y in ((20, 40), (40, 30), (70, 50), (110, 35), (140, 45), (170, 40)):
        dots[y - 1 : y + 2, x - 1 : x + 2] = True
    assert _mask_is_speckled(dots) is True


def test_parse_trace_requires_dense_stroke():
    sparse = {
        "ok": True,
        "view_width": 200,
        "view_height": 80,
        "stroke_width": 6,
        "strokes": [{"points": [[10, 40], [80, 40], [150, 40]]}],
    }
    assert _parse_trace_response(sparse) is None
    dense_pts = [[10 + i * 8, 40 + (i % 5) * 2] for i in range(20)]
    ok = _parse_trace_response(
        {"ok": True, "view_width": 200, "view_height": 80, "stroke_width": 6, "strokes": [{"points": dense_pts}]}
    )
    assert ok is not None
    img = _rasterize_trace(ok, canvas_size=(200, 80))
    import numpy as np

    arr = np.asarray(img, dtype=np.uint8)
    assert (arr < 80).sum() > 80


def test_extract_traces_lines_when_pixels_are_specks(tmp_path, monkeypatch):
    """ИИ проводит осевые, если порог оставил точки."""
    from PIL import Image, ImageDraw

    from core.services import scan_extractor

    speckle = Image.new("L", (400, 150), 255)
    draw = ImageDraw.Draw(speckle)
    for x, y in ((30, 70), (50, 60), (80, 75), (200, 40), (230, 55), (260, 70), (340, 50)):
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=25)

    monkeypatch.setattr(passport_signature, "_clean_signature_crop", lambda crop: speckle)
    monkeypatch.setattr(passport_signature, "_reconnect_from_photo", lambda crop: speckle)
    monkeypatch.setattr(passport_signature, "_clip_to_ink_map", lambda drawn, ink_map: drawn)

    dense_pts = [[20 + i * 45, 40 + (i % 4) * 8] for i in range(20)]

    def fake_claude(images, system_prompt, user_text, **_kwargs):
        if "трассируешь" in system_prompt:
            return {
                "ok": True,
                "view_width": 400,
                "view_height": 150,
                "stroke_width": 6,
                "strokes": [{"points": dense_pts}],
            }
        return {
            "found": True,
            "page_index": 0,
            "left": 0.55,
            "top": 0.62,
            "right": 0.92,
            "bottom": 0.80,
        }

    monkeypatch.setattr(scan_extractor, "_call_claude_vision", fake_claude)
    img = Image.new("RGB", (1200, 800), _PAPER)
    _draw_guilloche(img)
    path = tmp_path / "passport.jpg"
    img.save(path, format="JPEG", quality=80)
    png = passport_signature.extract_signature_from_passport(str(path))
    assert png and png[:4] == b"\x89PNG"
    out = Image.open(io.BytesIO(png)).convert("RGBA")
    ink = sum(1 for px in out.getdata() if px[3] > 180)
    assert ink > 200


def test_parse_locate_response_validates_garbage():
    ok = _parse_locate_response(
        {"found": True, "page_index": 0, "left": 0.5, "top": 0.6, "right": 0.9, "bottom": 0.75},
        page_count=1,
    )
    assert ok == (0, (0.5, 0.6, 0.9, 0.75))
    assert _parse_locate_response({"found": False}, page_count=1) is None
    assert _parse_locate_response({}, page_count=1) is None
    assert _parse_locate_response("не json", page_count=1) is None
    # Рамка вывернута / нереального размера.
    bad_boxes = [
        {"left": 0.9, "top": 0.6, "right": 0.5, "bottom": 0.75},
        {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0},
        {"left": 0.5, "top": 0.6, "right": 0.505, "bottom": 0.75},
    ]
    for box in bad_boxes:
        assert _parse_locate_response({"found": True, "page_index": 0, **box}, page_count=1) is None
    # Чуть вылезла за край — зажимаем, не отбрасываем.
    clamped = _parse_locate_response(
        {"found": True, "page_index": 0, "left": -0.02, "top": 0.6, "right": 0.4, "bottom": 0.75},
        page_count=1,
    )
    assert clamped == (0, (0.0, 0.6, 0.4, 0.75))
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


def test_extract_returns_none_on_empty_field(tmp_path, monkeypatch):
    """Пустое поле без росчерка — None, без выдуманной «подписи» по точкам."""
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
    img = Image.new("RGB", (1200, 800), _PAPER)
    _draw_guilloche(img)
    path = tmp_path / "empty.jpg"
    img.save(path, format="JPEG", quality=70)
    assert passport_signature.extract_signature_from_passport(str(path)) is None


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
