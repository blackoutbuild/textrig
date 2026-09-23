#!/usr/bin/env python3
"""Deterministically generate deform_swap_tex.png — a 128x64 TWO-region atlas
for the §11+§18 stepped-display-swap fixture.

ONE slot ("square") carries TWO unweighted mesh displays that share geometry
(a 3x3 grid, -32..32) but bind to DIFFERENT atlas regions, so a stepped
`displayFrame` swap is unmistakable to the eye AND the deform of each display
reads off its own quadrant colors:

  region "sq0" (x 0..64)   — the classic four quadrants:
    top-left red, top-right green, bot-left blue, bot-right yellow
  region "sq1" (x 64..128) — a DISTINCT palette so the swap pops:
    top-left cyan, top-right magenta, bot-left orange, bot-right purple

Fixture proves: (a) the swap is a HARD cut (colors flip in one frame, no blend);
(b) each display's own `ffd` timeline deforms IT (sq0 pulls its bottom-left
corner, sq1 its top-right corner) before AND after the swap; (c) contract §18a's
runtime re-match — an inactive display's deformVertices keep tracking so the
pose is CORRECT the instant it becomes active. Pure PIL; run with the venv:
    pipeline/.venv/bin/python \
        renderer/assets/deform-swap/make_deform_swap_png.py
"""
from pathlib import Path

from PIL import Image

ATLAS_W, ATLAS_H = 128, 64

# (x, y, w, h, rgba). Left half = sq0, right half = sq1 (shifted at x+64).
QUADS = [
    # sq0 — red / green / blue / yellow
    (0, 0, 32, 32, (0xD0, 0x30, 0x30, 255)),    # top-left  red
    (32, 0, 32, 32, (0x30, 0xC0, 0x40, 255)),   # top-right green
    (0, 32, 32, 32, (0x30, 0x50, 0xD0, 255)),   # bot-left  blue
    (32, 32, 32, 32, (0xE0, 0xC0, 0x20, 255)),  # bot-right yellow
    # sq1 — cyan / magenta / orange / purple
    (64, 0, 32, 32, (0x20, 0xC0, 0xC0, 255)),   # top-left  cyan
    (96, 0, 32, 32, (0xD0, 0x30, 0xC0, 255)),   # top-right magenta
    (64, 32, 32, 32, (0xE0, 0x70, 0x20, 255)),  # bot-left  orange
    (96, 32, 32, 32, (0x70, 0x40, 0xD0, 255)),  # bot-right purple
]


def main() -> None:
    img = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    for x, y, w, h, rgba in QUADS:
        img.paste(rgba, (x, y, x + w, y + h))
    out = Path(__file__).with_name("deform_swap_tex.png")
    img.save(out)
    print(f"wrote {out} ({ATLAS_W}x{ATLAS_H}, {len(QUADS)} quadrants)")


if __name__ == "__main__":
    main()
