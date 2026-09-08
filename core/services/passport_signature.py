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
4. Очищенный фрагмент проходит общий :func:`normalize_signature_image` —
   штрихи становятся синими «как от ручки», фон прозрачным, поля обрезаются.
   Результат неотличим от вручную загруженной подписи.

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
- Если подписи на страницах нет — верни {"found": false}.

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
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
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
