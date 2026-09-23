#!/usr/bin/env python3
"""Deterministically generate zorder_tex.png — an 80x40 two-region atlas.

Two solid-color, opaque 40x40 squares laid out left-to-right (each a separate
SubTexture in zorder_tex.json):

    back   40x40  blue #2040c0  at (0,  0)
    front  40x40  red  #c03030  at (40, 0)

Fixture for contract §15 (animated draw order / zOrder timeline). Pure PIL,
transparent background outside the squares. Run with the pipeline venv:
    pipeline/.venv/bin/python \
        renderer/assets/zorder/make_zorder_png.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ATLAS_W, ATLAS_H = 80, 40

# (name, x, y, w, h, rgba) — must match zorder_tex.json SubTexture rects.
REGIONS = [
    ("back", 0, 0, 40, 40, (0x20, 0x40, 0xC0, 255)),   # blue #2040c0
    ("front", 40, 0, 40, 40, (0xC0, 0x30, 0x30, 255)),  # red  #c03030
]


def main() -> None:
    img = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _name, x, y, w, h, rgba in REGIONS:
        # rectangle is inclusive of both corners, so subtract 1 from far edges.
        draw.rectangle([(x, y), (x + w - 1, y + h - 1)], fill=rgba)
    out = Path(__file__).with_name("zorder_tex.png")
    img.save(out)
    print(f"wrote {out} ({ATLAS_W}x{ATLAS_H}, {len(REGIONS)} regions)")


if __name__ == "__main__":
    main()
