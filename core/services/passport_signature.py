"""Авто-извлечение подписи владельца из скана паспорта (пакет автовоза).

Если клиент не загрузил фото подписи, при генерации документов система
вытягивает подпись прямо со скана/фото главной страницы паспорта:

1. Claude Vision находит рамку рукописного росчерка (не штамп и не
   печатную подпись поля). Координаты — доли страницы 0..1.
2. Фрагмент вырезается с запасом из рендера 300 dpi; мелкий кроп
   увеличивается, чтобы смазанные штрихи не схлопнулись в точку.
3. Фон паспорта убирается детерминированно, без перерисовки:
   * канал чернил — гибрид: синяя шариковая ручка по min(R,G), остальное
     по max RGB (цветная гильошная сетка уходит к фону);
   * выравнивание освещения; штампы и жирная печать (намного темнее
     бледной ручки) стираются, чтобы порог не «залипал» на номере страницы;
   * слабый росчерк растягивается по контрасту — это те же пиксели, не
     новая подпись.
4. Результат проходит :func:`normalize_signature_image` (синие штрихи,
   прозрачный фон).

Ошибки любого шага дают ``None`` — вызывающий код просит загрузить
подпись вручную. Модель не рисует росчерк заново по точкам: такой
контур не похож на подпись в паспорте.
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
  поля, штамп, номер страницы, печати должностных лиц.
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
    if cleaned is None:
        logger.info("passport_signature: очистка фрагмента не дала штрихов (%s)", path)
        return None

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
                "росчерка, без штампа и печатного текста поля."
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
    box = (
        max(0, int(left * w - margin_x)),
        max(0, int(top * h - margin_y)),
        min(w, int(right * w + margin_x)),
        min(h, int(bottom * h + margin_y)),
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
        if not keep[i] or lab == largest_label:
            continue
        ys, xs = np.nonzero(labels == lab)
        if xs.size == 0:
            continue
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
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
