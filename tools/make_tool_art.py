#!/usr/bin/env python3
"""Generate mood art for every tool from the Claude mascot set.

Each Claude mood image has its laptop/mug logos replaced with a neon glyph
for the tool (glyph colour sampled from the original logo, so the mood's
colour story is kept), and the "cooking" image is recoloured to the tool's
brand-ish colour. Run from the repo root:  python3 tools/make_tool_art.py
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
import numpy as np

IMG = Path(__file__).resolve().parent.parent / "images"

# (cx, cy, r) of every logo to replace, in 512x512 image coordinates.
LOGOS = {
    "cooking": [(230, 394, 62)],
    "waiting": [(68, 390, 44)],
    "success": [(296, 380, 46), (72, 394, 30)],
    "failed":  [(228, 400, 46)],
    "idle":    [(260, 418, 36), (56, 388, 28)],
    "crew":    [(132, 354, 30), (244, 256, 24), (368, 354, 40)],
    "push":    [(318, 404, 42)],
}
MOODS = ["cooking", "waiting", "success", "failed", "idle", "crew", "push"]

COLORS = {"chatgpt": "#10B981", "gemini": "#8B5CF6", "muse": "#0866FF", "grok": "#FBBF24",
          "copilot": "#6366F1", "opencode": "#A3E635", "aider": "#22C55E",
          "cursor": "#E5E7EB", "windsurf": "#14B8A6", "kiro": "#C026D3", "vscode": "#0EA5E9"}


def debug():
    out = Image.new("RGB", (512 * 4, 512 * 2))
    for i, m in enumerate(MOODS):
        im = Image.open(IMG / f"claude_{m}.jpg").convert("RGB")
        d = ImageDraw.Draw(im)
        for cx, cy, r in LOGOS[m]:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 255, 255), width=3)
        out.paste(im, ((i % 4) * 512, (i // 4) * 512))
    out.resize((1024, 512)).save(sys.argv[2])


# ------------------------------------------------------------ glyphs ----
# Each draws white strokes on a square canvas; coordinates are -1..1.

def _P(sz, x, y):
    return (sz / 2 + x * sz * 0.42, sz / 2 + y * sz * 0.42)


def g_chatgpt(d, sz, w):    # six-loop rosette knot
    for k in range(6):
        a = k * math.pi / 3
        pts = []
        for t in range(0, 361, 8):
            u = math.radians(t)
            x, y = 0.55 * math.cos(u), 0.28 * math.sin(u)
            x += 0.42
            pts.append(_P(sz, x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)))
        d.line(pts, fill="white", width=w, joint="curve")


def g_gemini(d, sz, w):     # four-pointed sparkle
    pts = []
    for t in range(0, 360, 3):
        u = math.radians(t)
        r = 1.0 / (abs(math.cos(u)) ** 0.5 + abs(math.sin(u)) ** 0.5) ** 2 * 1.05
        pts.append(_P(sz, r * math.cos(u), r * math.sin(u)))
    d.polygon(pts, fill="white")


def g_muse(d, sz, w):       # infinity loop
    pts = [_P(sz, 0.95 * math.cos(t) / (1 + math.sin(t) ** 2),
              0.95 * math.sin(t) * math.cos(t) / (1 + math.sin(t) ** 2))
           for t in [i * math.pi / 60 for i in range(121)]]
    d.line(pts, fill="white", width=w, joint="curve")


def g_grok(d, sz, w):       # event-horizon ring with a diagonal streak
    a, b = _P(sz, -0.62, -0.62), _P(sz, 0.62, 0.62)
    d.ellipse([a, b], outline="white", width=w)
    d.line([_P(sz, -0.95, 0.95), _P(sz, 0.95, -0.95)], fill="white", width=int(w * 1.1))


def g_copilot(d, sz, w):    # pilot goggles
    for sx in (-1, 1):
        d.rounded_rectangle([_P(sz, sx * 0.08 if sx > 0 else -0.92, -0.42),
                             _P(sz, 0.92 if sx > 0 else -0.08, 0.38)], radius=sz * 0.1,
                            outline="white", width=w)
    d.line([_P(sz, -0.25, 0.75), _P(sz, 0.25, 0.75)], fill="white", width=w)


def g_opencode(d, sz, w):   # { } braces
    for sx in (-1, 1):
        x0 = 0.75 * sx
        pts = [_P(sz, x0 * 0.55, -0.85), _P(sz, x0 * 0.75, -0.8), _P(sz, x0 * 0.75, -0.15),
               _P(sz, x0, 0), _P(sz, x0 * 0.75, 0.15), _P(sz, x0 * 0.75, 0.8), _P(sz, x0 * 0.55, 0.85)]
        d.line(pts, fill="white", width=w, joint="curve")


def g_aider(d, sz, w):      # two overlapping speech bubbles
    d.rounded_rectangle([_P(sz, -0.95, -0.85), _P(sz, 0.35, -0.05)], radius=sz * 0.08,
                        outline="white", width=w)
    d.rounded_rectangle([_P(sz, -0.35, 0.05), _P(sz, 0.95, 0.85)], radius=sz * 0.08,
                        outline="white", width=w)
    d.line([_P(sz, 0.55, 0.85), _P(sz, 0.75, 1.05)], fill="white", width=w)


def g_cursor(d, sz, w):     # arrow pointer
    pts = [_P(sz, -0.55, -0.9), _P(sz, 0.6, 0.15), _P(sz, 0.05, 0.2), _P(sz, 0.35, 0.85),
           _P(sz, 0.12, 0.95), _P(sz, -0.18, 0.32), _P(sz, -0.55, 0.65)]
    d.line(pts + [pts[0]], fill="white", width=w, joint="curve")


def g_windsurf(d, sz, w):   # sail over a wave
    d.line([_P(sz, -0.1, 0.35), _P(sz, -0.1, -0.95), _P(sz, 0.6, 0.35), _P(sz, -0.1, 0.35)],
           fill="white", width=w, joint="curve")
    pts = [_P(sz, -0.95 + i * 0.05, 0.65 + 0.15 * math.sin(i * 0.5)) for i in range(39)]
    d.line(pts, fill="white", width=w, joint="curve")


def g_kiro(d, sz, w):       # friendly ghost
    top = [_P(sz, 0.7 * math.cos(math.radians(t)), -0.15 - 0.75 * math.sin(math.radians(t)))
           for t in range(0, 181, 6)]
    bottom = [_P(sz, -0.7 + i * 0.05, 0.75 + 0.12 * math.sin(i * 0.9)) for i in range(29)]
    d.line(top + [_P(sz, -0.7, 0.75)] + bottom + [_P(sz, 0.7, -0.15)], fill="white",
           width=w, joint="curve")
    for ex in (-0.28, 0.28):
        a, b = _P(sz, ex - 0.12, -0.25), _P(sz, ex + 0.12, 0.05)
        d.ellipse([a, b], fill="white")


def g_vscode(d, sz, w):     # </> chevrons
    d.line([_P(sz, -0.35, -0.6), _P(sz, -0.9, 0), _P(sz, -0.35, 0.6)], fill="white", width=w, joint="curve")
    d.line([_P(sz, 0.35, -0.6), _P(sz, 0.9, 0), _P(sz, 0.35, 0.6)], fill="white", width=w, joint="curve")
    d.line([_P(sz, 0.18, -0.85), _P(sz, -0.18, 0.85)], fill="white", width=w)


GLYPHS = {k[2:]: v for k, v in globals().items() if k.startswith("g_")}


# ------------------------------------------------------------ painting ----

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def logo_color(im, cx, cy, r):
    """Mean of the brightest pixels inside the old logo: its glow colour."""
    a = np.asarray(im.crop((cx - r, cy - r, cx + r, cy + r)).convert("RGB")).reshape(-1, 3).astype(float)
    v = a.max(axis=1)
    top = a[v >= np.percentile(v, 88)]
    c = top.mean(axis=0)
    return tuple(int(x) for x in np.clip(c * (255 / max(c.max(), 1)), 0, 255))


def lid_color(im, cx, cy, r):
    """Dark laptop-lid colour from a ring just outside the logo."""
    a = np.asarray(im.convert("RGB")).astype(float)
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    d = np.hypot(xx - cx, yy - cy)
    ring = a[(d > r * 1.05) & (d < r * 1.5)]
    dark = ring[ring.max(axis=1) < np.percentile(ring.max(axis=1), 40)]
    return tuple(int(x) for x in np.median(dark if len(dark) else ring, axis=0))


def paint_glyph(im, tool, cx, cy, r, color):
    # 1) cover the old logo with the lid colour, feathered
    cover = Image.new("RGB", im.size, lid_color(im, cx, cy, r))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).ellipse([cx - r * 1.08, cy - r * 1.08, cx + r * 1.08, cy + r * 1.08], fill=255)
    im = Image.composite(cover, im, mask.filter(ImageFilter.GaussianBlur(r * 0.12)))
    # 2) draw the glyph supersampled, then glow + core
    S = 4
    sz = int(r * 2 * S)
    layer = Image.new("L", (sz, sz), 0)
    GLYPHS[tool](ImageDraw.Draw(layer), sz, max(2, int(r * 0.15 * S)))
    layer = layer.resize((int(r * 2), int(r * 2)), Image.LANCZOS)
    full = Image.new("L", im.size, 0)
    full.paste(layer, (int(cx - r), int(cy - r)))
    glow = full.filter(ImageFilter.GaussianBlur(r * 0.22))
    col = Image.new("RGB", im.size, color)
    im = Image.composite(col, im, glow.point(lambda v: min(255, int(v * 1.6))))
    core = tuple(int(c + (255 - c) * 0.45) for c in color)
    return Image.composite(Image.new("RGB", im.size, core), im, full)


def recolor(im, hexcol):
    """Rotate every saturated pixel's hue to the tool colour."""
    import colorsys
    tr, tg, tb = [c / 255 for c in hex_rgb(hexcol)]
    th, ts, _ = colorsys.rgb_to_hsv(tr, tg, tb)
    hsv = np.asarray(im.convert("HSV")).astype(float)
    sat = hsv[..., 1] > 40
    hsv[..., 0][sat] = th * 255
    if ts < 0.2:  # silver/white brands
        hsv[..., 1] *= 0.15
    return Image.fromarray(hsv.astype("uint8"), "HSV").convert("RGB")


def make(tool, mood):
    im = Image.open(IMG / f"claude_{mood}.jpg").convert("RGB")
    if mood == "cooking":
        im = recolor(im, COLORS[tool])
    for cx, cy, r in LOGOS[mood]:
        color = hex_rgb(COLORS[tool]) if mood == "cooking" else logo_color(
            Image.open(IMG / f"claude_{mood}.jpg"), cx, cy, r)
        im = paint_glyph(im, tool, cx, cy, r, color)
    return im


def main(tools, preview=None):
    made = []
    for tool in tools:
        for mood in MOODS:
            if tool == "chatgpt" and mood in ("cooking", "waiting", "idle"):
                continue  # hand-made art exists
            out = IMG / f"{tool}_{mood}.jpg"
            make(tool, mood).save(out, "JPEG", quality=88, optimize=True, progressive=True)
            made.append(out)
    if preview:
        cols = len(MOODS)
        sheet = Image.new("RGB", (160 * cols, 160 * len(tools)))
        for i, tool in enumerate(tools):
            for j, mood in enumerate(MOODS):
                f = IMG / f"{tool}_{mood}.jpg"
                if f.exists():
                    sheet.paste(Image.open(f).resize((160, 160)), (j * 160, i * 160))
        sheet.save(preview)
    print(f"wrote {len(made)} images")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--debug":
        debug()
    else:
        args = [a for a in sys.argv[1:] if not a.startswith("--preview=")]
        prev = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--preview=")), None)
        main(args or list(COLORS), prev)
