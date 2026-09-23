import sys
import time
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fill_occlusions import fill_occlusions
import fill_occlusions as fo

def _built_two_pieces():
    # body 60x60 at (0,0) green; arm 20x60 at (20,0) red, drawn ABOVE body.
    body = np.zeros((60, 60, 4), np.uint8); body[:, :] = [0, 200, 0, 255]
    # the body texture under the arm is polluted with arm pixels (as cut from a flat source):
    body[:, 20:40] = [200, 0, 0, 255]
    arm = np.zeros((60, 20, 4), np.uint8); arm[:, :] = [200, 0, 0, 255]
    canvas = (60, 60)
    def full(img, off):
        m = np.zeros(canvas, bool); m[off[1]:off[1]+img.shape[0], off[0]:off[0]+img.shape[1]] = img[:, :, 3] > 8
        return m
    return [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm",  "fill": "extend", "img": arm,  "offset": (20, 0), "mask_full": full(arm, (20, 0))},
    ]

def test_occluded_zone_filled_from_own_pixels():
    built = _built_two_pieces()
    fill_occlusions(built)
    body = built[0]["img"]
    # pixels under the arm must now look like body (green-ish), not arm (red)
    assert body[30, 30, 1] > body[30, 30, 0]
    # un-occluded pixels untouched
    assert tuple(body[30, 5, :3]) == (0, 200, 0)

def test_fill_none_leaves_piece_alone():
    built = _built_two_pieces()
    built[0]["fill"] = "none"
    before = built[0]["img"].copy()
    fill_occlusions(built)
    assert (built[0]["img"] == before).all()

def test_topmost_piece_never_modified():
    built = _built_two_pieces()
    before = built[1]["img"].copy()
    fill_occlusions(built)
    assert (built[1]["img"] == before).all()

def test_entirely_hidden_piece_left_unchanged():
    built = _built_two_pieces()
    # shrink body's own alpha to nothing outside the arm's footprint, so body has
    # no valid (un-occluded) pixels to extend from.
    body = built[0]["img"]
    body[:, :20] = 0
    body[:, 40:] = 0
    before = body.copy()
    fill_occlusions(built)
    assert (built[0]["img"] == before).all()

def test_no_occlusion_is_noop():
    built = _built_two_pieces()
    # move arm's mask_full off of body's own alpha entirely (no intersection)
    built[1]["mask_full"] = np.zeros((60, 60), bool)
    before = built[0]["img"].copy()
    fill_occlusions(built)
    assert (built[0]["img"] == before).all()

def _built_gradient_pieces():
    # body 60x60: rows 0-4 and 55-59 fully TRANSPARENT (hard-zero RGBA, as
    # cut_pieces produces), rows 5-54 opaque green in horizontal stripes of 10:
    # g = 100 for rows 5-9/20-29/40-49, g = 200 for rows 10-19/30-39/50-54.
    body = np.zeros((60, 60, 4), np.uint8)
    for r in range(5, 55):
        g = 100 if (r // 10) % 2 == 0 else 200
        body[r, :] = [0, g, 0, 255]
    # pollution under the arm (RGB only; alpha stays as-is):
    body[5:55, 20:40, :3] = [200, 0, 0]
    arm = np.zeros((60, 20, 4), np.uint8); arm[:, :] = [200, 0, 0, 255]
    canvas = (60, 60)
    def full(img, off):
        m = np.zeros(canvas, bool); m[off[1]:off[1]+img.shape[0], off[0]:off[0]+img.shape[1]] = img[:, :, 3] > 8
        return m
    return [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm",  "fill": "extend", "img": arm,  "offset": (20, 0), "mask_full": full(arm, (20, 0))},
    ]

def test_blur_actually_applied_on_gradient():
    built = _built_gradient_pieces()
    fill_occlusions(built)
    body = built[0]["img"]
    # (10, 30): nearest valid pixel is (10, 40) with green exactly 200 (stripe
    # value at row 10). Pure NN extension would leave 200; the blur mixes the
    # adjacent 100-stripe (rows 5-9), so the value must move noticeably.
    g = int(body[10, 30, 1])
    assert abs(g - 200) > 10, f"blur not applied: filled green {g} ~= NN value 200"
    assert body[10, 30, 1] > body[10, 30, 0]  # still body-colored, not arm red

def test_no_black_bleed_at_alpha_edge():
    built = _built_gradient_pieces()
    fill_occlusions(built)
    body = built[0]["img"]
    # (6, 30) is a filled pixel one row inside the alpha edge (rows 0-4 are
    # transparent black). Its valid same-row neighbors are green 100. A plain
    # blur over raw RGB would drag it toward black (< 100); the masked blur
    # must not make it darker than BOTH neighbors.
    g = int(body[6, 30, 1])
    left, right = int(body[6, 19, 1]), int(body[6, 40, 1])
    assert not (g < left and g < right), \
        f"black bleed at alpha edge: filled {g} < neighbors {left}/{right}"

def test_upper_variant_footprint_also_filled():
    built = _built_two_pieces()
    # arm's swap variant is WIDER than the base arm: covers cols 15..45.
    big = np.zeros((60, 30, 4), np.uint8); big[:, :] = [0, 0, 200, 255]
    built[1]["variants"] = [{"name": "arm_big", "img": big, "offset": (15, 0)}]
    # pollute body under the variant-only zone too
    built[0]["img"][:, 15:20] = [200, 0, 0, 255]
    fill_occlusions(built)
    body = built[0]["img"]
    # variant-only zone (col 17) must be repainted body-green as well
    assert body[30, 17, 1] > body[30, 17, 0]

def test_variant_overrunning_canvas_is_clipped_not_fatal():
    built = _built_two_pieces()
    big = np.zeros((80, 80, 4), np.uint8); big[:, :] = [0, 0, 200, 255]
    built[1]["variants"] = [{"name": "huge", "img": big, "offset": (-10, -10)}]
    fill_occlusions(built)  # must not raise

def test_alpha_channel_untouched():
    built = _built_two_pieces()
    before_alpha = built[0]["img"][:, :, 3].copy()
    fill_occlusions(built)
    assert (built[0]["img"][:, :, 3] == before_alpha).all()


# --- auto mode / diffusion inpaint --------------------------------------
#
# Reproduces the robot body/arm_r "ghost" bug: nearest-valid-pixel extension
# is a per-row-nearest lookup, so a MINORITY of off-tone valid pixels sitting
# right at the occlusion boundary get copied VERBATIM across the whole
# occluded width at that row. Deep (limb-sized) occlusions need diffusion
# instead so those minority boundary colors get diluted by their neighbors.

DARK = (32, 32, 32)     # #202020 — outline color that bleeds in the real bug
GLOW = (64, 192, 96)    # #40c060 — green rim-light color that bleeds too
BROWN = (128, 96, 64)   # #806040 — the piece's actual (correct) armor tone


def _built_deep_block_pieces():
    # body: 120x200 canvas, fully opaque brown. The "arm" occludes the FULL
    # height for columns [70,130) (60px wide) -- occlusion touches both the
    # top and bottom edges of the piece, so nearest-valid extension is purely
    # horizontal per row (matches the robot body/arm_r bug geometry: no
    # valid pixels above/below, only left/right). The immediate boundary
    # columns (69 and 130) carry a minority band of dark-outline pixels
    # (rows 43-47) and green-glow pixels (rows 73-77) on BOTH sides, amid
    # otherwise-uniform brown -- nearest-neighbor extension copies these
    # across the full 60px block width at those rows (diluted only by the
    # final blur pass), while diffusion mixes in the much larger brown
    # majority first.
    H, W = 120, 200
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, :3] = BROWN
    body[:, :, 3] = 255
    body[43:48, 69] = (*DARK, 255)
    body[43:48, 130] = (*DARK, 255)
    body[73:78, 69] = (*GLOW, 255)
    body[73:78, 130] = (*GLOW, 255)
    arm = np.zeros((H, 60, 4), np.uint8)
    arm[:, :] = [200, 0, 0, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    return [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (70, 0), "mask_full": full(arm, (70, 0))},
    ]


def _built_thin_seam_pieces():
    # Same canvas, but the occluding arm is only 4px wide -- a shallow seam
    # that must stay on the extend path under auto. Body rows are striped
    # (10px bands alternating BROWN / a second tone) rather than flat, so a
    # forced diffusion pass (mode="inpaint") has vertical color variation to
    # blend across -- proving the force-knob actually changes something,
    # while the (purely horizontal, per-row) extend result reproduces the
    # stripe pattern exactly across the thin gap.
    H, W = 120, 200
    body = np.zeros((H, W, 4), np.uint8)
    for r in range(H):
        body[r, :, :3] = BROWN if (r // 10) % 2 == 0 else (100, 140, 180)
    body[:, :, 3] = 255
    arm = np.zeros((H, 4, 4), np.uint8)
    arm[:, :] = [200, 0, 0, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    return [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (98, 0), "mask_full": full(arm, (98, 0))},
    ]


def test_thin_seam_byte_identical_to_extend():
    built_extend = _built_thin_seam_pieces()
    fill_occlusions(built_extend, mode="extend")
    built_auto = _built_thin_seam_pieces()
    fill_occlusions(built_auto, mode="auto")
    assert (built_extend[0]["img"] == built_auto[0]["img"]).all()


def test_deep_zone_uses_diffusion_in_auto():
    built_extend = _built_deep_block_pieces()
    fill_occlusions(built_extend, mode="extend")
    built_auto = _built_deep_block_pieces()
    fill_occlusions(built_auto, mode="auto")
    body_extend = built_extend[0]["img"]
    body_auto = built_auto[0]["img"]
    # inside the deep block, the two results must diverge: extend streaks
    # verbatim boundary colors, diffusion smooths them away
    block = np.s_[40:80, 70:130]
    assert not (body_extend[block] == body_auto[block]).all()


def test_diffusion_dilutes_minority_boundary_colors():
    built_extend = _built_deep_block_pieces()
    fill_occlusions(built_extend, mode="extend")
    built_auto = _built_deep_block_pieces()
    fill_occlusions(built_auto, mode="auto")
    body_extend = built_extend[0]["img"]
    body_auto = built_auto[0]["img"]

    center_cols = range(85, 116, 5)
    brown = np.array(BROWN, float)
    dark = np.array(DARK, float)
    glow = np.array(GLOW, float)

    # bug reproduction: even after the shared final blur pass, extend's
    # nearest-row copy leaves the block-center pixels closer to the
    # minority dark/green boundary color than to the piece's true (brown)
    # tone -- the leak survives all the way to the center (pins the bug).
    for x in center_cols:
        px = body_extend[45, x, :3].astype(float)
        assert np.linalg.norm(px - dark) < np.linalg.norm(px - brown), \
            f"extend not dark-polluted at (45,{x}): {px}"
        px = body_extend[75, x, :3].astype(float)
        assert np.linalg.norm(px - glow) < np.linalg.norm(px - brown), \
            f"extend not green-polluted at (75,{x}): {px}"

    # diffusion: every sampled block-center pixel must land closer to the
    # brown boundary tone than to either minority color (numeric, not just
    # "different")
    for y in (45, 75):
        for x in center_cols:
            px = body_auto[y, x, :3].astype(float)
            d_brown = np.linalg.norm(px - brown)
            d_dark = np.linalg.norm(px - dark)
            d_glow = np.linalg.norm(px - glow)
            assert d_brown < d_dark, f"diffusion pixel ({y},{x})={px} closer to dark than brown"
            assert d_brown < d_glow, f"diffusion pixel ({y},{x})={px} closer to glow than brown"


def test_valid_pixels_never_change():
    for mode in ("extend", "auto", "inpaint"):
        built = _built_deep_block_pieces()
        body_before = built[0]["img"].copy()
        own = body_before[:, :, 3] > 8
        occl = built[1]["mask_full"][:body_before.shape[0], :body_before.shape[1]] & own
        valid = own & ~occl
        fill_occlusions(built, mode=mode)
        body_after = built[0]["img"]
        assert (body_after[valid] == body_before[valid]).all(), f"mode={mode}"


def test_alpha_untouched():
    for mode in ("extend", "auto", "inpaint"):
        built = _built_deep_block_pieces()
        before_alpha = built[0]["img"][:, :, 3].copy()
        fill_occlusions(built, mode=mode)
        assert (built[0]["img"][:, :, 3] == before_alpha).all(), f"mode={mode}"


def test_mode_validation():
    built = _built_thin_seam_pieces()
    with pytest.raises(ValueError):
        fill_occlusions(built, mode="bogus")


def test_forced_inpaint_applies_to_thin_seams():
    built_extend = _built_thin_seam_pieces()
    fill_occlusions(built_extend, mode="extend")
    built_inpaint = _built_thin_seam_pieces()
    fill_occlusions(built_inpaint, mode="inpaint")
    assert not (built_extend[0]["img"] == built_inpaint[0]["img"]).all()


# --- pyramid regression: the multigrid restriction must actually operate ---
#
# A left-dark -> right-light linear gradient is harmonic (zero Laplacian) and
# depends only on x, so the exact converged fill for a deep occluded interior
# is that same linear ramp continued across the gap -- independent of row.
# Nearest-valid extension instead produces a hard step (whichever side is
# closer), and a broken pyramid (restriction collapses the Dirichlet ring at
# the first halving, see _diffuse_level's docstring) leaves that step almost
# fully intact after only a few hundred bounded Jacobi rounds. A working
# pyramid must resolve it close to the true ramp well within the iteration
# budget.

GRAD_DARK, GRAD_LIGHT = 20.0, 235.0


def _built_gradient_ramp_pieces(occl_w=200, margin=40, H=30):
    # canvas W = margin + occl_w + margin; body carries the TRUE linear ramp
    # (by column, same for every row) everywhere, then the columns under the
    # arm are polluted with a flat off-ramp color (as a cut source would
    # bake in) before the arm is drawn on top.
    W = margin * 2 + occl_w
    xs = np.arange(W)
    ramp = GRAD_DARK + (GRAD_LIGHT - GRAD_DARK) * (xs / (W - 1))
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, 3] = 255
    for c in range(3):
        body[:, :, c] = ramp[None, :].astype(np.uint8)
    body[:, margin:margin + occl_w, :3] = [200, 0, 0]  # pollution under the arm
    arm = np.zeros((H, occl_w, 4), np.uint8)
    arm[:, :] = [200, 0, 0, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    return [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (margin, 0), "mask_full": full(arm, (margin, 0))},
    ], W, margin, occl_w


def test_deep_gradient_interior_approximates_harmonic_interpolation():
    # deep interior (140px, well over DEEP_OCCLUSION_PX and DIFFUSE_PYRAMID_MIN).
    # Probe at the QUARTER point rather than dead-center: nearest-valid
    # extend produces a hard step exactly at the midpoint between the two
    # boundaries, and the final masked-blur pass (sigma=3) only smooths a
    # narrow band around that discontinuity -- a dead-center probe would
    # coincidentally land in that blurred band and look deceptively close to
    # the true ramp even under plain extend. A quarter-point probe sits deep
    # in extend's flat (unblurred) region, so it cleanly separates "actually
    # diffused" from "just extended".
    built, W, margin, occl_w = _built_gradient_ramp_pieces()
    h_mid = built[0]["img"].shape[0] // 2
    fill_occlusions(built, mode="inpaint")  # force diffusion for every component
    body = built[0]["img"]
    probe_x = margin + occl_w // 4
    truth = GRAD_DARK + (GRAD_LIGHT - GRAD_DARK) * (probe_x / (W - 1))
    probe = float(body[h_mid, probe_x, 0])
    assert abs(probe - truth) < 20, (
        f"probe pixel {probe} far from harmonic-interpolated truth {truth:.1f} "
        f"(a broken/bypassed pyramid leaves it near a flat extend step instead)"
    )
    # sanity: extend alone (no diffusion) must NOT already satisfy this on its
    # own, otherwise the assertion above wouldn't be exercising the pyramid.
    built_extend, _, _, _ = _built_gradient_ramp_pieces()
    fill_occlusions(built_extend, mode="extend")
    probe_extend = float(built_extend[0]["img"][h_mid, probe_x, 0])
    assert abs(probe_extend - truth) > 20, "test setup issue: extend alone already matches the ramp"


# --- edge cases (finding 4) ------------------------------------------------

def test_component_flush_with_image_boundary():
    # arm occludes cols [0,40) i.e. from the LEFT edge, and the full height,
    # so the occluded component touches three of the four canvas edges at
    # once -- no valid pixels exist above/below/left of it, only to the
    # right. _diffuse_fill's window padding can't pad on those sides at all
    # (clamped to 0). Must not crash; valid pixels/alpha stay untouched.
    H, W = 60, 100
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, 3] = 255
    body[:, :, 1] = 180  # green body
    body[:, :40, :3] = [200, 0, 0]  # pollution under the arm
    arm = np.zeros((H, 40, 4), np.uint8)
    arm[:, :] = [200, 0, 0, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    built = [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (0, 0), "mask_full": full(arm, (0, 0))},
    ]
    before_alpha = body[:, :, 3].copy()
    valid = np.ones((H, W), bool)
    valid[:, :40] = False
    valid_before = body.copy()

    fill_occlusions(built, mode="auto")  # must not raise

    after = built[0]["img"]
    assert (after[:, :, 3] == before_alpha).all()
    assert (after[valid] == valid_before[valid]).all()
    # occluded zone got repainted away from the raw pollution color
    assert after[30, 10, 1] > after[30, 10, 0]


def test_1px_wide_component_forced_inpaint():
    # a single-column occlusion, forced through the diffusion path even
    # though DEEP_OCCLUSION_PX would normally keep something this shallow on
    # extend under auto.
    H, W = 30, 50
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, 3] = 255
    body[:, :20, :3] = [40, 40, 40]
    body[:, 20:, :3] = [220, 220, 220]
    body[:, 24] = [128, 0, 0, 255]  # pollution under the 1px arm
    arm = np.zeros((H, 1, 4), np.uint8)
    arm[:, :] = [128, 0, 0, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    built = [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (24, 0), "mask_full": full(arm, (24, 0))},
    ]
    before_alpha = body[:, :, 3].copy()

    fill_occlusions(built, mode="inpaint")  # must not raise

    after = built[0]["img"]
    assert (after[:, :, 3] == before_alpha).all()
    assert (after[:, :20] == [40, 40, 40, 255]).all()
    assert (after[:, 25:] == [220, 220, 220, 255]).all()
    # the 1px gap must not still carry the raw pollution color
    assert not (after[:, 24, :3] == [128, 0, 0]).all()


def test_odd_dimension_deep_block():
    # odd height AND odd width, occluded block also odd-sized and deep
    # enough to exercise the pyramid's Hp/Wp odd-padding path at every level.
    H, W = 121, 199
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, 3] = 255
    body[:, :, 2] = 150  # blue body
    body[:, 39:150, :3] = [10, 10, 10]  # pollution under the arm (111px wide)
    arm = np.zeros((H, 111, 4), np.uint8)
    arm[:, :] = [10, 10, 10, 255]
    canvas = (H, W)

    def full(img, off):
        m = np.zeros(canvas, bool)
        m[off[1]:off[1] + img.shape[0], off[0]:off[0] + img.shape[1]] = img[:, :, 3] > 8
        return m

    built = [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": full(body, (0, 0))},
        {"name": "arm", "fill": "extend", "img": arm, "offset": (39, 0), "mask_full": full(arm, (39, 0))},
    ]
    before_alpha = body[:, :, 3].copy()

    fill_occlusions(built, mode="inpaint")  # must not raise despite odd dims

    after = built[0]["img"]
    assert (after[:, :, 3] == before_alpha).all()
    assert (after[:, :39] == [0, 0, 150, 255]).all()
    assert (after[:, 150:] == [0, 0, 150, 255]).all()
    assert after[60, 95, 2] > after[60, 95, 0]  # repainted blue-ish, not raw pollution


def test_many_small_components_runtime_sanity():
    # dozens of small, disjoint occluded blobs scattered across one piece --
    # must complete quickly (no accidental O(n^2)-per-component blowup) and
    # without crashing.
    H, W = 200, 200
    body = np.zeros((H, W, 4), np.uint8)
    body[:, :, 3] = 255
    body[:, :, 1] = 160
    canvas = (H, W)
    arm_mask = np.zeros(canvas, bool)
    for gy in range(0, H, 20):
        for gx in range(0, W, 20):
            y0, x0 = gy + 3, gx + 3
            body[y0:y0 + 14, x0:x0 + 14, :3] = [90, 0, 0]  # pollution
            arm_mask[y0:y0 + 14, x0:x0 + 14] = True

    built = [
        {"name": "body", "fill": "extend", "img": body, "offset": (0, 0), "mask_full": np.zeros(canvas, bool)},
        {"name": "arm", "fill": "extend", "img": np.zeros((1, 1, 4), np.uint8), "offset": (0, 0),
         "mask_full": arm_mask},
    ]
    t0 = time.time()
    fill_occlusions(built, mode="auto")
    dt = time.time() - t0
    assert dt < 5.0, f"many-small-components fill took too long: {dt:.2f}s"
    after = built[0]["img"]
    assert (after[:, :, 3] == 255).all()


def test_deep_occlusion_warns_invented_content(capsys):
    # A zone deeper than WARN_OCCLUSION_PX is invented content — the build must
    # say so loudly (markup smell: polygon claiming a neighbor's territory).
    built, _, _, _ = _built_gradient_ramp_pieces(occl_w=200)   # ~100px EDT depth
    fill_occlusions(built, mode="auto")
    err = capsys.readouterr().err
    assert "WARNING" in err and "invented content" in err
    assert built[0]["name"] in err          # names the offending piece
    assert "playbook" in err                # points at the rule to check


def test_thin_seam_does_not_warn(capsys):
    built = _built_thin_seam_pieces()
    fill_occlusions(built, mode="auto")
    assert "WARNING" not in capsys.readouterr().err


def test_warning_fires_in_extend_mode_too(capsys):
    # The warning is about the markup, not the fill algorithm — forcing the
    # old extend path must not silence it.
    built, _, _, _ = _built_gradient_ramp_pieces(occl_w=200)
    fill_occlusions(built, mode="extend")
    assert "invented content" in capsys.readouterr().err
