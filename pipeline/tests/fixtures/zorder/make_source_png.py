"""Generates source.png for the zorder golden fixture: an 80x70 transparent
canvas with a blue square at image (10,10)-(50,50) and a red square at
(30,30)-(70,70), the red square painted OVER the blue one in the 20x20
overlap — i.e. this is the "rest look" (back=blue drawn first, front=red
drawn on top). Deterministic / byte-identical on every run.
"""
import numpy as np
from PIL import Image
from pathlib import Path

W, H = 80, 70
BLUE = (0x20, 0x40, 0xc0, 255)
RED = (0xc0, 0x30, 0x30, 255)

img = np.zeros((H, W, 4), np.uint8)          # fully transparent background
img[10:50, 10:50] = BLUE                      # blue square (10,10)-(50,50)
img[30:70, 30:70] = RED                       # red square (30,30)-(70,70), on top

Image.fromarray(img, "RGBA").save(Path(__file__).resolve().parent / "source.png")
