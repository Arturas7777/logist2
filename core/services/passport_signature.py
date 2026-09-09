"""Авто-извлечение подписи владельца из скана паспорта (пакет автовоза).

Если клиент не загрузил фото подписи, при генерации документов система
вытягивает подпись прямо со скана/фото главной страницы паспорта:

1. Claude Vision находит рамку подписи на странице. Координаты запрашиваются
   в долях ширины/высоты (0..1) — они не зависят от того, что API ужимает
   отправляемую картинку, а вырезаем мы из рендера высокого разрешения.
2. Фрагмент вырезается с запасом полей из рендера 300 dpi.
3. Фон паспорта (тонированная бумага + цветная гильошная сетка) убирается
   детерминированно, без AI:
   * яркость берётся как max по RGB-каналам — чернила темны во всех каналах,
     а цветные защитные линии ярки хотя бы в одном и уходят к фону;
   * выравнивание освещения делением на сильно размытую копию убирает
     тонировку бумаги и градиенты света;
   * порог Оцу отделяет штрихи от остатков сетки;
   * фильтр связных компонент убирает брызги и обрезанный рамкой чужой
     текст/линии.
4. Если очистка не дала внятного росчерка (смазанное/тёмное фото, бледные
   чернила) — Claude по тому же кропу обводит видимые штрихи и слегка
   смыкает очевидные разрывы. Новую подпись он не придумывает: только то,
   что читается на исходнике.
5. Результат проходит общий :func:`normalize_signature_image` — штрихи
   становятся синими «как от ручки», фон прозрачным, поля обрезаются.

Ошибки любого шага дают ``None`` — вызывающий код просит клиента загрузить
подпись вручную, как раньше.
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
# Порог Оцу не поднимаем выше: гильошная сетка после выравнивания фона
# светлее ~215, чернила темнее ~190.
_THRESHOLD_CAP = 205.0
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
- Рамка должна включать росчерк ЦЕЛИКОМ с небольшим запасом, но не
  захватывать соседний печатный текст и фото.
- Фото часто смазанные и тёмные: даже бледный, рваный или частично
  читаемый росчерк — это found=true. Верни рамку вокруг ВСЕХ различимых
  рукописных штрихов в поле подписи.
- found: false — только если в поле подписи нет вообще никаких
  рукописных следов.

Верни ТОЛЬКО валидный JSON (без markdown):
{"found": true, "page_index": 0, "left": 0.55, "top": 0.60, "right": 0.92, "bottom": 0.74}
"""

SIGNATURE_ENHANCE_PROMPT = """Ты восстанавливаешь рукописную подпись владельца по кропу
с фото/скана паспорта. Качество исходника часто плохое: смаз, JPEG, тени.

Тебе дано одно или два изображения:
1) исходный кроп поля «Подпись владельца»;
2) опционально — детерминированная очистка (может быть дырявой или почти пустой).

Задача: обвести ВИДИМЫЕ рукописные штрихи и слегка сомкнуть очевидные
разрывы одного и того же росчерка (как будто шариковая ручка не оторвалась
на миллиметр-два). Результат должен быть похож на обычную человеческую
подпись, но это тот же росчерк, что на фото, а не новый.

Строго нельзя:
- придумывать новую подпись, другие буквы, завитки, дату, ФИО;
- добавлять элементы, которых нет даже намёком на исходнике;
- обводить печатный текст, рамку поля, MRZ, гильошную сетку паспорта.

Если рукописных штрихов совсем не видно — верни {"ok": false}.

Координаты — в viewBox (view_width × view_height), начало слева сверху.
Каждый stroke — одно непрерывное движение пера. Точек должно быть достаточно
часто (шаг примерно 1–3% ширины), чтобы линия выглядела гладкой.

Верни ТОЛЬКО валидный JSON (без markdown):
{"ok": true, "view_width": 1000, "view_height": 400, "stroke_width": 10,
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
    source = cleaned
    if not _signature_quality_ok(cleaned):
        logger.info("passport_signature: очистка слабая или пустая — пробуем дорисовку по кропу (%s)", path)
        enhanced = _enhance_signature_with_ai(crop, cleaned)
        if enhanced is not None:
            source = enhanced
        elif cleaned is None:
            logger.info("passport_signature: ни очистка, ни дорисовка не дали штрихов (%s)", path)
            return None
    if source is None:
        return None

    buf = io.BytesIO()
    source.save(buf, format="PNG")
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
            user_text="Найди рукописную подпись владельца на страницах паспорта и верни рамку по схеме.",
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
    box = (
        max(0, int(left * w - margin_x)),
        max(0, int(top * h - margin_y)),
        min(w, int(right * w + margin_x)),
        min(h, int(bottom * h + margin_y)),
    )
    if box[2] - box[0] < _MIN_CROP_SIDE or box[3] - box[1] < _MIN_CROP_SIDE:
        return None
    return page.crop(box)


# ── Дорисовка бледного кропа (Claude Vision → полилинии) ─────────────────────


def _signature_quality_ok(cleaned) -> bool:
    """Хватает ли детерминированной очистки, или нужна дорисовка по кропу."""
    if cleaned is None:
        return False
    import numpy as np
    from scipy import ndimage

    arr = np.asarray(cleaned.convert("L"), dtype=np.uint8)
    ink = arr < 200
    total = int(arr.size)
    ink_count = int(ink.sum())
    if ink_count < max(80, total * 0.003):
        return False
    labels, count = ndimage.label(ink)
    if count == 0 or count > 35:
        return False
    sizes = ndimage.sum_labels(ink, labels, index=np.arange(1, count + 1))
    largest = float(sizes.max())
    if largest < 40 or largest / ink_count < 0.25:
        return False
    if min(cleaned.size) < 24:
        return False
    return True


def _enhance_signature_with_ai(crop, cleaned=None):
    """Обводит видимые штрихи на кропе. None если модель не смогла."""
    from core.services import scan_extractor

    try:
        prepared = _prepare_crop_for_vision(crop)
        images = [scan_extractor._encode_jpeg_under_limit(prepared)]
        if cleaned is not None:
            images.append(scan_extractor._encode_jpeg_under_limit(cleaned.convert("RGB")))
        data = scan_extractor._call_claude_vision(
            images,
            system_prompt=SIGNATURE_ENHANCE_PROMPT,
            user_text=(
                "Восстанови подпись по кропу. Не выдумывай новый росчерк — "
                "только видимые штрихи и очевидные склейки разрывов."
            ),
        )
    except Exception as exc:
        logger.warning("passport_signature: дорисовка подписи не удалась: %s", exc)
        return None
    parsed = _parse_enhance_response(data)
    if parsed is None:
        return None
    return _rasterize_strokes(parsed)


def _prepare_crop_for_vision(crop):
    """Контраст + лёгкий апскейл, чтобы модели было проще увидеть бледные штрихи."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    img = crop.convert("RGB")
    img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.3)
    w, h = img.size
    long_side = max(w, h)
    if long_side < 480:
        scale = 480 / long_side
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return img


def _parse_enhance_response(data) -> dict | None:
    """Валидация JSON дорисовки: набор полилиний разумного размера."""
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
        if len(clean) >= 2:
            strokes.append({"points": clean})
    if not strokes:
        return None
    xs = [p[0] for s in strokes for p in s["points"]]
    ys = [p[1] for s in strokes for p in s["points"]]
    if len(xs) < 5:
        return None
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    if span_x < 8 or span_y < 3:
        return None
    try:
        view_width = float(data.get("view_width") or (max(xs) + 24))
        view_height = float(data.get("view_height") or (max(ys) + 24))
        stroke_width = float(data.get("stroke_width") or 10)
    except (TypeError, ValueError):
        return None
    view_width = max(120.0, min(2000.0, view_width))
    view_height = max(60.0, min(1200.0, view_height))
    stroke_width = max(6.0, min(22.0, stroke_width))
    return {
        "view_width": view_width,
        "view_height": view_height,
        "stroke_width": stroke_width,
        "strokes": strokes,
    }


def _rasterize_strokes(parsed: dict):
    """Полилинии модели → PIL L (белый фон, тёмные штрихи), обрезка полей."""
    from PIL import Image, ImageDraw

    vw = max(1, int(round(parsed["view_width"])))
    vh = max(1, int(round(parsed["view_height"])))
    stroke_w = int(round(parsed["stroke_width"]))
    img = Image.new("RGB", (vw, vh), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    ink = (25, 30, 40)
    radius = max(1.0, parsed["stroke_width"] / 2.0)
    for stroke in parsed["strokes"]:
        pts = [(p[0], p[1]) for p in stroke["points"]]
        try:
            draw.line(pts, fill=ink, width=stroke_w, joint="curve")
        except TypeError:
            draw.line(pts, fill=ink, width=stroke_w)
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=ink)
    gray = img.convert("L")
    mask = gray.point(lambda v: 0 if v > 240 else 255)
    bbox = mask.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    pad_x = max(6, int((right - left) * 0.08))
    pad_y = max(6, int((bottom - top) * 0.08))
    box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(vw, right + pad_x),
        min(vh, bottom + pad_y),
    )
    return gray.crop(box)


# ── Очистка фона паспорта (numpy + scipy, без AI) ───────────────────────────


def _clean_signature_crop(crop):
    """Фрагмент паспорта → PIL-«скан» подписи: белый фон, тёмные штрихи.

    None — если после очистки штрихов не осталось (рамка попала мимо).
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    if max(crop.size) > _MAX_CROP_SIDE:
        crop = crop.copy()
        crop.thumbnail((_MAX_CROP_SIDE, _MAX_CROP_SIDE), Image.LANCZOS)
    if min(crop.size) < _MIN_CROP_SIDE:
        return None

    rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
    # Чернила темны во всех каналах; цветная гильошная сетка яркая хотя бы
    # в одном — max по каналам поднимает её к фону ещё до порога.
    gray = rgb.max(axis=2)

    # Выравнивание фона: деление на сильно размытую копию убирает тонировку
    # бумаги и неравномерный свет (фон → ~255 везде).
    sigma = max(gray.shape) / 25.0
    background = ndimage.gaussian_filter(gray, sigma=sigma)
    flat = np.clip(gray / np.maximum(background, 1.0) * 255.0, 0.0, 255.0)

    threshold = min(_otsu_threshold(flat), _THRESHOLD_CAP)
    mask = flat < threshold
    total = mask.size
    if mask.sum() > total * _MAX_INK_FRACTION:
        # Порог сорвался на тёмный фон — жёсткий квантиль по доле чернил.
        threshold = float(np.quantile(flat, _MAX_INK_FRACTION))
        mask = flat < threshold
    if mask.sum() < total * _MIN_INK_FRACTION:
        return None

    mask = _filter_components(mask)
    if mask is None or mask.sum() < total * _MIN_INK_FRACTION:
        return None

    # Кольцо в 1 px вокруг штрихов оставляем с реальной яркостью —
    # полутона сгладят края после нормализации (анти-алиасинг).
    halo = ndimage.binary_dilation(mask, iterations=1)
    out = np.full(flat.shape, 255.0, dtype=np.float32)
    out[halo] = flat[halo]
    return Image.fromarray(out.astype("uint8"), mode="L")


def _filter_components(mask):
    """Оставляет крупные связные компоненты; None если ничего не осталось.

    * брызги и обрывки гильошной сетки мельче ``_MIN_COMPONENT_PX`` — долой;
    * компоненты, касающиеся границы кадра, — чужой текст/линии, разрезанные
      рамкой (запас полей уже добавлен, сама подпись до края не достаёт);
      крупнейшую компоненту не трогаем никогда.
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

    border = np.zeros(mask.shape, dtype=bool)
    border[:2, :] = border[-2:, :] = True
    border[:, :2] = border[:, -2:] = True
    for label in np.unique(labels[border & mask]):
        if label > 0 and label != largest_label and sizes[label - 1] < largest * 0.5:
            keep[label - 1] = False

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
