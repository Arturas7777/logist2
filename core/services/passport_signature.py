"""Авто-извлечение подписи владельца из скана паспорта (пакет автовоза).

Если клиент не загрузил фото подписи, при генерации документов система
вытягивает подпись прямо со скана/фото главной страницы паспорта:

1. Claude Vision находит рамку рукописного росчерка (не штамп и не
   печатную подпись поля). Координаты — доли страницы 0..1.
2. Фрагмент вырезается с запасом из рендера 300 dpi; мелкий кроп
   увеличивается, чтобы смазанные штрихи не схлопнулись в точку.
3. Фон паспорта убирается детерминированно. Печатная линейка поля
   (черта под росчерком) вычитается до порога, чтобы не стать «подписью».
4. Если от смазанной ручки остались точки, Claude проводит тонкую осевую
   по центру видимых штрихов. Клип — сплошная оболочка вокруг чернил
   (без дыр и без линейки): чужой росчерк нарисовать нельзя, свои линии
   не рвутся. Морфология — запасной путь и только с тонким пером.
5. :func:`normalize_signature_image` — синие штрихи, прозрачный фон.

Ошибки любого шага дают ``None`` — вызывающий код просит загрузить
подпись вручную.
"""

from __future__ import annotations

import io
import logging
import os

logger = logging.getLogger(__name__)

# DPI рендера страницы для вырезания: подпись мелкая, 150 dpi ей мало.
_CROP_DPI = 300
# Запас вокруг рамки от модели (доля от размера рамки) — модели часто
# отдают рамку впритык, а нормализатор сам обрежет лишние поля.
_MARGIN_RATIO = 0.12
# Ограничения размеров вырезанного фрагмента.
_MAX_CROP_SIDE = 1400
_MIN_CROP_SIDE = 40
# Санити-границы рамки (доли страницы): подпись не бывает во всю страницу
# и не бывает точкой.
_MIN_BBOX_W, _MAX_BBOX_W = 0.03, 0.85
_MIN_BBOX_H, _MAX_BBOX_H = 0.01, 0.5
# Доля «чернильных» пикселей после порога: меньше — подписи нет,
# больше — порог сорвался на тёмный фон.
_MIN_INK_FRACTION = 0.002
_MAX_INK_FRACTION = 0.30
# После выравнивания фона бумага ~255; бледная ручка 230–250, штамп << 210.
_THRESHOLD_CAP = 205.0
# Мелкий кроп с телефона увеличиваем, иначе штрих — несколько пикселей.
_MIN_PROCESS_SIDE = 360
# Мелкие компоненты (брызги, обрывки сетки) отбрасываются.
_MIN_COMPONENT_PX = 25

SIGNATURE_LOCATE_PROMPT = """Ты находишь рукописную подпись владельца на фото/скане
главной страницы паспорта гражданина Республики Беларусь.

Правила:
- Подпись владельца — рукописный росчерк в поле «Подпись владельца /
  Signature of bearer» (обычно в нижней части главной страницы).
  НЕ путай с печатным текстом, MRZ-строкой и подписями/печатями
  должностных лиц.
- page_index: номер изображения с подписью (0 — первое присланное).
- left/top/right/bottom — рамка вокруг подписи в ДОЛЯХ ширины и высоты
  изображения (числа от 0 до 1), начало координат — левый верхний угол.
- Рамка должна включать росчерк ЦЕЛИКОМ с небольшим запасом.
- НЕ включай в рамку: фото владельца, MRZ, печатную подпись поля
  («Подпись владельца» / «Signature of bearer»), горизонтальную линейку
  поля (чёрная/серая черта ПОД росчерком), штамп, номер страницы.
- Фото часто смазанные: даже бледный росчерк — это found=true.
- found: false — только если рукописных следов нет совсем.

Верни ТОЛЬКО валидный JSON (без markdown):
{"found": true, "page_index": 0, "left": 0.55, "top": 0.60, "right": 0.92, "bottom": 0.74}
"""

SIGNATURE_TRACE_PROMPT = """Ты трассируешь рукописную подпись по кропу поля паспорта.

Человек на этом фото видит непрерывные линии ручки (часто синяя шариковая),
даже если снимок смазан и «шумный». Твоя задача — провести ОСЕВУЮ линию
по центру каждого видимого штриха, а не упростить росчерк до трёх клякс.

Правила:
- Следуй фото: тот же старт, те же петли, тот же финальный росчерк.
  Не придумывай буквы, завитки, дату и ФИО, которых нет даже намёком.
- Не обводи штамп, номер страницы, печатный текст.
- Горизонтальную линейку поля ПОД подписью не трассируй — это не росчерк.
- Точек должно быть МНОГО: шаг примерно 1% ширины кадра, чтобы линия
  была гладкой. Одна дуга — десятки точек, не 4.
- stroke_width — тонкая шариковая (3–5 в координатах viewBox шириной ~1000).
- view_width/view_height совпадают с пропорциями картинки.

Если рукописных штрихов не видно — {"ok": false}.

Верни ТОЛЬКО JSON:
{"ok": true, "view_width": 1000, "view_height": 400, "stroke_width": 4,
 "strokes": [{"points": [[x, y], [x, y], ...]}]}
"""


def ai_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", ""))


def extract_signature_from_passport(path: str) -> bytes | None:
    """PNG подписи (прозрачный фон, синие штрихи) из скана паспорта или None."""
    from core.services import scan_extractor
    from core.services.signature_normalizer import normalize_signature_image

    try:
        pages = scan_extractor._load_page_images(path, dpi=_CROP_DPI)
    except Exception:
        logger.exception("passport_signature: не удалось отрендерить %s", path)
        return None
    if not pages:
        return None

    located = _locate_signature(pages)
    if located is None:
        return None
    page_index, bbox = located

    crop = _crop_with_margin(pages[page_index], bbox)
    if crop is None:
        return None
    cleaned = _clean_signature_crop(crop)
    if cleaned is not None:
        cleaned = _strip_rules_from_image(cleaned)
    if cleaned is not None and _image_is_speckled(cleaned):
        logger.info("passport_signature: порог дал точки — трассируем осевые (%s)", path)
        cleaned = _recover_strokes(crop, cleaned)
    if cleaned is None:
        logger.info("passport_signature: очистка фрагмента не дала штрихов (%s)", path)
        return None
    cleaned = _strip_rules_from_image(cleaned)

    buf = io.BytesIO()
    cleaned.save(buf, format="PNG")
    return normalize_signature_image(buf.getvalue())


# ── Поиск рамки подписи (Claude Vision) ─────────────────────────────────────


def _locate_signature(pages) -> tuple[int, tuple[float, float, float, float]] | None:
    """(page_index, (left, top, right, bottom)) в долях страницы или None."""
    from core.services import scan_extractor

    try:
        images = [scan_extractor._encode_jpeg_under_limit(img) for img in pages]
        data = scan_extractor._call_claude_vision(
            images,
            system_prompt=SIGNATURE_LOCATE_PROMPT,
            user_text=(
                "Найди рукописную подпись владельца. Рамка только вокруг "
                "росчерка, без штампа, без печатного текста и без линейки под ним."
            ),
        )
    except Exception as exc:
        logger.warning("passport_signature: поиск подписи не удался: %s", exc)
        return None
    return _parse_locate_response(data, page_count=len(pages))


def _parse_locate_response(data, *, page_count: int) -> tuple[int, tuple[float, float, float, float]] | None:
    """Валидация ответа модели: рамка разумного размера внутри страницы."""
    if not isinstance(data, dict) or not data.get("found"):
        return None
    try:
        page_index = int(data.get("page_index") or 0)
        left = float(data["left"])
        top = float(data["top"])
        right = float(data["right"])
        bottom = float(data["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= page_index < page_count:
        return None
    # Модель иногда отдаёт −0.01 / 1.02 — чуть вылезает за край, не мусор.
    left = min(max(0.0, left), 1.0)
    top = min(max(0.0, top), 1.0)
    right = min(max(0.0, right), 1.0)
    bottom = min(max(0.0, bottom), 1.0)
    if not (left < right and top < bottom):
        return None
    if not (_MIN_BBOX_W <= right - left <= _MAX_BBOX_W):
        return None
    if not (_MIN_BBOX_H <= bottom - top <= _MAX_BBOX_H):
        return None
    return page_index, (left, top, right, bottom)


def _crop_with_margin(page, bbox: tuple[float, float, float, float]):
    """Вырезает рамку с запасом полей; None если фрагмент слишком мал."""
    w, h = page.size
    left, top, right, bottom = bbox
    margin_x = (right - left) * w * _MARGIN_RATIO
    margin_y = (bottom - top) * h * _MARGIN_RATIO
    # Снизу почти не расширяем — иначе в кадр попадает линейка поля.
    box = (
        max(0, int(left * w - margin_x)),
        max(0, int(top * h - margin_y)),
        min(w, int(right * w + margin_x)),
        min(h, int(bottom * h + margin_y * 0.2)),
    )
    if box[2] - box[0] < _MIN_CROP_SIDE or box[3] - box[1] < _MIN_CROP_SIDE:
        return None
    return page.crop(box)


# ── Очистка фона паспорта (numpy + scipy, без перерисовки) ───────────────────


def _ink_luma(rgb):
    """Яркость, где синяя шариковая ручка тёмная, а цветная сетка — нет.

    max(RGB) поднимает гильош к бумаге, но синие чернила ярки в канале B
    и пропадают. Для пикселей «синее перо» берём min(R, G).
    """
    import numpy as np

    maxc = rgb.max(axis=2)
    min_rg = rgb[:, :, :2].min(axis=2)
    is_blue_pen = rgb[:, :, 2] > rgb[:, :, :2].max(axis=2) + 8.0
    return np.where(is_blue_pen, min_rg, maxc)


def _prepare_crop(crop):
    """Апскейл мелкого фото, ужим гиганта. None если кроп крошечный."""
    from PIL import Image

    img = crop.convert("RGB")
    if min(img.size) < _MIN_CROP_SIDE:
        return None
    long_side = max(img.size)
    if long_side < _MIN_PROCESS_SIDE:
        scale = int((_MIN_PROCESS_SIDE + long_side - 1) / long_side)
        img = img.resize((img.size[0] * scale, img.size[1] * scale), Image.LANCZOS)
    if max(img.size) > _MAX_CROP_SIDE:
        img = img.copy()
        img.thumbnail((_MAX_CROP_SIDE, _MAX_CROP_SIDE), Image.LANCZOS)
    return img


def _drop_corner_stamps(rel):
    """Стирает компактные тёмные пятна в углах (номер страницы, штамп)."""
    import numpy as np
    from scipy import ndimage

    dark = rel < 200.0
    labels, count = ndimage.label(dark)
    if count == 0:
        return rel
    h, w = rel.shape
    corner = np.zeros_like(dark)
    cy, cx = max(8, h // 5), max(8, w // 5)
    corner[:cy, :cx] = corner[:cy, -cx:] = True
    corner[-cy:, :cx] = corner[-cy:, -cx:] = True
    out = rel.copy()
    for lab in range(1, count + 1):
        comp = labels == lab
        ys, xs = np.nonzero(comp)
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        area = int(comp.sum())
        if area < 12:
            continue
        compact = max(bw, bh) / max(1, min(bw, bh)) < 2.6
        small = area < 0.08 * rel.size
        in_corner = bool(np.any(comp & corner))
        if compact and small and in_corner:
            out[ndimage.binary_dilation(comp, iterations=1)] = 255.0
    return out


def _horizontal_rule_mask(mask):
    """Печатная линейка поля: широкая тонкая полоса в нижней части кадра."""
    import numpy as np
    from scipy import ndimage

    h, w = mask.shape
    if h < 16 or w < 40:
        return np.zeros_like(mask, dtype=bool)
    search = np.zeros_like(mask, dtype=bool)
    search[int(h * 0.55) :, :] = True
    k = max(25, w // 5)
    opened = ndimage.binary_opening(mask & search, structure=np.ones((3, k), dtype=bool))
    if not opened.any():
        opened = ndimage.binary_opening(mask & search, structure=np.ones((1, k), dtype=bool))
    labels, n = ndimage.label(opened)
    keep = np.zeros_like(mask, dtype=bool)
    for i in range(1, n + 1):
        ys, xs = np.nonzero(labels == i)
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        if bw >= 0.45 * w and bh <= max(8, int(h * 0.12)):
            keep[labels == i] = True
    if keep.any():
        keep = ndimage.binary_dilation(keep, iterations=2)
    return keep


def _drop_horizontal_rules(mask):
    return mask & ~_horizontal_rule_mask(mask)


def _erase_bottom_rules(rel):
    """Закрашивает линейку поля белым, чтобы порог и клип её не видели."""
    from scipy import ndimage

    rules = _horizontal_rule_mask(rel < 249.0)
    if not rules.any():
        return rel
    out = rel.copy()
    out[ndimage.binary_dilation(rules, iterations=3)] = 255.0
    return out


def _strip_rules_from_image(img):
    """Убирает горизонтальную линейку из уже отрисованной подписи."""
    import numpy as np
    from PIL import Image

    arr = np.asarray(img.convert("L"), dtype=np.uint8)
    rules = _horizontal_rule_mask(arr < 200)
    if not rules.any():
        return img
    out = arr.copy()
    out[rules] = 255
    return Image.fromarray(out, mode="L")


def _clean_signature_crop(crop):
    """Фрагмент паспорта → PIL-«скан» подписи: белый фон, тёмные штрихи.

    Сохраняет исходные пиксели росчерка (контрастный stretch). None — если
    после очистки штрихов не осталось.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    img = _prepare_crop(crop)
    if img is None:
        return None

    rgb = np.asarray(img, dtype=np.float32)
    gray = _ink_luma(rgb)

    sigma = max(gray.shape) / 25.0
    background = ndimage.gaussian_filter(gray, sigma=sigma)
    rel = np.clip(gray / np.maximum(background, 1.0) * 255.0, 0.0, 255.0)
    rel = _drop_corner_stamps(rel)
    rel = _erase_bottom_rules(rel)

    total = rel.size
    threshold = min(_otsu_threshold(rel), _THRESHOLD_CAP)
    mask = rel < threshold
    if mask.sum() > total * _MAX_INK_FRACTION:
        threshold = float(np.quantile(rel, _MAX_INK_FRACTION))
        mask = rel < threshold
    if mask.sum() < total * _MIN_INK_FRACTION:
        return None

    mask = ndimage.binary_closing(mask, iterations=1)
    mask = _filter_components(mask)
    if mask is None or mask.sum() < total * _MIN_INK_FRACTION:
        return None

    vals = rel[mask]
    ink_lo = float(np.quantile(vals, 0.10))
    ink_hi = float(np.quantile(vals, 0.82))
    ink_hi = max(ink_hi, ink_lo + 6.0)
    stretched = np.clip((rel - ink_lo) / (ink_hi - ink_lo) * 200.0, 0.0, 255.0)

    halo = ndimage.binary_dilation(mask, iterations=1)
    out = np.full(rel.shape, 255.0, dtype=np.float32)
    out[halo] = np.minimum(255.0, stretched[halo] + 45.0)
    out[mask] = stretched[mask]
    return Image.fromarray(out.astype("uint8"), mode="L")


def _image_is_speckled(img) -> bool:
    """True если «подпись» — россыпь точек, а не связные штрихи."""
    import numpy as np

    arr = np.asarray(img.convert("L"), dtype=np.uint8)
    return _mask_is_speckled(arr < 200)


def _mask_is_speckled(mask) -> bool:
    import numpy as np
    from scipy import ndimage

    ink = int(mask.sum())
    if ink < 40:
        return True
    labels, count = ndimage.label(mask)
    if count <= 3:
        return False
    sizes = ndimage.sum_labels(mask, labels, index=np.arange(1, count + 1))
    if float(sizes.max()) / ink > 0.55:
        return False
    return count >= 6


def _flatten_crop(crop):
    """Подготовленный кроп + выровненная яркость чернил (бумага ~255)."""
    import numpy as np
    from scipy import ndimage

    img = _prepare_crop(crop)
    if img is None:
        return None, None
    rgb = np.asarray(img, dtype=np.float32)
    gray = _ink_luma(rgb)
    sigma = max(gray.shape) / 25.0
    background = ndimage.gaussian_filter(gray, sigma=sigma)
    rel = np.clip(gray / np.maximum(background, 1.0) * 255.0, 0.0, 255.0)
    rel = _drop_corner_stamps(rel)
    rel = _erase_bottom_rules(rel)
    return img, rel


def _render_from_mask(rel, mask):
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    vals = rel[mask]
    if vals.size < 20:
        return None
    ink_lo = float(np.quantile(vals, 0.10))
    ink_hi = float(np.quantile(vals, 0.82))
    ink_hi = max(ink_hi, ink_lo + 6.0)
    stretched = np.clip((rel - ink_lo) / (ink_hi - ink_lo) * 200.0, 0.0, 255.0)
    halo = ndimage.binary_dilation(mask, iterations=1)
    out = np.full(rel.shape, 255.0, dtype=np.float32)
    out[halo] = np.minimum(255.0, stretched[halo] + 45.0)
    out[mask] = stretched[mask]
    return Image.fromarray(out.astype("uint8"), mode="L")


def _to_thin_strokes(mask):
    """Толстые кляксы → перо ~2–3 px, без большого смыкания."""
    import numpy as np
    from scipy import ndimage

    if mask.sum() < 20:
        return mask
    dist = ndimage.distance_transform_edt(mask)
    med = float(np.median(dist[mask]))
    if med > 2.0:
        eroded = ndimage.binary_erosion(mask, iterations=max(1, int(round(med)) - 1))
        if eroded.sum() >= 20:
            mask = eroded
    mask = ndimage.binary_closing(mask, iterations=2)
    return ndimage.binary_dilation(mask, iterations=1)


def _stroke_envelope(rel):
    """Сплошная зона вокруг чернил: клип не дырявит осевые и не держит линейку."""
    from scipy import ndimage

    generous = rel < 249.0
    closed = ndimage.binary_closing(generous, iterations=2)
    dil = max(8, min(rel.shape) // 25)
    env = ndimage.binary_dilation(closed, iterations=dil)
    return _drop_horizontal_rules(env)


def _reconnect_from_photo(crop):
    """Запасной путь: тонкие штрихи по карте чернил, без жирного closing."""
    from scipy import ndimage

    _img, rel = _flatten_crop(crop)
    if rel is None:
        return None
    generous = rel < 249.0
    if generous.mean() < _MIN_INK_FRACTION:
        return None
    closed = ndimage.binary_closing(generous, iterations=2)
    closed = _drop_horizontal_rules(closed)
    mask = _filter_components(closed)
    if mask is None or mask.mean() > _MAX_INK_FRACTION:
        return None
    mask = _to_thin_strokes(mask)
    mask = _drop_horizontal_rules(mask)
    if mask.sum() < 20:
        return None
    rendered = _render_from_mask(rel, mask)
    if rendered is None:
        return None
    return _strip_rules_from_image(rendered)


def _recover_strokes(crop, speckled):
    """Точки порога → тонкая осевая по фото; морфология только как запас."""
    traced = _trace_centerlines_with_ai(crop, speckled)
    if traced is not None and not _image_is_speckled(traced):
        return _strip_rules_from_image(traced)
    reconnected = _reconnect_from_photo(crop)
    if reconnected is not None and not _image_is_speckled(reconnected):
        return reconnected
    if traced is not None:
        return _strip_rules_from_image(traced)
    return reconnected


def _trace_centerlines_with_ai(crop, hint=None):
    """Claude проводит осевые по видимым штрихам; клип по сплошной оболочке."""
    from core.services import scan_extractor

    prepared = _prepare_crop(crop)
    if prepared is None:
        return None
    _img, rel = _flatten_crop(crop)
    envelope = _stroke_envelope(rel) if rel is not None else None
    try:
        images = [scan_extractor._encode_jpeg_under_limit(prepared)]
        if hint is not None:
            images.append(scan_extractor._encode_jpeg_under_limit(hint.convert("RGB")))
        data = scan_extractor._call_claude_vision(
            images,
            system_prompt=SIGNATURE_TRACE_PROMPT,
            user_text=(
                "Проведи осевые по центру видимых штрихов ручки. Много точек, "
                "тонкая сплошная линия. Линейку поля под росчерком не обводи."
            ),
            max_tokens=8000,
        )
    except Exception as exc:
        logger.warning("passport_signature: трассировка подписи не удалась: %s", exc)
        return None
    parsed = _parse_trace_response(data)
    if parsed is None:
        return None
    drawn = _rasterize_trace(parsed, canvas_size=prepared.size)
    if drawn is None:
        return None
    drawn = _strip_rules_from_image(drawn)
    if envelope is not None:
        clipped = _clip_to_ink_map(drawn, envelope)
        # Дырявый клип хуже непрерывных осевых: оболочка уже без линейки.
        if clipped is not None and not _image_is_speckled(clipped):
            drawn = clipped
        elif clipped is not None and _image_is_speckled(drawn):
            drawn = clipped
    return drawn


def _parse_trace_response(data) -> dict | None:
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    raw_strokes = data.get("strokes")
    if not isinstance(raw_strokes, list | tuple) or not raw_strokes:
        return None
    strokes = []
    for stroke in raw_strokes:
        pts = stroke.get("points") if isinstance(stroke, dict) else stroke
        if not isinstance(pts, list | tuple):
            continue
        clean = []
        for point in pts:
            if not isinstance(point, list | tuple) or len(point) < 2:
                continue
            try:
                clean.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
        if len(clean) >= 4:
            strokes.append({"points": clean})
    if not strokes:
        return None
    total_pts = sum(len(s["points"]) for s in strokes)
    if total_pts < 16:
        return None
    xs = [p[0] for s in strokes for p in s["points"]]
    ys = [p[1] for s in strokes for p in s["points"]]
    if max(xs) - min(xs) < 12 or max(ys) - min(ys) < 4:
        return None
    try:
        view_width = float(data.get("view_width") or (max(xs) + 24))
        view_height = float(data.get("view_height") or (max(ys) + 24))
        stroke_width = float(data.get("stroke_width") or 4)
    except (TypeError, ValueError):
        return None
    return {
        "view_width": max(120.0, min(2000.0, view_width)),
        "view_height": max(60.0, min(1200.0, view_height)),
        "stroke_width": max(3.0, min(5.0, stroke_width)),
        "strokes": strokes,
    }


def _densify_polyline(pts, max_gap: float = 3.0):
    """Промежуточные точки, чтобы дуга не рвалась на длинных сегментах."""
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = (dx * dx + dy * dy) ** 0.5
        n = int(dist / max_gap)
        for i in range(1, n):
            t = i / n
            out.append((a[0] + dx * t, a[1] + dy * t))
        out.append(b)
    return out


def _rasterize_trace(parsed: dict, canvas_size: tuple[int, int]):
    from PIL import Image, ImageDraw

    cw, ch = canvas_size
    vw = parsed["view_width"]
    vh = parsed["view_height"]
    sx, sy = cw / vw, ch / vh
    stroke_w = max(2, min(4, int(round(parsed["stroke_width"] * min(sx, sy)))))
    img = Image.new("RGB", (cw, ch), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    ink = (25, 30, 40)
    radius = stroke_w / 2.0
    for stroke in parsed["strokes"]:
        pts = _densify_polyline([(p[0] * sx, p[1] * sy) for p in stroke["points"]])
        try:
            draw.line(pts, fill=ink, width=stroke_w, joint="curve")
        except TypeError:
            draw.line(pts, fill=ink, width=stroke_w)
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=ink)
    return img.convert("L")


def _clip_to_ink_map(drawn, ink_map):
    """Оставляет только штрихи, попавшие на чернила с фото."""
    import numpy as np
    from PIL import Image

    mask_img = Image.fromarray((ink_map.astype("uint8") * 255), mode="L")
    if mask_img.size != drawn.size:
        mask_img = mask_img.resize(drawn.size, Image.BILINEAR)
    ink = np.asarray(mask_img, dtype=np.uint8) > 40
    arr = np.asarray(drawn.convert("L"), dtype=np.uint8)
    out = np.full(arr.shape, 255, dtype=np.uint8)
    out[ink] = arr[ink]
    if (out < 200).sum() < 40:
        return None
    return Image.fromarray(out, mode="L")


def _filter_components(mask):
    """Оставляет крупные связные компоненты; None если ничего не осталось.

    * брызги и обрывки гильошной сетки мельче ``_MIN_COMPONENT_PX`` — долой;
    * компоненты у края кадра (штамп, печатная «Подпись») — долой, кроме
      крупнейшей (сама подпись при плотной рамке может касаться края);
    * широкая низкая полоса — линейка поля подписи.
    """
    import numpy as np
    from scipy import ndimage

    labels, count = ndimage.label(mask)
    if count == 0:
        return None
    sizes = ndimage.sum_labels(mask, labels, index=np.arange(1, count + 1))
    largest = float(sizes.max())
    largest_label = int(sizes.argmax()) + 1

    min_size = max(float(_MIN_COMPONENT_PX), largest * 0.005)
    keep = sizes >= min_size

    h, w = mask.shape
    my = max(2, int(h * 0.10))
    mx = max(2, int(w * 0.08))
    border = np.zeros(mask.shape, dtype=bool)
    border[:my, :] = border[-my:, :] = True
    border[:, :mx] = border[:, -mx:] = True
    for label in np.unique(labels[border & mask]):
        if label > 0 and label != largest_label and sizes[label - 1] < largest * 0.5:
            keep[label - 1] = False

    for i, lab in enumerate(range(1, count + 1)):
        if not keep[i]:
            continue
        ys, xs = np.nonzero(labels == lab)
        if xs.size == 0:
            continue
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        cy = float(ys.mean())
        # Линейка поля — даже если это крупнейший компонент.
        if (
            bw >= w * 0.45
            and bh <= max(6, int(h * 0.10))
            and bw / max(bh, 1) >= 6
            and cy >= h * 0.55
        ):
            keep[i] = False
            continue
        if lab == largest_label:
            continue
        if bw >= w * 0.5 and bh <= max(4, int(h * 0.07)):
            keep[i] = False

    keep_labels = np.flatnonzero(keep) + 1
    if keep_labels.size == 0:
        return None
    return np.isin(labels, keep_labels)


def _otsu_threshold(values) -> float:
    """Классический порог Оцу по гистограмме (0..255)."""
    import numpy as np

    hist, _ = np.histogram(values, bins=256, range=(0.0, 255.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 0.0
    bin_centers = np.arange(256, dtype=np.float64) + 0.5
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    cum_sum = np.cumsum(hist * bin_centers)
    mean_bg = np.where(weight_bg > 0, cum_sum / np.maximum(weight_bg, 1), 0.0)
    mean_fg = np.where(weight_fg > 0, (cum_sum[-1] - cum_sum) / np.maximum(weight_fg, 1), 0.0)
    variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    return float(bin_centers[int(np.argmax(variance))])
