import os

from PIL import Image

SRC = r"C:\Users\art-f\.cursor\projects\c-Users-art-f-PycharmProjects-logist2\assets\cs2_avatar_septer_bigeye.png"
OUT_DIR = r"C:\Users\art-f\.cursor\projects\c-Users-art-f-PycharmProjects-logist2\assets"
LIMIT = 1024 * 1024  # 1024 KB

img = Image.open(SRC).convert("RGB")

# center-crop to square (source is landscape; eye is centered)
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
img = img.crop((left, top, left + side, top + side))

# 1) Try PNG (optimized, downscaled if needed)
png_path = os.path.join(OUT_DIR, "cs2_avatar_septer_final.png")
best_png = None
for size in (1024, 768, 640, 512):
    im = img.resize((size, size), Image.LANCZOS)
    im.save(png_path, format="PNG", optimize=True)
    sz = os.path.getsize(png_path)
    if sz <= LIMIT:
        best_png = (size, sz)
        break

# 2) JPEG fallback / better compression at full 1024 res
jpg_path = os.path.join(OUT_DIR, "cs2_avatar_septer_final.jpg")
best_jpg = None
im1024 = img.resize((1024, 1024), Image.LANCZOS)
for q in (95, 92, 90, 88, 85, 82, 80):
    im1024.save(jpg_path, format="JPEG", quality=q, optimize=True, progressive=True)
    sz = os.path.getsize(jpg_path)
    if sz <= LIMIT:
        best_jpg = (q, sz)
        break

lines = []
if best_png:
    lines.append(f"PNG_OK size={best_png[0]} bytes={best_png[1]} kb={best_png[1] / 1024:.1f}")
else:
    lines.append(f"PNG_FAIL last_bytes={os.path.getsize(png_path)}")
if best_jpg:
    lines.append(f"JPG_OK q={best_jpg[0]} bytes={best_jpg[1]} kb={best_jpg[1] / 1024:.1f}")
else:
    lines.append(f"JPG_FAIL last_bytes={os.path.getsize(jpg_path)}")

result = "\n".join(lines)
print(result)
with open(os.path.join(OUT_DIR, "compress_result.txt"), "w", encoding="utf-8") as f:
    f.write(result + "\n")
