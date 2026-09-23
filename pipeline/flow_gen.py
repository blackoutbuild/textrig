"""Chained DIS optical flow sampled at mesh vertices (used by morph pieces).
API:

vertex_flow(frames, verts) -> (V,2) float array: accumulated displacement of
  each vertex from frames[0] coords to frames[-1] coords, chained through
  every intermediate frame (guides).
vertex_flow_series(frames, verts) -> (F,V,2): the same walk, but every
  intermediate position kept (motion capture needs per-frame positions, not
  just the endpoint).
segment_offsets(frames, verts_a, verts_b) -> {"half_a", "half_b", "warnings"}:
  two-sided morph half-offsets targeting the common midpoint shape, plus
  consistency warnings (round-trip disagreement + vanishing content).

Coordinate convention: points are (x, y) in image pixels, y down; returned
offsets are (dx, dy) displacements in the same units (cv2 flow convention).

Two-sided half definitions
--------------------------
Each side's displacement is a BLEND of two independent estimates (per the
spec's "blend the two estimates"), so the halves stay watertight where flow
disagrees:

  fwd  = chain walk of verts_a through the forward fields (frames[0] -> [-1])
  bwd  = chain walk of verts_b through the backward fields (frames[-1] -> [0])
  blended_a[i] = (fwd[i] - back_at_end[i]) / 2   where back_at_end = backward
      chain sampled at verts_a + fwd, so -back_at_end is the reverse estimate
      of A's forward displacement
  blended_b[i] = (bwd[i] - fwd_at_b_end[i]) / 2  symmetric for B
  half_a = 0.5 * blended_a;  half_b = 0.5 * blended_b

On perfectly consistent flow the two estimates coincide and the blend is a
no-op. The caller pairs verts_a and verts_b so that verts_b[i] is the
frames[-1] counterpart of verts_a[i] (the A-mesh vertex forward-projected onto
B). When that correspondence holds, verts_a[i]+half_a[i] and
verts_b[i]+half_b[i] coincide at the common midpoint (proven on the synthetic
translation case in the tests). half_b is anchored at verts_b (an independent
estimate of the same midpoint), so the halves-meet check doubles as a
correspondence check: if the two halves fail to meet, the A/B pairing or the
flow is wrong.

Two trust signals feed "warnings" (advisory; a morph may still be authored,
but it can tear where they fire — the blend patches the offsets, the warning
still tells the author where to add guides or a keyframe):
  1. round-trip: |fwd + back_at_end| should be ~0. Catches flow that disagrees
     forward vs backward.
  2. vanishing content: an opaque source vertex (alpha > ALPHA_OPAQUE) whose
     flow-displaced position in the far frame is actually transparent (alpha
     < ALPHA_VANISHED). The round-trip check is blind to this — against
     blank/occluded content DIS returns ~0 flow both ways, so the round trip
     is spuriously "consistent" at 0. The two thresholds are deliberately
     decoupled: anti-aliased edges (rembg) jitter a few alpha counts between
     frames, so a boundary vertex at src alpha ~131 legitimately lands on dst
     alpha ~119 — soft edge, not vanished. Only near-zero destination alpha
     counts as vanished.

DIS tuning (deviation from PRESET_MEDIUM defaults)
--------------------------------------------------
setPatchStride(1) + setUseSpatialPropagation(True). The MEDIUM default stride
(3) underestimates displacement on small isolated patches and degrades along a
multi-step chain (a clean 8px translation reads back as ~1-7px, asymmetric
between forward/backward). Dense patch sampling with spatial propagation
recovers the ground-truth translation symmetrically (see tests). This only
increases accuracy (at some compute cost); Gate 0's real-data result used
defaults on large, richly-textured frames where stride 3 was already adequate.
"""
import cv2
import numpy as np

CONSISTENCY_TOL = 6.0   # px round-trip error before we warn
ALPHA_OPAQUE = 128      # alpha > this counts as opaque source (matches gate0)
ALPHA_VANISHED = 24     # alpha < this counts as truly gone at the destination


def _gray(rgba):
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgba[:, :, :3].astype(np.float32) * a + 128.0 * (1 - a)
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)


def _dis():
    d = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    d.setPatchStride(1)              # dense inverse search (default 3 undershoots)
    d.setUseSpatialPropagation(True)
    return d


def _sample(field, pts):
    """bilinear sample HxWxC field at (x,y) float points -> (N,C)"""
    field = np.atleast_3d(field)
    h, w, ch = field.shape
    out = np.empty((len(pts), ch), np.float32)
    for i, (x, y) in enumerate(pts):
        x = min(max(float(x), 0.0), w - 1.001)
        y = min(max(float(y), 0.0), h - 1.001)
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        c = field[y0:y0+2, x0:x0+2]
        out[i] = (c[0, 0] * (1-fx) * (1-fy) + c[0, 1] * fx * (1-fy)
                  + c[1, 0] * (1-fx) * fy + c[1, 1] * fx * fy)
    return out


def _alpha_at(frame, pts):
    """bilinear alpha (0..255) sampled at float points -> (N,) float"""
    return _sample(frame[:, :, 3].astype(np.float32), pts)[:, 0]


def _step_flows(frames):
    """forward flow fields between consecutive frames (cv2 convention:
    calc(prev, next): prev pixel x lands at x + f(x) in next)."""
    grays = [_gray(f) for f in frames]
    dis = _dis()
    return [dis.calc(grays[i], grays[i+1], None) for i in range(len(grays)-1)]


def _walk(fields, pts):
    """Chain (x,y) points through an ordered list of flow fields, sampling
    each field at the points' current positions. Returns total (dx,dy)."""
    pts = np.asarray(pts, np.float32).copy()
    total = np.zeros_like(pts)
    for fl in fields:
        d = _sample(fl, pts)
        pts = pts + d
        total = total + d
    return total


def vertex_flow(frames, verts):
    """Accumulated displacement of each vertex from frames[0] coords to
    frames[-1] coords, chained through every intermediate frame.

    frames: RGBA uint8 arrays on a shared canvas. verts: (V,2) of (x,y) in
    image pixels, y down. Returns (V,2) float (dx,dy) in the same units."""
    return _walk(_step_flows(frames), verts)


def vertex_flow_series(frames, verts):
    """Position of each vertex at EVERY frame, chained through the same forward
    DIS fields `vertex_flow` walks. Returns (F, V, 2) absolute (x, y) image px;
    row 0 is `verts` itself, row i is where those points have travelled to by
    frames[i]. `vertex_flow(frames, verts) == series[-1] - series[0]` by
    construction — identical walk, intermediates retained."""
    pts = np.asarray(verts, np.float32).copy()
    out = [pts.copy()]
    for fl in _step_flows(frames):
        pts = pts + _sample(fl, pts)
        out.append(pts.copy())
    return np.stack(out)


DEADBAND_PX = 1.5   # offsets below this are flow noise on static regions


def smooth_offsets(offsets, triangles, iterations=2, lam=0.5,
                   deadband=None, canvas_w=None):
    """Laplacian smoothing of a per-vertex 2D OFFSET field over mesh adjacency.

    The deadband is pixel-absolute, so it scales with canvas size: pass
    `canvas_w` and it defaults to `canvas_w / 240` (≈1.5px at 358px wide, the
    width it was tuned on; ≈1.07px at 257px). An explicit `deadband` overrides;
    with neither, the legacy 1.5px default (`DEADBAND_PX`) holds. Resolution
    scaling keeps the static-region pin equivalent across build sizes — the
    same physical motion is gated the same way whether the figure was rendered
    large or small (the artifact this addresses moved with pixel scale).

    Before smoothing, offsets with magnitude < `deadband` are zeroed: DIS
    returns sub-pixel noise on genuinely static regions, and while raw noise
    is incoherent (invisible), SMOOTHED noise becomes a coherent low-frequency
    ripple that reads as a wave traveling through the figure. The deadband
    pins static regions to exactly zero deform; real motion (>> 1.5px) is
    untouched.

    offsets: (V,2) array of (dx,dy) per vertex. triangles: list of [i,j,k]
    vertex-index triples (mesh edges = triangle edges). Each iteration replaces
    every vertex's offset with a blend of itself and the MEAN of its edge-
    neighbors' offsets:

        v_new = (1 - lam) * v + lam * mean(offset over edge-neighbors)

    Applied to the half-offset fields before they become FFD keys, this irons
    out flow-field discontinuities that get sampled onto neighboring mesh
    vertices (a zigzag/staircase kink on a contour where two regions move at
    different rates, e.g. an arm/torso motion boundary).

    Two properties matter:
      - A UNIFORM (constant) offset field is an EXACT FIXED POINT: the neighbor
        mean equals the vertex value, so `v_new == v`. A pure translation of the
        whole mesh therefore passes through untouched — smoothing only attacks
        *local disagreement*, never global motion.
      - A vertex that appears in no triangle has no neighbors and is left as-is.

    Always on in the compiler at 2 iterations, lam=0.5 — no author knob yet (add
    one only if a real case needs a different strength)."""
    if deadband is None:
        deadband = canvas_w / 240.0 if canvas_w else DEADBAND_PX
    off = np.asarray(offsets, float).copy()
    if deadband > 0:
        off[np.linalg.norm(off, axis=1) < deadband] = 0.0
    V = len(off)
    neigh = [set() for _ in range(V)]
    for a, b, c in triangles:
        neigh[a].update((b, c))
        neigh[b].update((a, c))
        neigh[c].update((a, b))
    nb_idx = [sorted(s) for s in neigh]
    for _ in range(iterations):
        prev = off.copy()
        for v in range(V):
            nb = nb_idx[v]
            if not nb:
                continue
            off[v] = (1.0 - lam) * prev[v] + lam * prev[nb].mean(axis=0)
    return off


CONTENT_LO = 6.0    # local gray RMSE (0..255) at/below which a vertex sits on
CONTENT_HI = 18.0   # UNCHANGED content; at/above which content genuinely changed


def _patch_rmse(ga, gb, xc, yc, radius):
    """Gray RMSE (0..255) over a (2*radius+1) square patch centred at integer
    (xc, yc), clipped to the image. Returns None if the patch falls off-canvas
    (empty). This is the single per-patch primitive shared by content_gate (one
    call per mesh vertex) and register_frames' static-support detector (one call
    per coarse block) — the "shared static content" gate and the "unchanged
    content" gate are the SAME measurement at different sampling densities."""
    h, w = ga.shape
    x0, x1 = max(0, xc - radius), min(w, xc + radius + 1)
    y0, y1 = max(0, yc - radius), min(h, yc + radius + 1)
    pa = ga[y0:y1, x0:x1]
    pb = gb[y0:y1, x0:x1]
    if pa.size == 0:
        return None
    return float(np.sqrt(np.mean((pa - pb) ** 2)))


def content_gate(frame_a, frame_b, verts, radius=None, lo=CONTENT_LO, hi=CONTENT_HI):
    """Per-vertex weight in [0,1]: how much the LOCAL texture actually changes
    between two key frames AT THE SAME canvas location.

    `radius` is pixel-absolute, so it scales with canvas size: when None it
    defaults to `max(4, round(w/60))` (6px at 358px wide, the width it was tuned
    on; clamps to 4px on small canvases). An explicit radius overrides (the unit
    tests pin fixed patches). The lo/hi RMSE thresholds stay absolute — they are
    intensity units, invariant to pixel scale.

    A morph offset is only observable where content changes. DIS is a *dense*
    flow field: next to a large moving region (an arm sweeping up) it bleeds
    several px of flow onto the neighbouring STATIC content (the torso), and the
    round-trip check stays happy because the bleed is internally consistent. The
    deadband can't catch it (the bleed is 3-30px, far above 1.5px) and Laplacian
    smoothing doesn't create it (it is already in the raw field). The result is a
    band of warp that rides the moving region up the figure and back on the
    ping-pong.

    The fix is to read the signal DIS ignores: if the pixels under a vertex are
    the same in both keys, there is nothing to morph there and its offset must be
    zero, whatever the flow says. `radius`-px patch RMSE at the vertex, ramped
    lo->hi, gives a soft 0..1 weight: 0 on identical content (static torso), 1 on
    content that fully changed (the arm's path), a short ramp on partly-changed
    edge vertices. A featureless region that moved reads as "unchanged" and is
    gated to zero — visually harmless (you can't see a textureless region slide).

    frame_a, frame_b: RGBA uint8 on the shared canvas (the two key frames of a
    segment). verts: (V,2) (x,y) image px. Returns (V,) float32 in [0,1]."""
    ga = _gray(frame_a).astype(np.float32)
    gb = _gray(frame_b).astype(np.float32)
    h, w = ga.shape
    if radius is None:
        radius = max(4, round(w / 60))
    out = np.empty(len(verts), np.float32)
    for i, (x, y) in enumerate(verts):
        xc, yc = int(round(float(x))), int(round(float(y)))
        rmse = _patch_rmse(ga, gb, xc, yc, radius)
        if rmse is None:
            out[i] = 1.0     # off-canvas: don't suppress
            continue
        out[i] = min(max((rmse - lo) / (hi - lo), 0.0), 1.0)
    return out


def tracked_alive(frame_src, pts_src, frame_dst, pts_dst):
    """Which tracked points still sit on the character. A point is ALIVE unless
    it started on opaque source content and landed where the destination frame
    is (near-)transparent — the "content vanished" signal DIS itself is blind to
    (it returns ~0 flow against blank or occluded content, which the round-trip
    check then reads as spuriously consistent at 0).

    frame_src/frame_dst: RGBA uint8. pts_src: (V,2) rest positions; pts_dst:
    (V,2) the same points after the walk. Returns (V,) bool.

    NOT content_gate. That measures how much local texture CHANGED at a fixed
    canvas location: ~0 for a legitimately static point and ~1 for a point
    buried under a moving occluder — inverted for this question in both
    directions."""
    src_opaque = _alpha_at(frame_src, pts_src) > ALPHA_OPAQUE
    dst_alpha = _alpha_at(frame_dst, pts_dst)
    return ~(src_opaque & (dst_alpha < ALPHA_VANISHED))


def segment_offsets(frames, verts_a, verts_b):
    """Two-sided morph half-offsets for one segment.

    verts_a: (V,2) of (x,y) mesh vertices in frames[0] coords (image px,
    y down); verts_b: their frames[-1] counterparts, index-paired. Returns
    {"half_a", "half_b", "warnings"}: (V,2) float (dx,dy) offsets in image px
    moving each side to the common midpoint shape, plus a list of advisory
    warning strings. Blended-estimate definitions and trust signals are in
    the module docstring."""
    va = np.asarray(verts_a, np.float32)
    vb = np.asarray(verts_b, np.float32)

    # all flow fields once — one DIS instance, grays computed once; every
    # vertex walk below samples these cached fields
    grays = [_gray(f) for f in frames]
    dis = _dis()
    n = len(grays)
    fwd_fields = [dis.calc(grays[i], grays[i+1], None) for i in range(n - 1)]
    bwd_fields = [dis.calc(grays[i+1], grays[i], None) for i in range(n - 1)]
    bwd_chain = bwd_fields[::-1]     # walk order for frames[-1] -> frames[0]

    fwd = _walk(fwd_fields, va)              # A's forward estimate
    bwd = _walk(bwd_chain, vb)               # B's backward estimate
    a_end = va + fwd
    b_end = vb + bwd
    back_at_end = _walk(bwd_chain, a_end)    # reverse estimate for A (negated)
    fwd_at_b_end = _walk(fwd_fields, b_end)  # reverse estimate for B (negated)

    # blend the two estimates per side (no-op when flow is consistent)
    blended_a = (fwd - back_at_end) * 0.5
    blended_b = (bwd - fwd_at_b_end) * 0.5

    # signal 1 — round-trip disagreement
    rt = np.linalg.norm(fwd + back_at_end, axis=1)
    bad = rt > CONSISTENCY_TOL

    # signal 2 — vanishing content (round-trip is blind to it: DIS returns ~0
    # flow against blank/occluded content, spuriously "consistent" at 0)
    vanished = ~tracked_alive(frames[0], va, frames[-1], a_end)

    warnings = []
    flagged = bad | vanished
    if flagged.any():
        pts = va[flagged]
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        detail = f"max round-trip {rt.max():.1f}px"
        if vanished.any():
            detail += f", {int(vanished.sum())} vanish into transparency"
        warnings.append(
            f"flow inconsistency on {int(flagged.sum())} vertices "
            f"({detail}) bbox=({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) — morph "
            f"may tear here; add guide frames or a keyframe")
    return {"half_a": blended_a * 0.5, "half_b": blended_b * 0.5,
            "warnings": warnings}


def dominant_translation(offsets, moving_mask, min_frac=0.1, radius=3.0, min_cov=0.6):
    """The DOMINANT rigid translation shared by the moving vertices — the center
    of their LARGEST agreeing cluster (density mode) — but ONLY when that cluster
    covers a strong majority (`min_cov`) of the moving set, i.e. the motion really
    is a coherent whole-body translation. For motion decomposition.

    The extracted translation goes on the owner BONE as one exact uniform vector;
    only the residual `offset - g` stays on the mesh FFD, so the flat interior
    rides the global instead of warping behind the well-tracked edges (and which
    interior verts DIS can't track — hence the artifact — stops depending on
    pixel scale).

    Density mode, NOT a plain median/mean: content_gate removes the flat body
    interior (the region we want the global FOR is unobservable), so the moving
    set is the textured EDGES. A median is dragged toward a fast articulated LIMB
    (measured ~2× overshoot on real frames); the density mode locks onto the
    largest coherent cluster and treats the fast limb as the outlier it is. On a
    clean uniform translation every vertex is one cluster, so the mode returns it
    EXACTLY (residual then exactly zero).

    The `min_cov` GATE is what keeps articulated motion safe: when the figure is
    NOT translating as a whole (planted feet + a rising torso + a swinging arm are
    three different motions), the moving offsets split into several clusters and
    the largest covers well under half — there is no single "dominant rigid
    translation" to speak of, so we return ZERO and leave everything on the mesh
    (no bone push spuriously drags the planted parts). Only a genuinely coherent
    whole-body translation (one cluster covering >= min_cov) is lifted to the bone.

    `offsets`: (V,2). `moving_mask`: (V,) bool of vertices that genuinely moved
    (gated AND above a motion threshold). `radius`: px agreement radius defining a
    cluster. `min_cov`: min fraction of moving verts the dominant cluster must
    cover. Returns a (2,) vector, or the zero vector when fewer than `min_frac` of
    all vertices are moving OR the dominant cluster is below `min_cov`."""
    off = np.asarray(offsets, float)
    n = len(off)
    m = np.asarray(moving_mask, bool)
    if n == 0 or int(m.sum()) < max(1, int(np.ceil(min_frac * n))):
        return np.zeros(2)
    v = off[m]
    # neighbor count within `radius` for every candidate offset; the densest is
    # the center of the largest agreeing cluster. Refine to the mean of that
    # cluster's members (sub-bin accuracy; exact on a single uniform cluster).
    d2 = ((v[:, None, :] - v[None, :, :]) ** 2).sum(-1)
    counts = (d2 <= radius * radius).sum(1)
    seed = v[int(np.argmax(counts))]
    inliers = v[np.linalg.norm(v - seed, axis=1) <= radius]
    if len(inliers) < min_cov * len(v):
        return np.zeros(2)          # no coherent whole-body translation
    return inliers.mean(axis=0)


# --- inter-frame registration (extraction-jitter stabilizer) ---------------
# The extraction chain (Wan video -> rembg -> LANCZOS resize) injects a global
# sub-pixel/1px wobble between consecutive key frames even where the figure is
# genuinely still. That wobble reads as harsh frame-to-frame shaking at the
# display-swap cadence and, interpolated by the deform FFD, as the "traveling
# wave". register_frames measures the shift of the SHARED STATIC content between
# each consecutive pair (the content that did NOT change — same gate as
# content_gate) and cancels only the small drifts, leaving real motion alone.
REGISTER_JITTER_DIV = 180.0   # default max_jitter = canvas_w / this (~2px @358)
MIN_SUPPORT_FRAC = 0.10       # static support must cover >=10% of silhouette px


def _static_support_mask(frame_a, frame_b, block, lo=CONTENT_LO,
                         alpha_op=ALPHA_OPAQUE):
    """Boolean pixel mask of SHARED STATIC CONTENT between two RGBA frames, plus
    the silhouette (opaque-in-either) mask. A coarse `block`-grid version of the
    content gate: a block is "static support" iff it is opaque content in BOTH
    frames AND its patch RMSE (flow_gen._patch_rmse, the same primitive
    content_gate uses per vertex) is below `lo` — i.e. the pixels there are the
    same in both frames. Background (transparent both) is excluded (nothing to
    correlate) and so is anything that actually changed (real motion, edges).
    Coarse blocks are enough: we only need a stable textured support region to
    phase-correlate, not a per-pixel boundary."""
    ga = _gray(frame_a).astype(np.float32)
    gb = _gray(frame_b).astype(np.float32)
    h, w = ga.shape
    aa, ab = frame_a[:, :, 3], frame_b[:, :, 3]
    silhouette = (aa > alpha_op) | (ab > alpha_op)
    support = np.zeros((h, w), bool)
    step = max(2, block)
    for yc in range(step, h, step):
        for xc in range(step, w, step):
            y0, y1 = yc - step, min(h, yc + step)
            x0, x1 = xc - step, min(w, xc + step)
            # require real content in BOTH frames over the block (static
            # CONTENT, not empty background)
            if not ((aa[y0:y1, x0:x1] > alpha_op).any()
                    and (ab[y0:y1, x0:x1] > alpha_op).any()):
                continue
            r = _patch_rmse(ga, gb, xc, yc, step)
            if r is not None and r < lo:
                support[y0:y1, x0:x1] = True
    return support, silhouette


def _estimate_static_shift(frame_a, frame_b, support):
    """Sub-pixel global shift of the static support between two frames via phase
    correlation. Returns (shift (2,) float [dx,dy], support_px int), or
    (None, 0) if the support is empty.

    Robustness choices (justifying the "mask vs bbox" pick): we crop both grays
    to the support's bounding box, mean-subtract WITHIN the support and zero the
    non-support pixels (so the masked-out region sits at the DC level, not a
    hard bright/dark edge), and apply a Hanning window before phaseCorrelate.
    Cropping to the bbox keeps the correlated region tight around the actual
    shared content (a distant moving limb never enters the window), while the
    per-support mean-subtract + window suppress the spectral leakage that a raw
    hard mask edge would inject. phaseCorrelate(a, b) returns the shift of b's
    content relative to a (verified: content moved +5px -> ~+5).

    Self-calibration: cv2.phaseCorrelate carries a size/content-dependent
    sub-pixel bias that, on large crops (>~256px), pins to a spurious +0.5px in
    the long axis even for two IDENTICAL frames (measured; it accumulated ~8px
    of phantom global drift over a 30-frame sequence before this fix). We remove
    it by subtracting the SELF-correlation phaseCorrelate(a, a) — the residual
    of the estimator at true-shift-zero on this exact window — which is a no-op
    on small crops (self-corr ~0 there) and cancels the bias on large ones. This
    made the real win1 global-support shift read ~0.00/step (matching an
    independent unbiased upsampled-DFT estimator) instead of a false +0.5/step."""
    ga = _gray(frame_a).astype(np.float32)
    gb = _gray(frame_b).astype(np.float32)
    ys, xs = np.where(support)
    if len(xs) == 0:
        return None, 0
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    m = support[y0:y1, x0:x1].astype(np.float32)
    if m.sum() < 1:
        return None, 0
    pa, pb = ga[y0:y1, x0:x1], gb[y0:y1, x0:x1]
    ma = float((pa * m).sum() / m.sum())
    mb = float((pb * m).sum() / m.sum())
    a = np.ascontiguousarray((pa - ma) * m)
    b = np.ascontiguousarray((pb - mb) * m)
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(a, b, win)
    (bx, by), _ = cv2.phaseCorrelate(a, a.copy(), win)   # self-corr bias
    return np.array([dx - bx, dy - by], float), int(m.sum())


def _warp_frame(frame, shift):
    """Translate an RGBA frame by (dx,dy), sub-pixel, transparent border. All
    four channels warp together so alpha rides with color — no dark fringe (the
    border fills with (0,0,0,0), fully transparent, so revealed edges are clean
    transparency not black)."""
    if float(np.hypot(shift[0], shift[1])) < 1e-6:
        return frame.copy()
    h, w = frame.shape[:2]
    M = np.array([[1.0, 0.0, float(shift[0])],
                  [0.0, 1.0, float(shift[1])]], np.float32)
    return cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def register_frames(frames, max_jitter=None):
    """Stabilize extraction jitter across a time-ordered frame sequence.

    frames: list of HxWx4 uint8 RGBA arrays on a shared canvas (KEY frames AND
    the guide frames between them, in time order). max_jitter: max |shift| (px)
    still treated as jitter; default canvas_w / 180 (~2px at 358px wide —
    resolution-relative like the deadband/gate radius).

    For each consecutive pair we estimate the global shift of the SHARED STATIC
    support (see _static_support_mask / _estimate_static_shift). Decision per
    step:
      * |shift| <= max_jitter AND support >= MIN_SUPPORT_FRAC of the silhouette
        -> JITTER: subtract the increment into the running correction and warp
        this frame by the accumulated correction (cancels the drift so the
        stabilized timeline aligns to frame 0).
      * |shift| > max_jitter, OR the static support is too small/absent
        -> REAL MOTION (or unmeasurable): this step contributes ZERO to the
        correction, but the accumulator is NOT reset — every later frame still
        carries the jitter correction banked before the motion, so the
        stabilized timeline stays globally consistent across a real move.

    Frame 0 is the reference (untouched). Returns (frames_out, corrections):
    frames_out is a new list of warped RGBA arrays; corrections[k] is the (2,)
    accumulated correction actually applied to frame k (for logging)."""
    frames = list(frames)
    n = len(frames)
    if n == 0:
        return [], []
    h, w = frames[0].shape[:2]
    if max_jitter is None:
        max_jitter = w / REGISTER_JITTER_DIV
    block = max(4, round(w / 60))       # same scale as content_gate's radius
    cum = np.zeros(2)                   # accumulated correction to apply
    corrections = [cum.copy()]
    out = [frames[0].copy()]
    for k in range(1, n):
        support, silhouette = _static_support_mask(frames[k - 1], frames[k], block)
        support_px = int(support.sum())
        sil_px = int(silhouette.sum())
        shift, _ = _estimate_static_shift(frames[k - 1], frames[k], support)
        too_small = sil_px == 0 or support_px < MIN_SUPPORT_FRAC * sil_px
        if shift is not None and not too_small \
                and float(np.hypot(*shift)) <= max_jitter:
            cum = cum - shift          # cancel this jitter increment
        # else: real motion / unmeasurable -> no delta, accumulator persists
        corrections.append(cum.copy())
        out.append(_warp_frame(frames[k], cum))
    return out, corrections
