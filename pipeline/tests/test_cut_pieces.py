import sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cut_pieces import cut_all, polygon_mask

def _src():
    arr = np.zeros((100, 100, 4), np.uint8)
    arr[10:90, 10:90] = [50, 100, 150, 255]     # opaque block
    return arr

def test_polygon_mask_shape():
    m = polygon_mask([[0, 0], [10, 0], [10, 10], [0, 10]], (20, 20))
    assert m.dtype == bool and m.shape == (20, 20)
    assert m[5, 5] and not m[15, 15]

def test_polygon_piece_cut_and_offset():
    # source opaque EXACTLY where the polygon is — uncovered-pixel takeover
    # (seams.py) has nothing to absorb, so this asserts pure crop geometry
    src = np.zeros((100, 100, 4), np.uint8)
    src[20:61, 20:61] = [50, 100, 150, 255]
    pieces = [{"name": "p", "type": "rigid", "bone": "root", "fill": "extend",
               "variants": [], "base_dir": ".",
               "source": {"polygon": [[20, 20], [60, 20], [60, 60], [20, 60]]}}]
    built = cut_all(src, pieces)
    p = built[0]
    assert p["offset"] == (20, 20)
    # Pillow 12.x's ImageDraw.polygon fills inclusive of the closing vertex
    # (x=60,y=60), giving a 41x41 bbox for corners 20..60; older Pillow
    # excluded that last row/col (40x40). Assert against actual behavior.
    assert p["img"].shape[:2] == (41, 41)
    assert p["mask_full"].shape == (100, 100)
    assert p["img"][:, :, 3].max() == 255

def test_empty_intersection_fails():
    pieces = [{"name": "p", "type": "rigid", "bone": "root", "fill": "extend",
               "variants": [], "base_dir": ".",
               "source": {"polygon": [[0, 0], [5, 0], [5, 5], [0, 5]]}}]  # transparent zone
    with pytest.raises(SystemExit):
        cut_all(_src(), pieces)

def test_file_piece_and_variant(tmp_path):
    layer = np.zeros((30, 30, 4), np.uint8); layer[:, :] = [1, 2, 3, 255]
    Image.fromarray(layer).save(tmp_path / "arm.png")
    Image.fromarray(layer).save(tmp_path / "arm2.png")
    pieces = [{"name": "arm", "type": "swap", "bone": "root", "fill": "extend",
               "base_dir": str(tmp_path),
               "source": {"file": "arm.png", "offset": [40, 50]},
               "variants": [{"name": "arm2", "file": "arm2.png", "offset": [41, 51]}]}]
    built = cut_all(_src(), pieces)
    assert built[0]["offset"] == (40, 50)
    assert built[0]["variants"][0]["offset"] == (41, 51)

def test_polygon_bbox_crops_to_alpha_intersection():
    # polygon pokes into the transparent border: bbox must shrink to the
    # intersection of polygon & alpha>threshold, so offset lands at (10, 10)
    pieces = [{"name": "p2", "type": "rigid", "bone": "root", "fill": "extend",
               "variants": [], "base_dir": ".",
               "source": {"polygon": [[5, 5], [60, 5], [60, 60], [5, 60]]}}]
    built = cut_all(_src(), pieces)
    assert built[0]["offset"] == (10, 10)

def test_polygon_pixels_outside_polygon_zeroed():
    # right triangle: bbox is the square (20,20)-(60,60) but the top-right
    # bbox corner lies OUTSIDE the triangle -> its alpha must be zeroed,
    # while pixels inside the triangle keep alpha 255. A full-block companion
    # piece leaves takeover nothing to absorb; underlap=0 keeps tri's mask
    # from growing under it — pure polygon∧alpha semantics under test.
    pieces = [{"name": "tri", "type": "rigid", "bone": "root", "fill": "extend",
               "variants": [], "base_dir": ".",
               "source": {"polygon": [[20, 20], [20, 60], [60, 60]]}},
              {"name": "rest", "type": "rigid", "bone": "root", "fill": "extend",
               "variants": [], "base_dir": ".",
               "source": {"polygon": [[10, 10], [90, 10], [90, 90], [10, 90]]}}]
    built = cut_all(_src(), pieces, underlap=0)
    img = built[0]["img"]
    assert built[0]["offset"] == (20, 20)
    assert img[0, -1, 3] == 0          # top-right bbox corner: outside triangle
    assert img[-1, 0, 3] == 255        # bottom-left corner: triangle vertex
    assert img[img.shape[0] // 2, 2, 3] == 255   # near hypotenuse-free left edge, inside

def test_fully_transparent_layer_fails(tmp_path):
    layer = np.zeros((30, 30, 4), np.uint8)  # alpha 0 everywhere
    Image.fromarray(layer).save(tmp_path / "ghost.png")
    pieces = [{"name": "ghost", "type": "swap", "bone": "root", "fill": "extend",
               "base_dir": str(tmp_path), "variants": [],
               "source": {"file": "ghost.png", "offset": [0, 0]}}]
    with pytest.raises(SystemExit):
        cut_all(_src(), pieces)

def test_file_piece_offset_overflow_fails_loudly(tmp_path, capsys):
    # offset + layer size extends past the 100x100 canvas edge -> friendly
    # fail() naming the piece, layer size, offset, and canvas size
    layer = np.zeros((30, 30, 4), np.uint8); layer[:, :] = [1, 2, 3, 255]
    Image.fromarray(layer).save(tmp_path / "arm.png")
    pieces = [{"name": "arm", "type": "swap", "bone": "root", "fill": "extend",
               "base_dir": str(tmp_path), "variants": [],
               "source": {"file": "arm.png", "offset": [90, 90]}}]
    with pytest.raises(SystemExit):
        cut_all(_src(), pieces)
    err = capsys.readouterr().err
    assert "arm" in err and "30x30" in err and "(90, 90)" in err and "100x100" in err

def test_file_piece_mask_full_shape_and_content(tmp_path):
    layer = np.zeros((30, 30, 4), np.uint8); layer[:, :] = [1, 2, 3, 255]
    Image.fromarray(layer).save(tmp_path / "arm.png")
    pieces = [{"name": "arm", "type": "swap", "bone": "root", "fill": "extend",
               "base_dir": str(tmp_path), "variants": [],
               "source": {"file": "arm.png", "offset": [40, 50]}}]
    built = cut_all(_src(), pieces)
    mf = built[0]["mask_full"]
    assert mf.shape == (100, 100)
    assert mf[50, 40] and not mf[0, 0]
