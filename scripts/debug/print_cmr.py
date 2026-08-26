"""Печать бланка CMR в PDF так, как это делает браузер, и сверка линовки.

Chrome печатает без фоновых картинок (галка «Фоновая графика» в диалоге печати
по умолчанию снята), поэтому линовка должна быть нарисована элементами. Скрипт
печатает шаблон в PDF, растрирует первую страницу и считает горизонтальные линии
в графах — если линовка снова уедет в фон, число линий упадёт.

Запуск: ``python scripts\\debug\\print_cmr.py``
"""

import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import diff_cmr as D
import render_cmr as R

OUT = Path(".cache").resolve()
PDF = OUT / "cmr_print.pdf"
PNG = OUT / "cmr_print_frame.png"

# Графа: окно (y0, y1, x0, x1) в мм рамки и сколько линий там должно быть.
BOXES = {
    "1-5 (левая колонка)": (0.0, 99.5, 4, 85, 25),
    "16-18 (правая колонка)": (24.0, 99.5, 97, 179, 19),
    "6-12 (грузы)": (99.0, 143.5, 4, 179, 12),
    "19 (платежи)": (142.0, 181.0, 114, 134, 10),
    # Окно начинается ниже 143.6: там проходит линия стыка полос, и при 143.5
    # детектор считал её линией графы.
    "13 (указания)": (144.0, 181.0, 4, 85, 9),
    "15/20 (условия)": (181.0, 199.0, 4, 85, 4),
    "25-27 (транспорт)": (227.0, 252.5, 4, 40, 6),
}

# Жирный контур граф 16-18, 19 и 23 нарисован накладками поверх соседних линий:
# по этим координатам уже идёт базовая линия, и своя рамка дала бы двойную
# толщину. Накладка обязана быть рамкой, а не заливкой, иначе браузер её не
# напечатает и контур пропадёт. Линия | ось | координата мм | окно вдоль неё.
CONTOUR = {
    "низ рамки 16-18": ("h", 98.79, 100, 170),
    "правый край вдоль 16-18": ("v", 183.9, 30, 95),
    "правый край вдоль 19": ("v", 183.9, 150, 178),
    "левый край графы 19": ("v", 91.8, 149, 179),
    "низ графы 19 под итогом": ("h", 180.4, 95, 182),
    "делитель валюты в строке итога": ("v", 147.9, 176.5, 179.5),
    "верх шапки графы 19": ("h", 147.9, 96, 180),
    "верх рамки графы 23": ("h", 202.8, 62, 122),
}
# Линии, которые легко утолстить по ошибке: рядом с ними идёт жирный контур.
THIN = {
    "низ шапки графы 19": ("h", 152.5, 96, 180),
    "делитель граф 16/17": ("h", 49.4, 96, 180),
}
BOLD_MIN = 0.72  # мм: базовая линия 0.58, жирная 0.87


def main() -> None:
    # Имя файла обязано меняться от запуска к запуску: постоянное Chrome берёт
    # из кеша профиля и печатает прошлую разметку, молча и без ошибки.
    for stale in OUT.glob("cmr_print_*.html"):
        stale.unlink()
    src = OUT / f"cmr_print_{time.time_ns()}.html"
    src.write_text(R.render_html(blank="--blank" in sys.argv), encoding="utf-8")
    subprocess.run(
        [str(R.CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         f"--user-data-dir={OUT / 'chrome-pdf'}", f"--print-to-pdf={PDF}", src.as_uri()],
        check=True, capture_output=True,
    )
    import fitz
    from PIL import Image

    raw = OUT / "cmr_print_raw.png"
    fitz.open(PDF)[0].get_pixmap(dpi=190).save(raw)
    img = Image.open(raw).convert("RGB")
    img.crop(R._frame_box(img)).resize((R.FRAME_W, R.FRAME_H), Image.LANCZOS).save(PNG)
    print(f"pdf -> {PDF}\npng -> {PNG}")

    bad = 0
    for tag, (lo, hi, a, b, want) in BOXES.items():
        got = len(D.lines(PNG, "h", lo, hi, a, b, frac=0.72, delta=16))
        mark = "ок" if got == want else "ПРОПАЛИ ЛИНИИ"
        bad += got != want
        print(f"   {tag:<24} линий {got:3d} из {want:3d}  {mark}")

    for tag, (axis, at, a, b) in CONTOUR.items():
        got = D.ink(PNG, axis, at, a, b)
        mark = "ок" if got >= BOLD_MIN else "НЕ ЖИРНАЯ"
        bad += got < BOLD_MIN
        print(f"   {tag:<24} толщина {got:.2f} мм  {mark}")

    for tag, (axis, at, a, b) in THIN.items():
        got = D.ink(PNG, axis, at, a, b)
        mark = "ок" if got < BOLD_MIN else "ЛИШНЯЯ ЖИРНАЯ"
        bad += got >= BOLD_MIN
        print(f"   {tag:<24} толщина {got:.2f} мм  {mark}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
