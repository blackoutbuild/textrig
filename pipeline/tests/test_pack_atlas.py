import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack_atlas import pack, compose_atlas


def test_no_overlap_and_padding():
    rects = [("a", 50, 30), ("b", 20, 60), ("c", 40, 40), ("d", 10, 10)]
    pos, (W, H) = pack(rects, padding=2, max_width=128)
    boxes = {}
    for name, w, h in rects:
        x, y = pos[name]
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H
        boxes[name] = (x, y, w, h)
    names = list(boxes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            x1, y1, w1, h1 = boxes[names[i]]; x2, y2, w2, h2 = boxes[names[j]]
            # Full 2*padding gutter between regions (anti-bleed), not just padding.
            assert x1 + w1 + 4 <= x2 or x2 + w2 + 4 <= x1 or y1 + h1 + 4 <= y2 or y2 + h2 + 4 <= y1


def test_item_wider_than_atlas_fails():
    with pytest.raises(SystemExit):
        pack([("huge", 5000, 10)], max_width=2048)


def test_compose_atlas_pixels_land_at_positions():
    a = np.full((10, 20, 4), 100, np.uint8)
    b = np.full((30, 5, 4), 200, np.uint8)
    atlas, subtex = compose_atlas([("a", a), ("b", b)], padding=2, max_width=64)
    st = {s["name"]: s for s in subtex}
    sa = st["a"]
    assert atlas[sa["y"] + 1, sa["x"] + 1, 0] == 100
    assert sa["width"] == 20 and sa["height"] == 10


def test_shelf_wrap_forces_new_row():
    # Two items each ~40px wide; max_width only fits one per shelf -> different y.
    rects = [("a", 40, 30), ("b", 40, 20)]
    pos, (W, H) = pack(rects, padding=2, max_width=50)
    assert pos["a"][1] != pos["b"][1]  # landed on different shelves
    # Tallest-first ordering: 'a' (h30) on shelf 0, 'b' below it.
    assert pos["a"][1] < pos["b"][1]


def test_single_item_exact_extent():
    pos, (W, H) = pack([("only", 30, 40)], padding=2, max_width=128)
    assert pos["only"] == (2, 2)
    assert W == 34 and H == 44  # w+2*pad, h+2*pad


def test_item_exactly_fills_max_width():
    # w + 2*padding == max_width must be accepted, not rejected.
    pos, (W, H) = pack([("fit", 60, 10)], padding=2, max_width=64)
    assert pos["fit"] == (2, 2)
    assert W == 64


def test_height_one_items():
    rects = [("a", 10, 1), ("b", 10, 1)]
    pos, (W, H) = pack(rects, padding=2, max_width=128)
    # Both fit on one shelf; shelf height = 1 + 2*pad = 5.
    assert pos["a"][1] == pos["b"][1]
    assert H == 5


def test_empty_input_returns_zero_atlas():
    pos, (W, H) = pack([], padding=2, max_width=64)
    assert pos == {}
    assert (W, H) == (0, 0)


def test_duplicate_names_fail_loud():
    with pytest.raises(SystemExit):
        pack([("dup", 10, 10), ("dup", 20, 20)], padding=2, max_width=128)


def test_compose_subtex_matches_pack_positions():
    imgs = [
        ("a", np.full((30, 40, 4), 10, np.uint8)),
        ("b", np.full((20, 40, 4), 20, np.uint8)),
        ("c", np.full((10, 10, 4), 30, np.uint8)),
    ]
    rects = [(n, im.shape[1], im.shape[0]) for n, im in imgs]
    pos, (W, H) = pack(rects, padding=2, max_width=64)
    atlas, subtex = compose_atlas(imgs, padding=2, max_width=64)
    assert atlas.shape == (H, W, 4)
    st = {s["name"]: s for s in subtex}
    for name, im in imgs:
        x, y = pos[name]
        s = st[name]
        assert (s["x"], s["y"]) == (x, y)
        assert s["width"] == im.shape[1] and s["height"] == im.shape[0]
        # Untrimmed region: no frame* keys (contract §13).
        assert "frameX" not in s and "frameWidth" not in s
        # Pixels land at the region (interior sample avoids padding gutter).
        assert atlas[y + 1, x + 1, 0] == im[0, 0, 0]


def test_compose_rejects_float_image():
    a = np.full((10, 10, 4), 0.5, np.float32)
    with pytest.raises(SystemExit):
        compose_atlas([("a", a)], padding=2, max_width=64)


def test_atlas_over_8192_fails_loud():
    # Six 1400w x 2000h strips at max_width=1440 -> only one fits per shelf
    # (1404+1404 > 1440), so they stack into 6 rows of 2004px -> H = 12024,
    # well past the 8192 GPU texture limit. Fails before the big np.zeros
    # allocation (~67MB here, but the point is it never grows further).
    imgs = [(f"p{i}", np.zeros((2000, 1400, 4), np.uint8)) for i in range(6)]
    with pytest.raises(SystemExit):
        compose_atlas(imgs, max_width=1440)


def test_atlas_over_4096_warns(capsys):
    # Three 1400w x 1600h strips at max_width=1440 -> one per shelf -> 3 rows
    # of 1604px -> H = 4812, between the 4096 warn threshold and the 8192
    # hard limit.
    imgs = [(f"p{i}", np.zeros((1600, 1400, 4), np.uint8)) for i in range(3)]
    compose_atlas(imgs, max_width=1440)
    assert "4096" in capsys.readouterr().err


def test_atlas_under_4096_is_quiet(capsys):
    imgs = [(f"p{i}", np.full((100, 100, 4), 50, np.uint8)) for i in range(3)]
    compose_atlas(imgs, max_width=1440)
    assert capsys.readouterr().err == ""


def test_compose_no_pixel_bleed_between_regions():
    a = np.full((10, 10, 4), 111, np.uint8)
    b = np.full((10, 10, 4), 222, np.uint8)
    atlas, subtex = compose_atlas([("a", a), ("b", b)], padding=2, max_width=64)
    st = {s["name"]: s for s in subtex}
    # Padding gutter between the two regions stays zero (transparent).
    sa, sb = st["a"], st["b"]
    gutter_col = sa["x"] + sa["width"]  # first column past region a
    assert atlas[sa["y"], gutter_col, 0] == 0
