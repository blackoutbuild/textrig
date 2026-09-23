#!/usr/bin/env python3
"""Deterministically generate deform_tex.png — a 64x64 four-quadrant atlas.

Four clearly distinct opaque 32x32 colored quadrants, one SubTexture named
`square_mesh` covering the whole PNG (deform_tex.json). The quadrants let the
eye read WHICH mesh corner an `ffd` frame displaces:

    top-left   red    #d03030   (UV 0.0..0.5 , 0.0..0.5)
    top-right  green  #30c040   (UV 0.5..1.0 , 0.0..0.5)
    bot-left   blue   #3050d0   (UV 0.0..0.5 , 0.5..1.0)
    bot-right  yellow #e0c020   (UV 0.5..1.0 , 0.5..1.0)

Fixture for contract §18 (slot deform / ffd timelines). Pure PIL. Run with the
pipeline venv:
    pipeline/.venv/bin/python \
        renderer/assets/deform/make_deform_png.py
"""
from pathlib import Path

from PIL import Image

ATLAS_W, ATLAS_H = 64, 64

# (x, y, w, h, rgba) quadrants — indexed by UV, y-down.
QUADS = [
    (0, 0, 32, 32, (0xD0, 0x30, 0x30, 255)),   # top-left  red
    (32, 0, 32, 32, (0x30, 0xC0, 0x40, 255)),  # top-right green
    (0, 32, 32, 32, (0x30, 0x50, 0xD0, 255)),  # bot-left  blue
    (32, 32, 32, 32, (0xE0, 0xC0, 0x20, 255)),  # bot-right yellow
]


def main() -> None:
    img = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    for x, y, w, h, rgba in QUADS:
        img.paste(rgba, (x, y, x + w, y + h))
    out = Path(__file__).with_name("deform_tex.png")
    img.save(out)
    print(f"wrote {out} ({ATLAS_W}x{ATLAS_H}, {len(QUADS)} quadrants)")


if __name__ == "__main__":
    main()
