"""Cut pieces out of the normalized source image: polygon ∧ alpha, or verbatim
file layers. Loads swap variants. Output: per-piece RGBA + offset + full-canvas mask.

Between mask building and cropping, two seam passes run (seams.py): uncovered
opaque pixels are absorbed by the nearest piece, and each piece's mask grows an
`underlap` band under the pieces drawn above it (fill_occlusions inpaints the
band — its source pixels belong to the upper piece)."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import seams

ALPHA_THRESHOLD = 8            # same convention as mesh_gen.py


def fail(msg: str) -> None:
    print(f"cut_pieces: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def polygon_mask(polygon, shape_hw):
    h, w = shape_hw
    m = Image.new("1", (w, h), 0)
    ImageDraw.Draw(m).polygon([tuple(p) for p in polygon], fill=1)
    return np.array(m, dtype=bool)


def _load_layer(base_dir, entry):
    img = np.array(Image.open(Path(base_dir) / entry["file"]).convert("RGBA"))
    ox, oy = entry["offset"]
    return img, (int(ox), int(oy))


def _full_mask(shape_hw, img, offset, name):
    full = np.zeros(shape_hw, bool)
    ox, oy = offset
    h, w = img.shape[:2]
    ch, cw = shape_hw
    if ox < 0 or oy < 0 or ox + w > cw or oy + h > ch:
        fail(f"piece {name}: layer {w}x{h} at offset ({ox}, {oy}) overruns the "
             f"{cw}x{ch} canvas — fix the offset or crop the layer")
    full[oy:oy + h, ox:ox + w] = img[:, :, 3] > ALPHA_THRESHOLD
    return full


def cut_all(src_rgba: np.ndarray, pieces: list, underlap: int = 30) -> list:
    """src_rgba: HxWx4 uint8 (already normalized). pieces: from pieces.load_pieces.
    Returns list of {name, type, bone?, bones?, mesh?, fill, img, offset, mask_full,
    variants:[{name, img, offset}]} in manifest (= draw) order.

    Pass 1 builds full-canvas masks; the seam passes (absorb uncovered pixels,
    grow the underlap band) adjust polygon-sourced masks; pass 2 crops content
    from the FINAL masks — file layers keep their own content verbatim."""
    shape_hw = src_rgba.shape[:2]
    alpha = src_rgba[:, :, 3]
    masks, layers = [], []
    for p in pieces:
        if "polygon" in p["source"]:
            pmask = polygon_mask(p["source"]["polygon"], shape_hw)
            mask = pmask & (alpha > ALPHA_THRESHOLD)
            if not mask.any():
                fail(f"piece {p['name']}: empty after polygon ∧ alpha — "
                     f"move the polygon onto the silhouette")
            masks.append(mask)
            layers.append(None)
        else:
            img, offset = _load_layer(p["base_dir"], p["source"])
            if not (img[:, :, 3] > ALPHA_THRESHOLD).any():
                fail(f"piece {p['name']}: layer file is fully transparent")
            masks.append(_full_mask(shape_hw, img, offset, p["name"]))
            layers.append((img, offset))

    from_polygon = [layer is None for layer in layers]
    seams.absorb_uncovered(masks, from_polygon, alpha, [p["name"] for p in pieces])
    seams.underlap(masks,
                   [poly and p["fill"] != "none"
                    for poly, p in zip(from_polygon, pieces)],
                   alpha, underlap)

    built = []
    for p, mask_full, layer in zip(pieces, masks, layers):
        if layer is None:
            ys, xs = np.nonzero(mask_full)
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            img = src_rgba[y0:y1, x0:x1].copy()
            img[~mask_full[y0:y1, x0:x1]] = 0
            offset = (x0, y0)
        else:
            img, offset = layer
        variants = []
        for v in p["variants"]:
            vimg, voff = _load_layer(p["base_dir"], v)
            variants.append({"name": v["name"], "img": vimg, "offset": voff})
        built.append({**{k: p[k] for k in p if k not in ("source", "base_dir")},
                      "img": img, "offset": offset, "mask_full": mask_full,
                      "variants": variants})
    return built
