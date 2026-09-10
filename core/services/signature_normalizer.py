"""Нормализация фото подписи на белом фоне для документов автовоза.

Превращает снимок подписи (телефон / скан) в компактный PNG:
прозрачный фон, синие штрихи «как от шариковой ручки», обрезка полей,
разумный размер в пикселях.

Разрывы после порога восстанавливаем closing'ом и тонкими перемычками
между близкими обрывками — это их штрих, не новый автограф. Dilate
без erode не делаем: он рисует «маркер». Claude/полилинии не используем:
модель дорисовывает чужую геометрию.
"""

from __future__ import annotations

import io
import logging
import math

logger = logging.getLogger(__name__)

# Синий цвет шариковой ручки (RGB).
_INK_RGB = (25, 55, 160)
# Пиксели светлее порога — фон (прозрачный). Порог адаптивный, это нижняя граница.
_BG_LUMA_MIN = 200
# Перед порогом ужимаем огромные фото с телефона (ускорение).
_PROCESS_MAX_SIDE = 1600
# Макс. размер готовой подписи (длинная сторона).
_MAX_SIDE = 900
_MIN_SIDE = 120
# Ореол срезаем, но не настолько жёстко, чтобы съесть тонкие связки росчерка.
_HAZE_CUTOFF = 128
_INK_LUMA_GAP = 32
# Соединяем обрывки, если разрыв не больше этого (в пикселях после ужима).
_BRIDGE_MAX_GAP = 40
_BRIDGE_MIN_COMPONENT = 8


def normalize_signature_image(
    raw: bytes,
    *,
    ink_rgb: tuple[int, int, int] | None = None,
) -> bytes | None:
    """JPG/PNG → нормализованный PNG с прозрачным фоном; None если не картинка.

    ``ink_rgb`` — цвет штрихов (по умолчанию синий клиентских подписей).
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        logger.error("Pillow не установлен")
        return None

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = _to_rgb_on_white(img)
    except Exception as exc:
        logger.info("signature_normalizer: не удалось открыть изображение: %s", exc)
        return None

    # Сначала ужимаем исходник — порог по миллионам пикселей не нужен.
    if max(img.size) > _PROCESS_MAX_SIDE:
        img = img.copy()
        img.thumbnail((_PROCESS_MAX_SIDE, _PROCESS_MAX_SIDE), Image.LANCZOS)

    # Слабый blur сглаживает JPEG-шум, не раздувая штрих.
    gray = img.convert("L").filter(ImageFilter.GaussianBlur(radius=0.35))
    bg_luma = _estimate_bg_luma(gray)
    alpha = gray.point(lambda luma, t=bg_luma: _luma_to_alpha(luma, t))
    # Срезаем слабую альфу — иначе в PDF виден серый прямоугольник фона.
    cutoff = _adaptive_haze_cutoff(alpha)
    alpha = alpha.point(lambda a, c=cutoff: 0 if a < c else a)
    alpha = _reconnect_broken_strokes(alpha)

    ink = ink_rgb or _INK_RGB
    out = Image.new("RGBA", gray.size, (*ink, 0))
    out.putalpha(alpha)

    cropped = _crop_to_content(out)
    if cropped is None:
        return None
    resized = _fit_max_side(cropped, _MAX_SIDE)
    if min(resized.size) < 8:
        return None

    buf = io.BytesIO()
    resized.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _to_rgb_on_white(img):
    """RGBA/LA склеиваем на белый — повторная нормализация уже готовой PNG безопасна."""
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return img.convert("RGB")


def _estimate_bg_luma(gray) -> int:
    """Оценивает яркость бумаги по углам кадра (там обычно нет штрихов)."""
    w, h = gray.size
    cw, ch = max(1, w // 5), max(1, h // 5)
    samples = []
    for box in (
        (0, 0, cw, ch),
        (w - cw, 0, w, ch),
        (0, h - ch, cw, h),
        (w - cw, h - ch, w, h),
    ):
        crop = gray.crop(box)
        # Pillow 14 убирает getdata — предпочитаем get_flattened_data.
        data = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
        samples.extend(data)
    if not samples:
        return _BG_LUMA_MIN
    samples = sorted(samples)
    # 75-й перцентиль углов — типичная бумага; не ниже минимума.
    p75 = samples[int(len(samples) * 0.75)]
    return max(_BG_LUMA_MIN, min(248, int(p75) - 8))


def _luma_to_alpha(luma: int, bg_luma: int) -> int:
    # Бумага/тень чуть темнее углов не считается штрихом.
    effective_bg = bg_luma - _INK_LUMA_GAP
    if luma >= effective_bg:
        return 0
    # Чем темнее штрих — тем плотнее синий.
    span = max(40, effective_bg - 100)
    return min(255, int((effective_bg - luma) * (255 / span)))


def _adaptive_haze_cutoff(alpha) -> int:
    """Порог по гистограмме: отсекает «туман» бумаги, оставляет плотные штрихи."""
    data = alpha.get_flattened_data() if hasattr(alpha, "get_flattened_data") else alpha.getdata()
    strong = [a for a in data if a >= _HAZE_CUTOFF]
    if len(strong) < 80:
        # Мало кандидатов — не завышаем порог (тонкие штрихи на чистом скане).
        return _HAZE_CUTOFF
    strong.sort()
    # Нижняя граница плотных штрихов (10-й перцентиль сильных пикселей).
    p10 = strong[int(len(strong) * 0.10)]
    return max(_HAZE_CUTOFF, min(190, int(p10) - 15))


def _crop_to_content(img, pad_ratio: float = 0.08, alpha_min: int = 72):
    """Обрезает пустые поля вокруг штрихов; None если контента нет.

    В рамку идут только достаточно плотные пиксели (``alpha >= alpha_min``).
    Иначе полупрозрачный туман и пылинки раздувают картинку — в PDF подпись
    оказывается крошечной внутри большого прозрачного прямоугольника, и
    размер «скачет» от файла к файлу.
    """
    alpha = img.split()[-1]
    dense = alpha.point(lambda a, m=alpha_min: 255 if a >= m else 0)
    bbox = dense.getbbox() or alpha.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    w, h = img.size
    pad_x = max(4, int((right - left) * pad_ratio))
    pad_y = max(4, int((bottom - top) * pad_ratio))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(w, right + pad_x)
    bottom = min(h, bottom + pad_y)
    return img.crop((left, top, right, bottom))


def _reconnect_broken_strokes(alpha):
    """Смыть мелкие дыры и тонко соединить близкие обрывки одного штриха."""
    from PIL import ImageFilter

    bbox = alpha.getbbox()
    if bbox is None:
        return alpha
    piece = alpha.crop(bbox)
    closed = piece.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    closed = _bridge_nearby_fragments(closed)
    restored = alpha.copy()
    restored.paste(closed, (bbox[0], bbox[1]))
    return restored


def _bridge_nearby_fragments(alpha):
    """Краскал: перемычка 2 px только между ближайшими обрывками (разрыв ≤ 40 px)."""
    from PIL import ImageDraw

    components = _ink_components(alpha)
    if len(components) < 2:
        return alpha
    parent = list(range(len(components)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges = []
    samples = [_sample_points(pts) for pts in components]
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            dist, p1, p2 = _nearest_pair(samples[i], samples[j])
            if 2 < dist <= _BRIDGE_MAX_GAP:
                edges.append((dist, i, j, p1, p2))
    edges.sort(key=lambda item: item[0])
    draw = ImageDraw.Draw(alpha)
    for _dist, i, j, p1, p2 in edges:
        a, b = find(i), find(j)
        if a == b:
            continue
        parent[a] = b
        draw.line([p1, p2], fill=230, width=2)
    return alpha


def _ink_components(alpha, threshold: int = 80) -> list[list[tuple[int, int]]]:
    w, h = alpha.size
    pix = alpha.load()
    seen = bytearray(w * h)
    components = []
    for y in range(h):
        row = y * w
        for x in range(w):
            idx = row + x
            if seen[idx] or pix[x, y] < threshold:
                continue
            stack = [(x, y)]
            seen[idx] = 1
            pts = []
            while stack:
                cx, cy = stack.pop()
                pts.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if not seen[nidx] and pix[nx, ny] >= threshold:
                            seen[nidx] = 1
                            stack.append((nx, ny))
            if len(pts) >= _BRIDGE_MIN_COMPONENT:
                components.append(pts)
    return components


def _sample_points(pts: list[tuple[int, int]], limit: int = 80) -> list[tuple[int, int]]:
    if len(pts) <= limit:
        return pts
    step = max(1, len(pts) // limit)
    return pts[::step][:limit]


def _nearest_pair(
    a: list[tuple[int, int]], b: list[tuple[int, int]]
) -> tuple[float, tuple[int, int], tuple[int, int]]:
    best = (1e9, a[0], b[0])
    for x1, y1 in a:
        for x2, y2 in b:
            dist = math.hypot(x1 - x2, y1 - y2)
            if dist < best[0]:
                best = (dist, (x1, y1), (x2, y2))
    return best


def _fit_max_side(img, max_side: int):
    from PIL import Image

    w, h = img.size
    long_side = max(w, h)
    if _MIN_SIDE <= long_side <= max_side:
        return img
    scale = (max_side if long_side > max_side else _MIN_SIDE) / long_side
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)
