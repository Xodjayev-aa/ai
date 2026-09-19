#!/usr/bin/env python3
"""Generate the Aether icon set from the hand-crafted geometric mark.

The mark is a pointy-top hexagon (the "prism") with a triangular core knocked
out of it — one accent colour (#9aa0f5) on dark charcoal (#1e1e21), geometric,
no gradients, and still legible at 16px because the same construction is used
at every size (the small sizes just drop the plate and keep a heavier core).

    .venv/bin/python tools/make_icons.py

Writes frontend/icons/{favicon-16,favicon-32,apple-touch-icon,icon-192,
icon-512,splash-1170x2532,splash-1284x2778}.png
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

BG = (30, 30, 33, 255)          # --bg0 #1e1e21
ACCENT = (154, 160, 245, 255)   # #9aa0f5
SS = 8                          # supersample factor for clean edges

# 24x24 geometry (identical to the inline SVG in index.html).
HEXAGON = [(12.0, 2.5), (20.3, 7.3), (20.3, 16.7), (12.0, 21.5), (3.7, 16.7), (3.7, 7.3)]
TRIANGLE = [(12.0, 6.9), (16.4, 15.6), (7.6, 15.6)]


def _scaled(points: list[tuple[float, float]], factor: float) -> list[tuple[float, float]]:
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points]


def mark_image(size: int, *, padding: float = 0.05, core: float = 0.64,
               background: tuple[int, int, int, int] | None = None,
               radius: float | None = None) -> Image.Image:
    """Render the prism mark. `core` is the knockout size relative to the triangle."""
    canvas = size * SS
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    if background is not None:
        plate = Image.new("RGBA", (canvas, canvas), background)
        if radius:
            mask = Image.new("L", (canvas, canvas), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, canvas - 1, canvas - 1], radius=int(canvas * radius), fill=255)
            img.alpha_composite(Image.composite(plate, Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0)), mask))
        else:
            img.alpha_composite(plate)

    draw = ImageDraw.Draw(img)
    unit = (canvas * (1 - padding * 2)) / 24.0
    offset = (canvas - 24 * unit) / 2.0

    def point(p):
        return (offset + p[0] * unit, offset + p[1] * unit)

    draw.polygon([point(p) for p in HEXAGON], fill=ACCENT)
    draw.polygon([point(p) for p in _scaled(TRIANGLE, core)], fill=(0, 0, 0, 0))
    return img.resize((size, size), Image.LANCZOS)


def splash(width: int, height: int, logo: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), BG)
    img.alpha_composite(mark_image(logo, padding=0.0, core=0.62),
                        ((width - logo) // 2, (height - logo) // 2))
    return img


def main() -> None:
    out = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "icons"
    out.mkdir(parents=True, exist_ok=True)

    # Small sizes: no plate — the accent shape itself fills the tab icon.
    mark_image(16, padding=0.02, core=0.60).save(out / "favicon-16.png")
    mark_image(32, padding=0.06, core=0.62).save(out / "favicon-32.png")
    # App icons: accent mark on the dark app plate, inside the maskable safe zone.
    mark_image(180, padding=0.26, core=0.64, background=BG).save(out / "apple-touch-icon.png")
    mark_image(192, padding=0.26, core=0.64, background=BG).save(out / "icon-192.png")
    mark_image(512, padding=0.26, core=0.64, background=BG).save(out / "icon-512.png")
    splash(1170, 2532, 240).save(out / "splash-1170x2532.png")
    splash(1284, 2778, 260).save(out / "splash-1284x2778.png")

    for name in sorted(p.name for p in out.iterdir()):
        print(f"  {name}")


if __name__ == "__main__":
    main()
