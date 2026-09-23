#!/usr/bin/env python3
"""Deterministically generate path_tex.png — a 36x12 three-region atlas.

Three solid-color, opaque 12x12 squares laid out left-to-right (each a
separate SubTexture in path_tex.json):

    seg1  12x12  red    #c03030  at (0,  0)
    seg2  12x12  green  #30c040  at (12, 0)
    seg3  12x12  blue   #2040c0  at (24, 0)

Fixture for contract §16 (path display + path constraint). Pure PIL,
transparent background outside the squares. Run with the pipeline venv:
    pipeline/.venv/bin/python \
        renderer/assets/path/make_path_png.py

--- arc-length snippet (source of the "lengths" values in path_ske.json) ---
The curve in path_ske.json is two cubic beziers through world anchors
(64,100) -> (84,60) -> (64,20), a rightward bow:
    seg0: P0=(64,100) C1=(72,84) C2=(84,76) P1=(84,60)
    seg1: P0=(84,60)  C1=(84,44) C2=(72,36) P1=(64,20)
Per-segment arc length is measured by sampling each cubic at 64 points;
"lengths" is CUMULATIVE (last entry = total path length):

    def cubic(p0, c1, c2, p1, t):
        u = 1 - t
        return tuple(
            u*u*u*p0[i] + 3*u*u*t*c1[i] + 3*u*t*t*c2[i] + t*t*t*p1[i]
            for i in (0, 1))

    def arclen(p0, c1, c2, p1, n=64):
        pts = [cubic(p0, c1, c2, p1, k / n) for k in range(n + 1)]
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))

    s0 = arclen((64,100), (72,84), (84,76), (84,60))   # 45.413502
    s1 = arclen((84,60), (84,44), (72,36), (64,20))    # 45.413502
    lengths = [s0, s0 + s1]                            # [45.4135, 90.827]

Run this file with --lengths to print them.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ATLAS_W, ATLAS_H = 36, 12

# (name, x, y, w, h, rgba) — must match path_tex.json SubTexture rects.
REGIONS = [
    ("seg1", 0, 0, 12, 12, (0xC0, 0x30, 0x30, 255)),   # red   #c03030
    ("seg2", 12, 0, 12, 12, (0x30, 0xC0, 0x40, 255)),  # green #30c040
    ("seg3", 24, 0, 12, 12, (0x20, 0x40, 0xC0, 255)),  # blue  #2040c0
]

# The two cubic segments of the fixture curve (world/armature space, y down).
CURVE = [
    ((64, 100), (72, 84), (84, 76), (84, 60)),
    ((84, 60), (84, 44), (72, 36), (64, 20)),
]


def cubic(p0, c1, c2, p1, t):
    u = 1 - t
    return tuple(
        u * u * u * p0[i] + 3 * u * u * t * c1[i] + 3 * u * t * t * c2[i] + t * t * t * p1[i]
        for i in (0, 1))


def arclen(p0, c1, c2, p1, n=64):
    pts = [cubic(p0, c1, c2, p1, k / n) for k in range(n + 1)]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def main() -> None:
    if "--lengths" in sys.argv:
        total = 0.0
        for i, seg in enumerate(CURVE):
            s = arclen(*seg)
            total += s
            print(f"seg{i}: {s:.6f}  cumulative: {total:.6f}")
        return
    img = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _name, x, y, w, h, rgba in REGIONS:
        # rectangle is inclusive of both corners, so subtract 1 from far edges.
        draw.rectangle([(x, y), (x + w - 1, y + h - 1)], fill=rgba)
    out = Path(__file__).with_name("path_tex.png")
    img.save(out)
    print(f"wrote {out} ({ATLAS_W}x{ATLAS_H}, {len(REGIONS)} regions)")


if __name__ == "__main__":
    main()
