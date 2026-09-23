"""Seam passes: uncovered-pixel takeover + auto-underlap (seams.py, cut_all)."""
import numpy as np
import pytest

import cut_pieces
import seams


def canvas(w=100, h=60):
    """Opaque plate: left rect [5,55)x, right rect [55,95)x — distinct colors,
    3px transparent gap would be carved by the caller when needed."""
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[10:50, 5:55] = (200, 0, 0, 255)     # left: red
    rgba[10:50, 55:95] = (0, 0, 200, 255)    # right: blue
    return rgba


def piece(name, poly, **kw):
    return {"name": name, "type": "rigid", "bone": "b", "fill": kw.pop("fill", "auto"),
            "source": {"polygon": poly}, "variants": [], "base_dir": ".", **kw}


def masks_of(built):
    return {p["name"]: p["mask_full"] for p in built}


# ------------------------------------------------------------------ takeover

def test_uncovered_pixels_absorbed_by_nearest_piece(capsys):
    rgba = canvas()
    # polygons leave an uncovered vertical strip x=[40,70) in the middle
    built = cut_pieces.cut_all(rgba, [
        piece("left", [[5, 10], [40, 10], [40, 49], [5, 49]]),
        piece("right", [[70, 10], [94, 10], [94, 49], [70, 49]]),
    ], underlap=0)
    m = masks_of(built)
    opaque = rgba[:, :, 3] > cut_pieces.ALPHA_THRESHOLD
    assert not (opaque & ~(m["left"] | m["right"])).any(), "no opaque pixel may stay uncovered"
    # nearest-ownership: strip halves go to their closer piece
    assert m["left"][30, 45] and not m["right"][30, 45]
    assert m["right"][30, 65] and not m["left"][30, 65]
    assert "seams: WARNING" in capsys.readouterr().err


def test_takeover_silent_below_warn_threshold(capsys):
    rgba = canvas()
    # single 4x4 uncovered notch (16 px² < 32) at the left piece's edge
    built = cut_pieces.cut_all(rgba, [
        piece("left", [[5, 10], [54, 10], [54, 30], [50, 30], [50, 34],
                       [54, 34], [54, 49], [5, 49]]),
        piece("right", [[55, 10], [94, 10], [94, 49], [55, 49]]),
    ], underlap=0)
    m = masks_of(built)
    assert m["left"][32, 52]   # notch absorbed
    assert "seams: WARNING" not in capsys.readouterr().err


def test_takeover_never_routes_to_file_layers(tmp_path):
    from PIL import Image
    rgba = canvas()
    layer = tmp_path / "layer.png"
    Image.fromarray(rgba[10:50, 55:95]).save(layer)
    built = cut_pieces.cut_all(rgba, [
        piece("left", [[5, 10], [40, 10], [40, 49], [5, 49]]),
        {"name": "right", "type": "rigid", "bone": "b", "fill": "auto",
         "source": {"file": "layer.png", "offset": [55, 10]},
         "variants": [], "base_dir": str(tmp_path)},
    ], underlap=0)
    m = masks_of(built)
    # everything uncovered (x 40..55 strip AND right rect edge x=95 none) —
    # the whole strip must land on the polygon piece, never the layer
    opaque = rgba[:, :, 3] > cut_pieces.ALPHA_THRESHOLD
    assert not (opaque & ~(m["left"] | m["right"])).any()
    assert m["left"][30, 54], "file layer must not receive takeover pixels"


# ------------------------------------------------------------------ underlap

def test_underlap_band_grows_under_upper_and_is_inpainted():
    rgba = canvas()
    rgba[:, 55:95, :3] = (0, 0, 200)  # keep blue
    built = cut_pieces.cut_all(rgba, [
        piece("left", [[5, 10], [55, 10], [55, 49], [5, 49]]),
        piece("right", [[55, 10], [94, 10], [94, 49], [55, 49]]),
    ], underlap=12)
    m = masks_of(built)
    band = m["left"] & m["right"]
    assert band.any(), "left must dive under right"
    assert band[:, 68:].sum() == 0, "band must respect the 12px reach"
    assert m["right"][30, 60] and not m["left"][30, 80]
    # inpainted: the band pixels in LEFT's img must not ship blue (right's content)
    import fill_occlusions
    fill_occlusions.fill_occlusions(built, blur_sigma=0.0, mode="inpaint")
    left = built[0]
    ox, oy = left["offset"]
    ys, xs = np.where(band)
    vals = left["img"][ys - oy, xs - ox, :3].astype(int)
    assert (vals[:, 0] > vals[:, 2]).mean() > 0.9, "band must be filled from left's red, not right's blue"


def test_underlap_clamped_inside_solid_alpha():
    # right side translucent (< SOLID_ALPHA): the band has no solid room to
    # grow into — masks must come out exactly as with underlap off. (The 1px
    # shared-edge raster overlap exists in BOTH runs; only growth matters.)
    def run(u):
        rgba = canvas()
        rgba[10:50, 55:95, 3] = 60
        return masks_of(cut_pieces.cut_all(rgba, [
            piece("left", [[5, 10], [55, 10], [55, 49], [5, 49]]),
            piece("right", [[55, 10], [94, 10], [94, 49], [55, 49]]),
        ], underlap=u))
    off, on = run(0), run(12)
    assert (off["left"] == on["left"]).all(), \
        "no solid silhouette on the right — the band must not grow"


def test_underlap_zero_is_off_and_fill_none_excluded():
    def run(u, fill):
        built = cut_pieces.cut_all(canvas(), [
            piece("left", [[5, 10], [55, 10], [55, 49], [5, 49]], fill=fill),
            piece("right", [[55, 10], [94, 10], [94, 49], [55, 49]]),
        ], underlap=u)
        return masks_of(built)
    # fill:none piece must never grow, whatever the knob says
    assert (run(0, "none")["left"] == run(12, "none")["left"]).all()
    # sanity: with fill=auto the same knob DOES grow the mask
    assert run(12, "auto")["left"].sum() > run(0, "auto")["left"].sum()


def test_underlap_only_dives_under_later_pieces():
    rgba = canvas()
    built = cut_pieces.cut_all(rgba, [
        piece("left", [[5, 10], [55, 10], [55, 49], [5, 49]]),
        piece("right", [[55, 10], [94, 10], [94, 49], [55, 49]]),
    ], underlap=12)
    m = masks_of(built)
    # right (upper) must NOT grow into left (lower)
    assert not m["right"][30, 50], "upper piece must not dive under a lower one"
