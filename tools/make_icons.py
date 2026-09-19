#!/usr/bin/env python3
"""Generate the Aether icon set from the rounded-A monogram mark.

The mark is a single geometric "A" — two round-capped strokes plus a crossbar,
accent #9aa0f5 on the dark #1e1e21 plate. Same construction at every size, so
it stays legible at 16px (the tab icon) and calm at 512px (the PWA splash).

Geometry (identical to icons/favicon.svg and the inline SVG in index.html):

    plate    rounded rect 0,0 24x24, rx = 5.4
    ascent   5.4,19.8 -> 12,4.2 -> 18.6,19.8   (stroked, round caps + joins)
    crossbar 8.1,14.6 -> 15.9,14.6

Every raster is drawn at an integer supersample factor (up to 64x for the small
sizes, capped so the working canvas stays around 4096px) and finished with
LANCZOS, so the strokes land cleanly on the pixel grid instead of turning grey.

    .venv/bin/python tools/make_icons.py

Writes frontend/icons/{favicon-16,favicon-32,apple-touch-icon,icon-192,
icon-512,splash-1170x2532,splash-1284x2778}.png
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

BG = (30, 30, 33, 255)          # --bg0 #1e1e21
ACCENT = (154, 160, 245, 255)   # #9aa0f5

PLATE_RADIUS = 5.4 / 24.0       # rx as a fraction of the square
STROKE = 2.6                    # stroke width in the 24x24 space

ASCENT = [(5.4, 19.8), (12.0, 4.2), (18.6, 19.8)]   # left stem, apex, right stem
CROSSBAR = [(8.1, 14.6), (15.9, 14.6)]

try:                            # Pillow >= 9.1
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:          # pragma: no cover - older Pillow
    LANCZOS = Image.LANCZOS

MAX_CANVAS = 4096               # keeps 512px icons affordable while still supersampled


def supersample(size: int) -> int:
    """Integer supersample factor: 64x for 16px, capped for the big sizes."""
    return max(1, min(64, MAX_CANVAS // max(1, size)))


def _scale(points: list[tuple[float, float]], factor: float,
           cx: float = 12.0, cy: float = 12.0) -> list[tuple[float, float]]:
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points]


def _stroke(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]],
            width: float, fill: tuple[int, int, int, int]) -> None:
    """A polyline with round joins and round caps (Pillow's line() is butt-ended)."""
    draw.line(points, fill=fill, width=max(1, int(round(width))), joint="curve")
    radius = width / 2.0
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)


def mark_image(size: int, *, plate: bool = True, mark_scale: float = 1.0,
               background: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Render the monogram at `size` px (square)."""
    ss = supersample(size)
    canvas = size * ss
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    unit = canvas / 24.0

    if plate or background is not None:
        fill = background or BG
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1],
                               radius=int(round(canvas * PLATE_RADIUS)), fill=fill)

    draw = ImageDraw.Draw(img)
    ascent = [(x * unit, y * unit) for x, y in _scale(ASCENT, mark_scale)]
    crossbar = [(x * unit, y * unit) for x, y in _scale(CROSSBAR, mark_scale)]
    _stroke(draw, ascent, STROKE * unit, ACCENT)
    _stroke(draw, crossbar, STROKE * unit, ACCENT)

    return img.resize((size, size), LANCZOS)


def splash(width: int, height: int, logo: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), BG)
    mark = mark_image(logo, plate=False, mark_scale=1.0)
    img.alpha_composite(mark, ((width - logo) // 2, (height - logo) // 2))
    return img


def main() -> None:
    out = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "icons"
    out.mkdir(parents=True, exist_ok=True)

    # Tab icons: the plate is part of the mark, so they match favicon.svg exactly.
    mark_image(16).save(out / "favicon-16.png")
    mark_image(32).save(out / "favicon-32.png")

    # App icons: full-bleed plate (maskable) with the monogram inside the safe zone.
    mark_image(180, mark_scale=0.74).save(out / "apple-touch-icon.png")
    mark_image(192, mark_scale=0.74).save(out / "icon-192.png")
    mark_image(512, mark_scale=0.74).save(out / "icon-512.png")

    splash(1170, 2532, 240).save(out / "splash-1170x2532.png")
    splash(1284, 2778, 260).save(out / "splash-1284x2778.png")

    for name in sorted(p.name for p in out.iterdir()):
        print(f"  {name}")


if __name__ == "__main__":
    main()
