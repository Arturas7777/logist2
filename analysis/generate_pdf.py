"""
Генерация PDF-отчёта по доходам IV за 2025 г. + расчёт налогов.

Использует reportlab + Arial (для поддержки кириллицы и литовских символов).
Результат: analysis/IV_2025_pajamu_ataskaita.pdf
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------- шрифты ---------------------------
pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", r"C:\Windows\Fonts\ariali.ttf"))

FONT = "Arial"
FONT_B = "Arial-Bold"
FONT_I = "Arial-Italic"

# --------------------------- стили ---------------------------
styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", fontName=FONT_B, fontSize=20, leading=24,
                    spaceAfter=6, textColor=colors.HexColor("#0f172a"))
H2 = ParagraphStyle("H2", fontName=FONT_B, fontSize=14, leading=18,
                    spaceBefore=12, spaceAfter=6,
                    textColor=colors.HexColor("#0f172a"))
H3 = ParagraphStyle("H3", fontName=FONT_B, fontSize=11, leading=14,
                    spaceBefore=6, spaceAfter=4,
                    textColor=colors.HexColor("#334155"))
P = ParagraphStyle("P", fontName=FONT, fontSize=10, leading=14,
                   spaceAfter=4, textColor=colors.HexColor("#1f2937"))
SMALL = ParagraphStyle("SMALL", fontName=FONT, fontSize=8.5, leading=11,
                       textColor=colors.HexColor("#64748b"))
NOTE = ParagraphStyle("NOTE", fontName=FONT_I, fontSize=9, leading=12,
                      textColor=colors.HexColor("#475569"),
                      leftIndent=8, spaceAfter=4)
TOTAL = ParagraphStyle("TOTAL", fontName=FONT_B, fontSize=15, leading=20,
                       textColor=colors.HexColor("#15803d"))

ACCENT = colors.HexColor("#2563eb")
SUCCESS = colors.HexColor("#15803d")
WARN = colors.HexColor("#b45309")
MUTED = colors.HexColor("#94a3b8")

# --------------------------- данные ---------------------------

GROSS_INCOME = Decimal("5525.63")
EXPENSE_RATE = Decimal("0.30")
EXPENSE_DEDUCTION = (GROSS_INCOME * EXPENSE_RATE).quantize(Decimal("0.01"))
APM_PAJAMOS = (GROSS_INCOME - EXPENSE_DEDUCTION).quantize(Decimal("0.01"))

GPM_NOMINAL_RATE = Decimal("0.15")
GPM_CREDIT_RATE = Decimal("0.10")  # т.к. apm.pajamos < 20 000 EUR
GPM_NOMINAL = (APM_PAJAMOS * GPM_NOMINAL_RATE).quantize(Decimal("0.01"))
GPM_CREDIT = (APM_PAJAMOS * GPM_CREDIT_RATE).quantize(Decimal("0.01"))
GPM = (GPM_NOMINAL - GPM_CREDIT).quantize(Decimal("0.01"))

SODRA_BASE_RATE = Decimal("0.90")
SODRA_BASE = (APM_PAJAMOS * SODRA_BASE_RATE).quantize(Decimal("0.01"))
VSD_RATE = Decimal("0.1252")
PSD_RATE = Decimal("0.0698")
VSD = (SODRA_BASE * VSD_RATE).quantize(Decimal("0.01"))
PSD = (SODRA_BASE * PSD_RATE).quantize(Decimal("0.01"))

# Месячные минимумы Sodra (база ≈ MMA = 924 €/мес. в 2025 г.)
SODRA_MIN_BASE = Decimal("924.00")
VSD_MIN_MONTHLY = (SODRA_MIN_BASE * VSD_RATE).quantize(Decimal("0.01"))
PSD_MIN_MONTHLY = (SODRA_MIN_BASE * PSD_RATE).quantize(Decimal("0.01"))
VSD_MIN_ANNUAL = VSD_MIN_MONTHLY * 12
PSD_MIN_ANNUAL = PSD_MIN_MONTHLY * 12
SODRA_MIN_ANNUAL = VSD_MIN_ANNUAL + PSD_MIN_ANNUAL


def fmt(amount) -> str:
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{amount:,.2f}"
    return s.replace(",", "\u202f").replace(".", ",") + " €"


# --------------------------- содержимое ---------------------------

incomes_table_data = [
    ["Дата", "Счёт", "Сумма", "Источник", "Назначение"],
    ["28.01.2025", "Revolut USD", fmt("1166.63"), "DANIEL SOLTYS",
     "1 215,75 USD → курс LB 1,0421"],
    ["02.05.2025", "SEB", fmt("50.00"), "Наличные (BRINK'S ATM)", "Grynųjų pinigų įnešimas"],
    ["21.05.2025", "SEB", fmt("1200.00"), "Наличные (BRINK'S ATM)", "Grynųjų pinigų įnešimas"],
    ["15.07.2025", "SEB", fmt("1900.00"), "Наличные (BRINK'S ATM)", "Grynųjų pinigų įnešimas"],
    ["15.07.2025", "SEB", fmt("100.00"), "Наличные (BRINK'S ATM)", "Grynųjų pinigų įnešimas"],
    ["09.09.2025", "Revolut EUR", fmt("650.00"), "DANIEL SOLTYS", "Платёж от частного лица"],
    ["26.11.2025", "Paysera EVP1810…4196", fmt("4.00"), "Aliaksei Nestsiarovich", "«Магазин»"],
    ["04.12.2025", "SEB", fmt("455.00"), "Urasin Ruslan", "Uz paslaugas"],
]

accounts_table_data = [
    ["Счёт", "Доход 2025", "Операций (доход)"],
    ["SEB · LT98…4498", fmt("3705.00"), "5"],
    ["Paysera · EVP6110…5781", fmt("0.00"), "0"],
    ["Paysera · EVP1810…4196", fmt("4.00"), "1"],
    ["Revolut EUR · LT89…7316", fmt("650.00"), "1"],
    ["Revolut USD", fmt("1166.63"), "1"],
    ["ИТОГО", fmt("5525.63"), "8"],
]

excluded_table_data = [
    ["Категория", "Сумма", "Операций", "Почему не доход"],
    ["Переводы между своими счетами", fmt("12946.05"), "152",
     "Перемещения между SEB / Paysera / Revolut одного владельца."],
    ["Возвраты покупок (refund/grąžinimas)", fmt("1892.87"), "12",
     "Возврат денег за товар (Auvika UAB 1 874,16 € + микрорефанды)."],
    ["Пособие на ребёнка (Išmoka vaikui)", fmt("1443.75"), "12",
     "Социальная выплата — не относится к доходу IV."],
    ["Муниципальная компенсация", fmt("1000.00"), "1",
     "Дотация Ignalinos savivaldybė за септик — не доход IV."],
    ["Выигрыши Optibet", fmt("319.00"), "2",
     "Декларируются отдельно (код 42, GPM311)."],
    ["Парная FX-сторона Revolut", fmt("1163.67"), "1",
     "EUR-сторона FX-обмена 1 215,75 USD → EUR (доход уже учтён в USD)."],
    ["Внутренние операции Revolut", fmt("1080.00"), "2",
     "Перемещение между Vault/Pockets/Savings."],
]

base_calc_data = [
    ["Параметр", "Значение"],
    ["Совокупные пайамы IV (gross)", fmt(GROSS_INCOME)],
    ["Норматив сąnaud (30%, без чеков)", "−" + fmt(EXPENSE_DEDUCTION)],
    ["Apmokestinamosios pajamos", fmt(APM_PAJAMOS)],
    ["GPM нормативная база (15%)", fmt(GPM_NOMINAL)],
    ["Mokesčio kreditas (10% × apm.p., apm.p. < 20 000 €)", "−" + fmt(GPM_CREDIT)],
    ["GPM к уплате (эфф. ставка 5%)", fmt(GPM)],
    ["", ""],
    ["Sodra база (90% × apm.p.)", fmt(SODRA_BASE)],
    ["VSD годовой по факту (12,52%)", fmt(VSD)],
    ["PSD годовой по факту (6,98%)", fmt(PSD)],
]

# Сценарии
scenarios = [
    {
        "title": "Сценарий A · IV без работы по найму, без льгот, без минимумов",
        "tag": "наименее распространённый",
        "rows": [
            ("GPM", fmt(GPM)),
            ("VSD (12,52% × 90% × apm.p.)", fmt(VSD)),
            ("PSD (6,98% × 90% × apm.p.)", fmt(PSD)),
        ],
        "total": GPM + VSD + PSD,
        "note": ("Работает только если за 2025 год вы НЕ платили ежемесячные "
                 "минимумы Sodra (например, IV не был активен большинство месяцев или "
                 "вы были застрахованы средствами государства)."),
    },
    {
        "title": "Сценарий B · Платили ежемесячные минимумы Sodra (≈ 180,24 €/мес × 12)",
        "tag": "наиболее частый случай для одиночного IV",
        "rows": [
            (f"Уже уплачено за год: VSD min ({fmt(VSD_MIN_MONTHLY)} × 12)",
             fmt(VSD_MIN_ANNUAL)),
            (f"Уже уплачено за год: PSD min ({fmt(PSD_MIN_MONTHLY)} × 12)",
             fmt(PSD_MIN_ANNUAL)),
            ("Годовой VSD по факту (435,84 €) < уплачено — переплата НЕ возвращается", "—"),
            ("Годовой PSD по факту (242,98 €) < уплачено — переплата НЕ возвращается", "—"),
            ("К доплате с декларацией: только GPM", fmt(GPM)),
        ],
        "total": SODRA_MIN_ANNUAL + GPM,
        "note": ("В этом случае фактический налоговый «бюджет» за 2025 год — это сумма "
                 "ежемесячно уплаченных Sodra-минимумов плюс GPM. Доход 5 525,63 € "
                 "слишком мал, чтобы превысить минимальную базу 924 €/мес, поэтому "
                 "минимумы не возмещаются."),
    },
    {
        "title": "Сценарий C · Параллельная работа по найму (работодатель платил Sodra)",
        "tag": "если на основной работе VSD и PSD уже шли с зарплаты",
        "rows": [
            ("GPM по IV", fmt(GPM)),
            ("VSD по IV (доплата при декларации)", fmt(VSD)),
            ("PSD по IV (доплата при декларации, если работодатель не покрывал IV)",
             fmt(PSD)),
        ],
        "total": GPM + VSD + PSD,
        "note": ("Если работодатель полностью покрывал PSD по основному месту — "
                 "PSD по IV в годовой декларации может быть пересчитан/уменьшен. "
                 "VSD по IV почти всегда платится поверх зарплатных взносов. "
                 "Точный итог уточняйте в Mano Sodra."),
    },
]


# --------------------------- сборка PDF ---------------------------

def make_table(data, col_widths, header_bg=colors.HexColor("#1e293b"),
               header_fg=colors.white, total_row=False,
               highlight_row_idx: int | None = None) -> Table:
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8fafc")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cbd5e1")),
        ("LINEBELOW", (0, "splitlast"), (-1, "splitlast"), 0.25,
         colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), header_fg),
        ("FONTNAME", (0, 0), (-1, 0), FONT_B),
    ]
    if total_row:
        style_cmds.extend([
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dbeafe")),
            ("FONTNAME", (0, -1), (-1, -1), FONT_B),
        ])
    if highlight_row_idx is not None:
        style_cmds.append(
            ("BACKGROUND", (0, highlight_row_idx), (-1, highlight_row_idx),
             colors.HexColor("#fef3c7")))
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 10 * mm,
                      "Доход IV 2025 · подготовлено для GPM311 · автогенерация")
    canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Стр. {doc.page}")
    canvas.restoreState()


def build():
    out_path = Path(__file__).parent / "IV_2025_pajamu_ataskaita.pdf"
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Доход IV за 2025 — расчёт для GPM311",
        author="autogenerated", subject="GPM311 / Individuali veikla 2025",
    )
    story: list = []

    # === Заголовок и резюме ===
    story.append(Paragraph("Individuali veikla — доход и налоги за 2025 год", H1))
    story.append(Paragraph(
        "Анализ 5 банковских выписок (SEB, Paysera ×2, Revolut EUR, Revolut USD) за период "
        "01.01.2025 – 31.12.2025. Расчёт налогов выполнен по фиксированному "
        "30 % нормативу расходов (без чеков), для IV pagal pažymą.", P))
    story.append(Spacer(1, 6))

    # «карточка» с итоговой суммой
    summary_data = [
        ["Совокупный доход 2025 (gross)", fmt(GROSS_INCOME)],
        ["Apmokestinamosios pajamos (после 30 % нормы)", fmt(APM_PAJAMOS)],
        ["Эффективный налог (Сценарий A — годовой расчёт)",
         f"{fmt(GPM + VSD + PSD)}  ({((GPM + VSD + PSD) / GROSS_INCOME * 100):.1f} %)"],
    ]
    summary_t = Table(summary_data, colWidths=[110 * mm, 60 * mm])
    summary_t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("FONTNAME", (1, 0), (1, -1), FONT_B),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#cbd5e1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#dcfce7")),
    ]))
    story.append(summary_t)
    story.append(Spacer(1, 12))

    # === Доходы по операциям ===
    story.append(Paragraph("1. Все операции, признанные доходом", H2))
    story.append(make_table(
        incomes_table_data,
        col_widths=[22 * mm, 32 * mm, 24 * mm, 42 * mm, 50 * mm],
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Все операции — это входящие зачисления от внешних плательщиков. Перевод "
        "1 215,75 USD от DANIEL SOLTYS пересчитан в EUR по официальному курсу "
        "Lietuvos banko на 28.01.2025 (1 EUR = 1,0421 USD).", SMALL))
    story.append(Spacer(1, 10))

    # === Сводка по счетам ===
    story.append(Paragraph("2. Сводка по счетам", H2))
    story.append(make_table(
        accounts_table_data,
        col_widths=[80 * mm, 50 * mm, 40 * mm],
        total_row=True,
    ))
    story.append(Spacer(1, 10))

    # === Что исключено ===
    story.append(Paragraph("3. Что исключено из доходов и почему", H2))
    story.append(make_table(
        excluded_table_data,
        col_widths=[55 * mm, 25 * mm, 18 * mm, 75 * mm],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Дополнительно: переводы между собственными счетами идентифицированы по списку "
        "IBAN/EVP владельца (LT98…4498, LT53…5781, LT71…4196, LT89…7316, "
        "EVP6110…5781, EVP1810…4196) и по совпадению имени плательщика "
        "(Arturas Haizhutsis / HAIZHUTSIS ARTURAS).", SMALL))
    story.append(PageBreak())

    # === Налоговая база ===
    story.append(Paragraph("4. Расчёт налоговой базы", H2))
    story.append(Paragraph(
        "Применён нормативный вычет 30 % (без подтверждающих чеков). "
        "Apmokestinamosios pajamos = 70 % × gross.", P))
    story.append(make_table(
        base_calc_data,
        col_widths=[120 * mm, 50 * mm],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "GPM считается по прогрессивной формуле: nominal 15 % минус mokesčio kreditas. "
        "Поскольку apmokestinamosios pajamos = 3 867,94 € < 20 000 €, кредит максимальный "
        "(10 % от apm. pajamos), эффективная ставка GPM = 5 %.", SMALL))
    story.append(Spacer(1, 10))

    # === Сценарии ===
    story.append(Paragraph("5. Налоговая нагрузка по сценариям", H2))
    story.append(Paragraph(
        "Итоговая сумма налогов зависит от того, платили ли вы ежемесячные минимумы "
        "Sodra и был ли у вас параллельный работодатель.", P))

    for sc in scenarios:
        block: list = []
        block.append(Paragraph(sc["title"], H3))
        block.append(Paragraph(f"<i>{sc['tag']}</i>", NOTE))
        sc_data = [["Компонент", "Сумма"]]
        for k, v in sc["rows"]:
            sc_data.append([k, v])
        sc_data.append(["ИТОГО к оплате за 2025 г.", fmt(sc["total"])])
        block.append(make_table(
            sc_data,
            col_widths=[125 * mm, 45 * mm],
            header_bg=colors.HexColor("#334155"),
            total_row=True,
        ))
        block.append(Paragraph(sc["note"], SMALL))
        block.append(Spacer(1, 10))
        story.append(KeepTogether(block))

    story.append(Spacer(1, 6))

    # === Что отдельно по азартным играм ===
    story.append(Paragraph("6. Отдельно — выигрыши Optibet", H2))
    story.append(Paragraph(
        "Выигрыши Optibet (Baltic Bet UAB) на 319 € — это <b>azartinių lošimų laimėjimai</b>, "
        "класс B доходов. Декларируются отдельно в <b>GPM311</b> по коду <b>42</b>. "
        "Налоговая база — <i>netto</i> = (выигрыши за год) − (ставки за год). "
        "Если netto ≤ 0 — налога нет и можно не декларировать. Если netto > 0 — "
        "ставка GPM 15 %.", P))
    story.append(Paragraph(
        "Из банковской выписки невозможно вычислить netto — нужно скачать годовой "
        "отчёт с Optibet (статистика ставок и выигрышей за период 01.01.2025 – 31.12.2025).",
        SMALL))
    story.append(Spacer(1, 10))

    # === Чеклист ===
    story.append(Paragraph("7. Чеклист перед подачей GPM311", H2))
    checklist = [
        "Сверить 4 пополнения наличными BRINK'S ATM (3 250 €) с записями в "
        "Pajamų-išlaidų žurnalas и кассовыми чеками (kasos kvitas).",
        "Проверить, был ли DANIEL SOLTYS бизнес-клиентом — счета-фактуры по обеим "
        "выплатам (1 215,75 USD в январе и 650 € в сентябре).",
        "Скачать годовой отчёт Optibet и посчитать netto (выигрыши − ставки).",
        "Проверить, нет ли других валютных «карманов» Revolut "
        "(PLN/BYN/GBP) с поступлениями.",
        "В Mano Sodra сверить, какие именно VSD/PSD взносы вы уже уплатили за 2025 год.",
        "При наличии III-pakopos pensijų fondas — приложить 15 % grąžinimas.",
        "Подача и оплата GPM311: до 4 мая 2026 (1 мая — праздник).",
    ]
    for item in checklist:
        story.append(Paragraph("• " + item, P))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Дисклеймер: документ — автоматический расчёт по банковским выпискам, "
        "не является налоговой консультацией. Точные обязательства лучше "
        "сверить в Mano VMI / Mano Sodra или с бухгалтером.", NOTE))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(f"PDF saved: {out_path}")


if __name__ == "__main__":
    build()
