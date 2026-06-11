# -*- coding: utf-8 -*-
"""Одноразовый скрипт сжатия PNG-иконок в core/static/icons.

Иконки отображаются в админке мелкими (шапки карточек ~300-600px),
а исходники лежат по 4-5 MB. Ресайзим до max 640px по большей стороне,
квантизируем в палитру (с сохранением альфа-канала) и пересохраняем
с optimize=True. Запуск: .venv\\Scripts\\python.exe scripts\\compress_icons.py
"""
import os
import sys

from PIL import Image

ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'core', 'static', 'icons')
MAX_SIDE = 640

def compress_png(path: str) -> tuple[int, int]:
    before = os.path.getsize(path)
    img = Image.open(path)
    img.load()

    if max(img.size) > MAX_SIDE:
        ratio = MAX_SIDE / max(img.size)
        new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    # Квантизация в 256 цветов: для иконок потери незаметны, выигрыш в разы
    if img.mode in ('RGBA', 'RGB', 'P'):
        quant = img.convert('RGBA').quantize(colors=256, method=Image.FASTOCTREE)
        quant.save(path, optimize=True)
        # Если палитра дала артефакты по размеру (редко) — оставляем RGBA-оптимизацию
        if os.path.getsize(path) > before:
            img.save(path, optimize=True)
    else:
        img.save(path, optimize=True)

    return before, os.path.getsize(path)

def main() -> None:
    total_before = total_after = 0
    for root, _dirs, files in os.walk(ICONS_DIR):
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            path = os.path.join(root, fname)
            try:
                before, after = compress_png(path)
            except Exception as exc:  # noqa: BLE001
                print(f'SKIP {fname}: {exc}')
                continue
            total_before += before
            total_after += after
            if before != after:
                print(f'{fname}: {before / 1e6:.2f} MB -> {after / 1e6:.2f} MB')
    print(f'TOTAL: {total_before / 1e6:.1f} MB -> {total_after / 1e6:.1f} MB')

if __name__ == '__main__':
    sys.exit(main())
