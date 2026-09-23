#!/usr/bin/env python3
"""Deterministically generate figure2_tex.png — a 160x96 multi-region atlas.

Four solid-color, untrimmed regions laid out left-to-right (each a separate
SubTexture in figure2_tex.json):

    body        40x80  orange  at (0, 0)
    arm         16x56  blue    at (44, 0)
    eye         16x16  green   at (64, 0)
    eye_closed  16x16  red     at (84, 0)

Pure PIL, transparent background. Run with the pipeline venv:
    pipeline/.venv/bin/python \
        renderer/assets/figure2/make_figure2_png.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ATLAS_W, ATLAS_H = 160, 96

# (name, x, y, w, h, rgba) — must match figure2_tex.json SubTexture rects.
REGIONS = [
    ("body", 0, 0, 40, 80, (255, 140, 0, 255)),      # orange
    ("arm", 44, 0, 16, 56, (30, 90, 220, 255)),      # blue
    ("eye", 64, 0, 16, 16, (30, 180, 60, 255)),      # green
    ("eye_closed", 84, 0, 16, 16, (220, 40, 40, 255)),  # red
]


def main() -> None:
    img = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _name, x, y, w, h, rgba in REGIONS:
        # rectangle is inclusive of both corners, so subtract 1 from far edges.
        draw.rectangle([(x, y), (x + w - 1, y + h - 1)], fill=rgba)
    out = Path(__file__).with_name("figure2_tex.png")
    img.save(out)
    print(f"wrote {out} ({ATLAS_W}x{ATLAS_H}, {len(REGIONS)} regions)")


if __name__ == "__main__":
    main()
