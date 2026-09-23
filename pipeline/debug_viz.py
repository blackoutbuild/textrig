"""Debug visualization for the multi-piece pipeline: a single overlay PNG
showing piece cutout footprints + bone gizmos on top of the source art, plus
per-piece cutout PNGs. Debug aid only — not shipped, not art."""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
]


def _font(px: int):
    try:
        return ImageFont.load_default(size=px)   # Pillow >= 10.1: scalable default
    except Exception:
        return ImageFont.load_default()


def render_debug(src_rgba: np.ndarray, pieces_list: list, built: list, bones: list, out_path) -> None:
    """src_rgba: HxWx4 uint8 normalized source. pieces_list: pieces.load_pieces
    output (has 'source' with polygon|file). built: cut_pieces.cut_all output
    (has 'offset' + 'img' for the bbox fallback). bones: skeleton bone list.
    Writes a flattened RGBA PNG to out_path. Gizmos scale with image width so
    labels stay legible on large canvases (e.g. 896x1200)."""
    h, w = src_rgba.shape[:2]
    s = max(12, w // 45)                          # scale factor: font px + gizmo base
    line_w = max(2, s // 6)
    joint_r = max(3, s // 4)
    base = Image.fromarray(src_rgba, "RGBA")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(s)
    by_source = {p["name"]: p.get("source", {}) for p in pieces_list}

    for i, bp in enumerate(built):
        rgb = ImageColor.getrgb(PALETTE[i % len(PALETTE)])
        fill = rgb + (90,)
        outline = rgb + (255,)
        src = by_source.get(bp["name"], {})
        if "polygon" in src:
            poly = [tuple(pt) for pt in src["polygon"]]
            draw.polygon(poly, fill=fill, outline=outline)
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
        else:
            ox, oy = bp["offset"]
            bh, bw = bp["img"].shape[:2]
            draw.rectangle([ox, oy, ox + bw, oy + bh], fill=fill, outline=outline)
            cx, cy = ox + bw / 2.0, oy + bh / 2.0
        draw.text((cx, cy), bp["name"], fill=(255, 255, 255, 255), font=font)

    for i, b in enumerate(bones):
        rgb = ImageColor.getrgb(PALETTE[(i + 3) % len(PALETTE)])
        x, y = b["x"], b["y"]
        length = max(b.get("length", 0) or 0, s)
        rot = math.radians(b["rotation"])
        ex, ey = x + length * math.cos(rot), y + length * math.sin(rot)
        draw.line([x, y, ex, ey], fill=rgb + (255,), width=line_w)
        draw.ellipse([x - joint_r, y - joint_r, x + joint_r, y + joint_r], fill=rgb + (255,))
        draw.text((x + joint_r + s // 4, y - s), b["name"], fill=rgb + (255,), font=font)

    Image.alpha_composite(base, overlay).save(out_path)


def save_cutouts(built: list, out_dir) -> None:
    """Write one PNG per piece (and per swap variant) into out_dir, named after
    the atlas region name (piece.name / variant.name)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in built:
        Image.fromarray(p["img"], "RGBA").save(out_dir / f"{p['name']}.png")
        for v in p.get("variants", []):
            Image.fromarray(v["img"], "RGBA").save(out_dir / f"{v['name']}.png")
