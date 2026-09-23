"""Input normalization: guarantee a usable alpha channel. Cascade: existing alpha
-> uniform-border color key (flood from border) -> rembg (optional dep) -> fail loud."""
import sys

import numpy as np
from PIL import Image
from scipy import ndimage


def fail(msg: str) -> None:
    print(f"normalize_input: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def ensure_alpha(img: Image.Image, bg: str = "auto", tolerance: int = 24,
                 border_uniformity: float = 8.0) -> Image.Image:
    """Return an RGBA image with a meaningful alpha channel.

    bg: auto  — cascade (existing alpha -> uniform-border key -> fail)
        rembg — force rembg (lazy import; fail loud if not installed)
    tolerance: max per-channel distance from the border color to count as background.
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    if bg != "rembg" and (arr[:, :, 3] < 255).mean() > 0.005:
        return rgba                              # meaningful alpha present — trust it

    if bg == "rembg":
        return _rembg(rgba)

    rgb = arr[:, :, :3].astype(int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    if border.std(axis=0).max() > border_uniformity:
        fail("no alpha and background is not a uniform color — raise "
             "--bg-tolerance / border_uniformity if the background is merely "
             "noisy, rerun with --bg rembg, or provide "
             "pre-cut layers")
    bg_color = np.median(border, axis=0)
    close = (np.abs(rgb - bg_color) <= tolerance).all(axis=2)
    seed = np.zeros_like(close)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    seed &= close
    bg_mask = ndimage.binary_propagation(seed, mask=close)
    alpha = np.where(bg_mask, 0.0, 255.0)
    alpha = ndimage.gaussian_filter(alpha, sigma=1.0)     # feather the cut edge
    arr[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _rembg(rgba: Image.Image) -> Image.Image:
    try:
        from rembg import remove
    except ImportError:
        fail("--bg rembg requested but rembg is not installed — run "
             "first-time setup (pipeline/requirements.txt + "
             "pipeline/fetch_models.py)")
    return remove(rgba).convert("RGBA")
