import numpy as np
import flow_gen


def _square(shift):
    """64x64 RGBA, 20px textured square at 10+shift."""
    rng = np.random.default_rng(7)
    img = np.zeros((64, 64, 4), np.uint8)
    tex = rng.integers(60, 255, (20, 20, 3), np.uint8)
    x = 10 + shift
    img[22:42, x:x+20, :3] = tex
    img[22:42, x:x+20, 3] = 255
    return img


def test_vertex_flow_recovers_translation():
    a, b = _square(0), _square(8)
    verts = [[20.0, 32.0], [25.0, 30.0]]
    off = flow_gen.vertex_flow([a, b], verts)
    assert np.allclose(off, [[8, 0], [8, 0]], atol=1.5)


def test_chained_beats_direct_on_big_shift():
    frames = [_square(s) for s in (0, 8, 16, 24)]
    verts = [[20.0, 32.0]]
    chained = flow_gen.vertex_flow(frames, verts)
    assert np.allclose(chained, [[24, 0]], atol=2.5)


def test_two_sided_consistency_flags_garbage():
    a, b = _square(0), np.zeros((64, 64, 4), np.uint8)  # nothing to match
    seg = flow_gen.segment_offsets([a, b], [[20.0, 32.0]], [[20.0, 32.0]])
    assert seg["warnings"], "vanishing content must raise a consistency warning"


def test_two_sided_halves_meet():
    a, b = _square(0), _square(8)
    seg = flow_gen.segment_offsets([a, b], [[20.0, 32.0]], [[28.0, 32.0]])
    mid_a = np.array([20.0, 32.0]) + seg["half_a"][0]
    mid_b = np.array([28.0, 32.0]) + seg["half_b"][0]
    assert np.allclose(mid_a, mid_b, atol=1.5)


def test_single_pair_chain_equals_direct_flow():
    """A two-frame 'chain' is just the direct flow — no accumulation artifacts."""
    a, b = _square(0), _square(8)
    verts = [[20.0, 32.0], [25.0, 30.0], [15.0, 38.0]]
    chain = flow_gen.vertex_flow([a, b], verts)
    direct = flow_gen._sample(flow_gen._step_flows([a, b])[0], verts)
    assert np.allclose(chain, direct, atol=1e-4)


def test_clean_data_no_warnings():
    """Consistent flow on real (opaque->opaque) content must stay quiet."""
    frames = [_square(s) for s in (0, 8, 16)]
    seg = flow_gen.segment_offsets(frames, [[20.0, 32.0]], [[36.0, 32.0]])
    assert seg["warnings"] == []


def _soft_square(shift, dim=0):
    """_square with a 3px anti-aliased alpha ramp on the right edge; `dim`
    lowers the ramp, simulating rembg's frame-to-frame edge jitter."""
    img = _square(shift)
    x = 10 + shift
    img[22:42, x+17, 3] = 170 - dim
    img[22:42, x+18, 3] = 110 - dim
    img[22:42, x+19, 3] = 50 - dim
    return img


def test_soft_edge_jitter_does_not_warn():
    """A boundary vertex on an anti-aliased edge whose alpha dips slightly
    between frames (soft edge, NOT vanished content) must not trip the
    vanishing-content warning — otherwise real rembg frames warn on every
    segment and the signal becomes noise."""
    a, b = _soft_square(0), _soft_square(2, dim=12)
    # vertex ON the alpha ramp: src alpha ~131 (barely opaque), true 2px
    # translation lands it at dst alpha ~119 (barely soft, nowhere near gone)
    seg = flow_gen.segment_offsets([a, b], [[27.65, 32.0]], [[29.65, 32.0]])
    assert seg["warnings"] == []


# ---- Laplacian offset smoothing ------------------------------------------

def _grid_triangles(cols, rows):
    """Triangle list for a cols x rows vertex grid (row-major indices)."""
    tris = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            i = r * cols + c
            tris.append([i, i + 1, i + cols])
            tris.append([i + 1, i + cols + 1, i + cols])
    return tris


def test_smooth_offsets_constant_field_is_exact_fixed_point():
    # Laplacian of a constant field is zero → a uniform translation field must
    # pass through EXACTLY unchanged (the property with teeth).
    tris = _grid_triangles(5, 5)
    off = np.tile([2.0, -3.0], (25, 1))
    out = flow_gen.smooth_offsets(off, tris, iterations=2, lam=0.5)
    np.testing.assert_allclose(out, off, rtol=0, atol=1e-12)


def test_smooth_offsets_pulls_single_outlier_toward_neighbors():
    # One spike among an otherwise-uniform field gets pulled toward its
    # neighbors' value; neighbors barely move.
    tris = _grid_triangles(5, 5)
    off = np.zeros((25, 2))
    center = 12  # interior vertex of a 5x5 grid
    off[center] = [10.0, 0.0]
    out = flow_gen.smooth_offsets(off, tris, iterations=1, lam=0.5)
    assert out[center][0] < off[center][0]          # outlier pulled down
    assert out[center][0] > 0.0                      # but not all the way
    # a neighbor moved toward the outlier (was 0, now positive)
    assert out[11][0] > 0.0


def test_smooth_offsets_preserves_shape_and_mean():
    tris = _grid_triangles(6, 6)
    rng = np.random.default_rng(3)
    off = rng.normal(size=(36, 2))
    out = flow_gen.smooth_offsets(off, tris)
    assert out.shape == (36, 2)
    # smoothing averages locally: the global mean is essentially preserved
    np.testing.assert_allclose(out.mean(axis=0), off.mean(axis=0), atol=0.15)


def test_smooth_offsets_isolated_vertex_unchanged():
    # a vertex that appears in no triangle has no neighbors → left as-is
    tris = [[0, 1, 2]]
    off = np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [5.0, 7.0]])
    out = flow_gen.smooth_offsets(off, tris, iterations=3)
    np.testing.assert_allclose(out[3], [5.0, 7.0], rtol=0, atol=0)


def test_smooth_offsets_deadband_pins_static_noise():
    """Sub-deadband noise must become EXACTLY zero (coherent-ripple killer),
    while real motion above the deadband survives smoothing."""
    rng = np.random.default_rng(3)
    tris = [[0, 1, 2], [1, 2, 3], [2, 3, 4], [3, 4, 5]]
    noise = rng.uniform(-0.8, 0.8, (6, 2))          # static-region flow noise
    out = flow_gen.smooth_offsets(noise, tris)
    assert np.all(out == 0.0)
    real = np.full((6, 2), [8.0, 0.0])              # genuine uniform motion
    out2 = flow_gen.smooth_offsets(real, tris)
    assert np.allclose(out2, real)


# ---- content gate (kills DIS flow bleed onto static content) --------------

def _two_squares(shift):
    """96x64 RGBA: a MOVING textured square (left, at 10+shift) and a STATIC
    textured square (right, fixed at 60). Distinct textures."""
    rng = np.random.default_rng(11)
    img = np.zeros((64, 96, 4), np.uint8)
    mov = rng.integers(60, 255, (20, 20, 3), np.uint8)
    sta = rng.integers(60, 255, (20, 20, 3), np.uint8)
    img[22:42, 10 + shift:30 + shift, :3] = mov
    img[22:42, 10 + shift:30 + shift, 3] = 255
    img[22:42, 60:80, :3] = sta
    img[22:42, 60:80, 3] = 255
    return img


def test_content_gate_zero_on_unchanged_content():
    """Two identical frames change nothing anywhere → gate is 0 everywhere:
    a morph offset on content that did not change is spurious (DIS bleed)."""
    a = _two_squares(0)
    verts = [[20.0, 32.0], [70.0, 32.0], [45.0, 32.0]]
    g = flow_gen.content_gate(a, a, verts)
    assert np.all(g == 0.0)


def test_content_gate_keeps_moving_suppresses_static():
    """Moving square shifts, static square is untouched. A vertex on the moving
    square's texture (content there changed) keeps full weight; a vertex on the
    static square (identical content) is gated to zero — even though dense DIS
    flow bleeds several px onto it."""
    a, b = _two_squares(0), _two_squares(8)
    on_moving = [20.0, 32.0]     # inside the left square in frame a
    on_static = [70.0, 32.0]     # inside the right square, identical in both
    g = flow_gen.content_gate(a, b, [on_moving, on_static])
    assert g[0] > 0.9, "content that changed must keep its morph offset"
    assert g[1] == 0.0, "content identical in both keys must be gated to zero"


# ---- resolution-relative gating thresholds (Part 2) ----------------------

def test_content_gate_radius_scales_with_canvas_width():
    """The patch radius defaults to max(4, round(w/60)): a wide canvas gets a
    proportionally larger patch, a small one clamps at 4px. An explicit radius
    still overrides (tests keep their fixed patches)."""
    w60 = np.zeros((64, 60, 4), np.uint8)      # w/60 = 1 -> clamp to 4
    w360 = np.zeros((64, 360, 4), np.uint8)    # w/60 = 6
    # A vertex near the left edge: with radius 4 the patch is [x-4, x+4]; with
    # radius 6 it reaches [x-6, x+6]. Put a changing pixel exactly 5px away so
    # only the wider (360) patch sees it -> the two canvases gate differently.
    for img in (w60, w360):
        img[20:44, 0:40, :3] = 128
        img[20:44, 0:40, 3] = 255
    b60, b360 = w60.copy(), w360.copy()
    b60[32, 15, :3] = 0                         # a changed pixel 5px from x=10
    b360[32, 15, :3] = 0
    g_small = flow_gen.content_gate(w60, b60, [[10.0, 32.0]])
    g_wide = flow_gen.content_gate(w360, b360, [[10.0, 32.0]])
    assert g_small[0] == 0.0, "4px patch must not see the pixel 5px away"
    assert g_wide[0] > 0.0, "6px patch (w=360) must see the pixel 5px away"


def test_content_gate_explicit_radius_overrides_scaling():
    a = np.zeros((64, 360, 4), np.uint8)
    a[20:44, 0:40, :3] = 128
    a[20:44, 0:40, 3] = 255
    b = a.copy()
    b[32, 15, :3] = 0
    g = flow_gen.content_gate(a, b, [[10.0, 32.0]], radius=4)
    assert g[0] == 0.0, "explicit radius=4 must ignore canvas-width scaling"


def test_smooth_offsets_deadband_scales_with_canvas_width():
    """deadband defaults to canvas_w/240; a small canvas gets a smaller
    deadband so the SAME sub-pixel motion is (or isn't) pinned equivalently at
    both scales. With no canvas_w, the legacy 1.5px default holds."""
    tris = [[0, 1, 2], [1, 2, 3]]
    off = np.full((4, 2), [0.9, 0.0])          # 0.9px motion
    # canvas_w=360 -> deadband 1.5 -> 0.9px pinned to zero
    out_wide = flow_gen.smooth_offsets(off, tris, canvas_w=360)
    assert np.all(out_wide == 0.0)
    # canvas_w=120 -> deadband 0.5 -> 0.9px survives
    out_small = flow_gen.smooth_offsets(off, tris, canvas_w=120)
    assert np.any(out_small != 0.0)
    # explicit deadband overrides canvas scaling
    out_expl = flow_gen.smooth_offsets(off, tris, canvas_w=120, deadband=1.5)
    assert np.all(out_expl == 0.0)


# ---- dominant translation (motion decomposition, Part 1) -----------------

def test_dominant_translation_cluster_center_of_moving():
    off = np.array([[8.0, 0.0], [8.2, 0.1], [7.9, -0.1], [0.0, 0.0], [0.0, 0.0]])
    moving = np.array([True, True, True, False, False])
    g = flow_gen.dominant_translation(off, moving)
    assert np.allclose(g, [8.0, 0.0], atol=0.3)


def test_dominant_translation_locks_onto_majority_cluster_not_fast_minority():
    """Density mode, not median: a MAJORITY body cluster at ~4px and a fast-limb
    minority spread at 12-24px. A median is dragged toward the fast values; the
    mode locks onto the majority body cluster and treats the limb as the outlier."""
    body = np.tile([0.0, 4.0], (30, 1)) + np.random.default_rng(1).normal(0, 0.4, (30, 2))
    limb = np.column_stack([np.zeros(10), np.linspace(12, 24, 10)])  # spread, fast
    off = np.vstack([body, limb])                        # body is 75% majority
    moving = np.ones(len(off), bool)
    g = flow_gen.dominant_translation(off, moving)
    assert np.allclose(g, [0.0, 4.0], atol=1.0), g       # body cluster wins, mean 4
    assert off[:, 1].mean() > 6.0                        # a MEAN would be dragged up


def test_dominant_translation_declines_on_articulated_split():
    """When motion is ARTICULATED (no single whole-body translation) the moving
    offsets split into clusters with no majority — the dominant cluster covers
    well under min_cov, so we return ZERO (leave it all on the mesh; do not push
    the planted parts with a spurious global)."""
    planted = np.zeros((14, 2))                          # planted lower body
    torso = np.tile([0.0, -10.0], (14, 1))               # rising torso
    arm = np.column_stack([np.linspace(-8, 8, 14), np.linspace(-20, -30, 14)])  # sweep
    off = np.vstack([planted, torso, arm])               # three motions, no majority
    moving = np.ones(len(off), bool)
    g = flow_gen.dominant_translation(off, moving, min_cov=0.6)
    assert np.allclose(g, [0.0, 0.0]), g                 # declines: no coherent global


def test_dominant_translation_zero_when_too_few_moving():
    """Fewer than min_frac (10%) of vertices moving -> pure local segment,
    no global to extract."""
    off = np.zeros((100, 2))
    off[:5] = [8.0, 0.0]                             # 5% moving
    moving = np.zeros(100, bool)
    moving[:5] = True
    g = flow_gen.dominant_translation(off, moving, min_frac=0.1)
    assert np.allclose(g, [0.0, 0.0])
    # 15% moving -> global extracted
    moving[:15] = True
    off[:15] = [8.0, 0.0]
    g2 = flow_gen.dominant_translation(off, moving, min_frac=0.1)
    assert np.allclose(g2, [8.0, 0.0], atol=0.1)


def test_residual_is_exactly_zero_for_uniform_translation():
    """The decomposition residual `sm - g` is EXACTLY zero when every moving
    vertex shares one translation — the bone carries all of it, the mesh none."""
    sm = np.tile([8.0, -3.0], (20, 1))
    moving = np.ones(20, bool)
    g = flow_gen.dominant_translation(sm, moving)
    residual = sm - g
    np.testing.assert_allclose(residual, 0.0, rtol=0, atol=0.0)


def test_residual_isolates_only_the_local_part():
    """Mixed segment: uniform translation + one locally-moving vertex. The bone
    carries the MEDIAN; the residual is exactly zero everywhere except the one
    local vertex, which keeps only its local component."""
    sm = np.tile([8.0, 0.0], (20, 1))
    sm[5] = [8.0, 5.0]                      # extra local +y on one vertex
    moving = np.ones(20, bool)
    g = flow_gen.dominant_translation(sm, moving)
    np.testing.assert_allclose(g, [8.0, 0.0], atol=1e-9)
    residual = sm - g
    np.testing.assert_allclose(residual[0], [0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(residual[5], [0.0, 5.0], atol=1e-9)


# --- inter-frame registration ------------------------------------------------
import cv2
import pytest


def _smooth_square(cum_shift, size=140, sq=90, x0=25, y0=25):
    """RGBA canvas with a BAND-LIMITED (blurred noise) textured square, shifted
    sub-pixel by cum_shift=(dx,dy). Broadband-but-smooth texture models a still
    limb: a ~1px shift keeps patch RMSE below CONTENT_LO (so it registers as
    static support), yet has enough mid-frequency energy for sub-pixel phase
    correlation to lock (pure sinusoids are pathological for phaseCorrelate)."""
    rng = np.random.default_rng(5)
    tex = cv2.GaussianBlur(
        rng.integers(20, 240, (sq, sq)).astype(np.float32), (0, 0), 1.2)
    img = np.zeros((size, size, 4), np.uint8)
    img[y0:y0 + sq, x0:x0 + sq, :3] = np.clip(tex, 0, 255).astype(np.uint8)[..., None]
    img[y0:y0 + sq, x0:x0 + sq, 3] = 255
    M = np.array([[1, 0, cum_shift[0]], [0, 1, cum_shift[1]]], np.float32)
    return cv2.warpAffine(img, M, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def _measure_shift(a, b):
    """residual static shift between two registered frames (support-gated)."""
    support, _ = flow_gen._static_support_mask(a, b, block=4)
    s, _ = flow_gen._estimate_static_shift(a, b, support)
    return np.array([0.0, 0.0]) if s is None else s


def test_register_recovers_injected_jitter():
    """Known cumulative jitter on a static textured square -> after registration
    the consecutive residual shifts collapse to ~zero."""
    cum = [(0.0, 0.0), (0.7, -0.3), (0.3, 0.4), (1.1, -0.1), (0.6, 0.5)]
    frames = [_smooth_square(c) for c in cum]
    # pre: consecutive shifts are the injected increments (well above zero)
    pre = [np.hypot(*_measure_shift(frames[k - 1], frames[k]))
           for k in range(1, len(frames))]
    assert max(pre) > 0.3, f"test setup: injected jitter too small {pre}"
    out, corr = flow_gen.register_frames(frames, max_jitter=2.0)
    post = [np.hypot(*_measure_shift(out[k - 1], out[k]))
            for k in range(1, len(out))]
    # residual collapses toward the phase-correlation noise floor and is a
    # large reduction from the injected jitter (>3x on the worst step)
    assert max(post) < 0.25, f"residual after registration too large: {post}"
    assert max(post) < max(pre) / 3.0, f"insufficient reduction {pre} -> {post}"
    # the applied correction opposes the injected cumulative shift (per-step
    # phase-corr noise ~0.2px accumulates along the chain, hence the loose atol;
    # the residual check above is the tight one)
    for k, c in enumerate(cum):
        np.testing.assert_allclose(corr[k], [-c[0], -c[1]], atol=0.5)


def test_register_leaves_real_motion_untouched():
    """A real 8px shift (>> max_jitter) is passed through: correction 0."""
    frames = [_smooth_square((0.0, 0.0)), _smooth_square((8.0, 0.0))]
    out, corr = flow_gen.register_frames(frames, max_jitter=2.0)
    np.testing.assert_allclose(corr[0], [0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(corr[1], [0.0, 0.0], atol=1e-9)
    # frame untouched: identical bytes
    assert np.array_equal(out[1], frames[1])


def test_register_alpha_survives_warp_no_dark_fringe():
    """Warping all 4 channels with a transparent border leaves no dark fringe:
    revealed border pixels are fully transparent, opaque interior stays opaque
    with its color intact (color not multiplied toward black)."""
    frames = [_smooth_square((0.0, 0.0)), _smooth_square((1.3, 0.9))]
    out, corr = flow_gen.register_frames(frames, max_jitter=2.0)
    warped = out[1]
    # a corner that must be background stays fully transparent (no black halo)
    assert warped[0, 0, 3] == 0
    # nowhere is there an opaque-black fringe pixel (alpha>0 but near-black rgb
    # where the source had bright content) — check color rides with alpha
    opaque = warped[:, :, 3] > 200
    assert opaque.sum() > 1000
    # interior opaque pixels keep bright color (source was ~73..228, never ~0)
    assert warped[opaque][:, :3].mean() > 80


def test_register_accumulated_correction_persists_past_real_motion():
    """jitter, jitter, REAL-MOTION, jitter: the correction banked before the
    real move survives it (not reset) and the later frame keeps it."""
    seq = [(0.0, 0.0),      # ref
           (0.8, 0.0),      # jitter -> bank -0.8
           (1.4, 0.0),      # jitter -> bank another -0.6 (cum -1.4)
           (9.4, 0.0),      # REAL 8px move -> no delta, cum stays -1.4
           (10.1, 0.0)]     # jitter (+0.7 on top of the moved pose)
    frames = [_smooth_square(c) for c in seq]
    out, corr = flow_gen.register_frames(frames, max_jitter=2.0)
    # cum after the two jitters ~ -1.4
    assert corr[2][0] == pytest.approx(-1.4, abs=0.3)
    # real-motion step contributes ZERO delta: cum unchanged across step 3
    np.testing.assert_allclose(corr[3], corr[2], atol=1e-9)
    # accumulator persists (still ~-1.4, not reset to 0) and next jitter adds on
    assert corr[4][0] == pytest.approx(-2.1, abs=0.35)


def test_vertex_flow_series_shape_and_endpoint():
    frames = [_square(s) for s in (0, 8, 16)]
    verts = [[20.0, 32.0], [25.0, 30.0]]
    series = flow_gen.vertex_flow_series(frames, verts)
    assert series.shape == (3, 2, 2)
    # row 0 is the input, untouched
    assert np.allclose(series[0], verts, atol=1e-6)
    # the endpoint is exactly what vertex_flow reports — same walk, kept intermediates
    endpoint = series[-1] - np.asarray(verts, np.float32)
    assert np.allclose(endpoint, flow_gen.vertex_flow(frames, verts), atol=1e-4)


def test_vertex_flow_series_tracks_each_step():
    frames = [_square(s) for s in (0, 8, 16)]
    series = flow_gen.vertex_flow_series(frames, [[20.0, 32.0]])
    assert np.allclose(series[1][0], [28.0, 32.0], atol=1.5)
    assert np.allclose(series[2][0], [36.0, 32.0], atol=2.5)


def _framed(alpha_box, tex_seed=3):
    """64x64 RGBA, textured content only inside alpha_box=(x0,y0,x1,y1)."""
    rng = np.random.default_rng(tex_seed)
    img = np.zeros((64, 64, 4), np.uint8)
    x0, y0, x1, y1 = alpha_box
    img[y0:y1, x0:x1, :3] = rng.integers(60, 255, (y1 - y0, x1 - x0, 3), np.uint8)
    img[y0:y1, x0:x1, 3] = 255
    return img


def test_tracked_alive_keeps_static_point():
    a = _framed((10, 10, 40, 40))
    alive = flow_gen.tracked_alive(a, [[20.0, 20.0]], a, [[20.0, 20.0]])
    assert alive.tolist() == [True]


def test_tracked_alive_flags_point_that_landed_on_nothing():
    a = _framed((10, 10, 40, 40))
    b = _framed((10, 10, 20, 20))          # the right half of the shape is gone
    alive = flow_gen.tracked_alive(a, [[30.0, 30.0]], b, [[30.0, 30.0]])
    assert alive.tolist() == [False]


def test_tracked_alive_ignores_points_that_started_off_content():
    a = _framed((10, 10, 40, 40))
    alive = flow_gen.tracked_alive(a, [[55.0, 55.0]], a, [[55.0, 55.0]])
    assert alive.tolist() == [True]        # never was on content — not our verdict to make
