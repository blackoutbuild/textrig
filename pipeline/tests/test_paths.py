"""load_skeleton + validate_paths (path constraints v1)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import assemble
from assemble import load_skeleton, validate_paths
from dragonbones_writer import (build_path_elements, catmull_rom_to_bezier, bezier_arc_lengths,
                                bones_to_db, _path_chain_bones)

PIPELINE = Path(__file__).resolve().parent.parent

BONES = [
    {"name": "root", "parent": None, "x": 64, "y": 100, "rotation": -90, "length": 20},
    {"name": "c1", "parent": "root", "x": 64, "y": 80, "rotation": -90, "length": 20},
    {"name": "c2", "parent": "c1", "x": 64, "y": 60, "rotation": -90, "length": 20},
]

def good_path():
    return {"name": "flow", "bone": "root",
            "points": [[64, 100], [84, 60], [64, 20]],
            "chain": ["c1", "c2"]}


def test_load_skeleton_array(tmp_path):
    p = tmp_path / "skel.json"
    p.write_text('[{"name": "root", "parent": null, "x": 1, "y": 2, "rotation": 0, "length": 3}]')
    bones, paths = load_skeleton(p)
    assert bones[0]["name"] == "root" and paths == []


def test_load_skeleton_object(tmp_path):
    import json
    p = tmp_path / "skel.json"
    p.write_text(json.dumps({"bones": BONES, "paths": [good_path()]}))
    bones, paths = load_skeleton(p)
    assert len(bones) == 3 and paths[0]["name"] == "flow"


def test_load_skeleton_unknown_key(tmp_path, capsys):
    p = tmp_path / "skel.json"
    p.write_text('{"bones": [], "pathz": []}')
    with pytest.raises(SystemExit):
        load_skeleton(p)
    assert "unknown keys" in capsys.readouterr().err


def test_validate_paths_ok():
    validate_paths([good_path()], BONES, 128, 128)  # no raise


@pytest.mark.parametrize("mutate,msg", [
    (lambda p: p.update(bone="nope"), "unknown owner bone"),
    (lambda p: p.update(chain=[]), "chain must be a non-empty bone list"),
    (lambda p: p.update(chain=["c1", "nope"]), "unknown chain bone"),
    (lambda p: p.update(chain=["c1", "c1"]), "duplicate bone in chain"),
    (lambda p: p.update(points=[[64, 100]]), "points must list >=2"),
    (lambda p: p.update(points=[[64, 100], [64, 1e9]]), "outside image bounds"),
    (lambda p: p.update(rotateMode="spiral"), "rotateMode"),
    (lambda p: p.update(wiggle=1), "unknown keys"),
    (lambda p: p.pop("name"), "path without a name"),
    # closed geometry is never generated in v1: the runtime's closed branch
    # would read a nonexistent curveLengths entry -> NaN chain, silently.
    (lambda p: p.update(closed=True), "closed paths are not supported in v1"),
    # coincident consecutive anchors -> zero-length segment -> t = p/segLen
    # divides by zero at the runtime -> NaN, silently.
    (lambda p: p.update(points=[[64, 100], [64, 100], [64, 20]]),
     "consecutive points 0 and 1 coincide"),
    (lambda p: p.update(weighted="yes"), "weighted must be a boolean"),
])
def test_validate_paths_rejects(mutate, msg, capsys):
    p = good_path()
    mutate(p)
    with pytest.raises(SystemExit):
        validate_paths([p], BONES, 128, 128)
    assert msg in capsys.readouterr().err


def test_validate_paths_duplicate_names(capsys):
    with pytest.raises(SystemExit):
        validate_paths([good_path(), good_path()], BONES, 128, 128)
    assert "duplicate path name" in capsys.readouterr().err


def test_validate_paths_zero_length_chain_bone(capsys):
    # spacingMode "length" (hardcoded by the writer) divides by each chain
    # bone's setup length (contract §16d); length 0 -> NaN, collapsed chain.
    bones = [dict(b) for b in BONES]
    bones[2]["length"] = 0  # c2
    with pytest.raises(SystemExit):
        validate_paths([good_path()], bones, 128, 128)
    err = capsys.readouterr().err
    assert "chain bone 'c2'" in err and "length" in err


# ---------------------------------------------------------------------------
# dragonbones_writer emission: catmull_rom_to_bezier, bezier_arc_lengths,
# build_path_elements (contract §16, PROVEN by renderer/assets/path).
# ---------------------------------------------------------------------------
def test_catmull_rom_endpoints():
    # segments interpolate the anchors exactly
    segs = catmull_rom_to_bezier([[0, 0], [10, 0], [20, 10]])
    assert len(segs) == 2
    assert segs[0][0] == [0, 0] and segs[0][3] == [10, 0]
    assert segs[1][0] == [10, 0] and segs[1][3] == [20, 10]


def test_catmull_rom_interior_handles_exact():
    # pin the tangent math, not just endpoint interpolation:
    # hIn/hOut of an interior anchor are P_i -/+ (P_{i+1} - P_{i-1})/6 exactly.
    segs = catmull_rom_to_bezier([[0, 0], [10, 0], [20, 10]])
    # interior anchor P1 = [10, 0]; (P2 - P0)/6 = [20/6, 10/6]
    assert segs[0][2] == [10 - 20 / 6.0, 0 - 10 / 6.0]   # hIn  = P1 - (P2-P0)/6
    assert segs[1][1] == [10 + 20 / 6.0, 0 + 10 / 6.0]   # hOut = P1 + (P2-P0)/6


def test_bezier_arc_lengths_cumulative_straight_line():
    # a straight "curve": cumulative lengths are exact
    segs = catmull_rom_to_bezier([[0, 0], [10, 0], [30, 0]])
    lens = bezier_arc_lengths(segs)
    assert len(lens) == 2
    assert abs(lens[0] - 10) < 0.1 and abs(lens[1] - 30) < 0.1  # CUMULATIVE


def test_bezier_arc_lengths_pinned_curved():
    # a known curved case pinned to 6 decimals — locks the 64-point sampling
    # and the _round convention (any change to either fails loud, so lengths
    # drift can't slip into shipped rigs unnoticed).
    segs = catmull_rom_to_bezier([[64, 100], [84, 60], [64, 20]])
    assert bezier_arc_lengths(segs) == [45.218865, 90.437729]


def test_build_path_elements_shape():
    paths = [{"name": "flow", "bone": "root",
              "points": [[64, 100], [84, 60], [64, 20]], "chain": ["c1", "c2"]}]
    # anchor bone: child of owner, to be appended AFTER all existing bones
    extra_bones, slots, skin_slots, constraints = build_path_elements(paths, BONES)
    assert len(extra_bones) == 1 and extra_bones[0]["parent"] == "root"
    assert slots == [{"name": "flow", "parent": "root"}]
    disp = skin_slots[0]["display"][0]
    assert disp["type"] == "path" and disp["name"] == "flow"
    assert len(disp["lengths"]) == 2                      # 2 segments (3 anchors)
    assert disp["lengths"][1] > disp["lengths"][0]        # cumulative
    assert disp["vertexCount"] == 9                       # 3 * n_anchors
    assert len(disp["vertices"]) == 18
    assert "weights" in disp and "slotPose" in disp and "bonePose" in disp
    c = constraints[0]
    assert c["target"] == "flow" and c["bones"] == ["c1", "c2"]
    assert c["rotateMode"] == "chain" and c["translateMix"] == 1


def test_bones_to_db_emits_length_on_path_chain_bones():
    # contract §16d: the runtime's spacingMode:"length" divides by each chain
    # bone's setup length; a MISSING `length` parses to 0 -> NaN -> collapsed,
    # invisible chain. The hand-authored fixture carried `length`; the writer
    # did NOT emit it (shape tests never rendered, so it slipped through until
    # the reaper worked example froze). Guard it: chain bones carry length,
    # non-chain bones stay length-free (keeps path-free rigs golden-identical).
    chain = _path_chain_bones([good_path()])          # -> {"c1", "c2"}
    by = {b["name"]: b for b in bones_to_db(BONES, chain)}
    assert by["c1"]["length"] == 20 and by["c2"]["length"] == 20
    assert "length" not in by["root"]                 # non-chain: omitted


def test_bones_to_db_no_paths_omits_length():
    for b in bones_to_db(BONES):                       # default: no length bones
        assert "length" not in b


def test_path_name_collides_with_bone_slot():
    paths = [{"name": "c1", "bone": "root", "points": [[0, 0], [1, 1]], "chain": ["c2"]}]
    with pytest.raises(ValueError):
        build_path_elements(paths, BONES, taken_slot_names={"c1"})


def test_path_anchor_bone_index_points_into_final_bone_array():
    # weights must reference the anchor's GLOBAL index in the final bone array
    # (existing bones, anchor appended last) — not a local/relative index.
    paths = [{"name": "flow", "bone": "root",
              "points": [[64, 100], [84, 60], [64, 20]], "chain": ["c1", "c2"]}]
    extra_bones, _, skin_slots, _ = build_path_elements(paths, BONES)
    anchor_gidx = len(BONES)  # anchor is appended right after BONES (len 3) -> index 3
    disp = skin_slots[0]["display"][0]
    assert disp["bonePose"][0] == anchor_gidx
    # every weight triple is [1, anchor_gidx, 1.0]
    n_verts = disp["vertexCount"]
    assert len(disp["weights"]) == 3 * n_verts
    for i in range(n_verts):
        assert disp["weights"][3 * i: 3 * i + 3] == [1, anchor_gidx, 1.0]


# ---------------------------------------------------------------------------
# weighted paths (v2, contract §17): one anchor per on-curve point, the curve
# bends. PROVEN by renderer/assets/path-weighted.
# ---------------------------------------------------------------------------
def weighted_path():
    p = good_path()
    p["weighted"] = True
    return p


def test_validate_paths_weighted_ok():
    validate_paths([weighted_path()], BONES, 128, 128)  # no raise


def test_weighted_emits_one_tangent_anchor_per_point():
    import math
    extra_bones, _, skin_slots, _ = build_path_elements([weighted_path()], BONES)
    assert [b["name"] for b in extra_bones] == ["flow_a0", "flow_a1", "flow_a2"]
    assert all(b["parent"] == "root" and b["length"] == 0 for b in extra_bones)
    # anchors sit AT their on-curve point...
    assert [(b["x"], b["y"]) for b in extra_bones] == [(64, 100), (84, 60), (64, 20)]
    # ...rotated to the local curve tangent (local +Y = curve normal, the wave
    # offset axis). Interior anchor: tangent = hOut - hIn = P2 - P0 direction.
    assert math.isclose(extra_bones[1]["rotation"],
                        math.degrees(math.atan2(20 - 100, 64 - 64)))  # -90: straight up
    disp = skin_slots[0]["display"][0]
    # each 3-vertex group [hIn, pt, hOut] rides its OWN anchor, weight 1.0
    for j in range(3):
        gidx = len(BONES) + j
        for k in range(3):
            v = 3 * j + k
            assert disp["weights"][3 * v: 3 * v + 3] == [1, gidx, 1.0]
    # bonePose: one [gidx, world bind matrix] entry per anchor (7 numbers each)
    assert len(disp["bonePose"]) == 3 * 7
    assert [disp["bonePose"][7 * j] for j in range(3)] == [3, 4, 5]


def test_weighted_anchor_name_collision_rejected():
    bones = BONES + [{"name": "flow_a1", "parent": "root",
                      "x": 0, "y": 0, "rotation": 0, "length": 10}]
    with pytest.raises(ValueError, match="flow_a1"):
        build_path_elements([weighted_path()], bones)


# ---------------------------------------------------------------------------
# legacy target: paths in the skeleton must exit loud, not silently ignore
# the path solver the runtime doesn't have.
# ---------------------------------------------------------------------------
def make_legacy_inputs(tmp: Path):
    from PIL import Image
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(tmp / "tex.png")
    (tmp / "mesh.json").write_text(json.dumps({
        "imageSize": [128, 128],
        "vertices": [[0, 0], [128, 0], [0, 128], [128, 128]],
        "uvs": [[0, 0], [1, 0], [0, 1], [1, 1]],
        "triangles": [[0, 1, 2], [1, 3, 2]],
    }))
    (tmp / "skeleton.json").write_text(json.dumps({"bones": BONES, "paths": [good_path()]}))
    (tmp / "weights.json").write_text(json.dumps([{"bones": [0], "w": [1.0]}] * 4))
    (tmp / "animations.json").write_text(json.dumps({
        "idle": {"duration": 1.0, "loop": True, "tracks": [
            {"bone": "root", "prop": "rotation", "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": 0}]}]}}))


def test_assemble_legacy_target_rejects_paths(tmp_path):
    make_legacy_inputs(tmp_path)
    r = subprocess.run(
        [sys.executable, str(PIPELINE / "assemble.py"),
         "--mesh", str(tmp_path / "mesh.json"), "--skeleton", str(tmp_path / "skeleton.json"),
         "--weights", str(tmp_path / "weights.json"), "--animations", str(tmp_path / "animations.json"),
         "--texture", str(tmp_path / "tex.png"), "--out", str(tmp_path / "rig.json")],
        capture_output=True, text=True)
    assert r.returncode == 1
    assert "paths" in r.stderr and "DragonBones" in r.stderr
