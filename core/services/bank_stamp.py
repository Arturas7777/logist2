"""Печать банка + подпись операциониста для платёжного поручения.

Генерирует PNG с прозрачным фоном по образцу штампа ZAO «БТА Банк»:
дата платежа в центре, случайная подпись из 30 вариантов, лёгкая
«неидеальность» (текстура чернил, смещение подписи). Размещение
в поле формы (угол/позиция) — в :func:`compose_executor_stamp_field`.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ASSETS_DIR = Path(__file__).resolve().parent.parent / "pdf_assets"
SIGNATURES_DIR = ASSETS_DIR / "operator_signatures"
FONTS_DIR = ASSETS_DIR / "fonts"

# Цвет штемпельной краски: синий (не фиолетовый), достаточно яркий
# на белой бумаге.
_INK = (35, 75, 185)
# Синий шариковой ручки — как у клиентских подписей (signature_normalizer).
_SIG_INK = (25, 55, 160)
# Реальные подписи операционистов (после нормализации) — op_real_XX.png.
_REAL_PREFIX = "op_real_"

_MONTHS_RU_SHORT = (
    "ЯНВ",
    "ФЕВ",
    "МАР",
    "АПР",
    "МАЙ",
    "ИЮН",
    "ИЮЛ",
    "АВГ",
    "СЕН",
    "ОКТ",
    "НОЯ",
    "ДЕК",
)


def stamp_rng(payment_number: str, payment_date: datetime.date) -> random.Random:
    """Детерминированный RNG: одна платёжка — одна печать; разные — разные."""
    raw = f"{payment_number}|{payment_date.isoformat()}".encode()
    seed = int(hashlib.sha256(raw).hexdigest()[:16], 16)
    return random.Random(seed)


def format_stamp_date(d: datetime.date) -> str:
    return f"{d.day:02d} {_MONTHS_RU_SHORT[d.month - 1]} {d.year}"


def ensure_operator_signatures() -> list[Path]:
    """Список реальных подписей операционистов (прозрачный PNG).

    Предпочитает ``op_real_*.png``. Если их нет — генерирует запасные
    процедурные (для тестов/CI без ассетов).
    """
    SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)
    real = sorted(SIGNATURES_DIR.glob(f"{_REAL_PREFIX}*.png"))
    if real:
        return real
    # Fallback для окружений без загруженных фото.
    paths = []
    for i in range(8):
        path = SIGNATURES_DIR / f"op_sign_fallback_{i:02d}.png"
        if not path.exists():
            path.write_bytes(_generate_operator_signature_png(i))
        paths.append(path)
    return paths


def pick_operator_signature(rng: random.Random) -> bytes:
    paths = ensure_operator_signatures()
    path = paths[rng.randrange(len(paths))]
    return path.read_bytes()


def generate_bank_stamp_png(
    payment_date: datetime.date,
    *,
    rng: random.Random | None = None,
    clerk_left: int | None = None,
    clerk_right: int | None = None,
) -> bytes:
    """Прямоугольная печать банка с датой (без подписи операциониста)."""
    rng = rng or random.Random()
    left_n = clerk_left if clerk_left is not None else rng.randint(1, 9)
    right_n = clerk_right if clerk_right is not None else rng.randint(10, 28)
    date_text = format_stamp_date(payment_date)

    # Компактная вёрстка; scale=2 — меньше даунскейла (он мылит текст).
    # Bold — как у резинового штампа (штрих чуть плотнее обычного экранного шрифта).
    scale = 2
    font_sm = _font(11 * scale, bold=True)
    font_md = _font(12 * scale, bold=True)
    font_lg = _font(int(13 * scale * 1.2), bold=True)  # боковые числа +20%
    # Дата (+30% базово, ещё +20% к текущему размеру); ширина печати — по верхней надписи.
    font_date = _font(int(12 * scale * 1.3 * 1.2), bold=True)

    # Ширина: надпись + по 10 px с каждой стороны (дата ширину не раздувает).
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    top1 = "ЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО"
    content_w = int(probe.textlength(top1, font=font_sm))
    side_pad_title = 10 * scale  # по 10 px слева/справа от верхней надписи
    num_gap = 10 * scale  # зазор между боксом даты и числами по бокам
    num_slot = int(18 * scale * 1.2)  # место под цифры (+20% вместе с кеглем)
    inner_w = content_w + 2 * side_pad_title
    pad_x = 3 * scale
    # Зазор между двойной рамкой — с запасом под толстые линии оттиска.
    frame_gap = 4 * scale
    # Над верхней надписью — 4 px; под «г. Минск» — 4+4 px (печать выше).
    text_pad_top = 4 * scale
    text_pad_bottom = 8 * scale
    w = inner_w + 2 * pad_x + 2 * frame_gap
    # Бокс даты: отступ рамка→дата ×3 (ширину всей печати не трогаем).
    max_box_w = inner_w - 2 * (num_gap + num_slot)
    date_w = int(probe.textlength(date_text, font=font_date))
    # Внутренняя рамка даты +20% по отступам (ширину всей печати не раздуваем).
    box_w = min(date_w + int(30 * scale * 1.2), max_box_w)

    # Высота: над/под датой — 15+5 px; бокс даты выше (+20%).
    line1_h = int(font_sm.size * 0.95)
    line2_h = int(font_md.size * 0.95)
    gap_above_date = 20 * scale
    box_h = int(font_date.size * 1.45 * 1.2)
    gap_below_date = 20 * scale
    line_bic_h = int(font_md.size * 0.95)
    line_city_h = int(font_sm.size * 0.95)
    outer = 2 * scale
    h = (
        outer
        + frame_gap
        + text_pad_top
        + line1_h
        + line2_h
        + gap_above_date
        + box_h
        + gap_below_date
        + line_bic_h
        + line_city_h
        + text_pad_bottom
        + frame_gap
        + outer
    )

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ink = _INK + (255,)

    # Резиновый штамп: линии заметно толще «волоска» (иначе выглядит как вектор).
    line_w = max(3, scale + 1)  # scale=2 → 3 px на холсте
    line_inner = max(2, scale)
    draw.rectangle([outer, outer, w - outer - 1, h - outer - 1], outline=ink, width=line_w)
    inner = outer + frame_gap
    draw.rectangle([inner, inner, w - inner - 1, h - inner - 1], outline=ink, width=line_inner)

    cx = w // 2
    y = inner + text_pad_top
    for text, font, lh in ((top1, font_sm, line1_h), ("«БТА Банк»", font_md, line2_h)):
        tw = draw.textlength(text, font=font)
        draw.text((round(cx - tw / 2), y), text, font=font, fill=ink)
        y += lh

    y += gap_above_date
    # Бокс и дата — строго по центру печати (числа по бокам на центр не влияют).
    box_x0 = (w - box_w) // 2
    box_y0 = y
    box_x1, box_y1 = box_x0 + box_w, box_y0 + box_h
    draw.rectangle([box_x0, box_y0, box_x1, box_y1], outline=ink, width=line_w)  # бокс даты — той же толщины
    dtw = draw.textlength(date_text, font=font_date)
    date_x = round(cx - dtw / 2)
    date_y = round(box_y0 + (box_h - font_date.size) / 2 - scale)
    draw.text((date_x, date_y), date_text, font=font_date, fill=ink)

    side_y = box_y0 + (box_h - font_lg.size) / 2 - scale
    left_tw = draw.textlength(str(left_n), font=font_lg)
    # Числа с нормальным зазором от внутренней рамки даты (не вплотную).
    draw.text((box_x0 - num_gap - left_tw, side_y), str(left_n), font=font_lg, fill=ink)
    draw.text((box_x1 + num_gap, side_y), str(right_n), font=font_lg, fill=ink)

    y = box_y1 + gap_below_date
    for text, font, lh in (("БИК AEBKBY2X", font_md, line_bic_h), ("г. Минск", font_sm, line_city_h)):
        tw = draw.textlength(text, font=font)
        draw.text((round(cx - tw / 2), y), text, font=font, fill=ink)
        y += lh

    # Без «песка» текстуры — она дробит штрихи и текст становится нечитаемым.
    # «Живость» дают угол/сдвиг при постановке в поле.
    out_w = min(320, w)
    if out_w < w:
        out_h = max(1, int(out_w * h / w))
        img = img.resize((out_w, out_h), Image.LANCZOS)
    out = _apply_uneven_impression(_recolor_ink(img), rng)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _prepare_operator_signature(rng: random.Random, stamp_w: int) -> Image.Image:
    """Синяя подпись (как у клиента), размер относительно ширины печати."""
    sig = Image.open(io.BytesIO(pick_operator_signature(rng))).convert("RGBA")
    sig = _recolor_ink(sig, ink=_SIG_INK, solid=90)
    sig_w = int(stamp_w * rng.uniform(0.50, 0.64))
    sig_h = int(sig_w * sig.height / max(1, sig.width))
    # Портретные фото не должны тянуться на всю высоту печати.
    max_h = int(stamp_w * 0.36)
    if sig_h > max_h:
        scale = max_h / sig_h
        sig_w = max(1, int(sig_w * scale))
        sig_h = max(1, int(sig_h * scale))
    sig = sig.resize((max(1, sig_w), max(1, sig_h)), Image.LANCZOS)
    sig = sig.rotate(rng.uniform(-10, 8), expand=True, resample=Image.BICUBIC)
    return sig


def _overlay_signature_on_stamp(
    stamp: Image.Image,
    sig: Image.Image,
    rng: random.Random,
) -> Image.Image:
    """Кладёт подпись поверх печати: сильно накрывает правый нижний угол.

    Холст расширяется так, чтобы росчерк не обрезался по краю.
    """
    # Чем больше коэффициент — тем сильнее подпись заходит на печать.
    sx = stamp.width - int(sig.width * rng.uniform(0.82, 1.02))
    sy = stamp.height - int(sig.height * rng.uniform(0.78, 0.98))
    pad_l = max(0, -sx)
    pad_t = max(0, -sy)
    pad_r = max(0, sx + sig.width - stamp.width) + 4
    pad_b = max(0, sy + sig.height - stamp.height) + 4
    sx += pad_l
    sy += pad_t
    canvas = Image.new(
        "RGBA",
        (stamp.width + pad_l + pad_r, stamp.height + pad_t + pad_b),
        (0, 0, 0, 0),
    )
    canvas.paste(stamp, (pad_l, pad_t), stamp)
    canvas.alpha_composite(sig, (sx, sy))
    return _crop_alpha(canvas, pad=2)


def compose_stamp_with_signature(
    payment_date: datetime.date,
    *,
    rng: random.Random | None = None,
) -> bytes:
    """Печать + синяя подпись операциониста (перекрывает правый нижний угол)."""
    rng = rng or random.Random()
    stamp = Image.open(io.BytesIO(generate_bank_stamp_png(payment_date, rng=rng))).convert("RGBA")
    stamp = _recolor_ink(stamp)
    sig = _prepare_operator_signature(rng, stamp.width)
    composed = _overlay_signature_on_stamp(stamp, sig, rng)
    buf = io.BytesIO()
    composed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def compose_executor_stamp_field(
    payment_date: datetime.date,
    *,
    field_width_pt: float,
    field_height_pt: float,
    payment_number: str = "",
    rng: random.Random | None = None,
    dpi: int = 240,
) -> bytes:
    """Печать+подпись, случайно повёрнутая и сдвинутая внутри поля формы.

    Печать — штемпельный цвет; подпись — синяя как у клиента. Сначала
    композит, потом общий поворот (без перекраски подписи).
    """
    rng = rng or stamp_rng(payment_number, payment_date)
    stamp = Image.open(io.BytesIO(generate_bank_stamp_png(payment_date, rng=rng))).convert("RGBA")

    field_w = max(80, int(field_width_pt * dpi / 72))
    field_h = max(80, int(field_height_pt * dpi / 72))
    field = Image.new("RGBA", (field_w, field_h), (0, 0, 0, 0))
    margin = 6

    # Печать компактнее — запас по высоте после поворота и вылета подписи.
    target_w = int(field_w * rng.uniform(0.38, 0.46))
    target_h = int(target_w * stamp.height / max(1, stamp.width))
    max_stamp_h = int(field_h * 0.55)
    if target_h > max_stamp_h:
        target_h = max_stamp_h
        target_w = int(target_h * stamp.width / max(1, stamp.height))

    stamp_s = _recolor_ink(stamp.resize((target_w, target_h), Image.LANCZOS))
    stamp_s = _apply_uneven_impression(stamp_s, rng)
    sig = _prepare_operator_signature(rng, target_w)
    composed = _overlay_signature_on_stamp(stamp_s, sig, rng)

    # Общий поворот в 3× → даунскейл (цвета печати и подписи сохраняются).
    angle = rng.uniform(-12, 9)
    hi = composed.resize((composed.width * 3, composed.height * 3), Image.LANCZOS)
    hi = hi.rotate(angle, expand=True, resample=Image.BICUBIC)
    rotated = hi.resize((max(1, hi.width // 3), max(1, hi.height // 3)), Image.LANCZOS)

    # После expand гарантированно вписываем в поле — без обрезания снизу.
    max_w = field_w - 2 * margin
    max_h = field_h - 2 * margin
    if rotated.width > max_w or rotated.height > max_h:
        scale = min(max_w / rotated.width, max_h / rotated.height)
        rotated = rotated.resize(
            (max(1, int(rotated.width * scale)), max(1, int(rotated.height * scale))),
            Image.LANCZOS,
        )

    max_x = max(margin, field_w - rotated.width - margin)
    max_y = max(margin, field_h - rotated.height - margin)
    x = rng.randint(margin, max_x) if max_x > margin else margin
    y = rng.randint(margin, max_y) if max_y > margin else margin
    field.alpha_composite(rotated, (x, y))

    buf = io.BytesIO()
    field.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Подписи операционистов (30 вариантов)
# ---------------------------------------------------------------------------


def _generate_operator_signature_png(index: int) -> bytes:
    """Процедурная «рукописная» подпись; index 0..29 задаёт характер штрихов."""
    rng = random.Random(10_000 + index)
    w, h = 480, 200
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    base_y = h * rng.uniform(0.42, 0.58)
    start_x = 25.0
    # Основной росчерк — цепочка кубических Безье (плавный «почерк»).
    cursor = (start_x, base_y + rng.uniform(-8, 8))
    segments = 4 + index % 4
    width = 3 + (index % 3)
    for s in range(segments):
        dx = rng.uniform(55, 85)
        # Высота волн / петель зависит от index — разные характеры подписей.
        up = rng.uniform(25, 55) * (1.0 if (index + s) % 2 == 0 else 0.6)
        down = rng.uniform(10, 35)
        p1 = (cursor[0] + dx * 0.35, cursor[1] - up)
        p2 = (cursor[0] + dx * 0.7, cursor[1] + down)
        end = (cursor[0] + dx, base_y + rng.uniform(-12, 12) + math.sin(index + s) * 6)
        pts = _cubic_bezier(cursor, p1, p2, end, steps=28)
        draw.line(pts, fill=_INK + (250,), width=width, joint="curve")
        cursor = end

    # Завиток в конце (как росчерк фамилии).
    if index % 5 != 4:
        cx, cy = cursor
        flourish = _cubic_bezier(
            (cx, cy),
            (cx + rng.uniform(20, 40), cy - rng.uniform(30, 55)),
            (cx + rng.uniform(45, 70), cy + rng.uniform(-10, 20)),
            (cx + rng.uniform(15, 40), cy + rng.uniform(15, 40)),
            steps=24,
        )
        draw.line(flourish, fill=_INK + (240,), width=max(2, width - 1), joint="curve")

    # Короткий начальный штрих / точка у части подписей.
    if index % 2 == 0:
        draw.ellipse(
            [start_x - 3, base_y - 18, start_x + 5, base_y - 10],
            fill=_INK + (230,),
        )

    # Подчёркивание.
    if index % 3 != 0:
        y_line = max(cursor[1], base_y) + rng.uniform(18, 32)
        underline = _cubic_bezier(
            (30, y_line),
            (w * 0.35, y_line + rng.uniform(-4, 4)),
            (w * 0.65, y_line + rng.uniform(-4, 4)),
            (min(w - 30, cursor[0] + 20), y_line + rng.uniform(-3, 5)),
            steps=16,
        )
        draw.line(underline, fill=_INK + (200,), width=2, joint="curve")

    img = _recolor_ink(img, solid=100)
    cropped = _crop_alpha(img, pad=6)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _cubic_bezier(p0, p1, p2, p3, steps: int = 20) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = FONTS_DIR / name
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError:
        return ImageFont.load_default()


def _recolor_ink(img: Image.Image, ink: tuple[int, int, int] = _INK, solid: int = 110) -> Image.Image:
    """Чистый цвет чернил, жёсткий порог — без мыльной полупрозрачной каймы."""
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size
    r0, g0, b0 = ink
    for y in range(h):
        for x in range(w):
            _r, _g, _b, a = pixels[x, y]
            if a >= solid:
                pixels[x, y] = (r0, g0, b0, 255)
            elif a:
                pixels[x, y] = (0, 0, 0, 0)
    return img


def _apply_uneven_impression(img: Image.Image, rng: random.Random) -> Image.Image:
    """Лёгкие огрехи оттиска: где прилегание хуже, краска бледнее или пропадает.

    Крупные мягкие пятна (не пиксельный шум) — текст остаётся читаемым.
    """
    img = img.convert("RGBA")
    w, h = img.size
    if w < 8 or h < 8:
        return img

    # Маска силы оттиска: 255 = полный контакт, ниже = слабее.
    contact = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(contact)

    # 2–4 зоны слабого прижатия.
    for _ in range(rng.randint(2, 4)):
        cx = rng.randint(int(w * 0.05), int(w * 0.95))
        cy = rng.randint(int(h * 0.05), int(h * 0.95))
        rw = rng.randint(max(8, w // 10), max(12, w // 3))
        rh = rng.randint(max(6, h // 10), max(10, h // 3))
        # Насколько «не дожали» (чем меньше fill — тем бледнее пятно).
        strength = rng.randint(90, 170)
        draw.ellipse([cx - rw, cy - rh, cx + rw, cy + rh], fill=strength)

    # Иногда чуть «съедает» край рамки — как при перекосе подушки штампа.
    if rng.random() < 0.75:
        side = rng.choice(("top", "bottom", "left", "right"))
        band = rng.randint(max(3, min(w, h) // 18), max(5, min(w, h) // 10))
        fade = rng.randint(110, 175)
        if side == "top":
            draw.rectangle([0, 0, w, band], fill=fade)
        elif side == "bottom":
            draw.rectangle([0, h - band, w, h], fill=fade)
        elif side == "left":
            draw.rectangle([0, 0, band, h], fill=fade)
        else:
            draw.rectangle([w - band, 0, w, h], fill=fade)

    # Крупное низкочастотное варьирование (не песок).
    nw, nh = max(4, w // 12), max(4, h // 12)
    grain = Image.new("L", (nw, nh))
    gp = grain.load()
    for gy in range(nh):
        for gx in range(nw):
            gp[gx, gy] = rng.randint(200, 255)
    grain = grain.resize((w, h), Image.BILINEAR)
    contact = ImageChops.multiply(contact, grain)
    contact = contact.filter(ImageFilter.GaussianBlur(radius=max(3.0, min(w, h) / 18)))

    base = img.copy()
    pixels = base.load()
    m = contact.load()
    r0, g0, b0 = _INK
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            factor = m[x, y] / 255.0
            # Слабый контакт = бледнее тот же цвет (не уводим в тёмно-серый).
            na = int(a * (0.55 + 0.45 * factor))
            if na < 55:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (r0, g0, b0, min(255, na))
    return base


def _apply_ink_texture(
    img: Image.Image,
    rng: random.Random,
    strength: float = 0.18,
    min_alpha: int = 0,
    blur: float = 0.25,
) -> Image.Image:
    """Неравномерная прозрачность — как от резинового штампа / ручки."""
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size
    # Крупная сетка шума — быстрее полного per-pixel Random.
    cell = 4
    noise = {}
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            noise[(x, y)] = rng.uniform(1.0 - strength, 1.0)
    r0, g0, b0 = _INK
    for y in range(h):
        for x in range(w):
            _r, _g, _b, a = pixels[x, y]
            if a == 0:
                continue
            factor = noise[(x - x % cell, y - y % cell)]
            # Чуть «рваные» края: у слабой альфы выше шанс исчезнуть.
            if a < 100 and rng.random() < 0.08:
                pixels[x, y] = (r0, g0, b0, 0)
            else:
                na = max(min_alpha, min(255, int(a * factor))) if a >= 100 else max(0, min(255, int(a * factor)))
                # RGB всегда чистый синий — не серый от полупрозрачного антиалиаса.
                pixels[x, y] = (r0, g0, b0, na)
    if blur and blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))
        img = _recolor_ink(img)
    return img


def _crop_alpha(img: Image.Image, pad: int = 2) -> Image.Image:
    bbox = img.split()[-1].getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.width, right + pad)
    bottom = min(img.height, bottom + pad)
    return img.crop((left, top, right, bottom))
