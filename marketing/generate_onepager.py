"""Одностраничное коммерческое предложение склада Caromoto Lithuania (A4, PDF).

Вся информация — на одном листе: заголовок с фото порта, три ключевые цифры,
восемь преимуществ с иконками, таймлайн процесса по дням, фотоблок, цена
с составом услуг и контакты.

Тексты собраны в константах ниже — их можно править, не трогая вёрстку.

Запуск:
    python marketing\\generate_onepager.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
OUT_PATH = BASE_DIR / "Caromoto_Lithuania_sklad_1_list.pdf"

W, H = A4
MARGIN = 13 * mm

# ---------------------------------------------------------------- фирменный стиль

INK = HexColor("#0E1116")
INK_SOFT = HexColor("#232A33")
BODY = HexColor("#454E59")
MUTED = HexColor("#8C949E")
HAIRLINE = HexColor("#E3E7EC")
CANVAS_BG = HexColor("#FFFFFF")
SOFT_BG = HexColor("#F5F6F8")
GREEN = HexColor("#009E0F")
YELLOW = HexColor("#FCCC00")
RED = HexColor("#E30613")
UA_BLUE = HexColor("#0057B7")
UA_YELLOW = HexColor("#FFD700")

CONTACT_SITE = "caromoto-lt.com"


def _register_fonts() -> dict[str, str]:
    """Montserrat (шрифт сайта), с откатом на Arial и встроенный Helvetica."""
    wanted = {
        "light": "Montserrat-Light.ttf",
        "regular": "Montserrat-Regular.ttf",
        "medium": "Montserrat-Medium.ttf",
        "semibold": "Montserrat-SemiBold.ttf",
        "bold": "Montserrat-Bold.ttf",
        "xbold": "Montserrat-ExtraBold.ttf",
    }
    found: dict[str, str] = {}
    for key, filename in wanted.items():
        path = FONTS_DIR / filename
        if not path.exists():
            continue
        name = f"Mont-{key}"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
        found[key] = name
    if len(found) == len(wanted):
        return found

    arial = Path(r"C:\Windows\Fonts\arial.ttf")
    arial_bold = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if arial.exists() and arial_bold.exists():
        for name, path in (("Fallback", arial), ("Fallback-Bold", arial_bold)):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
        return {
            "light": "Fallback", "regular": "Fallback", "medium": "Fallback",
            "semibold": "Fallback-Bold", "bold": "Fallback-Bold", "xbold": "Fallback-Bold",
        }
    return {
        "light": "Helvetica", "regular": "Helvetica", "medium": "Helvetica",
        "semibold": "Helvetica-Bold", "bold": "Helvetica-Bold", "xbold": "Helvetica-Bold",
    }


FONTS = _register_fonts()
F_LIGHT = FONTS["light"]
F_REG = FONTS["regular"]
F_MED = FONTS["medium"]
F_SEMI = FONTS["semibold"]
F_BOLD = FONTS["bold"]
F_XBOLD = FONTS["xbold"]


# ---------------------------------------------------------------- примитивы

def rrect_path(c: canvas.Canvas, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    p = c.beginPath()
    p.moveTo(x + r, y)
    p.lineTo(x + w - r, y)
    p.arcTo(x + w - 2 * r, y, x + w, y + 2 * r, startAng=-90, extent=90)
    p.lineTo(x + w, y + h - r)
    p.arcTo(x + w - 2 * r, y + h - 2 * r, x + w, y + h, startAng=0, extent=90)
    p.lineTo(x + r, y + h)
    p.arcTo(x, y + h - 2 * r, x + 2 * r, y + h, startAng=90, extent=90)
    p.lineTo(x, y + r)
    p.arcTo(x, y, x + 2 * r, y + 2 * r, startAng=180, extent=90)
    p.close()
    return p


def str_w(s, font, size, tracking=0.0):
    return pdfmetrics.stringWidth(s, font, size) + tracking * max(len(s) - 1, 0)


def text(c, x, y, s, font, size, color, align="l", tracking=0.0):
    c.saveState()
    c.setFillColor(color)
    if tracking:
        width = str_w(s, font, size, tracking)
        if align == "c":
            x -= width / 2
        elif align == "r":
            x -= width
        obj = c.beginText(x, y)
        obj.setFont(font, size)
        obj.setCharSpace(tracking)
        obj.textOut(s)
        c.drawText(obj)
    else:
        c.setFont(font, size)
        if align == "l":
            c.drawString(x, y, s)
        elif align == "c":
            c.drawCentredString(x, y, s)
        else:
            c.drawRightString(x, y, s)
    c.restoreState()


def wrap(s, font, size, max_w):
    words, lines, cur = s.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def para(c, x, y, s, font, size, color, max_w, leading=None, align="l"):
    """Рисует абзац сверху вниз. Возвращает y последней базовой линии."""
    leading = leading or size * 1.5
    last = y
    for i, line in enumerate(wrap(s, font, size, max_w)):
        last = y - i * leading
        text(c, x, last, line, font, size, color, align=align)
    return last


def shade(c, x, y, w, h, color=black, a_start=0.9, a_end=0.0, steps=90, horizontal=False):
    """Линейный градиент прозрачности: a_start у левого/нижнего края."""
    c.saveState()
    c.setFillColor(color)
    span = w if horizontal else h
    step = span / steps
    for i in range(steps):
        t = i / (steps - 1)
        c.setFillAlpha(a_start + (a_end - a_start) * t)
        if horizontal:
            c.rect(x + i * step, y, step + 0.4, h, stroke=0, fill=1)
        else:
            c.rect(x, y + i * step, w, step + 0.4, stroke=0, fill=1)
    c.restoreState()


def image_cover(c, filename, x, y, w, h, radius=0, shift_y=0.0):
    """Вписывает картинку в область по принципу CSS background-size: cover.

    Отрицательный shift_y сдвигает кадр к верхней части снимка.
    """
    img = ImageReader(str(ASSETS_DIR / filename))
    iw, ih = img.getSize()
    scale = max(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    c.saveState()
    c.clipPath(rrect_path(c, x, y, w, h, radius or 0.01), stroke=0, fill=0)
    c.drawImage(img, x - (dw - w) / 2, y - (dh - h) / 2 + shift_y, dw, dh, mask="auto")
    c.restoreState()


def card(c, x, y, w, h, radius=3 * mm, fill=white, border=HAIRLINE, shadow=True):
    if shadow:
        c.saveState()
        c.setFillColor(black)
        for i in range(4):
            c.setFillAlpha(0.030 - i * 0.006)
            c.drawPath(rrect_path(c, x - i * 0.4, y - 0.9 * mm - i * 0.4, w + 0.8 * i, h + 0.8 * i, radius),
                       stroke=0, fill=1)
        c.restoreState()
    c.saveState()
    c.setFillColor(fill)
    if border:
        c.setStrokeColor(border)
        c.setLineWidth(0.6)
    c.drawPath(rrect_path(c, x, y, w, h, radius), stroke=1 if border else 0, fill=1)
    c.restoreState()


def accent_dots(c, x, y, size=2.2 * mm, gap=1.4 * mm):
    for i, color in enumerate((GREEN, YELLOW, RED)):
        c.setFillColor(color)
        c.rect(x + i * (size + gap), y, size, size, stroke=0, fill=1)


def logo(c, x, y, size, on_dark=False):
    """Надпись CAROMOTO с брендовыми «O» и подписью LITHUANIA."""
    base = white if on_dark else INK
    letters = [
        ("C", base), ("A", base), ("R", base), ("O", GREEN),
        ("M", base), ("O", YELLOW), ("T", base), ("O", RED),
    ]
    tracking = size * 0.02
    cx = x
    for ch, color in letters:
        text(c, cx, y, ch, F_XBOLD, size, color)
        cx += pdfmetrics.stringWidth(ch, F_XBOLD, size) + tracking
    sub_size = size * 0.30
    text(
        c, x + 0.3, y - size * 0.44, "LITHUANIA", F_MED, sub_size,
        Color(1, 1, 1, 0.62) if on_dark else MUTED, tracking=sub_size * 0.52,
    )


# ---------------------------------------------------------------- иконки

def _icon_setup(c, color, s, weight=0.085):
    c.setStrokeColor(color)
    c.setLineWidth(s * weight)
    c.setLineCap(1)
    c.setLineJoin(1)


def icon_bell(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    p = c.beginPath()
    p.moveTo(cx - 0.44 * s, cy - 0.22 * s)
    p.lineTo(cx + 0.44 * s, cy - 0.22 * s)
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx - 0.32 * s, cy - 0.22 * s)
    p.lineTo(cx - 0.32 * s, cy + 0.02 * s)
    p.arcTo(cx - 0.32 * s, cy - 0.30 * s, cx + 0.32 * s, cy + 0.34 * s, startAng=180, extent=-180)
    p.lineTo(cx + 0.32 * s, cy - 0.22 * s)
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx, cy + 0.34 * s)
    p.lineTo(cx, cy + 0.44 * s)
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx - 0.12 * s, cy - 0.34 * s)
    p.arcTo(cx - 0.12 * s, cy - 0.46 * s, cx + 0.12 * s, cy - 0.22 * s, startAng=180, extent=180)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_truck(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    c.drawPath(rrect_path(c, cx - 0.46 * s, cy - 0.16 * s, 0.52 * s, 0.38 * s, 0.05 * s), stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx + 0.06 * s, cy - 0.16 * s)
    p.lineTo(cx + 0.06 * s, cy + 0.10 * s)
    p.lineTo(cx + 0.24 * s, cy + 0.10 * s)
    p.lineTo(cx + 0.44 * s, cy - 0.02 * s)
    p.lineTo(cx + 0.44 * s, cy - 0.16 * s)
    c.drawPath(p, stroke=1, fill=0)
    for dx in (-0.26, 0.26):
        c.circle(cx + dx * s, cy - 0.26 * s, 0.10 * s, stroke=1, fill=0)
    c.restoreState()


def icon_calendar(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    c.drawPath(rrect_path(c, cx - 0.40 * s, cy - 0.42 * s, 0.80 * s, 0.74 * s, 0.07 * s), stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx - 0.40 * s, cy + 0.14 * s)
    p.lineTo(cx + 0.40 * s, cy + 0.14 * s)
    c.drawPath(p, stroke=1, fill=0)
    for dx in (-0.20, 0.20):
        p = c.beginPath()
        p.moveTo(cx + dx * s, cy + 0.32 * s)
        p.lineTo(cx + dx * s, cy + 0.46 * s)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_camera(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    c.drawPath(rrect_path(c, cx - 0.46 * s, cy - 0.30 * s, 0.92 * s, 0.54 * s, 0.08 * s), stroke=1, fill=0)
    c.drawPath(rrect_path(c, cx - 0.14 * s, cy + 0.24 * s, 0.28 * s, 0.10 * s, 0.03 * s), stroke=1, fill=0)
    c.circle(cx, cy - 0.03 * s, 0.16 * s, stroke=1, fill=0)
    c.setFillColor(color)
    c.circle(cx + 0.31 * s, cy + 0.14 * s, 0.035 * s, stroke=0, fill=1)
    c.restoreState()


def icon_monitor(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    c.drawPath(rrect_path(c, cx - 0.46 * s, cy - 0.14 * s, 0.92 * s, 0.60 * s, 0.06 * s), stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx, cy - 0.14 * s)
    p.lineTo(cx, cy - 0.32 * s)
    p.moveTo(cx - 0.18 * s, cy - 0.34 * s)
    p.lineTo(cx + 0.18 * s, cy - 0.34 * s)
    c.drawPath(p, stroke=1, fill=0)
    c.setLineWidth(s * 0.06)
    for i, dx in enumerate((0.30, 0.20, 0.36)):
        p = c.beginPath()
        p.moveTo(cx - 0.32 * s, cy + (0.28 - i * 0.11) * s)
        p.lineTo(cx - 0.32 * s + dx * s, cy + (0.28 - i * 0.11) * s)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_headset(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    p = c.beginPath()
    p.moveTo(cx - 0.34 * s, cy - 0.02 * s)
    p.arcTo(cx - 0.34 * s, cy - 0.36 * s, cx + 0.34 * s, cy + 0.32 * s, startAng=180, extent=-180)
    c.drawPath(p, stroke=1, fill=0)
    for dx in (-0.34, 0.34):
        c.drawPath(rrect_path(c, cx + dx * s - 0.08 * s, cy - 0.32 * s, 0.16 * s, 0.32 * s, 0.07 * s),
                   stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx + 0.34 * s, cy - 0.32 * s)
    p.lineTo(cx + 0.34 * s, cy - 0.42 * s)
    p.lineTo(cx + 0.06 * s, cy - 0.42 * s)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_doc(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    p = c.beginPath()
    p.moveTo(cx - 0.32 * s, cy - 0.44 * s)
    p.lineTo(cx - 0.32 * s, cy + 0.44 * s)
    p.lineTo(cx + 0.10 * s, cy + 0.44 * s)
    p.lineTo(cx + 0.32 * s, cy + 0.22 * s)
    p.lineTo(cx + 0.32 * s, cy - 0.44 * s)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx + 0.10 * s, cy + 0.44 * s)
    p.lineTo(cx + 0.10 * s, cy + 0.22 * s)
    p.lineTo(cx + 0.32 * s, cy + 0.22 * s)
    c.drawPath(p, stroke=1, fill=0)
    c.setLineWidth(s * 0.06)
    for i in range(3):
        p = c.beginPath()
        yy = cy + (0.04 - i * 0.16) * s
        p.moveTo(cx - 0.18 * s, yy)
        p.lineTo(cx + (0.18 if i < 2 else 0.02) * s, yy)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_euro(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s, weight=0.075)
    c.circle(cx, cy, 0.44 * s, stroke=1, fill=0)
    text(c, cx, cy - 0.20 * s, "€", F_BOLD, s * 0.62, color, align="c")
    c.restoreState()


def icon_clock(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s, weight=0.075)
    c.circle(cx, cy, 0.44 * s, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx, cy + 0.24 * s)
    p.lineTo(cx, cy)
    p.lineTo(cx + 0.18 * s, cy - 0.10 * s)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def icon_shield(c, cx, cy, s, color):
    c.saveState()
    _icon_setup(c, color, s)
    p = c.beginPath()
    p.moveTo(cx, cy + 0.44 * s)
    p.lineTo(cx + 0.34 * s, cy + 0.24 * s)
    p.lineTo(cx + 0.34 * s, cy - 0.10 * s)
    p.curveTo(cx + 0.34 * s, cy - 0.30 * s, cx + 0.16 * s, cy - 0.40 * s, cx, cy - 0.46 * s)
    p.curveTo(cx - 0.16 * s, cy - 0.40 * s, cx - 0.34 * s, cy - 0.30 * s, cx - 0.34 * s, cy - 0.10 * s)
    p.lineTo(cx - 0.34 * s, cy + 0.24 * s)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(cx - 0.14 * s, cy + 0.02 * s)
    p.lineTo(cx - 0.02 * s, cy - 0.12 * s)
    p.lineTo(cx + 0.17 * s, cy + 0.16 * s)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def flag_ua(c, x, y, w):
    """Флаг Украины: пропорции 2:3, (x, y) — левый нижний угол."""
    h = w * 2 / 3
    c.saveState()
    c.clipPath(rrect_path(c, x, y, w, h, 0.4 * mm), stroke=0, fill=0)
    c.setFillColor(UA_BLUE)
    c.rect(x, y + h / 2, w, h / 2, stroke=0, fill=1)
    c.setFillColor(UA_YELLOW)
    c.rect(x, y, w, h / 2, stroke=0, fill=1)
    c.restoreState()
    c.saveState()
    c.setStrokeColor(HAIRLINE)
    c.setLineWidth(0.4)
    c.drawPath(rrect_path(c, x, y, w, h, 0.4 * mm), stroke=1, fill=0)
    c.restoreState()


def tick(c, cx, cy, r, color=GREEN, on_dark=False):
    c.saveState()
    c.setFillColor(color)
    c.circle(cx, cy, r, stroke=0, fill=1)
    c.setStrokeColor(INK if on_dark else white)
    c.setLineWidth(r * 0.30)
    c.setLineCap(1)
    p = c.beginPath()
    p.moveTo(cx - 0.44 * r, cy + 0.04 * r)
    p.lineTo(cx - 0.10 * r, cy - 0.32 * r)
    p.lineTo(cx + 0.48 * r, cy + 0.34 * r)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


# ---------------------------------------------------------------- контент

PRICE_UA, PRICE_OTHER = "240", "260"
STORAGE_RATE = "5 € в сутки"

HEADLINE = "Разгрузка автомобилей из США на складе в Клайпеде (Литва)"
SUBLINE = (
    "Забираем контейнер из порта за 1–2 дня, выгружаем с полной фото- и "
    "видеофиксацией, 7 дней бесплатно храним авто на складе. Дату выгрузки сообщаем "
    f"заранее, документы для декларации — в личном кабинете. От {PRICE_UA} € за автомобиль."
)

# (приставка, число, единица, подпись, мелкая строка, цвет, иконка)
STATS = [
    ("", "1–2", "дня", "Забор из порта", "сразу после прибытия", GREEN, icon_truck),
    ("", "7", "дней", "Бесплатно храним авто", "далее — 5 €/сутки", YELLOW, icon_calendar),
]
# короткая версия — в узкой карточке наверху, полная — в сносках тарифного блока
MAERSK_NOTE_SHORT = "* Для автомобилей, доставленных MAERSK, действует отдельный тариф."
MAERSK_NOTE = ("* Для автомобилей, доставленных MAERSK, действует отдельный тариф — "
               "уточняйте у менеджера.")

ADVANTAGES = [
    (icon_bell, "Вы узнаёте о выгрузке первым",
     "Плановая и фактическая дата выгрузки — в Telegram и на e-mail, без звонков."),
    (icon_truck, "Забор из порта за 1–2 дня",
     "Забираем сразу после прибытия: без демереджа и простоя в порту."),
    (icon_calendar, "7 дней бесплатного хранения авто",
     "Неделя стоянки на складе без оплаты — спокойно оформляете документы и находите перевозчика."),
    (icon_camera, "Фото и видео всей выгрузки",
     "Состояние авто зафиксировано на момент выхода из контейнера — споров нет."),
    (icon_monitor, "Личный кабинет 24/7",
     "Ближайшие прибытия, статусы, фотоотчёты и заявка на вывоз авто — онлайн."),
    (icon_doc, "Документы для декларации",
     "Пакет генерируется прямо в личном кабинете — поможем с подготовкой всех необходимых бумаг."),
    (icon_clock, "Быстрое оформление деклараций",
     "Транзитные и экспортные декларации делаем быстро — авто не ждёт бумаг."),
    (icon_headset, "Персональный менеджер",
     "Поможем решить любые вопросы: ключи, тайтлы, повреждения при перевозке."),
]

TIMELINE = [
    ("День 0", "Контейнер в порту", GREEN),
    ("День 1–2", "Забрали из порта", GREEN),
    ("День 2", "Выгрузка и фото", YELLOW),
    ("Дни 2–9", "Хранение авто", YELLOW),
    ("Далее", "Документы и вывоз", RED),
]

PHOTOS = [
    ("warehouse.jpg", "Выгрузка с фотофиксацией"),
    ("notify.jpg", "Уведомления в Telegram"),
    ("docs.jpg", "Документы для декларации"),
]

PRICE_INCLUDED = [
    "Забор из порта 1–2 дня",
    "Выгрузка из контейнера",
    "Фото и видео фиксация",
    "7 дней хранения авто",
    "Декларация T1 / экспорт",
    "Пакет документов онлайн",
    "Кабинет и уведомления",
    "Персональный менеджер",
]

PRICES = [
    ("Украина", PRICE_UA, GREEN),
    ("Другие направления", PRICE_OTHER, YELLOW),
]
PRICE_NOTE = "за автомобиль — весь пакет услуг"
PRICE_FOOTNOTES = [
    f"Хранение автомобиля после 7 бесплатных дней — {STORAGE_RATE}.",
    MAERSK_NOTE,
]

# ---------------------------------------------------------------- сетка листа

HERO_BOT = H - 70 * mm
# Карточки с цифрами наезжают на фото — так шапка и цифры читаются одним блоком.
STATS_TOP, STATS_H = HERO_BOT + 8 * mm, 29 * mm
STAT_W = 52 * mm
ADV_HEAD_Y = STATS_TOP - STATS_H - 5 * mm
ADV_TOP = ADV_HEAD_Y - 6 * mm
ADV_ROW = 16 * mm
TL_HEAD_Y = 125 * mm
TL_TOP, TL_H = 119 * mm, 25 * mm
PHOTO_TOP, PHOTO_H = 88 * mm, 26 * mm
PRICE_TOP, PRICE_H = 56 * mm, 43 * mm
CONTENT_W = W - 2 * MARGIN


def section_head(c, y, label):
    accent_dots(c, MARGIN, y - 1.5 * mm)
    text(c, MARGIN + 12 * mm, y - 1.1 * mm, label.upper(), F_BOLD, 9, INK_SOFT, tracking=1.5)


def draw_hero(c):
    image_cover(c, "cover_port.jpg", 0, HERO_BOT, W, 70 * mm, shift_y=-7 * mm)
    shade(c, 0, HERO_BOT, W * 0.72, 70 * mm, black, a_start=0.90, a_end=0.05, horizontal=True)
    shade(c, 0, HERO_BOT, W, 24 * mm, black, a_start=0.50, a_end=0.0)
    shade(c, 0, H - 17 * mm, W, 17 * mm, black, a_start=0.0, a_end=0.50)

    logo(c, MARGIN, H - 18.5 * mm, 15, on_dark=True)

    pill_w = str_w(CONTACT_SITE, F_SEMI, 9) + 7 * mm
    pill_h = 6.6 * mm
    c.saveState()
    c.setFillColor(black)
    c.setFillAlpha(0.42)
    c.drawPath(rrect_path(c, W - MARGIN - pill_w, H - 19.4 * mm, pill_w, pill_h, pill_h / 2), stroke=0, fill=1)
    c.restoreState()
    text(c, W - MARGIN - 3.5 * mm, H - 17.3 * mm, CONTACT_SITE, F_SEMI, 9, white, align="r")

    y = para(c, MARGIN, HERO_BOT + 42 * mm, HEADLINE, F_XBOLD, 20.5, white, W * 0.72, leading=24.5)
    para(c, MARGIN, y - 9.5 * mm, SUBLINE, F_REG, 8.6, Color(1, 1, 1, 0.86), W * 0.66, leading=12.5)


def card_bar(c, x, cy, w, color):
    """Цветная полоса по верхней кромке карточки."""
    c.setFillColor(color)
    c.drawPath(rrect_path(c, x, cy + STATS_H - 2.2 * mm, w, 2.2 * mm, 1.1 * mm), stroke=0, fill=1)
    c.rect(x, cy + STATS_H - 2.2 * mm, w, 1.1 * mm, stroke=0, fill=1)


def draw_stats(c):
    gap = 5 * mm
    cy = STATS_TOP - STATS_H
    for i, (prefix, num, unit, title, sub, color, icon) in enumerate(STATS):
        x = MARGIN + i * (STAT_W + gap)
        card(c, x, cy, STAT_W, STATS_H, radius=2.5 * mm)
        card_bar(c, x, cy, STAT_W, color)

        ny = cy + 15.5 * mm
        icon(c, x + 7.5 * mm, ny + 1 * mm, 8.5 * mm, color)
        nx = x + 13.5 * mm
        if prefix:
            text(c, nx, ny, prefix, F_MED, 8, MUTED)
            nx += str_w(prefix, F_MED, 8) + 1.2 * mm
        text(c, nx, ny, num, F_XBOLD, 21, INK)
        text(c, nx + str_w(num, F_XBOLD, 21) + 1.2 * mm, ny, unit, F_SEMI, 8.5, color)
        text(c, x + 13.5 * mm, cy + 9 * mm, title, F_BOLD, 7.4, INK_SOFT)
        text(c, x + 13.5 * mm, cy + 4.4 * mm, sub, F_MED, 6.3, MUTED)

    px = MARGIN + 2 * (STAT_W + gap)
    draw_price_card(c, px, cy, W - MARGIN - px)


def draw_price_card(c, x, cy, w):
    """Карточка с двумя равнозначными ценами и сноской про MAERSK под обеими."""
    card(c, x, cy, w, STATS_H, radius=2.5 * mm)
    card_bar(c, x, cy, w, RED)

    lx, rx = x + 5.5 * mm, x + w - 5.5 * mm

    def price(base, value):
        w_from = str_w("от", F_MED, 8)
        w_num = str_w(value, F_XBOLD, 20)
        w_eur = str_w("€", F_SEMI, 9.5)
        sx = rx - (w_from + 1.2 * mm + w_num + 1.2 * mm + w_eur + 0.5 * mm
                   + str_w("*", F_BOLD, 8))
        text(c, sx, base, "от", F_MED, 8, MUTED)
        sx += w_from + 1.2 * mm
        text(c, sx, base, value, F_XBOLD, 20, INK)
        sx += w_num + 1.2 * mm
        text(c, sx, base, "€", F_SEMI, 9.5, RED)
        text(c, sx + w_eur + 0.5 * mm, base + 1.6 * mm, "*", F_BOLD, 8, MUTED)

    base_ua = cy + 19.5 * mm
    # флаг центрируем по прописной высоте подписи, а не по базовой линии
    flag_ua(c, lx, base_ua - 0.7 * mm, 5.0 * mm)
    text(c, lx + 7.2 * mm, base_ua, "Украина", F_BOLD, 8.2, INK_SOFT)
    price(base_ua, PRICE_UA)

    base_other = cy + 12 * mm
    text(c, lx, base_other, "Другие направления", F_BOLD, 8.2, INK_SOFT)
    price(base_other, PRICE_OTHER)

    para(c, lx, cy + 6.3 * mm, MAERSK_NOTE_SHORT, F_REG, 6.2, MUTED, rx - lx, leading=8.8)


def draw_advantages(c):
    section_head(c, ADV_HEAD_Y, "Что получает клиент")
    gap = 8 * mm
    cw = (CONTENT_W - gap) / 2
    for i, (icon, title, note) in enumerate(ADVANTAGES):
        col, row = i % 2, i // 2
        x = MARGIN + col * (cw + gap)
        ry = ADV_TOP - row * ADV_ROW
        icon(c, x + 4 * mm, ry - 1.2 * mm, 8.5 * mm, GREEN)
        text(c, x + 11 * mm, ry, title, F_BOLD, 9, INK)
        para(c, x + 11 * mm, ry - 4.6 * mm, note, F_REG, 7.6, BODY, cw - 13 * mm, leading=10)


def draw_timeline(c):
    section_head(c, TL_HEAD_Y, "Как это работает")
    cy = TL_TOP - TL_H
    card(c, MARGIN, cy, CONTENT_W, TL_H, radius=3 * mm, fill=SOFT_BG, border=None, shadow=False)

    seg = (CONTENT_W - 10 * mm) / len(TIMELINE)
    x0 = MARGIN + 5 * mm
    line_y = cy + 12 * mm
    c.setStrokeColor(HexColor("#DDE2E8"))
    c.setLineWidth(1.6)
    c.setLineCap(1)
    c.line(x0 + seg * 0.5 - 5 * mm, line_y, x0 + seg * (len(TIMELINE) - 0.5) + 5 * mm, line_y)

    for i, (day, title, color) in enumerate(TIMELINE):
        cx = x0 + seg * (i + 0.5)
        c.setFillColor(SOFT_BG)
        c.circle(cx, line_y, 3.4 * mm, stroke=0, fill=1)
        c.setStrokeColor(color)
        c.setLineWidth(1.5)
        c.circle(cx, line_y, 2.8 * mm, stroke=1, fill=0)
        c.setFillColor(color)
        c.circle(cx, line_y, 1.3 * mm, stroke=0, fill=1)

        text(c, cx, line_y + 6 * mm, day.upper(), F_BOLD, 7.2, color, align="c", tracking=0.7)
        text(c, cx, line_y - 7.5 * mm, title, F_BOLD, 7.8, INK, align="c")


def draw_photos(c):
    gap = 5 * mm
    pw = (CONTENT_W - 2 * gap) / 3
    py = PHOTO_TOP - PHOTO_H
    for i, (filename, caption) in enumerate(PHOTOS):
        x = MARGIN + i * (pw + gap)
        image_cover(c, filename, x, py, pw, PHOTO_H, radius=2 * mm)
        c.saveState()
        c.clipPath(rrect_path(c, x, py, pw, PHOTO_H, 2 * mm), stroke=0, fill=0)
        shade(c, x, py, pw, PHOTO_H * 0.48, black, a_start=0.86, a_end=0.0)
        c.restoreState()
        text(c, x + 3.5 * mm, py + 3.2 * mm, caption, F_SEMI, 7.2, white)


def draw_price(c):
    py = PRICE_TOP - PRICE_H
    c.setFillColor(INK)
    c.drawPath(rrect_path(c, MARGIN, py, CONTENT_W, PRICE_H, 3 * mm), stroke=0, fill=1)

    lx = MARGIN + 11 * mm
    chip_w, chip_h = 65 * mm, 9 * mm
    for i, (region, price, color) in enumerate(PRICES):
        chip_y = py + 30.5 * mm - i * 11 * mm
        c.saveState()
        c.setFillColor(Color(1, 1, 1, 0.09))
        c.drawPath(rrect_path(c, lx, chip_y, chip_w, chip_h, 2 * mm), stroke=0, fill=1)
        c.setFillColor(color)
        c.drawPath(rrect_path(c, lx, chip_y, 1.6 * mm, chip_h, 0.8 * mm), stroke=0, fill=1)
        c.restoreState()

        base = chip_y + 3.1 * mm
        text(c, lx + 5.5 * mm, base, region, F_SEMI, 8.4, white)
        w_from = str_w("от", F_LIGHT, 7.6)
        w_num = str_w(price, F_XBOLD, 14)
        w_eur = str_w("€", F_BOLD, 9.5)
        px = lx + chip_w - 4.5 * mm - (w_from + 1.5 * mm + w_num + 1.2 * mm + w_eur
                                       + 0.5 * mm + str_w("*", F_BOLD, 7.5))
        text(c, px, base, "от", F_LIGHT, 7.6, Color(1, 1, 1, 0.6))
        px += w_from + 1.5 * mm
        text(c, px, base, price, F_XBOLD, 14, white)
        px += w_num + 1.2 * mm
        text(c, px, base, "€", F_BOLD, 9.5, YELLOW)
        text(c, px + w_eur + 0.5 * mm, base + 1.4 * mm, "*", F_BOLD, 7.5, Color(1, 1, 1, 0.5))

    text(c, lx, py + 15.3 * mm, PRICE_NOTE, F_MED, 7.6, Color(1, 1, 1, 0.85))

    c.setStrokeColor(Color(1, 1, 1, 0.16))
    c.setLineWidth(0.7)
    c.line(MARGIN + 80 * mm, py + 14 * mm, MARGIN + 80 * mm, py + PRICE_H - 4 * mm)
    c.line(lx, py + 11.8 * mm, W - MARGIN - 11 * mm, py + 11.8 * mm)

    rx = MARGIN + 88 * mm
    text(c, rx, py + 36.5 * mm, "ЧТО ВХОДИТ В ЦЕНУ", F_BOLD, 7.4, YELLOW, tracking=1.2)
    col_w = (W - MARGIN - 11 * mm - rx) / 2
    for i, item in enumerate(PRICE_INCLUDED):
        col, row = i // 4, i % 4
        x = rx + col * col_w
        ry = py + 30 * mm - row * 5 * mm
        tick(c, x + 1.6 * mm, ry + 1 * mm, 1.6 * mm, YELLOW, on_dark=True)
        text(c, x + 5.5 * mm, ry, item, F_MED, 7.4, Color(1, 1, 1, 0.9))

    for i, note in enumerate(PRICE_FOOTNOTES):
        text(c, lx, py + 8 * mm - i * 4.2 * mm, note, F_REG, 6.8, Color(1, 1, 1, 0.5))


def build(out_path: Path = OUT_PATH) -> Path:
    c = canvas.Canvas(str(out_path), pagesize=(W, H))
    c.setTitle("Caromoto Lithuania — склад для разгрузки авто из США")
    c.setAuthor("Caromoto Lithuania")
    c.setSubject("Коммерческое предложение склада")

    c.setFillColor(CANVAS_BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)

    draw_hero(c)
    draw_stats(c)
    draw_advantages(c)
    draw_timeline(c)
    draw_photos(c)
    draw_price(c)

    c.showPage()
    c.save()
    return out_path


if __name__ == "__main__":
    print(f"PDF готов: {build()}")
