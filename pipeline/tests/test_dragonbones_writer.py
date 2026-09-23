"""TDD for dragonbones_writer.py — our rig inputs → DragonBones _ske/_tex JSON.

Numbers for the parent-local port are lifted verbatim from
runtime/test/skeleton.test.ts ('restLocal is exact for rotated rest with offset
parent') so the two implementations are provably the same math.
"""
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE))

import dragonbones_writer as dw  # noqa: E402


# ---------------------------------------------------------------------------
# parent-local conversion (ported from runtime/src/skeleton.ts restLocal)
# ---------------------------------------------------------------------------
def test_parent_local_exact_rotated_offset_parent():
    # runtime/test/skeleton.test.ts: parent (7,-3) rot -63 len 10, child at the
    # parent tip rot -110 → local (10, 0, -47).
    parent = {"name": "p", "parent": None, "x": 7, "y": -3, "rotation": -63, "length": 10}
    tipx, tipy = dw.mat_apply(dw.from_trs(7, -3, -63), 10, 0)
    child = {"name": "c", "parent": "p", "x": tipx, "y": tipy, "rotation": -110, "length": 5}
    lx, ly, lrot = dw.parent_local(child, parent)
    assert abs(lx - 10) < 1e-8
    assert abs(ly - 0) < 1e-8
    assert abs(lrot - (-47)) < 1e-8


def test_parent_local_root_is_absolute():
    root = {"name": "r", "parent": None, "x": 12.5, "y": -4.0, "rotation": 33.0, "length": 0}
    lx, ly, lrot = dw.parent_local(root, None)
    assert (lx, ly, lrot) == (12.5, -4.0, 33.0)


def test_parent_local_roundtrip_recomposes_to_world():
    # A random 4-bone chain: parent-local transforms recomposed down the chain
    # must reproduce every bone's original absolute rest world matrix (1e-6).
    rng = random.Random(20260710)
    bones = [{"name": "b0", "parent": None,
              "x": rng.uniform(-100, 100), "y": rng.uniform(-100, 100),
              "rotation": rng.uniform(-180, 180), "length": rng.uniform(1, 50)}]
    for i in range(1, 4):
        bones.append({"name": f"b{i}", "parent": f"b{i-1}",
                      "x": rng.uniform(-100, 100), "y": rng.uniform(-100, 100),
                      "rotation": rng.uniform(-180, 180), "length": rng.uniform(1, 50)})
    by_name = {b["name"]: b for b in bones}
    world = {}
    for b in bones:
        parent = None if b["parent"] is None else by_name[b["parent"]]
        lx, ly, lrot = dw.parent_local(b, parent)
        local = dw.from_trs(lx, ly, lrot)
        world[b["name"]] = local if parent is None else dw.mat_mul(world[parent["name"]], local)
        expected = dw.from_trs(b["x"], b["y"], b["rotation"])
        assert np.allclose(world[b["name"]], expected, atol=1e-6), b["name"]


# ---------------------------------------------------------------------------
# weights packing + bonePose
# ---------------------------------------------------------------------------
def test_pack_weights_golden():
    weights = [
        {"bones": [0], "w": [1.0]},
        {"bones": [0, 1], "w": [0.5, 0.5]},
        {"bones": [1], "w": [1.0]},
    ]
    packed = dw.pack_weights(weights)
    assert packed == [1, 0, 1.0, 2, 0, 0.5, 1, 0.5, 1, 1, 1.0]
    n = len(weights)
    total_counts = sum(len(w["bones"]) for w in weights)
    # DB parser derives influence count as (weights.length - N)/2.
    assert (len(packed) - n) / 2 == total_counts == 4


def test_referenced_bones_sorted_unique():
    weights = [{"bones": [3], "w": [1.0]}, {"bones": [1, 3], "w": [0.5, 0.5]}]
    assert dw.referenced_bones(weights) == [1, 3]


def test_build_bonepose_covers_every_referenced_bone():
    bones = [
        {"name": "root", "parent": None, "x": 5, "y": 6, "rotation": 0, "length": 0},
        {"name": "b", "parent": "root", "x": 10, "y": 6, "rotation": -90, "length": 4},
    ]
    referenced = [0, 1]
    bp = dw.build_bonepose(bones, referenced)
    assert len(bp) == 2 * 7
    assert bp[0] == 0                       # gIdx of first entry
    assert bp[7] == 1                       # gIdx of second entry
    # each entry's [a..ty] is that bone's world bind matrix (from_trs of absolute)
    assert np.allclose(bp[1:7], dw.from_trs(5, 6, 0))
    assert np.allclose(bp[8:14], dw.from_trs(10, 6, -90))


# ---------------------------------------------------------------------------
# easing mapping
# ---------------------------------------------------------------------------
def test_ease_mapping_closed_forms():
    assert dw.ease_to_db("linear") == {"tweenEasing": 0}
    assert dw.ease_to_db("sineInOut") == {"tweenEasing": 2}
    assert dw.ease_to_db("quadOut") == {"tweenEasing": 1}
    assert dw.ease_to_db("quadIn") == {"tweenEasing": -1}


def test_ease_mapping_bezier_curves():
    for name in ("sineIn", "sineOut", "quadInOut", "backOut"):
        frag = dw.ease_to_db(name)
        assert "curve" in frag and len(frag["curve"]) == 4


def test_ease_mapping_unknown_fails_loud():
    try:
        dw.ease_to_db("bounceIn")
    except ValueError:
        return
    raise AssertionError("expected ValueError on unmapped easing")


# ---------------------------------------------------------------------------
# timeline conversion
# ---------------------------------------------------------------------------
def test_rotate_timeline_sineInOut_shape():
    # square's rotation track: -25 → +25 → -25 over 2.0s @ 30fps.
    tracks = [{"bone": "upper", "prop": "rotation", "keys": [
        {"t": 0.0, "v": -25, "ease": "sineInOut"},
        {"t": 1.0, "v": 25, "ease": "sineInOut"},
        {"t": 2.0, "v": -25},
    ]}]
    tl = dw.build_bone_timelines("upper", tracks, fps=30, total_frames=60)
    rf = tl["rotateFrame"]
    assert rf == [
        {"duration": 30, "tweenEasing": 2, "rotate": -25},
        {"duration": 30, "tweenEasing": 2, "rotate": 25},
        {"duration": 0, "rotate": -25},
    ]


def test_scale_timeline_merges_x_y():
    tracks = [
        {"bone": "body", "prop": "scaleX", "keys": [
            {"t": 0.0, "v": 1.0, "ease": "sineInOut"},
            {"t": 1.5, "v": 1.02, "ease": "sineInOut"},
            {"t": 3.0, "v": 1.0}]},
        {"bone": "body", "prop": "scaleY", "keys": [
            {"t": 0.0, "v": 1.0, "ease": "sineInOut"},
            {"t": 1.5, "v": 1.045, "ease": "sineInOut"},
            {"t": 3.0, "v": 1.0}]},
    ]
    tl = dw.build_bone_timelines("body", tracks, fps=30, total_frames=90)
    sf = tl["scaleFrame"]
    assert sf[0] == {"duration": 45, "tweenEasing": 2, "x": 1.0, "y": 1.0}
    assert sf[1] == {"duration": 45, "tweenEasing": 2, "x": 1.02, "y": 1.045}
    assert sf[2] == {"duration": 0, "x": 1.0, "y": 1.0}


def test_playtimes_respects_loop_flag():
    tracks = [{"bone": "b", "prop": "rotation", "keys": [
        {"t": 0.0, "v": 0, "ease": "linear"}, {"t": 1.0, "v": 0}]}]
    looped = dw.build_animation("idle", {"duration": 1.0, "loop": True, "tracks": tracks}, fps=30)
    once = dw.build_animation("intro", {"duration": 1.0, "loop": False, "tracks": tracks}, fps=30)
    assert looped["playTimes"] == 0
    assert once["playTimes"] == 1


def test_keys_collapsing_to_same_frame_fail_loud():
    # 0.01s apart @ 30fps → both round to frame 0 → interior {"duration": 0}
    # would NaN DragonBones' progress division. Must fail loud.
    tracks = [{"bone": "b", "prop": "rotation", "keys": [
        {"t": 0.0, "v": 0, "ease": "linear"},
        {"t": 0.01, "v": 5, "ease": "linear"},
        {"t": 1.0, "v": 0}]}]
    try:
        dw.build_bone_timelines("b", tracks, fps=30, total_frames=30)
    except ValueError as e:
        assert "same frame" in str(e)
        return
    raise AssertionError("expected ValueError on frame collision")


def test_alpha_track_fails_loud():
    tracks = [{"bone": "root", "prop": "alpha", "keys": [
        {"t": 0.0, "v": 1.0}, {"t": 1.0, "v": 1.0}]}]
    try:
        dw.build_bone_timelines("root", tracks, fps=30, total_frames=30)
    except ValueError:
        return
    raise AssertionError("expected ValueError on alpha track")


# ---------------------------------------------------------------------------
# zOrder timeline (contract §15) — merge per-piece `order` tracks into one
# DragonBones zOrder timeline of absolute deviation-set snapshots.
# ---------------------------------------------------------------------------
PIECES_ABC = [{"name": n} for n in ("a", "b", "c")]


def test_zorder_single_track_snapshot_and_hold():
    tracks = [{"piece": "a", "prop": "order",
               "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 1}, {"t": 1.5, "v": 0}]}]
    frames = dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)
    assert frames == [{"duration": 15},
                      {"duration": 30, "zOrder": [0, 1]},
                      {"duration": 15}]


def test_zorder_two_tracks_merge_union_of_change_times():
    tracks = [
        {"piece": "a", "prop": "order", "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 2}, {"t": 1.5, "v": 0}]},
        {"piece": "c", "prop": "order", "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": -1}, {"t": 1.5, "v": 0}]},
    ]
    frames = dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)
    assert frames == [{"duration": 15},
                      {"duration": 15, "zOrder": [0, 2]},
                      {"duration": 15, "zOrder": [0, 2, 2, -1]},   # snapshot: BOTH pairs
                      {"duration": 15}]


def test_zorder_frame_collapse_rejected():
    tracks = [{"piece": "a", "prop": "order",
               "keys": [{"t": 0.0, "v": 0}, {"t": 0.01, "v": 1}]}]
    with pytest.raises(ValueError, match="collapse"):
        dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)


def test_zorder_negative_target_position_rejected():
    # slot 0 shifted by -1 → target position -1: passes pieces.py range
    # validation (|shift| < piece count) but the §15d parser (a JS array)
    # would write to a negative index — the slot silently vanishes and the
    # runtime reads an undefined index. Must fail loud instead.
    tracks = [{"piece": "a", "prop": "order",
               "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -1}, {"t": 1.5, "v": 0}]}]
    with pytest.raises(ValueError, match="out-of-range position"):
        dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)


def test_zorder_overflow_target_position_rejected():
    # last slot (2) shifted by +2 → target position 4 in a 3-slot rig.
    tracks = [{"piece": "c", "prop": "order",
               "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 2}, {"t": 1.5, "v": 0}]}]
    with pytest.raises(ValueError, match="out-of-range position"):
        dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)


def test_zorder_colliding_targets_rejected():
    # a (slot 0) shifts +1 → target 1; c (slot 2) shifts -1 → target 1 too —
    # both pairs target the SAME final slot position in the same frame, which
    # the parser resolves by last-write-wins and silently drops a mover
    # (contract §15d). Must fail loud instead.
    tracks = [
        {"piece": "a", "prop": "order", "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 1}, {"t": 1.5, "v": 0}]},
        {"piece": "c", "prop": "order", "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -1}, {"t": 1.5, "v": 0}]},
    ]
    with pytest.raises(ValueError, match="collide"):
        dw.build_zorder_frames("wave", tracks, PIECES_ABC, fps=30, total_frames=60)


# ---------------------------------------------------------------------------
# structural: full golden run through the CLI on tiny inputs
# ---------------------------------------------------------------------------
def make_inputs(tmp: Path, weight_sum_ok: bool = True):
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(tmp / "tex.png")
    (tmp / "mesh.json").write_text(json.dumps({
        "imageSize": [4, 4],
        "vertices": [[0, 0], [4, 0], [0, 4], [4, 4]],
        "uvs": [[0, 0], [1, 0], [0, 1], [1, 1]],
        "triangles": [[0, 1, 2], [1, 3, 2]],
    }))
    (tmp / "skeleton.json").write_text(json.dumps([
        {"name": "root", "parent": None, "x": 2, "y": 4, "rotation": -90, "length": 0, "skin": False},
        {"name": "spine", "parent": "root", "x": 2, "y": 4, "rotation": -90, "length": 4},
    ]))
    w = 1.0 if weight_sum_ok else 0.5
    (tmp / "weights.json").write_text(json.dumps([{"bones": [1], "w": [w]}] * 4))
    (tmp / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [
            {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": -10, "ease": "sineInOut"},
                      {"t": 1.0, "v": 10, "ease": "sineInOut"},
                      {"t": 2.0, "v": -10}]}]}}))


def run_writer(tmp: Path, name: str = "rig"):
    return subprocess.run(
        [sys.executable, str(PIPELINE / "dragonbones_writer.py"),
         "--mesh", str(tmp / "mesh.json"), "--skeleton", str(tmp / "skeleton.json"),
         "--weights", str(tmp / "weights.json"), "--animations", str(tmp / "animations.json"),
         "--texture", str(tmp / "tex.png"), "--out", str(tmp), "--name", name],
        capture_output=True, text=True)


def test_full_golden_run_structure(tmp_path):
    make_inputs(tmp_path)
    r = run_writer(tmp_path, "rig")
    assert r.returncode == 0, r.stderr
    ske = json.loads((tmp_path / "rig_ske.json").read_text())
    assert ske["version"] == "5.5" and ske["compatibleVersion"] == "5.5"
    assert ske["frameRate"] == 30
    arm = ske["armature"][0]
    assert arm["name"] == "rig"
    assert [b["name"] for b in arm["bone"]] == ["root", "spine"]
    assert arm["slot"] and arm["skin"] and arm["animation"]
    assert arm["defaultActions"][0]["gotoAndPlay"] == "idle"
    disp = arm["skin"][0]["slot"][0]["display"][0]
    assert disp["type"] == "mesh"
    assert len(disp["slotPose"]) == 6 and disp["slotPose"] == [1, 0, 0, 1, 0, 0]
    assert len(disp["bonePose"]) % 7 == 0
    anim = arm["animation"][0]
    assert anim["name"] == "idle"
    assert anim["duration"] == 60  # 2.0s * 30fps
    # tex.json single full-image region
    tex = json.loads((tmp_path / "rig_tex.json").read_text())
    assert tex["width"] == 4 and tex["height"] == 4
    assert len(tex["SubTexture"]) == 1
    assert tex["SubTexture"][0]["name"] == disp["name"]
    assert (tmp_path / "rig_tex.png").exists()


def test_validators_reject_bad_weights(tmp_path):
    make_inputs(tmp_path, weight_sum_ok=False)
    r = run_writer(tmp_path)
    assert r.returncode == 1
    assert "weights sum" in r.stderr


def test_validators_reject_nan_weight(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "weights.json").write_text(
        '[{"bones": [1], "w": [NaN]}, {"bones": [1], "w": [1.0]}, '
        '{"bones": [1], "w": [1.0]}, {"bones": [1], "w": [1.0]}]')
    r = run_writer(tmp_path)
    assert r.returncode == 1
    assert "non-finite" in r.stderr
