"""Shelf packing (rows, sorted by height desc) + atlas PNG composition.
2px default padding on every side prevents linear-filter bleeding between regions."""
import sys

import numpy as np

# WebGL's max texture size is commonly 8192px (desktop) and can be as low as
# 4096px on older/mobile GPUs. An atlas over either limit doesn't error —
# it renders BLACK, silently, in both the DragonBones runtime and the debug
# runtime. This is the failure class this module's size check exists to kill.
MAX_ATLAS_SIDE = 8192
WARN_ATLAS_SIDE = 4096


def fail(msg: str) -> None:
    print(f"pack_atlas: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def pack(rects, padding=2, max_width=2048):
    """rects: [(name, w, h)]. Returns ({name: (x, y)}, (atlas_w, atlas_h))."""
    seen = set()
    for name, w, h in rects:
        if name in seen:
            fail(f"duplicate region name {name!r} (names must be globally unique)")
        seen.add(name)
        if w + 2 * padding > max_width:
            fail(f"region {name!r} ({w}px) wider than atlas max width {max_width} "
                 f"(raise --atlas-max-width)")
    order = sorted(rects, key=lambda r: -r[2])
    pos = {}
    x = y = shelf_h = 0
    atlas_w = 0
    for name, w, h in order:
        if x + w + 2 * padding > max_width:
            y += shelf_h
            x = shelf_h = 0
        pos[name] = (x + padding, y + padding)
        x += w + 2 * padding
        shelf_h = max(shelf_h, h + 2 * padding)
        atlas_w = max(atlas_w, x)
    return pos, (atlas_w, y + shelf_h)


def compose_atlas(images, padding=2, max_width=2048):
    """images: [(name, HxWx4 uint8)]. Returns (atlas HxWx4, SubTexture dicts)."""
    for name, img in images:
        if img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 4:
            fail(f"region {name!r} must be HxWx4 uint8, got {img.shape} {img.dtype}")
    rects = [(name, img.shape[1], img.shape[0]) for name, img in images]
    pos, (W, H) = pack(rects, padding=padding, max_width=max_width)
    if W > MAX_ATLAS_SIDE or H > MAX_ATLAS_SIDE:
        fail(f"atlas {W}x{H} exceeds the 8192px GPU texture limit — the "
             f"runtime renders BLACK silently; downscale frames, reduce "
             f"keyframes, or split pieces")
    elif W > WARN_ATLAS_SIDE or H > WARN_ATLAS_SIDE:
        print(f"pack_atlas: WARNING: atlas {W}x{H} exceeds 4096px on a side "
              f"— older and mobile GPUs cap texture size there and may "
              f"render BLACK; consider downscaling frames or splitting "
              f"pieces", file=sys.stderr)
    atlas = np.zeros((H, W, 4), np.uint8)
    subtex = []
    for name, img in images:
        x, y = pos[name]
        h, w = img.shape[:2]
        atlas[y:y + h, x:x + w] = img
        subtex.append({"name": name, "x": x, "y": y, "width": w, "height": h})
    return atlas, subtex
