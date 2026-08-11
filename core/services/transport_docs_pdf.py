"""Отрисовка PDF-документов пакета автовоза (reportlab).

Макеты повторяют реальный пакет документов для оформления на Беларусь:
инвойс продавца (CAROMOTO LLC, США), платёжное поручение белорусского банка,
гарантийное письмо продавца («Письмо USA»), обязательство клиента и договор
на перевозку (двуязычный LT/EN). Валидация дат и данных — в
:mod:`.transport_docs`.
"""

from __future__ import annotations

import datetime
import io
from decimal import Decimal
from pathlib import Path

from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.services.bank_stamp import compose_executor_stamp_field

ASSETS_DIR = Path(__file__).resolve().parent.parent / "pdf_assets"
FONTS_DIR = ASSETS_DIR / "fonts"

# Опциональная графика фирменных документов (кладётся в core/pdf_assets/):
#   invoice_logo.png      — логотип слева в шапке инвойса (вместо надписи INVOICE);
#   letter_logo.png       — крупный логотип в шапке письма USA;
#   invoice_stamp.png     — печать компании (инвойс и письмо USA);
#   invoice_signature.png — факсимиле подписи президента.
# Файлы отсутствуют — документ рендерится без них. PNG лучше с прозрачным фоном.
LOGO_ASSET = "invoice_logo.png"
LETTER_LOGO_ASSET = "letter_logo.png"
STAMP_ASSET = "invoice_stamp.png"
PRESIDENT_SIGNATURE_ASSET = "invoice_signature.png"

# Реквизиты продавца (США) — из реального пакета документов.
SELLER_NAME = "CAROMOTO LLC"
SELLER_ADDRESS = "4602 148th AVE NE, Redmond WA 98052, United States of America"
SELLER_BANK = "WELLS FARGO BANK, N.A."
SELLER_BANK_ADDRESS = "420 Montgomery St. San Francisco, CA 94104, United States of America"
SELLER_ACCOUNT_USD = "9162856943"
SELLER_SWIFT = "WFBIUS6S"
SELLER_ABA = "121000248"
SELLER_PRESIDENT = "Kim Tkhe Sik"

# Заказчик в договоре перевозки — из реального договора.
CONTRACT_CUSTOMER_NAME = "Exportgroup LLC dba Caromoto"
CONTRACT_CUSTOMER_ADDRESS = ["4602 148th Ave NE", "Redmond, WA 98052", "United States of America"]
CONTRACT_CUSTOMER_BANK = [
    "Wells Fargo Bank, N.A.",
    "420 Montgomery St.",
    "San Francisco, CA 94104",
    "United States of America",
    f"Account number (USD): {SELLER_ACCOUNT_USD}",
    f"SWIFT: {SELLER_SWIFT}",
    f"ABA (Routing number): {SELLER_ABA}",
]

MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
MONTHS_LT_GENITIVE = [
    "sausio",
    "vasario",
    "kovo",
    "balandžio",
    "gegužės",
    "birželio",
    "liepos",
    "rugpjūčio",
    "rugsėjo",
    "spalio",
    "lapkričio",
    "gruodžio",
]


def _register_fonts():
    registered = set(pdfmetrics.getRegisteredFontNames())
    if "DejaVuSans" not in registered:
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(FONTS_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Oblique", str(FONTS_DIR / "DejaVuSans-Oblique.ttf")))
        # Жирный/курсив — только через <b>/<i>-разметку в параграфах (базовый шрифт
        # у стилей всегда DejaVuSans, иначе ломается обратный маппинг семейства).
        pdfmetrics.registerFontFamily(
            "DejaVuSans",
            normal="DejaVuSans",
            bold="DejaVuSans-Bold",
            italic="DejaVuSans-Oblique",
            boldItalic="DejaVuSans-Bold",
        )
    if "TimesNewRoman" not in registered and (FONTS_DIR / "times.ttf").exists():
        pdfmetrics.registerFont(TTFont("TimesNewRoman", str(FONTS_DIR / "times.ttf")))
        pdfmetrics.registerFont(TTFont("TimesNewRoman-Bold", str(FONTS_DIR / "timesbd.ttf")))
        pdfmetrics.registerFont(TTFont("TimesNewRoman-Italic", str(FONTS_DIR / "timesi.ttf")))
        pdfmetrics.registerFont(TTFont("TimesNewRoman-BoldItalic", str(FONTS_DIR / "timesbi.ttf")))
        pdfmetrics.registerFontFamily(
            "TimesNewRoman",
            normal="TimesNewRoman",
            bold="TimesNewRoman-Bold",
            italic="TimesNewRoman-Italic",
            boldItalic="TimesNewRoman-BoldItalic",
        )


def _style(name="base", **kwargs):
    # Paragraph парсит разметку при создании — шрифты должны быть
    # зарегистрированы до первого параграфа, а не только в _build_pdf.
    _register_fonts()
    defaults = {"fontName": "DejaVuSans", "fontSize": 10, "leading": 13}
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)


def _pp_style(name="pp", **kwargs):
    """Стили платёжки — Times New Roman (как в банковском образце)."""
    _register_fonts()
    font = "TimesNewRoman" if "TimesNewRoman" in pdfmetrics.getRegisteredFontNames() else "DejaVuSans"
    defaults = {"fontName": font, "fontSize": 9, "leading": 11}
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)


def _date_en(day: datetime.date) -> str:
    return f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}"


def _date_ru(day: datetime.date) -> str:
    return day.strftime("%d.%m.%Y")


def _date_lt(day: datetime.date) -> str:
    return f"{day.year} m. {MONTHS_LT_GENITIVE[day.month - 1]} {day.day} d."


def _wrap_address_html(address: str) -> str:
    """Адрес в 1–2 строки: последняя часть (обычно страна) с новой строки."""
    lines = []
    for raw in (address or "").splitlines():
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if not parts:
            continue
        if len(parts) == 1:
            lines.append(parts[0])
        else:
            lines.append(", ".join(parts[:-1]) + ",<br/>" + parts[-1])
    return "<br/>".join(lines)


def _money_usd(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def _amount_digits_by(amount: Decimal) -> str:
    """«2 850,00» — формат суммы в белорусской платёжке."""
    return f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    tail = abs(number) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def amount_in_words_ru(amount: Decimal) -> str:
    """«Две тысячи восемьсот пятьдесят долларов США ноль центов»."""
    dollars = int(amount)
    cents = int((amount - dollars) * 100)
    dollars_words = num2words(dollars, lang="ru")
    cents_words = num2words(cents, lang="ru")
    dollars_unit = _plural_ru(dollars, "доллар", "доллара", "долларов")
    cents_unit = _plural_ru(cents, "цент", "цента", "центов")
    text = f"{dollars_words} {dollars_unit} США {cents_words} {cents_unit}"
    return text[0].upper() + text[1:]


def car_description(car) -> str:
    """«2023 CHEVROLET MALIBU, 1G1ZD5ST0PF171248»."""
    return f"{car.year} {car.brand.upper()}, {car.vin}"


def _build_pdf(story, *, margins=(2 * cm, 2 * cm, 2 * cm, 2 * cm)) -> bytes:
    _register_fonts()
    buffer = io.BytesIO()
    top, right, bottom, left = margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=top,
        rightMargin=right,
        bottomMargin=bottom,
        leftMargin=left,
        title="Document",
    )
    doc.build(story)
    return buffer.getvalue()


def _signature_flowable(signature_bytes: bytes | None, height=1.4 * cm):
    """Картинка подписи (jpg/png) с сохранением пропорций; None если нет/не картинка."""
    if not signature_bytes:
        return None
    try:
        reader = ImageReader(io.BytesIO(signature_bytes))
        width_px, height_px = reader.getSize()
    except Exception:  # не картинка (например, PDF) — просто без подписи
        return None
    width = height * width_px / height_px
    return Image(io.BytesIO(signature_bytes), width=width, height=height)


def _asset_flowable(name: str, *, max_height: float, max_width: float | None = None):
    """PNG-ассет из core/pdf_assets/ с сохранением пропорций; None если файла нет.

    Картинка вписывается в ``max_height`` × ``max_width`` (если задан),
    без растягивания — иначе широкий логотип раздувается на всю колонку.
    """
    path = ASSETS_DIR / name
    if not path.exists():
        return None
    try:
        reader = ImageReader(str(path))
        width_px, height_px = reader.getSize()
    except Exception:
        return None
    if not width_px or not height_px:
        return None
    width = max_height * width_px / height_px
    height = max_height
    if max_width and width > max_width:
        height = max_width * height_px / width_px
        width = max_width
    image = Image(str(path), width=width, height=height)
    image.hAlign = "LEFT"
    return image


class _StampWithPresident(Flowable):
    """Печать + надпись President поверх неё.

    ``stamp_dx`` / ``stamp_dy`` двигают только PNG печати; надпись остаётся
    на якоре и рисуется после печати (сверху по z-order).
    """

    def __init__(
        self,
        stamp: Image,
        text: Paragraph,
        *,
        text_lift: float = 0.45 * cm,
        stamp_dx: float = 0,
        stamp_dy: float = 0,
    ):
        super().__init__()
        self.stamp = stamp
        self.text = text
        self.text_lift = text_lift
        self.stamp_dx = stamp_dx
        self.stamp_dy = stamp_dy
        self._stamp_w = float(stamp.drawWidth)
        self._stamp_h = float(stamp.drawHeight)
        self._origin_y = 0.0

    def wrap(self, availWidth, availHeight):
        # Учитываем отрицательный stamp_dy (печать ниже якоря текста).
        bottom = min(self.stamp_dy, 0)
        top = max(self._stamp_h + self.stamp_dy, self.text_lift + 14)
        self._origin_y = -bottom
        self.width = max(self._stamp_w + self.stamp_dx, self._stamp_w)
        self.height = top - bottom
        return self.width, self.height

    def draw(self):
        oy = self._origin_y
        self.stamp.drawOn(self.canv, self.stamp_dx, oy + self.stamp_dy)
        tw, _th = self.text.wrap(self._stamp_w, 20)
        # Надпись по центру «исходной» ширины печати, без stamp_dx — на месте.
        x = max(0, (self._stamp_w - tw) / 2)
        self.text.drawOn(self.canv, x, oy + self.text_lift)


def _president_block(style):
    """Блок «President ...» с факсимиле подписи и печатью (если ассеты добавлены).

    Надпись рисуется поверх печати и не следует за сдвигами PNG.
    """
    signature = _asset_flowable(PRESIDENT_SIGNATURE_ASSET, max_height=1.6 * cm, max_width=5 * cm)
    stamp = _asset_flowable(STAMP_ASSET, max_height=4.2 * cm, max_width=6.5 * cm)
    president = Paragraph(f"President {SELLER_PRESIDENT}", style)

    if stamp is None:
        left_cell = ([signature] if signature else []) + [president]
        if signature is None:
            return president
        table = Table([[left_cell]], colWidths=[17 * cm])
        table.hAlign = "LEFT"
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return table

    # Только печать правее (+100 pt) и чуть ниже; надпись на якоре.
    overlay = _StampWithPresident(
        stamp,
        president,
        text_lift=0.45 * cm,
        stamp_dx=0.4 * cm + 45,  # ещё 15 pt левее
        stamp_dy=-0.6 * cm,
    )

    if signature is None:
        overlay.hAlign = "LEFT"
        return overlay

    table = Table([[signature, overlay]], colWidths=[4.4 * cm, 10 * cm])
    table.hAlign = "LEFT"
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


# ---------------------------------------------------------------------------
# Инвойс
# ---------------------------------------------------------------------------


def generate_invoice_pdf(
    car,
    *,
    number: str,
    date: datetime.date,
    amount: Decimal,
    buyer: dict,
    extra_lines: list[dict] | None = None,
) -> bytes:
    """PDF инвойса: строка авто + опциональные доп. строки, итог = сумма всех.

    ``amount`` — цена автомобиля. ``extra_lines`` — список
    ``{"description": str, "amount": Decimal}``.
    """
    base = _style()
    bold = _style("bold")
    small = _style("small", fontSize=9, leading=12)

    buyer_lines = [buyer["name"]]
    if buyer.get("passport_number"):
        buyer_lines.append(buyer["passport_number"])
    address_html = _wrap_address_html(buyer.get("address") or "")
    if address_html:
        buyer_lines.append(address_html)
    buyer_html = "<br/>".join(buyer_lines)

    # Слева — логотип (вместо большой надписи INVOICE); справа уже есть INVOICE # / DATE.
    # Размер на странице задаётся здесь (пиксели PNG на это почти не влияют — только качество).
    left_header = _asset_flowable(LOGO_ASSET, max_height=2.6 * cm, max_width=6.5 * cm) or Paragraph("", base)
    header = Table(
        [
            [
                left_header,
                Paragraph(
                    f"<b>INVOICE #:</b> {number}<br/><b>DATE:</b> {_date_en(date)}",
                    _style("hdr", fontSize=10, leading=14, alignment=TA_RIGHT),
                ),
            ]
        ],
        colWidths=[8.5 * cm, 8.5 * cm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    seller_address_html = _wrap_address_html(SELLER_ADDRESS)
    seller_bank_address_html = _wrap_address_html(SELLER_BANK_ADDRESS)
    requisites = Paragraph(
        f"<b>ACCOUNT NAME:</b> {SELLER_NAME}<br/>{seller_address_html}<br/><br/>"
        f"<b>BANK REFERENCE:</b> {SELLER_BANK}<br/>{seller_bank_address_html}<br/>"
        f"<b>ACCOUNT NUMBER USD:</b> {SELLER_ACCOUNT_USD}<br/>"
        f"<b>INTERNATIONAL SWIFT CODE:</b> {SELLER_SWIFT}<br/>"
        f"<b>ABA (ROUTING NUMBER):</b> {SELLER_ABA}",
        small,
    )
    to_blocks = Table(
        [
            [
                Paragraph(f"<b>TO:</b><br/>{buyer_html}", small),
                Paragraph(f"<b>SHIP TO:</b><br/>{buyer_html}", small),
            ]
        ],
        colWidths=[8.5 * cm, 8.5 * cm],
    )
    to_blocks.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    car_money = _money_usd(amount)
    category = buyer.get("car_category") or "М1"
    total = amount
    item_rows = [
        [
            Paragraph("<b>QUANTITY</b>", base),
            Paragraph("<b>DESCRIPTION</b>", base),
            Paragraph("<b>Unit price</b>", base),
            Paragraph("<b>Total</b>", base),
        ],
        [
            Paragraph("1", base),
            Paragraph(f"{car_description(car)}<br/>Car category - {category}", base),
            Paragraph(car_money, base),
            Paragraph(car_money, base),
        ],
    ]
    for line in extra_lines or []:
        line_amount = line["amount"]
        total += line_amount
        line_money = _money_usd(line_amount)
        item_rows.append(
            [
                Paragraph("1", base),
                Paragraph(str(line["description"]), base),
                Paragraph(line_money, base),
                Paragraph(line_money, base),
            ]
        )

    items = Table(item_rows, colWidths=[2.6 * cm, 9.4 * cm, 2.5 * cm, 2.5 * cm])
    items.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.92)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    total_money = _money_usd(total)
    totals = Table(
        [
            ["", Paragraph("<b>SUBTOTAL</b>", base), Paragraph(total_money, base)],
            ["", Paragraph("<b>SALES TAX</b>", base), Paragraph("-", base)],
            ["", Paragraph("<b>SHIPPING &amp; HANDLING</b>", base), Paragraph("-", base)],
            ["", Paragraph("<b>TOTAL DUE</b>", bold), Paragraph(f"<b>{total_money}</b>", bold)],
        ],
        colWidths=[9.5 * cm, 5.0 * cm, 2.5 * cm],
    )
    totals.setStyle(
        TableStyle(
            [
                ("GRID", (1, 0), (-1, -1), 0.6, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    story = [
        header,
        Spacer(1, 0.5 * cm),
        requisites,
        Spacer(1, 0.6 * cm),
        to_blocks,
        Spacer(1, 0.6 * cm),
        items,
        Spacer(1, 0.3 * cm),
        totals,
        Spacer(1, 1.6 * cm),
        _president_block(base),
    ]
    return _build_pdf(story)


# ---------------------------------------------------------------------------
# Платёжное поручение (Беларусь) — форма как в банковском образце
# ---------------------------------------------------------------------------

# Реквизиты получателя в платёжке — как в реальном пакете (короткий адрес USA).
_PAYMENT_BENEFICIARY = "CAROMOTO LLC, 4602 148TH AVE NE, REDMOND WA 98052, USA"
_PAYMENT_BENEFICIARY_BANK = "WELLS FARGO BANK, 420 MONTGOMERY STREET, SAN FRANCISCO.CA,94104, USA"


def generate_payment_order_pdf(
    car,
    *,
    number: str,
    date: datetime.date,
    amount: Decimal,
    invoice_number: str,
    invoice_date: datetime.date,
    payer: dict,
    signature_bytes: bytes | None = None,
) -> bytes:
    """Платёжное поручение в виде банковской сетки (как DOCS MALIBU 1248)."""
    tiny = _pp_style("pp_tiny", fontSize=7.5, leading=9.5)
    label = _pp_style("pp_label", fontSize=8.5, leading=10.5)
    value = _pp_style("pp_value", fontSize=9, leading=11)
    value_sm = _pp_style("pp_value_sm", fontSize=8.5, leading=10.5)
    value_c = _pp_style("pp_value_c", fontSize=9, leading=11, alignment=TA_CENTER)
    value_r = _pp_style("pp_value_r", fontSize=9, leading=11, alignment=TA_RIGHT, rightIndent=8)
    # Чёрные линии <~0.2 pt на экране выглядят одинаково (hairline / Enhance Thin Lines).
    # Серая сетка визуально тоньше чёрной при той же геометрической толщине.
    line_w = 0.25
    line_c = colors.Color(0.42, 0.42, 0.42)

    payer_line = ", ".join(filter(None, (payer.get("name"), payer.get("passport_number"), payer.get("address"))))
    iban = payer.get("iban") or ""
    bank_name = payer.get("bank_name") or ""
    bank_code = payer.get("bank_code") or ""
    purpose = (
        f"PAYMENT FOR:  {car_description(car)}  INVOICE № {invoice_number}<br/>"
        f"DATE {_date_ru(invoice_date)} /TRANSLATION IS NOT RELATED TO BUSINESS ACTIVITIES/<br/>"
        f"ОПЛАТА ПО ИНВОЙСУ № {invoice_number}<br/>"
        f"ДАТА {_date_ru(invoice_date)}  /ПЕРЕВОД НЕ СВЯЗАН С ПРЕДПРИНИМАТЕЛЬСКОЙ ДЕЯТЕЛЬНОСТЬЮ/"
    )
    signature = _signature_flowable(signature_bytes, height=2.2 * cm)

    def P(text, style=label):
        return Paragraph(text, style)

    def box(role="mid", extra=None):
        """Сетка без двойных линий: стыки таблиц дают одну толщину.

        role: first — верх формы; mid — середина; last — низ.
        """
        cmds = [
            ("INNERGRID", (0, 0), (-1, -1), line_w, line_c),
            ("LINEBEFORE", (0, 0), (0, -1), line_w, line_c),
            ("LINEAFTER", (-1, 0), (-1, -1), line_w, line_c),
            ("LINEBELOW", (0, -1), (-1, -1), line_w, line_c),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        if role == "first":
            cmds.append(("LINEABOVE", (0, 0), (-1, 0), line_w, line_c))
        if extra:
            cmds.extend(extra)
        return TableStyle(cmds)

    # Рабочая ширина ≈ образец A4 с полями ~1.2 см.
    W = 18.2 * cm
    L = 4.6 * cm
    mid = W - L - 2.4 * cm - 3.2 * cm

    header = Table(
        [
            [
                P("<b>ПЛАТЕЖНОЕ ПОРУЧЕНИЕ №</b>", label),
                P(f"<b>{number}</b>", value_c),
                P("Дата", label),
                P(_date_ru(date), value_c),
                P("Срочный", label),
                P("□", value_c),
                P("Несрочный", label),
                P("<b>■</b>", value_c),
            ]
        ],
        colWidths=[4.2 * cm, 2.2 * cm, 1.2 * cm, 2.4 * cm, 1.8 * cm, 0.9 * cm, 2.2 * cm, W - 14.9 * cm],
    )
    header.setStyle(
        box(
            "first",
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("ALIGN", (3, 0), (3, 0), "CENTER"),
                ("ALIGN", (5, 0), (5, 0), "CENTER"),
                ("ALIGN", (7, 0), (7, 0), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ],
        )
    )

    amount_row = Table(
        [[P("Сумма и валюта:", label), P(amount_in_words_ru(amount), value)]],
        colWidths=[L, W - L],
        rowHeights=[0.9 * cm],
    )
    amount_row.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    currency_row = Table(
        [
            [
                P("Код валюты", label),
                P("<b>840</b>", value_c),
                P("Сумма цифрами", label),
                P(f"<b>{_amount_digits_by(amount)}</b>", value_r),
            ]
        ],
        colWidths=[L, 3.5 * cm, 4.5 * cm, W - L - 8 * cm],
        rowHeights=[0.95 * cm],
    )
    currency_row.setStyle(
        box(
            "mid",
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("ALIGN", (3, 0), (3, 0), "RIGHT"),
                ("RIGHTPADDING", (3, 0), (3, 0), 10),
            ],
        )
    )

    payer_block = Table(
        [
            [P("Плательщик:", label), P(payer_line, value)],
            [P("Счет №", label), P(iban, value)],
        ],
        colWidths=[L, W - L],
        rowHeights=[1.5 * cm, 0.8 * cm],
    )
    payer_block.setStyle(box("mid", [("VALIGN", (0, 0), (-1, 0), "TOP"), ("VALIGN", (0, 1), (-1, 1), "MIDDLE")]))

    send_bank = Table(
        [[P("Банк-отправитель:", label), P(bank_name, value), P("Код банка", label), P(bank_code, value)]],
        colWidths=[L, mid, 2.4 * cm, 3.2 * cm],
        rowHeights=[0.9 * cm],
    )
    send_bank.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    recv_bank = Table(
        [
            [
                P("Банк-получатель:", label),
                P(_PAYMENT_BENEFICIARY_BANK, value_sm),
                P("Код банка", label),
                P(SELLER_SWIFT, value),
            ]
        ],
        colWidths=[L, mid, 2.4 * cm, 3.2 * cm],
        rowHeights=[1.1 * cm],
    )
    recv_bank.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    bene = Table(
        [
            [P("Бенефициар:", label), P(_PAYMENT_BENEFICIARY, value)],
            [P("Счет №", label), P(SELLER_ACCOUNT_USD, value)],
        ],
        colWidths=[L, W - L],
        rowHeights=[1.0 * cm, 0.8 * cm],
    )
    bene.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    purpose_top = Table(
        [[P("Назначение платежа:", label), P(purpose, value_sm)]],
        colWidths=[L, W - L],
        rowHeights=[2.25 * cm],
    )
    purpose_top.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "TOP")]))

    c1, c2, c3, c4 = 4.5 * cm, 4.5 * cm, 5.0 * cm, W - 14 * cm
    purpose_refs = Table(
        [
            [P("№ документа:", tiny), P("", tiny), P("Дата документа:", tiny), P("", tiny)],
            [
                P("Уполномоченный орган", tiny),
                P("Номер законодательного акта:", tiny),
                P("Дата законодательного акта:", tiny),
                P("", tiny),
            ],
        ],
        colWidths=[c1, c2, c3, c4],
    )
    purpose_refs.setStyle(box("mid"))

    unp_widths = [2.6 * cm, 1.4 * cm, 2.6 * cm, 1.4 * cm, 2.8 * cm, 1.2 * cm, 2.0 * cm, 1.0 * cm, 1.5 * cm]
    unp_widths.append(W - sum(unp_widths))
    unp = Table(
        [
            [
                P("УНП плательщика:", tiny),
                P("", tiny),
                P("УНП бенефициара:", tiny),
                P("", tiny),
                P("УНП третьего лица:", tiny),
                P("", tiny),
                P("Код платежа:", tiny),
                P("<b>00</b>", value),
                P("Очередь:", tiny),
                P("", tiny),
            ]
        ],
        colWidths=unp_widths,
    )
    unp.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    corr_widths = [5.0 * cm, 4.0 * cm, 2.0 * cm, 2.2 * cm, 1.5 * cm]
    corr_widths.append(W - sum(corr_widths))
    corr = Table(
        [
            [
                P("Корреспондент банка-получателя:", tiny),
                P("", tiny),
                P("Код банка:", tiny),
                P("", tiny),
                P("Счет №", tiny),
                P("", tiny),
            ]
        ],
        colWidths=corr_widths,
    )
    corr.setStyle(
        box(
            "mid",
            [
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ],
        )
    )

    fees_widths = [3.5 * cm, 4.8 * cm, 4.5 * cm]
    fees_widths.append(W - sum(fees_widths))
    fees = Table(
        [
            [
                P("Расходы по переводу:", label),
                P("ПЛ&nbsp;&nbsp;<b>■</b>&nbsp;&nbsp;&nbsp;БН&nbsp;&nbsp;□&nbsp;&nbsp;&nbsp;ПЛ/БН&nbsp;&nbsp;□", value),
                P("Комиссию списать со счета №:", tiny),
                P(iban, value_sm),
            ]
        ],
        colWidths=fees_widths,
    )
    fees.setStyle(box("mid", [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    details = Table(
        [
            [P("Регистрационный номер сделки:", tiny), P("", tiny)],
            [P("Детали платежа:", label), P("ВАЛЮТНЫЙ ДОГОВОР НЕ ПОДЛЕЖИТ РЕГИСТРАЦИИ", value)],
        ],
        colWidths=[5.2 * cm, W - 5.2 * cm],
    )
    details.setStyle(box("mid"))

    # Нижняя часть (банк + подписи) — одна общая сетка; высота подогнана под 1×A4.
    sign_h = 5.6 * cm
    sign_left_cell = [
        P("Подпись плательщика:", label),
        Spacer(1, 0.2 * cm),
        signature or P("", value),
    ]
    # Печать банка + подпись операциониста: дата = дата платежа, случайный
    # вариант подписи/угол/позиция в пределах правого поля (детерминировано
    # от номера и даты — одна платёжка не «прыгает» при перегенерации).
    stamp_field_w = W - 6.8 * cm - 0.2 * cm
    # Чуть выше — подпись на печати не упирается в нижнюю границу ячейки.
    stamp_field_h = 3.8 * cm
    executor_stamp = Image(
        io.BytesIO(
            compose_executor_stamp_field(
                date,
                field_width_pt=stamp_field_w,
                field_height_pt=stamp_field_h,
                payment_number=number,
            )
        ),
        width=stamp_field_w,
        height=stamp_field_h,
        mask="auto",
    )
    sign_right_cell = [
        P("Подпись исполнителя банка:", label),
        P(f"Дата поступления: {_date_ru(date)}", label),
        P("Штамп банка:", label),
        Spacer(1, 0.05 * cm),
        executor_stamp,
    ]
    bottom = Table(
        [
            [P("<b>Заполняется банком</b>", tiny), "", "", "", ""],
            [P("Сумма к перечислению/списанию:", tiny), "", "", "", ""],
            [P("Корреспондент банка-отправителя:", tiny), "", "", "", ""],
            [P("Дата валютирования:", tiny), "", P("Подпись:", tiny), "", ""],
            [
                P("Дебет счета:", tiny),
                P("Кредит счета:", tiny),
                P("Код валюты:", tiny),
                P("Сумма перевода:", tiny),
                P("Эквивалент в<br/>белорусских рублях", tiny),
            ],
            ["", "", "", "", ""],
            [sign_left_cell, "", sign_right_cell, "", ""],
        ],
        colWidths=[3.4 * cm, 3.4 * cm, 2.8 * cm, 3.4 * cm, W - 13.0 * cm],
        rowHeights=[0.6 * cm, 0.7 * cm, 0.7 * cm, 0.7 * cm, 0.85 * cm, 0.95 * cm, sign_h],
    )
    bottom.setStyle(
        box(
            "last",
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("SPAN", (0, 1), (-1, 1)),
                ("SPAN", (0, 2), (-1, 2)),
                ("SPAN", (0, 3), (1, 3)),
                ("SPAN", (2, 3), (-1, 3)),
                ("SPAN", (0, 6), (1, 6)),
                ("SPAN", (2, 6), (-1, 6)),
                ("VALIGN", (0, 0), (-1, 5), "MIDDLE"),
                ("VALIGN", (0, 6), (-1, 6), "TOP"),
                ("TOPPADDING", (0, 6), (-1, 6), 3),
                ("LEFTPADDING", (0, 6), (-1, 6), 3),
            ],
        )
    )

    # Одна оболочка без отступов — секции стыкуются без зазоров и двойных линий.
    form = Table(
        [
            [header],
            [amount_row],
            [currency_row],
            [payer_block],
            [send_bank],
            [recv_bank],
            [bene],
            [purpose_top],
            [purpose_refs],
            [unp],
            [corr],
            [fees],
            [details],
            [bottom],
        ],
        colWidths=[W],
    )
    form.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return _build_pdf([form], margins=(0.8 * cm, 1.0 * cm, 0.8 * cm, 1.0 * cm))


# ---------------------------------------------------------------------------
# Письмо USA (гарантийное письмо продавца)
# ---------------------------------------------------------------------------


def generate_letter_usa_pdf(car, *, date: datetime.date) -> bytes:
    base = _style(fontSize=11, leading=16)
    # Баннер CAROMOTO примерно до середины страницы; fallback — логотип инвойса.
    logo = _asset_flowable(LETTER_LOGO_ASSET, max_height=1.6 * cm, max_width=8.5 * cm)
    if logo is None:
        logo = _asset_flowable(LOGO_ASSET, max_height=2.6 * cm, max_width=6.5 * cm)
    story = []
    if logo is not None:
        story.extend([logo, Spacer(1, 1.3 * cm)])
    story.extend(
        [
            Paragraph(
                f"<b>ACCOUNT NAME:</b> {SELLER_NAME}<br/>"
                "4602 148th AVE NE, Redmond WA 98052,<br/>"
                "United States of America",
                _style("h", fontSize=10, leading=14),
            ),
            Spacer(1, 1.4 * cm),
            Paragraph(
                f"{SELLER_NAME}, {SELLER_ADDRESS}, guarantees that the vehicle "
                f"<b>{car_description(car)}</b> will be used only in the territory of the "
                "Republic of Belarus. We will not sell or rent it to citizens of the Russian "
                "Federation, and we will not use it on the territory of the Russian Federation.",
                _style("b", fontSize=11, leading=16, alignment=TA_JUSTIFY),
            ),
            Spacer(1, 1.0 * cm),
            Paragraph(_date_en(date), _style("date_r", fontSize=11, leading=16, alignment=TA_RIGHT)),
            Spacer(1, 0.35 * cm),  # блок подписи/печати чуть выше (~25 px)
            _president_block(base),
        ]
    )
    return _build_pdf(story)


# ---------------------------------------------------------------------------
# Обязательство клиента
# ---------------------------------------------------------------------------


def generate_obligation_pdf(car, *, date: datetime.date, buyer: dict, signature_bytes: bytes | None = None) -> bytes:
    name = buyer.get("name_ru") or buyer["name"]
    address = buyer.get("address_ru") or buyer.get("address") or ""

    intro = f"Я, {name}"
    birth = buyer.get("birth_date")
    if birth:
        intro += f", {_date_ru(birth)} года рождения"
    intro += f", паспорт {buyer['passport_number']}"
    issue = buyer.get("passport_issue_date")
    if issue:
        intro += f" от {_date_ru(issue)}"
    text = (
        f"{intro}, адрес проживания: {address}, обязуюсь использовать принадлежащий мне "
        f"<b>{car_description(car)}</b> только в личных целях на территории РБ. Гарантирую, "
        "что продавать или сдавать в аренду гражданам РФ и использовать его на территории "
        "России, не буду."
    )

    signature = _signature_flowable(signature_bytes)
    sign_row = Table(
        [
            [
                Paragraph(f"{name}", _style(fontSize=11)),
                signature or Paragraph("", _style()),
                Paragraph(_date_ru(date), _style("d", fontSize=11, alignment=TA_RIGHT)),
            ]
        ],
        colWidths=[8 * cm, 5 * cm, 4 * cm],
    )
    sign_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    story = [
        Paragraph("<b>Обязательство</b>", _style("t", fontSize=14, alignment=TA_CENTER)),
        Spacer(1, 1.2 * cm),
        Paragraph(text, _style("b", fontSize=11, leading=17, alignment=TA_JUSTIFY)),
        Spacer(1, 1.6 * cm),
        sign_row,
    ]
    return _build_pdf(story)


# ---------------------------------------------------------------------------
# Договор на перевозку (двуязычный LT/EN)
# ---------------------------------------------------------------------------

# Статьи договора: (LT, EN). Плейсхолдеры подставляются при генерации.
_CONTRACT_CLAUSES = [
    (
        "<b>1. SUTARTIES OBJEKTAS</b><br/>1.1. Šios Sutarties objektas yra santykiai, atsirandantys "
        "tarp Ekspeditoriaus ir Kliento planuojant, skaičiuojant įkainius bei vykdant krovinių "
        "pervežimus tarptautiniais ir tarpmiestiniais maršrutais.",
        "<b>1. SUBJECT OF THE CONTRACT</b><br/>1.1. The subject of the present Contract shall be the "
        "procedure of relationships originating between the Forwarder and the Customer within the "
        "scheduling, making settlements and carrying out of freight transportation in international "
        "and intercity haulage.",
    ),
    (
        "<b>2. BENDROSIOS NUOSTATOS</b><br/>2.1. Klientas ir Ekspeditorius veikia savo vardu arba "
        "organizacijų pavedimu, su kuriomis jie turi sudarę tiesiogines sutartis.",
        "<b>2. GENERAL PROVISIONS</b><br/>2.1. The Customer and the Forwarder act on their own behalf "
        "and on behalf of the organisations with whom they have direct contracts.",
    ),
    (
        "<b>3. ŠALIŲ PAREIGOS</b><br/>3.1. Klientas įsipareigoja:<br/>"
        "3.1.1. Suderinti vežamų krovinių kiekius ir nomenklatūrą su Ekspeditoriumi. Klientas privalo "
        "paruošti krovinį taip, kad būtų užtikrintas krovinio saugumas transportavimo metu.<br/>"
        "3.1.2. Laiku vykdyti visus su krovinio transportavimu susijusius mokėjimus ir kitus "
        "Ekspeditoriaus nurodytus mokėjimus.<br/>"
        "3.1.3. Laiku teikti Ekspeditoriui krovinio transportavimui reikalingą informaciją, jo "
        "transportavimo būdą; laiku teikti visus būtinus krovinio dokumentus, užtikrinančius laisvą "
        "transporto priemonių judėjimą per išvykimo, atvykimo, tranzito šalių valstybines sienas.",
        "<b>3. LIABILITIES OF THE PARTIES</b><br/>3.1. The Customer undertakes:<br/>"
        "3.1.1. To submit freights for transportation for Forwarder within the nomenclature and in "
        "the volumes agreed with the Forwarder; to ensure preparation of the freight dispatched that "
        "ensures safety both of the freight.<br/>"
        "3.1.2. To remit the amounts of freight rate and other amounts due to the Forwarder in "
        "proper time.<br/>"
        "3.1.3. To inform the Forwarder on all the information necessary for carrying out a "
        "transportation about the freight and way of transportation, and to provide in proper time "
        "at the Forwarder's disposal all the necessary documents guaranteeing unobstructed travel of "
        "the vehicles across the state borders of the departure, destination and transit countries.",
    ),
    (
        "3.2. Ekspeditorius įsipareigoja:<br/>"
        "3.2.1. Informuoti Klientą apie priverstinius transporto priemonių sustojimus kelyje, "
        "avarijas ir kitas nenumatytas aplinkybes, kurios neleidžia laiku pristatyti krovinio.<br/>"
        "3.2.2. Transporto priemonei priverstinai sustojus kelyje, reikalauti iš Kliento informacijos "
        "ir dokumentų, reikalingų užtikrinti laisvą transporto priemonių judėjimą per išvykimo, "
        "atvykimo, tranzito šalių valstybines sienas.",
        "3.2. The Forwarder undertakes:<br/>"
        "3.2.1. To inform the Customer about forced delays of vehicles on the way, accidents and "
        "other unforeseen emergencies preventing from timely delivery of freights.<br/>"
        "3.2.2. To request from the Customer the information and documents necessary for "
        "unobstructed travel of vehicles across the state borders of the departure, destination and "
        "transit countries, and in cases of delays of means of transport on the way.",
    ),
    (
        "<b>4. UŽSAKYMO IR TRANSPORTO PRIEMONĖS PATEIKIMO TVARKA, ŠALIŲ ATSAKOMYBĖ</b><br/>"
        "4.1. Klientas pateikia Ekspeditoriui užsakymą, nurodydamas pervežimo maršrutą, transporto "
        "priemonės rūšį, pakrovimo vietas ir datas, iškrovimo vietas ir datas, krovinio savybes, "
        "sutartą pervežimo tarifą, valiutą ir kitus reikalingus duomenis.<br/>"
        "4.2. Už laiku nepateiktą Klientui transporto priemonę, Ekspeditorius Klientui sumoka "
        "100,- EUR.<br/>"
        "4.3. Jeigu pakrovimas neįmanomas dėl Kliento kaltės, jis moka Ekspeditoriui 100,- EUR.<br/>"
        "4.4. Jeigu Ekspeditorius vėluoja laiku pateikti transporto priemonę į pakrovimo vietą, jis "
        "moka Klientui 100,- EUR Europos Sąjungos šalių teritorijoje; vėluojant pateikti transporto "
        "priemonę NVS šalių teritorijoje moka Klientui 100,- EUR.<br/>"
        "4.5. Klientas, gavęs Vežėjo užsakymą, per 24 val. raštu informuoja Ekspeditorių ir perduoda "
        "jam visus reikalingus dokumentus.<br/>"
        "4.6. Jeigu Klientas vėluoja pateikti Ekspeditoriui informaciją ir dokumentus, reikalingus "
        "vykdyti įsipareigojimus pagal šią Sutartį, Klientas moka Ekspeditoriui 100,- EUR už "
        "kiekvieną uždelstą dieną.<br/>"
        "4.7. Jeigu Klientas nevykdo šios Sutarties 5.2. punkto reikalavimų, jis moka Ekspeditoriui "
        "0,2% nuo neapmokėtos sumos už kiekvieną uždelstą dieną.<br/>"
        "4.8. Atliktų darbų priėmimo aktu laikomas krovinio važtaraštis - CMR, pasirašytas gavėjo.<br/>"
        "4.9. Ekspeditorius prisiima atsakomybę prieš užsienio ir NVS šalių krovinių siuntėjus ir "
        "krovinių gavėjus už patikėto krovinio saugumą kelyje ir pristatymą laiku.",
        "<b>4. PROCEDURE OF ORDERING AND DELIVERING OF VEHICLES, RESPONSIBILITIES OF THE PARTIES</b><br/>"
        "4.1. Prior to transportation, the Customer sends an application to the Forwarder's address "
        "with indication of the route of transportation, type of vehicle, date and place of loading, "
        "date and place of unloading, nature of the freight, agreed rate for transportation, "
        "currency of payment and other necessary data.<br/>"
        "4.2. For any failure to submit vehicles to the Customer on the dates agreed with the same, "
        "the Forwarder shall pay 100.- EUR to the Customer.<br/>"
        "4.3. For any failure of the loading the Customer shall pay 100.- EUR to the Forwarder.<br/>"
        "4.4. For any delay in the delivery dates of vehicles by the Forwarder to the place of "
        "loading, the same shall pay 100.- EUR to the Customer in the territory of Europe and "
        "100 EUR in the territory of CIS countries.<br/>"
        "4.5. The Customer informs in writing and hands over to the Forwarder all the necessary "
        "documents within 24 hours from the moment of receipt by the Customer of the Forwarder's "
        "request.<br/>"
        "4.6. For any delay in the time of submission of information and documents according to the "
        "present Contract, the Customer shall pay 100.- EUR to the Forwarder for every day of delay.<br/>"
        "4.7. For any delay of payment according to point 5.2 of the present Contract, the Customer "
        "shall pay to the Forwarder 0.2% of the outstanding sum for every day of delay.<br/>"
        "4.8. The CMR note bearing a mark of the consignee shall be the acceptance act of the works "
        "fulfilled.<br/>"
        "4.9. The Forwarder bears independent responsibility before consignors and consignees abroad "
        "and located in the territory of the CIS, for safety on the way and timeliness of delivery "
        "of the freights transported.",
    ),
    (
        "<b>5. ATSISKAITYMO TVARKA</b><br/>"
        "5.1. Už vežimo paslaugas Klientas atsiskaito Ekspeditoriui pagal iš anksto suderintus "
        "tarifus, savo ruožtu Ekspeditorius pateikia Klientui:<br/>"
        "- sąskaitą-faktūrą (vieną egzempliorių);<br/>"
        "- krovinio gavėjo pasirašytą važtaraščio egzempliorių, patvirtinantį krovinio gavimą.<br/>"
        "5.2. Klientas už vežimo paslaugas atsiskaito pagal pateiktą sąskaitą-faktūrą eurais, ne "
        "vėliau kaip per 30 dienų nuo sąskaitos-faktūros gavimo dienos.<br/>"
        "5.3. Visas piniginių lėšų pervedimo išlaidas apmoka mokėtojas.<br/>"
        "5.4. Jeigu Klientas nevykdo įsipareigojimų pagal šios Sutarties 5.1., 5.2 punktus, "
        "Ekspeditorius turi teisę reikalauti apmokėjimo už uždelstą apmokėti laiką.",
        "<b>5. ORDER OF SETTLEMENTS</b><br/>"
        "5.1. The settlements between the Forwarder and the Customer for freight transportation "
        "shall be effected under the rates agreed in advance upon submission to the Customer of "
        "originals of the following documents:<br/>"
        "- an invoice in one copy;<br/>"
        "- a CMR note bearing a mark of the consignee about receipt of the goods.<br/>"
        "5.2. The payment of the bills for transportation fulfilled shall be effected by the "
        "Customer in EUR not later than 30 days after receiving them from the Forwarder.<br/>"
        "5.3. All the expenditures on remittance of monetary assets are made at the expense of the "
        "payer.<br/>"
        "5.4. In case of non-payment of the bills according to points 5.1, 5.2 of the present "
        "Contract, the Forwarder has the right to submit the payment requirement on collection.",
    ),
    (
        "<b>6. ARBITRAŽAS</b><br/>"
        "6.1. Kilus ginčams Šalys stengsis juos spręsti derybų keliu arba raštu. Jeigu Šalys negali "
        "susitarti derybų keliu, ginčai tarp jų sprendžiami teisme. Teismo vieta nustatoma derybų "
        "keliu.",
        "<b>6. ARBITRATION</b><br/>"
        "6.1. Should any disputes arise, the Parties will strive to settle them through negotiations "
        "or letter exchange. If the Parties fail to reach agreement, the disputes between them shall "
        "be settled by legal proceedings. Place of hearings shall be determined as agreed by the "
        "parties.",
    ),
    (
        "<b>7. SUTARTIES SĄLYGOS IR GALIOJIMAS</b><br/>"
        "7.1. Ši Sutartis įsigalioja ją pasirašius.<br/>"
        "7.2. Ši Sutartis gali būti pakeista, papildyta arba nutraukta tik gavus išankstinį vienos "
        "iš Šalių rašytinį pranešimą prieš 60 dienų. Visi šios Sutarties pakeitimai turi būti "
        "įforminti raštu asmenų, turinčių visus reikiamus įgaliojimus.<br/>"
        "7.3. Šalys patvirtina, kad dokumentai, gauti faksu ir elektroniniu paštu, turi pilną "
        "juridinę galią.<br/>"
        "7.4. Sutartis sudaryta dviem egzemplioriais.",
        "<b>7. CONDITIONS AND DURATION OF THE CONTRACT</b><br/>"
        "7.1. The present Contract shall come into force from the moment of signing.<br/>"
        "7.2. The present Contract may be changed or supplemented at the consent of both Parties, "
        "and terminated before time upon the initiative of any of the Parties under the condition "
        "of prior written notification of the other Party 60 days in advance. All changes and "
        "supplements to the Contract should be made in writing by the persons, especially "
        "authorised thereto.<br/>"
        "7.3. The Parties recognise the legal force of documents transmitted by means of electronic "
        "and facsimile communication.<br/>"
        "7.4. The Contract has been signed in 2 copies.",
    ),
]


def generate_contract_pdf(*, number: str, date: datetime.date, forwarder: dict) -> bytes:
    cell = _style("cell", fontSize=8.5, leading=11.5, alignment=TA_JUSTIFY)
    center = _style("center", fontSize=10.5, leading=13, alignment=TA_CENTER)

    forwarder_name = forwarder["name"]
    director = forwarder.get("director") or ""
    director_lt = f", atstovaujama direktoriaus {director}" if director else ""
    director_en = f", represented by the director {director}" if director else ""

    preamble_lt = (
        f"{forwarder_name}, veikianti pagal įmonės įstatus{director_lt}, toliau vadinamas "
        f'"Ekspeditorius", iš vienos pusės, ir {CONTRACT_CUSTOMER_NAME}, veikianti pagal įmonės '
        'įstatus, atstovaujama generalinio direktoriaus Kim Tkhe Sik, toliau vadinama "Klientas", '
        "sudarė šią sutartį dėl:"
    )
    preamble_en = (
        f'{forwarder_name}, hereinafter referred to as the "Forwarder"{director_en}, acting on the '
        f"grounds of the Statute, on the one hand, and {CONTRACT_CUSTOMER_NAME}, hereinafter "
        'referred to as "the Customer", represented by general manager Kim Tkhe Sik, acting on the '
        "grounds of the Statute, on the other hand, have concluded the present Contract on the "
        "following:"
    )

    forwarder_lines = [forwarder_name]
    forwarder_lines.extend(filter(None, (forwarder.get("address") or "").splitlines()))
    for key, label in (("regon", "REGON"), ("nip", "NIP"), ("krs", "Numer KRS")):
        if forwarder.get(key):
            forwarder_lines.append(f"{label}: {forwarder[key]}")
    forwarder_html = "<br/>".join(forwarder_lines)
    customer_html = "<br/>".join([CONTRACT_CUSTOMER_NAME, *CONTRACT_CUSTOMER_ADDRESS, *CONTRACT_CUSTOMER_BANK])

    rows = [
        [
            Paragraph(f"<b>Transportavimo ir ekspedijavimo paslaugų<br/>sutartis Nr. {number}</b>", center),
            Paragraph(f"<b>CONTRACT No. {number}<br/>on transportation and forwarding services</b>", center),
        ],
        [
            Paragraph(f"Klaipėda, {_date_lt(date)}", cell),
            Paragraph(f"Klaipeda, {_date_en(date)}", cell),
        ],
        [Paragraph(preamble_lt, cell), Paragraph(preamble_en, cell)],
    ]
    rows.extend([Paragraph(lt, cell), Paragraph(en, cell)] for lt, en in _CONTRACT_CLAUSES)
    rows.append(
        [
            Paragraph(
                f"<b>8. JURIDINIAI ŠALIŲ ADRESAI</b><br/><br/><b>Ekspeditorius:</b><br/>{forwarder_html}<br/><br/><b>Klientas:</b><br/>{customer_html}",
                cell,
            ),
            Paragraph(
                f"<b>8. LEGAL ADDRESSES OF THE PARTIES</b><br/><br/><b>Forwarder:</b><br/>{forwarder_html}<br/><br/><b>Customer:</b><br/>{customer_html}",
                cell,
            ),
        ]
    )
    signer_lt = f"Ekspeditoriaus vardu<br/>{director}" if director else "Ekspeditoriaus vardu"
    signer_en = f"On Forwarder's behalf<br/>{director}" if director else "On Forwarder's behalf"
    rows.append(
        [
            Paragraph(
                f"<b>9. Šalių parašai</b><br/><br/>{signer_lt}<br/><br/><br/>_____________________"
                f"<br/><br/>Kliento vardu<br/>{SELLER_PRESIDENT}<br/>General Manager<br/><br/><br/>_____________________",
                cell,
            ),
            Paragraph(
                f"<b>9. SIGNATURES OF THE PARTIES</b><br/><br/>{signer_en}<br/><br/><br/>_____________________"
                f"<br/><br/>On Client's behalf<br/>{SELLER_PRESIDENT}<br/>General Manager<br/><br/><br/>_____________________",
                cell,
            ),
        ]
    )

    table = Table(rows, colWidths=[8.5 * cm, 8.5 * cm], repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return _build_pdf([table], margins=(1.5 * cm, 1.5 * cm, 1.5 * cm, 1.5 * cm))
