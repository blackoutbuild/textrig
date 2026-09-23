"""For every piece: find zones covered by pieces drawn above it (later in list)
and repaint them. In-place.

Two repaint paths, selected per connected component of the occluded zone:

- **extend** (`_extend_fill`): nearest-valid-pixel extension + a local masked
  blur. Battle-proven on thin seams (a few px deep — hoses, eye overlaps):
  copies the nearest own-pixel color verbatim, then softens it. On
  limb-sized occlusions this streaks whatever minority off-tone pixels sit
  right at the boundary (dark outline, rim-light glow) verbatim deep into
  the interior, because nearest-neighbor lookup is local and doesn't blend
  across the boundary.
- **inpaint** (`_diffuse_fill`): harmonic diffusion. Initializes from the
  extend result, then iteratively relaxes each occluded pixel to the masked
  average of its 3x3 neighborhood (own-alpha support only). Valid pixels are
  never written by the relaxation (`_relax` only ever assigns into the
  occluded mask), so they stay pinned automatically — an implicit Dirichlet
  boundary, not an explicit re-pin step. Dilutes minority boundary colors
  instead of streaking them — used for occlusions too deep for a single
  nearest-neighbor lookup to be plausible. Naive (single-resolution) Jacobi
  relaxation needs ~O(depth^2) rounds to propagate boundary information all
  the way across a component, which is fine for a shallow/synthetic-sized
  component but far too slow for a 100px+-deep limb-sized one. So
  `_diffuse_level` solves coarse-to-fine: components bigger than
  `DIFFUSE_PYRAMID_MIN` are first solved at half resolution (recursively,
  down to a base case at or below `DIFFUSE_PYRAMID_MIN` that gets a full
  `DIFFUSE_MAX_ITER`/`DIFFUSE_TOL` solve), and that solution is upsampled as
  each finer level's initial guess before a short, FIXED-length local
  refinement (`DIFFUSE_REFINE_ITER` rounds — not a full re-solve at every
  level, only the base level gets that). The restriction that builds each
  coarser level is VALID-PRESERVING: a coarse pixel is valid (Dirichlet-
  fixed) if ANY of its 2x2 children is valid, colored by the support-weighted
  average of ONLY its valid children; it's occluded only where NO child is
  valid. This is what makes the boundary survive every halving instead of
  being absorbed into "everything occluded" at the first level (which
  degenerates into an unanchored blur toward the local mean — see
  `_diffuse_level`'s docstring for the fuller account, including why a
  naive `any()`-on-occluded restriction breaks this).

`mode="auto"` (default) picks per connected component of a piece's occluded
zone: max distance-to-nearest-valid-pixel (EDT) <= DEEP_OCCLUSION_PX uses
extend, deeper components use diffusion. `mode="extend"`/`mode="inpaint"`
force the respective path for every component (e.g. to A/B compare, or to
force detail-preserving extend back on for a component auto misjudged).

The blur (in `_masked_blur`, applied once at the end over the union of all
occluded pixels regardless of which path filled them) is a normalized
(masked) convolution over the piece's own alpha support, so transparent
black pixels contribute zero weight — a plain blur would drag the fill
toward black at the piece's alpha edge.

Upper pieces' swap-variant footprints are unioned into the occlusion mask as
a defensive over-fill: the source image must show every swap piece in its BASE
state, but a variant can be larger than the base and would otherwise reveal
unfilled pixels when swapped in.
"""
import sys

import numpy as np
from scipy import ndimage

ALPHA_THRESHOLD = 8
DEEP_OCCLUSION_PX = 16   # component EDT depth above which auto switches to diffusion
WARN_OCCLUSION_PX = 64   # deeper than any legit joint seam — warn: invented content
DIFFUSE_MAX_ITER = 1500  # iteration cap for the coarsest (base) level's full solve
DIFFUSE_TOL = 0.01       # early-stop when max per-round change (0-255 scale) drops below this
DIFFUSE_PYRAMID_MIN = 48  # below this longest-side (px), relax directly at full res (base level)
DIFFUSE_REFINE_ITER = 40  # short local-smoothing budget for every level ABOVE the base


def _variant_mask(shape_hw, variant):
    """Full-canvas alpha footprint of a swap variant, clipped to the canvas
    (variant bounds are not validated upstream, unlike base layers)."""
    m = np.zeros(shape_hw, bool)
    img = variant["img"]
    ox, oy = variant["offset"]
    h, w = img.shape[:2]
    ch, cw = shape_hw
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + w, cw), min(oy + h, ch)
    if x0 >= x1 or y0 >= y1:
        return m
    m[y0:y1, x0:x1] = img[y0 - oy:y1 - oy, x0 - ox:x1 - ox, 3] > ALPHA_THRESHOLD
    return m


def _extend_fill(rgb, occl, iy, ix):
    """Nearest-valid-pixel extension, in place over `rgb` (float array),
    restricted to `occl`. `iy`/`ix` are the EDT nearest-valid-pixel index
    arrays for every position (from `distance_transform_edt(~valid,
    return_indices=True)`). Today's original algorithm, unchanged."""
    rgb[occl] = rgb[iy[occl], ix[occl]]


def _masked_blur(rgb, occl, own, blur_sigma):
    """Normalized (masked) gaussian blur over `own`-alpha support, applied
    to `occl` positions only. Today's original algorithm, unchanged."""
    support = own.astype(float)
    den = ndimage.gaussian_filter(support, blur_sigma)
    blurred = np.stack(
        [ndimage.gaussian_filter(rgb[:, :, c] * support, blur_sigma)
         for c in range(3)], axis=2) / np.maximum(den, 1e-6)[:, :, None]
    rgb[occl] = blurred[occl]


def _relax(cur, mc, support, max_iter, tol):
    """One resolution level of Jacobi relaxation: each round, every `mc`
    pixel becomes the `support`-masked 3x3 average of its neighborhood
    (which includes both still-untouched valid pixels and already-updated
    occluded pixels — non-`mc` positions are never written to, so they stay
    pinned automatically without an explicit re-pin step). Capped iterations
    with early stop once the max per-round change is small. Returns a new
    array; does not mutate `cur`."""
    cur = cur.copy()
    if not mc.any():
        return cur
    for _ in range(max_iter):
        den = ndimage.uniform_filter(support, size=3, mode="constant", cval=0.0)
        num = np.stack(
            [ndimage.uniform_filter(cur[:, :, c] * support, size=3, mode="constant", cval=0.0)
             for c in range(3)], axis=2)
        avg = num / np.maximum(den, 1e-6)[:, :, None]
        new = cur.copy()
        new[mc] = avg[mc]
        delta = np.abs(new[mc] - cur[mc]).max()
        cur = new
        if delta < tol:
            break
    return cur


def _diffuse_level(cur, mc, support, max_iter, tol, min_size, refine_iter=DIFFUSE_REFINE_ITER):
    """Coarse-to-fine Jacobi: naive relaxation needs ~O(depth^2) rounds to
    propagate boundary information all the way across a component, which is
    cheap for seam-sized components but far too slow (and, capped at
    `max_iter`, would simply under-converge) for limb-sized ones — a 150px+
    deep component would need tens of thousands of rounds. So: if this level
    is still bigger than `min_size`, first solve a half-resolution copy
    (recursively, same rule) using a VALID-PRESERVING restriction, upsample
    that solution as THIS level's initial guess for its occluded pixels only
    (valid pixels keep their real values), then relax locally with only
    `refine_iter` rounds — a short SMOOTHING pass, not a full solve. Only the
    base level (already <= `min_size`, no more halving possible) gets the
    full `max_iter`/`tol` budget, because that's the one level whose own
    equation actually needs solving from scratch; every level above it only
    needs to clean up the blockiness introduced by upsampling (`np.repeat`)
    a good coarse answer, which takes a handful of rounds, not O(size^2).

    Valid-preserving restriction is the part that makes the boundary survive
    every halving: a coarse pixel is VALID (Dirichlet-fixed, never written by
    `_relax`) if ANY of its 2x2 children is valid (`support`-own and not
    `mc`), and its color is the support-weighted average of ONLY those valid
    children — never diluted by a sibling occluded child's not-yet-solved
    value. A coarse pixel is occluded (`mc`, writable) only where NO child is
    valid, i.e. the whole 2x2 block is either interior-occluded or entirely
    outside the piece's own support. Using `any()` on `mc` alone (as an
    earlier version of this function did) discards the boundary at the very
    first halving — a 1px-wide Dirichlet ring paired against any neighboring
    occluded pixel marks the WHOLE coarse pixel occluded, so every level past
    the first has no boundary at all and just relaxes towards the local mean.
    With the fix, the coarse solve's occluded seed already carries the right
    large-scale gradient, so only a short local refinement is needed at each
    level, not a full O(size^2) solve."""
    if max(cur.shape[0], cur.shape[1]) > min_size:
        H, W = cur.shape[:2]
        Hp, Wp = H + (H % 2), W + (W % 2)
        pad_hw = ((0, Hp - H), (0, Wp - W))
        supp_p = np.pad(support, pad_hw, mode="edge")
        cur_p = np.pad(cur, pad_hw + ((0, 0),), mode="edge")
        mc_p = np.pad(mc, pad_hw, mode="edge")
        valid_p = (supp_p > 0) & ~mc_p

        # coarse own-density: total own coverage (valid + still-occluded) per
        # 2x2 block, used only to weight this level's own `_relax` averaging
        # (unchanged semantics from before the fix).
        own_count = supp_p.reshape(Hp // 2, 2, Wp // 2, 2).sum(axis=(1, 3))
        supp_lo = own_count / 4.0

        # valid-preserving color restriction: weight by valid children only.
        valid_f = valid_p.astype(float)
        valid_count = valid_f.reshape(Hp // 2, 2, Wp // 2, 2).sum(axis=(1, 3))
        valid_lo = valid_count > 0
        num_valid = (cur_p * valid_f[:, :, None]).reshape(Hp // 2, 2, Wp // 2, 2, 3).sum(axis=(1, 3))
        cur_valid_avg = num_valid / np.maximum(valid_count, 1e-6)[:, :, None]

        # fallback seed for occluded coarse pixels (no valid child): own-
        # weighted average of whatever's there today, purely for a plausible
        # starting point — correctness doesn't depend on it, since further
        # restriction only ever weights by `valid`, never by this fallback.
        num_own = (cur_p * supp_p[:, :, None]).reshape(Hp // 2, 2, Wp // 2, 2, 3).sum(axis=(1, 3))
        cur_own_avg = num_own / np.maximum(own_count, 1e-6)[:, :, None]

        cur_lo = np.where(valid_lo[:, :, None], cur_valid_avg, cur_own_avg)
        mc_lo = (own_count > 0) & ~valid_lo

        cur_lo = _diffuse_level(cur_lo, mc_lo, supp_lo, max_iter, tol, min_size, refine_iter)

        seed = np.repeat(np.repeat(cur_lo, 2, axis=0), 2, axis=1)[:H, :W]
        cur = cur.copy()
        cur[mc] = seed[mc]

        return _relax(cur, mc, support, refine_iter, tol)  # short smoothing pass only

    return _relax(cur, mc, support, max_iter, tol)  # base level: full solve


def _diffuse_fill(rgb, mask_c, own, max_iter=DIFFUSE_MAX_ITER, tol=DIFFUSE_TOL,
                   min_size=DIFFUSE_PYRAMID_MIN):
    """Harmonic diffusion inpaint for one connected component `mask_c` of
    the occluded zone, in place over `rgb` (float array already initialized
    by `_extend_fill`). See `_diffuse_level` for the coarse-to-fine
    relaxation. Scoped to the component's bounding box (+1px pad so the 3x3
    window can see the boundary valid pixels) for performance.
    """
    ys, xs = np.where(mask_c)
    if ys.size == 0:
        return
    H, W = mask_c.shape
    y0, y1 = max(ys.min() - 1, 0), min(ys.max() + 2, H)
    x0, x1 = max(xs.min() - 1, 0), min(xs.max() + 2, W)
    sl = (slice(y0, y1), slice(x0, x1))

    mc = mask_c[sl]
    support = own[sl].astype(float)
    cur = rgb[sl].copy()

    cur = _diffuse_level(cur, mc, support, max_iter, tol, min_size)

    rgb[sl][mc] = cur[mc]


def fill_occlusions(built_pieces: list, blur_sigma: float = 3.0, mode: str = "auto") -> None:
    if mode not in ("auto", "extend", "inpaint"):
        raise ValueError(f"unknown fill mode: {mode!r} (expected auto/extend/inpaint)")
    n = len(built_pieces)
    for i, p in enumerate(built_pieces):
        if p["fill"] == "none" or i == n - 1:
            continue
        occl_full = np.zeros_like(p["mask_full"])
        for upper in built_pieces[i + 1:]:
            occl_full |= upper["mask_full"]
            for v in upper.get("variants", []):
                occl_full |= _variant_mask(occl_full.shape, v)
        ox, oy = p["offset"]
        h, w = p["img"].shape[:2]
        occl = occl_full[oy:oy + h, ox:ox + w].copy()
        own = p["img"][:, :, 3] > ALPHA_THRESHOLD
        occl &= own
        valid = own & ~occl
        if not occl.any():
            continue
        if not valid.any():
            # piece entirely hidden behind uppers — nothing to extend from; leave it
            continue

        img = p["img"]
        rgb = img[:, :, :3].astype(float)
        dist, (iy, ix) = ndimage.distance_transform_edt(~valid, return_distances=True, return_indices=True)
        _extend_fill(rgb, occl, iy, ix)   # always runs: today's result, and the diffusion init

        labeled, n_labels = ndimage.label(occl)
        for lbl in range(1, n_labels + 1):
            mask_c = labeled == lbl
            depth = dist[mask_c].max()
            if depth > WARN_OCCLUSION_PX:
                # A fill this deep is INVENTED content — no algorithm can know
                # what the source never drew there. Nearly always a markup
                # smell: the polygon claims a neighbor's territory across a
                # gap instead of hugging its own silhouette (playbook §2).
                ys, xs = np.where(mask_c)
                print(f"fill_occlusions: WARNING: piece {p['name']!r}: occlusion "
                      f"zone {depth:.0f}px deep (image bbox "
                      f"[{xs.min() + ox}, {ys.min() + oy}, {xs.max() + ox}, {ys.max() + oy}]) "
                      f"— this fill is invented content (usual cause: the "
                      f"polygon claims territory across a gap instead of "
                      f"hugging its own silhouette, rigging-playbook §2; a "
                      f"region render shows the zone up close)",
                      file=sys.stderr)
            if mode == "extend" or (mode == "auto" and depth <= DEEP_OCCLUSION_PX):
                continue  # shallow component (or forced extend): keep the extend result
            _diffuse_fill(rgb, mask_c, own)

        _masked_blur(rgb, occl, own, blur_sigma)
        img[:, :, :3] = np.rint(np.clip(rgb, 0, 255)).astype(np.uint8)
