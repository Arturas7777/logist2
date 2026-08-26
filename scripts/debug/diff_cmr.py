"""Числовое сравнение линий эталона CMR и рендера.

Работает по нормализованным рамкам из ``render_cmr.py`` (обе 1390x2079 px на
184.2x275.5 мм), поэтому одни и те же мм-координаты означают одно и то же место
в обоих файлах. Порог адаптивный: фон считается по светлому квантилю окна, а
линией признаётся полоса, где почти вся ширина окна темнее фона. Текст так не
проходит — у него доля тёмных пикселей мала.

Примеры:
    python scripts\\debug\\diff_cmr.py h 0 26 5 85     # линии по Y в графе 1
    python scripts\\debug\\diff_cmr.py v 0 184 60 90   # линии по X в блоке 6-12
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(".cache")
REF = OUT / "cmr_ref_frame.png"
GOT = OUT / "cmr_frame.png"

MM_X = 1390 / 184.2
MM_Y = 2079 / 275.5


def lines(path: Path, axis: str, lo: float, hi: float, a: float, b: float,
          frac: float = 0.7, delta: float = 45):
    """Центры линий в мм: ``axis='h'`` — по Y в окне X=[a,b], 'v' — наоборот.

    ``delta`` — насколько линия должна быть темнее фона. Линовка на скане бледная
    (серее фона всего на 30-40 единиц), для неё порог надо понижать.
    """
    g = Image.open(path).convert("L")
    px = g.load()
    w, h = g.size
    if axis == "h":
        i0, i1 = max(0, round(lo * MM_Y)), min(h, round(hi * MM_Y))
        j0, j1 = max(0, round(a * MM_X)), min(w, round(b * MM_X))
        scale = MM_Y
        get = lambda i, j: px[j, i]  # noqa: E731
    else:
        i0, i1 = max(0, round(lo * MM_X)), min(w, round(hi * MM_X))
        j0, j1 = max(0, round(a * MM_Y)), min(h, round(b * MM_Y))
        scale = MM_X
        get = lambda i, j: px[i, j]  # noqa: E731

    strips = [[get(i, j) for j in range(j0, j1)] for i in range(i0, i1)]
    means = sorted(sum(s) / len(s) for s in strips)
    base = means[int(len(means) * 0.85)]
    cut = base - delta

    hits = [sum(1 for v in s if v < cut) / len(s) for s in strips]
    runs, start = [], None
    for k, d in enumerate([*hits, 0.0]):
        if d >= frac and start is None:
            start = k
        elif d < frac and start is not None:
            mid = i0 + (start + k - 1) / 2
            dark = min(sum(s) / len(s) for s in strips[start:k])
            runs.append((mid / scale, k - start, dark))
            start = None
    return runs


def ink(path: Path, axis: str, at: float, a: float, b: float, half: float = 0.75) -> float:
    """Толщина линии в мм: интеграл затемнения в поперечном профиле.

    Замер по порогу тут врёт: и сканер, и растеризация PDF размывают линию, и
    тонкая чёрная выглядит как широкая серая. Интеграл (фон - яркость) от
    размытия не зависит и даёт толщину, сравнимую с заданной в CSS.

    ``at`` — координата линии в мм, ``a``..``b`` — окно вдоль неё; ``half`` —
    полуширина поперечного окна, его хватает захватить линию целиком.
    """
    g = Image.open(path).convert("L")
    px = g.load()
    w, h = g.size
    scale, along = (MM_Y, MM_X) if axis == "h" else (MM_X, MM_Y)
    i0 = max(0, round((at - half) * scale))
    i1 = min(h if axis == "h" else w, round((at + half) * scale))
    j0, j1 = round(a * along), round(b * along)
    get = (lambda i, j: px[j, i]) if axis == "h" else (lambda i, j: px[i, j])  # noqa: E731

    means = [sum(get(i, j) for j in range(j0, j1)) / (j1 - j0) for i in range(i0, i1)]
    bg = max(means)
    return sum(max(0.0, bg - m) for m in means) / bg / scale


def bbox(path: Path, y0: float, y1: float, x0: float, x1: float, cut: float = 128):
    """Габарит тёмных пикселей в окне (мм) — для сверки кеглей и размеров."""
    g = Image.open(path).convert("L")
    px = g.load()
    i0, i1 = round(y0 * MM_Y), round(y1 * MM_Y)
    j0, j1 = round(x0 * MM_X), round(x1 * MM_X)
    xs = [j for j in range(j0, j1) for i in range(i0, i1) if px[j, i] < cut]
    ys = [i for i in range(i0, i1) for j in range(j0, j1) if px[j, i] < cut]
    if not xs:
        return None
    return min(xs) / MM_X, max(xs) / MM_X, min(ys) / MM_Y, max(ys) / MM_Y


def main() -> None:
    axis = sys.argv[1]
    lo, hi, a, b = (float(v) for v in sys.argv[2:6])
    frac = float(sys.argv[6]) if len(sys.argv) > 6 else 0.7

    if axis == "bbox":
        for tag, path in (("ref   ", REF), ("render", GOT)):
            r = bbox(path, lo, hi, a, b, frac if len(sys.argv) > 6 else 128)
            if r is None:
                print(f"{tag}: empty")
            else:
                print(f"{tag}: x {r[0]:7.2f}..{r[1]:7.2f} ({r[1] - r[0]:6.2f})  y {r[2]:7.2f}..{r[3]:7.2f} ({r[3] - r[2]:5.2f})")
        return

    ref = lines(REF, axis, lo, hi, a, b, frac)
    got = lines(GOT, axis, lo, hi, a, b, frac)
    print(f"{'ref mm':>9}{'px':>4}{'gray':>6} | {'render mm':>9}{'px':>4}{'gray':>6} | {'delta':>7}")
    for k in range(max(len(ref), len(got))):
        r = f"{ref[k][0]:9.2f}{ref[k][1]:4d}{ref[k][2]:6.0f}" if k < len(ref) else " " * 19
        g = f"{got[k][0]:9.2f}{got[k][1]:4d}{got[k][2]:6.0f}" if k < len(got) else " " * 19
        d = f"{got[k][0] - ref[k][0]:7.2f}" if k < len(ref) and k < len(got) else ""
        print(f"{r} | {g} | {d}")
    print(f"lines: ref {len(ref)}, render {len(got)}")


if __name__ == "__main__":
    main()
