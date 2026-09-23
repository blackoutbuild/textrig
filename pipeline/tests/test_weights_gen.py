import json

import pytest

from weights_gen import compute_weights, dist_point_seg

def test_dist_point_seg():
    assert dist_point_seg(0, 5, 0, 0, 10, 0) == 5.0     # clamped to endpoint a
    assert dist_point_seg(-3, 4, 0, 0, 10, 0) == 5.0    # clamped to endpoint
    assert dist_point_seg(3, 4, 1, 4, 1, 4) == 2.0      # zero-length bone = point

BONES = [
    {"name": "root", "parent": None, "x": 0, "y": 0, "rotation": 0, "length": 0, "skin": False},
    {"name": "a", "parent": "root", "x": 0, "y": 0, "rotation": 0, "length": 10},
    {"name": "b", "parent": "root", "x": 100, "y": 0, "rotation": 0, "length": 10},
]

def test_nearest_bone_dominates_and_root_excluded():
    w = compute_weights([[2, 1], [105, 1]], BONES, p=4.0)
    v0, v1 = w[0], w[1]
    assert 0 not in v0["bones"] and 0 not in v1["bones"]     # skin:false never weighted
    assert v0["w"][v0["bones"].index(1)] > 0.99              # near bone a
    assert v1["w"][v1["bones"].index(2)] > 0.99              # near bone b

def test_normalized_and_capped():
    w = compute_weights([[50, 0], [2, 1]], BONES, p=4.0)
    for vw in w:
        assert abs(sum(vw["w"]) - 1.0) < 0.01
        assert len(vw["bones"]) == len(vw["w"]) <= 4
        assert all(x >= 0.05 for x in vw["w"])

def test_joint_vertex_blends_smoothly():
    # two collinear bones meeting at (10, 0): vertex at the joint co-weights ~50/50
    bones = [
        {"name": "root", "parent": None, "x": 0, "y": 0, "rotation": 0, "length": 0, "skin": False},
        {"name": "a", "parent": "root", "x": 0, "y": 0, "rotation": 0, "length": 10},
        {"name": "b", "parent": "a", "x": 10, "y": 0, "rotation": 0, "length": 10},
    ]
    vw = compute_weights([[10, 0]], bones)[0]
    assert sorted(vw["bones"]) == [1, 2]
    assert abs(vw["w"][0] - 0.5) < 0.01 and abs(vw["w"][1] - 0.5) < 0.01

NAMED_BONES = [
    {"name": "root", "parent": None, "x": 0, "y": 0, "rotation": 0, "length": 0, "skin": False},
    {"name": "arm", "parent": "root", "x": 0, "y": 0, "rotation": 0, "length": 10},
    {"name": "leg", "parent": "root", "x": 100, "y": 0, "rotation": 0, "length": 10},
]


def test_only_bones_restricts_influences():
    # A vertex nearest "leg" is forced onto "arm" (index 1) when only_bones={"arm"}.
    vw = compute_weights([[105, 1]], NAMED_BONES, only_bones={"arm"})[0]
    assert vw["bones"] == [1]
    assert abs(sum(vw["w"]) - 1.0) < 1e-6


def test_only_bones_none_is_default_behavior():
    assert compute_weights([[2, 1], [105, 1]], NAMED_BONES, only_bones=None) == \
        compute_weights([[2, 1], [105, 1]], NAMED_BONES)


def test_only_bones_unknown_name_raises():
    with pytest.raises(ValueError, match="ghost"):
        compute_weights([[0, 0]], NAMED_BONES, only_bones={"arm", "ghost"})


def test_only_bones_non_skin_name_raises():
    # "root" is a real bone but skin:false → not a legal weight recipient.
    with pytest.raises(ValueError, match="root"):
        compute_weights([[0, 0]], NAMED_BONES, only_bones={"root"})


def test_equidistant_bones_capped_at_four():
    # 5 zero-length skin bones, all at distance 5 from the origin: top-4 kept, sum ~1
    positions = [(5, 0), (-5, 0), (0, 5), (0, -5), (3, 4)]
    bones = [{"name": f"b{i}", "parent": None if i == 0 else "b0",
              "x": x, "y": y, "rotation": 0, "length": 0}
             for i, (x, y) in enumerate(positions)]
    vw = compute_weights([[0, 0]], bones)[0]
    assert len(vw["bones"]) == 4
    assert abs(sum(vw["w"]) - 1.0) < 0.01


import numpy as np

from weights_gen import compute_weights_heat


def _gap_alpha():
    """Body strip and cloak strip separated by a fully transparent gap."""
    a = np.zeros((40, 100), np.uint8)
    a[5:35, 5:45] = 255     # body
    a[5:35, 55:95] = 255    # cloak (disconnected)
    return a

GAP_BONES = [
    {"name": "body", "parent": None, "x": 10, "y": 5, "rotation": 90, "length": 30},
    {"name": "cloak", "parent": "body", "x": 90, "y": 5, "rotation": 90, "length": 30},
]


def test_heat_no_leak_across_gap():
    # Vertex at the body's right edge: IDW gives the cloak bone a visible
    # share straight across the gap; heat gives it exactly nothing.
    v = [[44, 20]]
    idw = compute_weights(v, GAP_BONES)[0]
    heat = compute_weights_heat(v, GAP_BONES, _gap_alpha())[0]
    cloak_idw = sum(w for b, w in zip(idw["bones"], idw["w"]) if b == 1)
    cloak_heat = sum(w for b, w in zip(heat["bones"], heat["w"]) if b == 1)
    assert cloak_idw > 0.15          # the IDW failure this feature exists for
    assert cloak_heat == 0.0


def test_heat_no_leak_across_gap_downsampled():
    # Production assets run at scale 4-6 (heat_res=256 vs 896-1440px images);
    # exercise the downsampling path: 100px wide at heat_res=32 -> scale=4,
    # the 10px gap still spans >=1 empty cell column, so no leak.
    heat = compute_weights_heat([[44, 20]], GAP_BONES, _gap_alpha(), heat_res=32)[0]
    cloak_heat = sum(w for b, w in zip(heat["bones"], heat["w"]) if b == 1)
    assert cloak_heat == 0.0


def _shoulder_alpha():
    """Torso and arm side by side, connected ONLY at the top (the shoulder) —
    the man.png geometry."""
    a = np.zeros((70, 70), np.uint8)
    a[5:60, 10:40] = 255    # torso
    a[5:60, 45:60] = 255    # arm alongside it
    a[5:12, 40:45] = 255    # shoulder bridge
    return a

SHOULDER_BONES = [
    {"name": "spine", "parent": None, "x": 25, "y": 10, "rotation": 90, "length": 45},
    {"name": "arm", "parent": "spine", "x": 52, "y": 10, "rotation": 90, "length": 45},
]


def test_heat_dies_across_narrow_junction():
    # Mid-torso vertex near the arm: straight-line distance to the arm bone is
    # about equal to the spine distance (IDW co-weights them), but heat must
    # travel up through the shoulder and back down — arm influence collapses.
    v = [[38, 40]]
    idw = compute_weights(v, SHOULDER_BONES)[0]
    heat = compute_weights_heat(v, SHOULDER_BONES, _shoulder_alpha())[0]
    arm_idw = sum(w for b, w in zip(idw["bones"], idw["w"]) if b == 1)
    arm_heat = sum(w for b, w in zip(heat["bones"], heat["w"]) if b == 1)
    assert arm_idw > 0.3
    assert arm_heat < 0.05


def test_heat_dies_across_narrow_junction_downsampled():
    # Same shoulder geometry through the downsampling path: 70px at
    # heat_res=24 -> scale=3; the 5px torso-arm gap stays open (one empty
    # cell column below the shoulder bridge), so arm influence still
    # collapses at the mid-torso vertex.
    heat = compute_weights_heat([[38, 40]], SHOULDER_BONES, _shoulder_alpha(),
                                heat_res=24)[0]
    arm_heat = sum(w for b, w in zip(heat["bones"], heat["w"]) if b == 1)
    assert arm_heat < 0.05


def test_heat_island_without_bone_falls_back_to_idw(capsys):
    # A mask island containing no bone source cannot receive heat: its
    # vertices fall back to IDW with a loud warning instead of crashing.
    a = _gap_alpha()
    bones = [GAP_BONES[0]]  # only the body bone; the cloak strip is a bare island
    v = [[90, 20]]          # vertex on the bare island
    heat = compute_weights_heat(v, bones, a)[0]
    assert heat["bones"] == [0] and heat["w"] == [1.0]
    assert "island" in capsys.readouterr().err


def test_heat_source_outside_mask_clamps_with_warning(capsys):
    # A bone whose segment pokes outside the silhouette still works: source
    # cells clamp to the nearest masked cell, stderr says so.
    bones = [{"name": "body", "parent": None, "x": 2, "y": 20, "rotation": 0, "length": 5}]
    a = _gap_alpha()  # mask starts at x=5, so cells x∈[2,5) are outside
    heat = compute_weights_heat([[10, 20]], bones, a)[0]
    assert heat["bones"] == [0]
    assert "outside the silhouette" in capsys.readouterr().err


def test_heat_matches_finish_contract():
    # Same output shape/invariants as IDW: ≤4 influences, normalized, cutoff.
    a = _shoulder_alpha()
    for vw in compute_weights_heat([[20, 30], [50, 30], [42, 8]], SHOULDER_BONES, a):
        assert abs(sum(vw["w"]) - 1.0) < 0.01
        assert len(vw["bones"]) == len(vw["w"]) <= 4
        assert all(x >= 0.05 for x in vw["w"])


def test_heat_respects_only_bones():
    with pytest.raises(ValueError, match="ghost"):
        compute_weights_heat([[20, 30]], SHOULDER_BONES, _shoulder_alpha(),
                             only_bones={"spine", "ghost"})
    vw = compute_weights_heat([[50, 30]], SHOULDER_BONES, _shoulder_alpha(),
                              only_bones={"spine"})[0]
    assert vw["bones"] == [0]


import weights_gen
from PIL import Image


def test_main_algo_heat_requires_image_and_runs(tmp_path):
    a = _gap_alpha()
    rgba = np.zeros((*a.shape, 4), np.uint8)
    rgba[:, :, 3] = a
    Image.fromarray(rgba, "RGBA").save(tmp_path / "src.png")
    (tmp_path / "mesh.json").write_text(json.dumps(
        {"vertices": [[10, 20], [90, 20]], "uvs": [], "triangles": []}))
    (tmp_path / "skeleton.json").write_text(json.dumps(GAP_BONES))
    base = [str(tmp_path / "mesh.json"), str(tmp_path / "skeleton.json"),
            str(tmp_path / "weights.json")]

    with pytest.raises(SystemExit, match="requires --image"):
        weights_gen.main(base + ["--algo", "heat"])

    weights_gen.main(base + ["--algo", "heat", "--image", str(tmp_path / "src.png")])
    ws = json.loads((tmp_path / "weights.json").read_text())
    assert len(ws) == 2
    assert ws[0]["bones"] == [0]   # body vertex: no cross-gap leak


def test_default_algo_is_heat(tmp_path):
    a = _gap_alpha()
    rgba = np.zeros((*a.shape, 4), np.uint8)
    rgba[:, :, 3] = a
    Image.fromarray(rgba, "RGBA").save(tmp_path / "src.png")
    (tmp_path / "mesh.json").write_text(json.dumps(
        {"vertices": [[44, 20]], "uvs": [], "triangles": []}))
    (tmp_path / "skeleton.json").write_text(json.dumps(GAP_BONES))
    weights_gen.main([str(tmp_path / "mesh.json"), str(tmp_path / "skeleton.json"),
                      str(tmp_path / "weights.json"), "--image", str(tmp_path / "src.png")])
    ws = json.loads((tmp_path / "weights.json").read_text())
    assert ws[0]["bones"] == [0]   # heat behavior: no cross-gap leak by default
