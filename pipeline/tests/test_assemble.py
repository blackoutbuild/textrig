import json
import subprocess
import sys
from pathlib import Path
from PIL import Image

PIPELINE = Path(__file__).resolve().parent.parent

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
             "keys": [{"t": 0.0, "v": -10, "ease": "sineInOut"}, {"t": 1.0, "v": 10, "ease": "sineInOut"}, {"t": 2.0, "v": -10}]}
        ]}
    }))

def run_assemble(tmp: Path):
    return subprocess.run(
        [sys.executable, str(PIPELINE / "assemble.py"),
         "--mesh", str(tmp / "mesh.json"), "--skeleton", str(tmp / "skeleton.json"),
         "--weights", str(tmp / "weights.json"), "--animations", str(tmp / "animations.json"),
         "--texture", str(tmp / "tex.png"), "--out", str(tmp / "rig.json")],
        capture_output=True, text=True)

def test_valid_inputs_produce_rig(tmp_path):
    make_inputs(tmp_path)
    r = run_assemble(tmp_path)
    assert r.returncode == 0, r.stderr
    rig = json.loads((tmp_path / "rig.json").read_text())
    assert rig["version"] == 1
    assert rig["texture"] == "tex.png"
    assert rig["imageSize"] == [4, 4]
    assert len(rig["meshes"][0]["weights"]) == 4
    assert "idle" in rig["animations"]

def test_bad_weight_sum_fails(tmp_path):
    make_inputs(tmp_path, weight_sum_ok=False)
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "weights sum" in r.stderr

def test_nan_weight_fails(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "weights.json").write_text(
        '[{"bones": [1], "w": [NaN]}, {"bones": [1], "w": [1.0]}, '
        '{"bones": [1], "w": [1.0]}, {"bones": [1], "w": [1.0]}]')
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "non-finite" in r.stderr

def test_duplicate_track_fails(tmp_path):
    make_inputs(tmp_path)
    track = {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": -10}, {"t": 2.0, "v": 10}]}
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [track, track]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "duplicate track" in r.stderr

def test_piece_track_in_single_mesh_build_fails_clearly(tmp_path):
    # piece tracks (display/alpha/order) are multi-piece-only; the legacy
    # single-mesh path must say so instead of dying on a KeyError.
    make_inputs(tmp_path)
    anims = json.loads((tmp_path / "animations.json").read_text())
    anims["idle"]["tracks"].append(
        {"piece": "hat", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 2.0, "v": 0}]})
    (tmp_path / "animations.json").write_text(json.dumps(anims))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "multi-piece pipeline" in r.stderr

def test_bone_missing_name_fails(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "skeleton.json").write_text(json.dumps([
        {"parent": None, "x": 2, "y": 4, "rotation": -90, "length": 0, "skin": False},
        {"name": "spine", "parent": "root", "x": 2, "y": 4, "rotation": -90, "length": 4},
    ]))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "malformed input" in r.stderr

def test_negative_bone_length_fails(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "skeleton.json").write_text(json.dumps([
        {"name": "root", "parent": None, "x": 2, "y": 4, "rotation": -90, "length": 0, "skin": False},
        {"name": "spine", "parent": "root", "x": 2, "y": 4, "rotation": -90, "length": -4},
    ]))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "negative length" in r.stderr

def test_loop_animation_first_last_mismatch_fails(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [
            {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": -10}, {"t": 1.0, "v": 10}, {"t": 2.0, "v": 5}]}
        ]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "idle" in r.stderr
    assert "spine.rotation" in r.stderr
    assert "-10" in r.stderr and "5" in r.stderr

def test_scale_key_too_close_to_zero_fails(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [
            {"bone": "spine", "prop": "scaleX",
             "keys": [{"t": 0.0, "v": 1.0}, {"t": 2.0, "v": 0.05}]}
        ]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 1
    assert "absolute factors" in r.stderr
    assert "not deltas" in r.stderr

def test_non_loop_animation_exempt_from_loop_check(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "animations.json").write_text(json.dumps({
        "wave": {"duration": 2.0, "loop": False, "tracks": [
            {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": -10}, {"t": 2.0, "v": 30}]}
        ]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 0, r.stderr

def test_loop_track_without_key_at_duration_not_flagged(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [
            {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": -10}, {"t": 1.0, "v": 30}]}
        ]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 0, r.stderr

def test_loop_first_last_within_tolerance_passes(tmp_path):
    make_inputs(tmp_path)
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 2.0, "loop": True, "tracks": [
            {"bone": "spine", "prop": "rotation",
             "keys": [{"t": 0.0, "v": 0.1000001}, {"t": 1.0, "v": 10}, {"t": 2.0, "v": 0.1000000}]},
            {"bone": "spine", "prop": "x",
             "keys": [{"t": 0.0, "v": 5.0000001}, {"t": 2.0, "v": 5.0000000}]},
            {"bone": "spine", "prop": "y",
             "keys": [{"t": 0.0, "v": -3.0000001}, {"t": 2.0, "v": -3.0000000}]},
        ]}
    }))
    r = run_assemble(tmp_path)
    assert r.returncode == 0, r.stderr
