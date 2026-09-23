"""Seam passes over full-canvas piece masks, between cutting and cropping.

Both passes exist because markup boundaries are edge-to-edge (the markup gate's
linked handles produce exactly that): takeover makes losing source pixels
impossible, underlap gives every seam an inpaintable zone so motion never opens
onto nothing.
"""
import sys

import numpy as np
from scipy import ndimage

ALPHA_THRESHOLD = 8      # cut_pieces convention: a pixel this opaque is content
SOLID_ALPHA = 128        # underlap clamps to the SOLID silhouette, not the glow
UNDERLAP_MARGIN = 6      # px the band stays inside the solid silhouette
TAKEOVER_WARN_PX2 = 32   # uncovered components at least this big get a warning


def _disk(radius: int) -> np.ndarray:
    r = int(radius)
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


def absorb_uncovered(masks: list, eligible: list, alpha: np.ndarray,
                     names: list) -> None:
    """Assign every opaque pixel covered by NO mask to the piece owning the
    nearest covered pixel. masks: full-canvas bool arrays, mutated in place.
    eligible: bool per piece — may RECEIVE pixels (polygon-sourced pieces);
    ineligible masks still count as cover. Warns per component >= 32 px²."""
    if not any(eligible):
        return
    opaque = alpha > ALPHA_THRESHOLD
    # owner id per covered pixel; topmost (later-drawn) piece wins, and only
    # eligible pieces can own — takeover must not route content to file layers
    owner = np.full(alpha.shape, -1, np.int32)
    covered = np.zeros(alpha.shape, bool)
    for i, m in enumerate(masks):
        covered |= m
        if eligible[i]:
            owner[m] = i
    uncovered = opaque & ~covered
    if not uncovered.any():
        return
    has_owner = owner >= 0
    if not has_owner.any():
        return
    _, (iy, ix) = ndimage.distance_transform_edt(
        ~has_owner, return_distances=True, return_indices=True)
    assignee = owner[iy, ix]

    labeled, n_labels = ndimage.label(uncovered)
    for lbl in range(1, n_labels + 1):
        comp = labeled == lbl
        area = int(comp.sum())
        if area >= TAKEOVER_WARN_PX2:
            ys, xs = np.where(comp)
            got = sorted({names[i] for i in np.unique(assignee[comp])})
            print(f"seams: WARNING: {area}px² of opaque source covered by no "
                  f"piece (image bbox [{xs.min()}, {ys.min()}, {xs.max()}, "
                  f"{ys.max()}]) — absorbed into {', '.join(got)}; tighten the "
                  f"markup if the boundary matters", file=sys.stderr)
    for i in range(len(masks)):
        if eligible[i]:
            masks[i] |= uncovered & (assignee == i)


def underlap(masks: list, eligible: list, alpha: np.ndarray, px: int) -> None:
    """Grow each eligible mask `px` into the union of masks ABOVE it (later in
    draw order), clamped to stay UNDERLAP_MARGIN inside the solid silhouette.
    The band's source pixels belong to the upper piece — fill_occlusions sees
    the band as occluded and inpaints it. Mutates masks in place."""
    if px <= 0:
        return
    solid = alpha >= SOLID_ALPHA
    margin = _disk(UNDERLAP_MARGIN)
    room = ndimage.binary_erosion(solid, structure=margin)
    grow = _disk(px)
    above = np.zeros(alpha.shape, bool)
    for i in range(len(masks) - 2, -1, -1):
        above |= masks[i + 1]
        if not eligible[i]:
            continue
        band = ndimage.binary_dilation(masks[i], structure=grow) & above & room
        band &= ~masks[i]
        if band.any():
            masks[i] |= band
