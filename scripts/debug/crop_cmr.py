"""Парные увеличенные фрагменты бланка CMR: эталон сверху, рендер снизу.

Работает по нормализованным рамкам, которые пишет ``render_cmr.py``
(``.cache/cmr_ref_frame.png`` и ``.cache/cmr_frame.png``, обе 1390x2079),
поэтому одни и те же координаты вырезают одну и ту же графу в обоих файлах.
"""

import sys
from pathlib import Path

from PIL import Image

OUT = Path(".cache")
REF = OUT / "cmr_ref_frame.png"
GOT = OUT / "cmr_frame.png"

MM_X = 1390 / 184.2
MM_Y = 2079 / 275.5

# x0, y0, x1, y1 в миллиметрах рамки бланка + масштаб увеличения.
CROPS = {
    "a_head": (0, 0, 184, 26, 3),
    "b_216": (0, 23, 184, 50, 3),
    "c_345": (0, 47, 184, 100, 2),
    "d_goods": (0, 96, 184, 144, 2),
    "e_13_19": (0, 140, 184, 182, 3),
    "f_141520": (0, 172, 184, 200, 3),
    "g_21_24": (0, 196, 184, 229, 3),
    "h_25_27": (0, 226, 184, 253, 4),
    "i_28_29": (0, 250, 184, 277, 3),
    "z_title": (88, 0, 184, 26, 5),
    "z_pay19": (88, 140, 184, 182, 4),
    "z_23": (55, 200, 130, 229, 5),
    "z_24": (124, 196, 184, 229, 5),
    "z_27": (88, 226, 184, 253, 6),
    "z_2829": (0, 250, 120, 277, 4),
}


def main() -> None:
    ref, got = Image.open(REF), Image.open(GOT)
    only = sys.argv[1:]
    for name, (mx0, my0, mx1, my1, scale) in CROPS.items():
        if only and name not in only:
            continue
        x0, x1 = round(mx0 * MM_X), round(mx1 * MM_X)
        y0, y1 = round(my0 * MM_Y), round(my1 * MM_Y)
        w, h = (x1 - x0) * scale, (y1 - y0) * scale
        pair = Image.new("RGB", (w, h * 2 + 6), "#c00")
        for i, src in enumerate((ref, got)):
            part = src.crop((x0, y0, x1, y1)).resize((w, h), Image.LANCZOS)
            pair.paste(part, (0, i * (h + 6)))
        pair.save(OUT / f"{name}.png")
        print(name, pair.size)


if __name__ == "__main__":
    main()
