import sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from normalize_input import ensure_alpha

def _img(arr):
    return Image.fromarray(arr.astype(np.uint8))

def test_existing_alpha_passthrough():
    arr = np.zeros((20, 20, 4), np.uint8)
    arr[5:15, 5:15] = [255, 0, 0, 255]          # opaque square, transparent bg
    out = np.array(ensure_alpha(_img(arr)))
    assert (out == arr).all()

def test_uniform_bg_keyed_out():
    arr = np.full((40, 40, 3), 250, np.uint8)   # near-white bg
    arr[10:30, 10:30] = [200, 30, 30]           # red square subject
    out = np.array(ensure_alpha(_img(arr)))
    assert out.shape[2] == 4
    assert out[0, 0, 3] == 0                    # corner is background
    assert out[20, 20, 3] == 255                # subject opaque

def test_subject_holes_not_keyed():
    # bg-colored pixels INSIDE the subject must stay opaque (not border-connected)
    arr = np.full((40, 40, 3), 250, np.uint8)
    arr[5:35, 5:35] = [200, 30, 30]
    arr[18:22, 18:22] = 250                     # bg-colored patch inside subject
    out = np.array(ensure_alpha(_img(arr)))
    assert out[20, 20, 3] == 255

def test_complex_bg_fails_loud_without_rembg():
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 255, (40, 40, 3), dtype=np.uint8)
    with pytest.raises(SystemExit):
        ensure_alpha(_img(arr))

def test_feathered_edge_has_intermediate_alpha():
    arr = np.full((40, 40, 3), 250, np.uint8)
    arr[10:30, 10:30] = [200, 30, 30]
    out = np.array(ensure_alpha(_img(arr)))
    edge = out[10, 20, 3]                       # right on the cut boundary
    assert 0 < edge < 255

def test_bg_rembg_without_rembg_installed_fails_loud(monkeypatch):
    # rembg is a baked dependency in this venv (pipeline/fetch_models.py), so
    # simulate its absence via import machinery rather than relying on an
    # actually-missing package — this still exercises the fail() path in
    # normalize_input._rembg's ImportError branch.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rembg":
            raise ImportError("simulated: rembg not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    arr = np.full((20, 20, 3), 250, np.uint8)
    with pytest.raises(SystemExit):
        ensure_alpha(_img(arr), bg="rembg")

def test_single_stray_alpha_pixel_still_keyed():
    # one 254-alpha pixel (JPEG-sourced / editor-mangled PNG) is NOT a real
    # alpha channel — the image must still be color-keyed, not passed through
    arr = np.full((40, 40, 4), [250, 250, 250, 255], np.uint8)
    arr[10:30, 10:30] = [200, 30, 30, 255]
    arr[0, 5, 3] = 254                          # single stray sub-opaque pixel
    out = np.array(ensure_alpha(_img(arr)))
    assert out[0, 0, 3] == 0                    # bg got keyed, not passed through
    assert out[20, 20, 3] == 255

def test_tolerance_knob_changes_outcome():
    # bg patch at distance 30 from the border color: beyond default tolerance
    # (24) it stays opaque; with tolerance=40 it is border-connected and keyed
    arr = np.full((40, 40, 3), 250, np.uint8)
    arr[15:30, 15:30] = [200, 30, 30]           # subject
    arr[2:10, 2:10] = 220                       # off-color bg patch near border
    out_default = np.array(ensure_alpha(_img(arr)))
    out_raised = np.array(ensure_alpha(_img(arr), tolerance=40))
    assert out_default[6, 6, 3] > 200           # not keyed at default tolerance
    assert out_raised[6, 6, 3] < 50             # keyed once tolerance is raised

def test_non_square_image_keys_correctly():
    arr = np.full((30, 60, 3), 250, np.uint8)   # 60 wide x 30 tall
    arr[8:22, 20:40] = [200, 30, 30]
    out = np.array(ensure_alpha(_img(arr)))
    assert out[0, 0, 3] == 0
    assert out[29, 59, 3] == 0
    assert out[15, 30, 3] == 255
