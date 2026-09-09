"""Авто-извлечение подписи владельца из скана паспорта (пакет автовоза).

Если клиент не загрузил фото подписи, при генерации документов система
вытягивает подпись прямо со скана/фото главной страницы паспорта:

1. Claude Vision находит рамку рукописного росчерка (не штамп и не
   печатную подпись поля). Координаты — доли страницы 0..1.
2. Фрагмент вырезается с запасом из рендера 300 dpi; мелкий кроп
   увеличивается, чтобы смазанные штрихи не схлопнулись в точку.
3. Чёткий скан: фон паспорта убирается детерминированно (порог Оцу,
   фильтр компонент), результат идёт в :func:`normalize_signature_image`.
4. Смазанное фото (порог даёт крап): мягкое хрома-извлечение
   (:func:`_extract_soft_chroma`) — карта «чернильности» по отклонению
   от цвета бумаги идёт прямо в альфа-канал, БЕЗ бинаризации и без
   перерисовки. Полутона сохраняются, штрихи остаются сплошными —
   картинка та же, что видит человек на фото. Линейка поля вычитается
   подгонкой прямой (фото бывает под углом), печать/штампы — фильтром
   компонент по хроме и геометрии.
5. Если и мягкий путь не дал росчерка — честный ``None``: вызывающий
   код просит загрузить фото подписи. Никакой дорисовки «по мотивам».
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
# Мягкое хрома-извлечение: рабочая длинная сторона кропа.
_SOFT_PROCESS_SIDE = 800

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
    if cleaned is None or _image_is_speckled(cleaned):
        # Смазанное фото: бинаризация рвёт штрихи. Мягкое хрома-извлечение
        # отдаёт готовый PNG (полутона в альфе, нормализатор не нужен).
        logger.info("passport_signature: порог не дал штрихов — мягкий хрома-путь (%s)", path)
        soft = _extract_soft_chroma(crop)
        if soft is not None:
            return soft
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


# ── Мягкое хрома-извлечение (смазанные фото) ─────────────────────────────────
#
# Принцип: человек видит подпись на смазе за счёт полутонов и синего цвета.
# Поэтому вместо порога Оцу строится непрерывная карта «чернильности»
# (насколько пиксель синее и темнее локальной бумаги), линии усиливаются
# вдоль своего направления (разрывы смыкаются), и штрих рендерится тонкой
# лентой вокруг связного скелета — как перо, а не как кляксы бинаризации.


def _extract_soft_chroma(crop) -> bytes | None:
    """Смазанное фото → готовый PNG (синие штрихи, прозрачный фон) или None.

    Нормализатор (:func:`normalize_signature_image`) не вызывается: его
    haze-cutoff и dilate заточены под чёткое фото на белом и съедают
    полутона, ради которых этот путь и существует.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    from core.services.signature_normalizer import _INK_RGB, _fit_max_side

    img = crop.convert("RGB")
    if min(img.size) < _MIN_CROP_SIDE:
        return None
    # Фиксированный рабочий масштаб: параметры сглаживания/фильтров стабильны.
    scale = _SOFT_PROCESS_SIDE / max(img.size)
    if scale != 1.0:
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )
    rgb = np.asarray(img, dtype=np.float32)
    h, w = rgb.shape[:2]

    # Цвет бумаги — локально, по каждому каналу (фон паспорта неоднородный).
    sigma = max(h, w) / 12.0
    paper = np.stack(
        [ndimage.gaussian_filter(rgb[:, :, c], sigma=sigma) for c in range(3)], axis=2
    )
    diff = paper - rgb  # положительное = темнее бумаги
    # «Синева»: R и G упали заметно сильнее, чем B (профиль синей пасты).
    blueness = np.clip(np.minimum(diff[:, :, 0], diff[:, :, 1]) - diff[:, :, 2], 0.0, None)
    darkness = np.clip(diff.mean(axis=2), 0.0, None)
    # Пиксельный гейт по хроме не делаем: на смазе синева слабеет именно в
    # размытых участках штриха. Хрома решает на уровне компонент (ниже).
    score = 0.65 * blueness + 0.35 * darkness

    score = _suppress_field_rule_soft(score, blueness, darkness)
    alpha01 = _soft_alpha(score)
    if alpha01 is None:
        return None
    alpha01 = _filter_soft_components(alpha01, blueness, darkness)
    if alpha01 is None:
        return None

    ink_fraction = float((alpha01 > 0.25).mean())
    if not (_MIN_INK_FRACTION <= ink_fraction <= _MAX_INK_FRACTION):
        return None

    alpha = (alpha01 * 255.0).astype(np.uint8)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 0], out[:, :, 1], out[:, :, 2] = _INK_RGB
    out[:, :, 3] = alpha
    result = Image.fromarray(out, mode="RGBA")

    dense = Image.fromarray((alpha >= 60).astype(np.uint8) * 255, mode="L")
    bbox = dense.getbbox()
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    if right - left < 24 or bottom - top < 10:
        return None
    pad = 12
    result = result.crop(
        (max(0, left - pad), max(0, top - pad), min(w, right + pad), min(h, bottom + pad))
    )
    result = _fit_max_side(result, 900)

    import io as _io

    buf = _io.BytesIO()
    result.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _suppress_field_rule_soft(score, blueness, darkness):
    """Гасит печатную линейку поля на карте чернильности.

    Фото бывает под углом, линейка идёт по диагонали — горизонтальный
    opening её целиком не ловит. Поэтому: находим короткие горизонтальные
    сегменты ахроматичной (не синей) черноты, подгоняем по ним прямую и
    гасим узкую полосу вдоль неё. Явно синие пиксели (росчерк поверх
    линейки) не трогаем.
    """
    import numpy as np
    from scipy import ndimage

    h, w = score.shape
    achromatic = (darkness > 12.0) & (blueness < 0.35 * darkness)
    lower = np.zeros_like(achromatic)
    lower[int(h * 0.45) :, :] = True
    seg_k = max(20, w // 18)
    segments = ndimage.binary_opening(
        ndimage.binary_closing(achromatic & lower, structure=np.ones((3, 3), dtype=bool)),
        structure=np.ones((3, seg_k), dtype=bool),
    )
    out = score.copy()
    keep_blue = blueness > 0.55 * np.maximum(darkness, 1.0)
    ys, xs = np.nonzero(segments)
    if xs.size > 50 and np.ptp(xs) > 0.35 * w:
        slope, intercept = np.polyfit(xs, ys, 1)
        yy, xx = np.mgrid[0:h, 0:w]
        band = np.abs(yy - (slope * xx + intercept)) <= max(5.0, h * 0.015)
        out[band & ~keep_blue] = 0.0
    if segments.any():
        seg_zone = ndimage.binary_dilation(segments, iterations=3)
        out[seg_zone & ~keep_blue] = 0.0
    return out


def _oriented_enhance(score, *, sig_along=8.0, sig_across=1.4, n_orient=12):
    """Усиление линий вдоль их направления: max по банку вытянутых гауссиан.

    Смыкает разрывы смазанного штриха (энергия копится вдоль линии),
    не усиливая изотропные кляксы и зерно бумаги. Затем вычитается
    локальный изотропный фон — иначе низкочастотный «подъём» вокруг
    тёмных пятен прячет соседние бледные штрихи под глобальным порогом.
    """
    import numpy as np
    from scipy import ndimage, signal

    r = int(2.5 * sig_along)
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    best = np.full(score.shape, -1e9, dtype=np.float32)
    for k in range(n_orient):
        th = np.pi * k / n_orient
        u = xx * np.cos(th) + yy * np.sin(th)
        v = -xx * np.sin(th) + yy * np.cos(th)
        g = np.exp(-(u**2 / (2 * sig_along**2) + v**2 / (2 * sig_across**2)))
        g /= g.sum()
        resp = signal.fftconvolve(score, g, mode="same").astype(np.float32)
        np.maximum(best, resp, out=best)
    return np.clip(best - 0.55 * ndimage.gaussian_filter(best, sigma=5.0), 0.0, None)


def _zhang_suen_thin(mask, max_iter=60):
    """Скелет Чжана–Суэна: связная осевая, петли сохраняются (нет в scipy)."""
    import numpy as np

    img = mask.astype(np.uint8)

    def neighbors(a):
        p2 = np.roll(a, -1, axis=0)
        p3 = np.roll(np.roll(a, -1, axis=0), 1, axis=1)
        p4 = np.roll(a, 1, axis=1)
        p5 = np.roll(np.roll(a, 1, axis=0), 1, axis=1)
        p6 = np.roll(a, 1, axis=0)
        p7 = np.roll(np.roll(a, 1, axis=0), -1, axis=1)
        p8 = np.roll(a, -1, axis=1)
        p9 = np.roll(np.roll(a, -1, axis=0), -1, axis=1)
        return p2, p3, p4, p5, p6, p7, p8, p9

    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            p2, p3, p4, p5, p6, p7, p8, p9 = neighbors(img)
            ring = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            a = np.zeros_like(img)
            for i in range(8):
                a += ((ring[i] == 0) & (ring[i + 1] == 1)).astype(np.uint8)
            if step == 0:
                cond = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            remove = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & cond
            if remove.any():
                img[remove] = 0
                changed = True
        if not changed:
            break
    return img.astype(bool)


def _soft_alpha(score):
    """Карта чернильности → альфа 0..1: тонкие сплошные штрихи.

    1. Ориентированное усиление смыкает разрывы смазанной линии.
    2. Гистерезис (как в Canny): бледный пиксель живёт, только если его
       связный регион содержит уверенно тёмный.
    3. Скелет Чжана–Суэна — связная осевая с петлями; «лента» ±2 px вокруг
       него ограничивает ширину штриха до пера, тело чернил внутри ленты
       даёт честную форму и нажим.

    None — если сигнала нет (пустое поле).
    """
    import numpy as np
    from scipy import ndimage

    enh = _oriented_enhance(score)
    flat = enh.ravel()
    noise = float(np.quantile(flat, 0.90))
    ink = float(np.quantile(flat, 0.997))
    span = ink - noise
    if span < 3.0:
        return None  # нет контраста — на кропе нечего извлекать

    weak = enh > noise + 0.05 * span
    strong = enh > noise + 0.30 * span
    labels, n = ndimage.label(weak)
    if n:
        has_strong = ndimage.maximum(
            strong.astype(np.uint8), labels, index=np.arange(1, n + 1)
        )
        hyst = np.isin(labels, np.flatnonzero(has_strong > 0) + 1)
    else:
        hyst = weak
    if not hyst.any():
        return None

    skeleton = _zhang_suen_thin(hyst)
    ribbon = ndimage.binary_dilation(skeleton, iterations=2)
    ribbon_soft = ndimage.gaussian_filter(ribbon.astype(np.float32), sigma=1.0)

    body = np.clip((enh - (noise + 0.05 * span)) / (0.42 * span), 0.0, 1.0) ** 0.65
    line = np.clip((enh - (noise + 0.05 * span)) / (0.38 * span), 0.0, 1.0) ** 0.7
    spine = np.where(skeleton, np.maximum(line, 0.5), 0.0)
    spine = ndimage.grey_dilation(spine, size=(3, 3))

    alpha01 = np.maximum(body * ribbon_soft, spine * 0.9)
    return np.clip(ndimage.gaussian_filter(alpha01, sigma=0.7) * 1.15, 0.0, 1.0)


def _filter_soft_components(alpha01, blueness, darkness):
    """Отбрасывает мусорные регионы целиком, не трогая полутона штрихов.

    Правила по компонентам поддержки (alpha > 0.30, слегка расширенной):

    * мелкие брызги — долой;
    * ахроматичные (чёрный печатный текст, тёмный штамп) — долой,
      кроме крупнейшего (подпись может быть и не синей);
    * широкий плоский регион в нижней части — сегмент линейки поля;
    * компактное пятно в углу с низкой синевой — штамп / номер страницы;
    * мелочь далеко от bbox основных штрихов — брызги и обрезки кромки.
    """
    import numpy as np
    from scipy import ndimage

    h, w = alpha01.shape
    support = alpha01 > 0.30
    labels, count = ndimage.label(ndimage.binary_dilation(support, iterations=2))
    if not count:
        return None
    idx = np.arange(1, count + 1)
    sizes = ndimage.sum_labels(support, labels, index=idx)
    mean_blue = ndimage.mean(blueness, labels, index=idx)
    mean_dark = ndimage.mean(darkness, labels, index=idx)
    biggest = float(sizes.max())
    boxes = ndimage.find_objects(labels)

    keep = np.zeros(count + 1, dtype=bool)
    for i in range(count):
        ratio = float(mean_blue[i]) / max(float(mean_dark[i]), 1.0)
        sl = boxes[i]
        bh = sl[0].stop - sl[0].start
        bw = sl[1].stop - sl[1].start
        cy = (sl[0].start + sl[0].stop) / 2.0
        cx = (sl[1].start + sl[1].stop) / 2.0
        in_corner = (cy < h * 0.22 or cy > h * 0.78) and (cx < w * 0.22 or cx > w * 0.78)
        if sizes[i] < max(40.0, biggest * 0.01):
            continue  # брызги
        # Порог низкий: усиление размазывает темноту и разбавляет хрому даже
        # у настоящей синей пасты (у печати/штампов ratio 0.00–0.09).
        if ratio < 0.10 and sizes[i] < biggest:
            continue  # печатный текст, чёрный штамп
        if bw >= 0.15 * w and bw / max(bh, 1) >= 3.5 and cy > h * 0.62 and ratio < 0.35:
            continue  # уцелевший сегмент линейки поля
        if in_corner and max(bw, bh) < 0.28 * min(h, w) and ratio < 0.28:
            continue  # штамп в углу
        keep[i + 1] = True

    # Близость: мелочь живёт только рядом с bbox основных штрихов.
    main_ids = [i for i in range(count) if keep[i + 1] and sizes[i] >= biggest * 0.2]
    if main_ids:
        top = min(boxes[i][0].start for i in main_ids)
        bottom = max(boxes[i][0].stop for i in main_ids)
        left = min(boxes[i][1].start for i in main_ids)
        right = max(boxes[i][1].stop for i in main_ids)
        pad_y, pad_x = int(h * 0.08), int(w * 0.08)
        for i in range(count):
            if not keep[i + 1] or sizes[i] >= biggest * 0.2:
                continue
            sl = boxes[i]
            if (
                sl[0].stop < top - pad_y
                or sl[0].start > bottom + pad_y
                or sl[1].stop < left - pad_x
                or sl[1].start > right + pad_x
            ):
                keep[i + 1] = False

    if not keep.any():
        return None
    zone = ndimage.binary_dilation(keep[labels], iterations=8)
    return np.where(zone, alpha01, 0.0)


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
