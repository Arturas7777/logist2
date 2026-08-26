"""Рендер бланка CMR в PNG и сборка сравнения с эталонным сканом.

Шаблон рендерится минимальным Django-движком (без БД), результат снимается
headless Chrome и склеивается с эталоном для визуальной сверки. Рамка бланка на
обеих картинках ищется автоматически и приводится к одному размеру, поэтому
мм-координаты в них означают одно и то же место.

Эталон — ``.cache/ref2.jpg``: скан заполненного бланка, распечатанного
программой «Muitinė». Кладётся в кеш вручную (путь до вложения в чате длиннее
лимита Windows, и PIL его не открывает).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import django
from django.conf import settings
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".cache"
HTML = OUT / "cmr_preview.html"
SHOT = OUT / "cmr_render.png"
SIDE = OUT / "cmr_compare.png"
REF = OUT / "ref2.jpg"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

# 96 dpi: 1 мм = 3.779528 px, лист A4 = 793.7 x 1122.5 px. Снимаем с scale=2,
# иначе волосяные линии бланка теряются при ресемплинге.
SHOT_W, SHOT_H = 794, 1123
SCALE = 2

# Скриншот снимаем на экране, а сверяемся со сканом бумажного бланка, поэтому в
# превью подменяем экранные толщины печатными. Значения не дублируем, а
# вынимаем из @media print самого шаблона: иначе они разъезжаются, и сверка
# идёт по толщинам скрипта, а не по тем, что реально уйдут на бумагу.
# Обёртка @media screen обязательна — без неё правило перебивает блок печати,
# и print_cmr.py печатает толщины превью.
PREVIEW_CSS = """
<style>
  body { background: #fff !important; }
  .cmr-toolbar, .cmr-flash, .cmr-hint { display: none !important; }
  .cmr-wrap { padding: 0 !important; }
  .cmr-sheet { box-shadow: none !important; margin: 0 !important; }
  @media screen { .cmr-sheet { %s } }
</style>
"""
PRINT_VARS = re.compile(r"--rule:[^;]+;\s*--base:[^;]+;\s*--bold:[^;]+;")


def render_html(blank: bool) -> str:
    settings.configure(
        DEBUG=True,
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(ROOT / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
    )
    django.setup()
    from django.template.loader import render_to_string

    sys.path.insert(0, str(ROOT))
    from core.services.cmr import CMR_KEYS, pay_rows

    data = {key: "" for key in CMR_KEYS}
    if not blank:
        data.update(
            {
                "sender": "UAB CARIBIA\nVilniaus g. 1, Vilnius\nLietuva\nĮm.k. 305000000, PVM LT305000000",
                "consignee": "OOO TRANSBEL\nMinsk, Nezavisimosti 1\nBaltarusija\nĮm.k. 123456789",
                "carrier": "MAXER TRANSPORT Sp. z o.o.\nul. Testowa 5, Warszawa\nLenkija",
                "delivery_place": "Minsk",
                "delivery_country": "Baltarusija",
                "takeover_place": "Klaipeda Terminal, Minijos g. 180",
                "takeover_country": "Lietuva",
                "takeover_date": "01.09.2026",
                "annexed_docs": "T1, Title",
                "marks": "1G1ZD5ST0PF171248",
                "packages": "1",
                "goods_nature": "NAUDOTAS AUTOMOBILIS / USED VEHICLE\nChevrolet Malibu 2023\nVIN 1G1ZD5ST0PF171248",
                "weight_kg": "1850",
                "sender_instructions": "Siena / Border: Medininkai\nTranzitas",
                "drivers": "Jonas Petraitis",
                "truck_reg": "ABC123",
                "trailer_reg": "XYZ789",
                "truck_type": "DAF XF",
                "trailer_type": "Krone",
                "established_place": "Klaipėda",
                "established_date": "01.09.2026",
                "pay_carriage_sender": "1200",
                "pay_carriage_sender_c": "00",
                "pay_carriage_currency": "EUR",
                "pay_total_sender": "1200,00",
                "pay_total_currency": "EUR",
                "journey_sheet": "LT-4417",
                "journey_sheet_year": "26",
            }
        )

    html = render_to_string(
        "admin/cmr_editor.html",
        {
            "d": data,
            "pay_rows": pay_rows(data),
            "car": {"vin": "1G1ZD5ST0PF171248", "brand": "Chevrolet Malibu"},
            "transport_request": {"number": "TR-000123"},
            "list_url": "#",
            "card_url": "#",
            "auto_print": False,
            "messages": [],
        },
    )
    return html


def preview_html(blank: bool) -> str:
    """HTML для скриншота: то же, но экранные толщины подменены печатными."""
    html = render_html(blank)
    found = PRINT_VARS.findall(html)
    if len(found) != 1:
        raise SystemExit(f"в шаблоне не одно объявление толщин, а {len(found)}")
    return html.replace("</head>", PREVIEW_CSS % found[0] + "</head>")


def shoot() -> None:
    subprocess.run(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={SCALE}",
            f"--window-size={SHOT_W},{SHOT_H}",
            f"--screenshot={SHOT}",
            HTML.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


FRAME_W, FRAME_H = 1390, 2079


def _frame_box(img: Image.Image) -> tuple[int, int, int, int]:
    """Габарит рамки бланка: крайние строка/столбец с длинной чёрной линией."""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    rows = [y for y in range(h) if sum(1 for x in range(w) if px[x, y] < 140) > w * 0.6]
    cols = [x for x in range(w) if sum(1 for y in range(h) if px[x, y] < 140) > h * 0.6]
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


def compare() -> None:
    src = Image.open(REF).convert("RGB")
    ref_box = _frame_box(src)
    print(f"ref frame box = {ref_box}")
    ref = src.crop(ref_box).resize((FRAME_W, FRAME_H), Image.LANCZOS)
    shot = Image.open(SHOT).convert("RGB")
    box = _frame_box(shot)
    print(f"render frame box = {box}")
    got = shot.crop(box).resize((FRAME_W, FRAME_H), Image.LANCZOS)

    side = Image.new("RGB", (FRAME_W * 2 + 8, FRAME_H), "#888")
    side.paste(ref, (0, 0))
    side.paste(got, (FRAME_W + 8, 0))
    side.save(SIDE)

    # Наложение: эталон — красным каналом, рендер — зелёным.
    r, g = ref.convert("L"), got.convert("L")
    Image.merge("RGB", (r, g, Image.new("L", (FRAME_W, FRAME_H), 255))).save(OUT / "cmr_overlay.png")
    ref.save(OUT / "cmr_ref_frame.png")
    got.save(OUT / "cmr_frame.png")
    print(f"side -> {SIDE}\noverlay -> {OUT / 'cmr_overlay.png'}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    HTML.write_text(preview_html(blank="--blank" in sys.argv), encoding="utf-8")
    shoot()
    compare()
    print(f"html -> {HTML}\nshot -> {SHOT}")


if __name__ == "__main__":
    main()
