"""Golden: the writer's path emission matches the fixture-proven §16 shape
(contract `docs/dragonbones-format-contract.md` §16).

Precedent: `test_zorder_golden.py`. Unlike that test, this one does NOT compare
against the hand-authored renderer fixture (`renderer/assets/path/path_ske.json`)
— that fixture's interior handles were hand-picked, while the writer derives
handles via Catmull-Rom (`dragonbones_writer.catmull_rom_to_bezier`), so its
vertices/lengths legitimately differ from the fixture through the SAME anchor
points. This golden instead pins the WRITER'S OWN output (built in-process via
`build_ske`), hand-verified once against §16 at authoring time, as a regression
net against future refactors of `build_path_elements`.
"""
import json
from pathlib import Path

from dragonbones_writer import build_ske

FIXDIR = Path(__file__).parent / "fixtures"

BONES = [
    {"name": "root", "parent": None, "x": 64, "y": 100, "rotation": 0, "length": 20},
    {"name": "c1", "parent": "root", "x": 64, "y": 80, "rotation": -90, "length": 20},
    {"name": "c2", "parent": "c1", "x": 64, "y": 60, "rotation": -90, "length": 20},
]
PATHS = [{"name": "flow", "bone": "root",
          "points": [[64, 100], [84, 60], [64, 20]], "chain": ["c1", "c2"]}]
MESH = {"vertices": [[0, 0], [128, 0], [0, 128]], "uvs": [[0, 0], [1, 0], [0, 1]],
        "triangles": [[0, 1, 2]], "imageSize": [128, 128]}
WEIGHTS = [{"bones": [0], "w": [1.0]}] * 3
ANIMS = {"idle": {"duration": 2.0, "loop": True, "tracks": [
    {"bone": "root", "prop": "rotation",
     "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 10}, {"t": 2.0, "v": 0}]}]}}


def _build():
    ske = build_ske("pathdemo", MESH, BONES, WEIGHTS, ANIMS, 128, 128, paths=PATHS)
    arm = ske["armature"][0]
    return {
        "bone": [b for b in arm["bone"] if "flow" in b["name"]],   # the anchor bone
        "slot": [s for s in arm["slot"] if s["name"] == "flow"],
        "skin": [s for s in arm["skin"][0]["slot"] if s["name"] == "flow"],
        "path": arm["path"],
    }


def test_path_golden():
    got = _build()
    expected = json.loads((FIXDIR / "path_golden_ske.json").read_text())
    assert got == expected


def test_path_golden_would_catch_a_perturbed_float():
    """Sensitivity check: the golden equality reacts to real generator output —
    corrupting one pinned float in the expectation must break the assertion."""
    got = _build()
    expected = json.loads((FIXDIR / "path_golden_ske.json").read_text())
    expected["skin"][0]["display"][0]["lengths"][0] += 1.0
    assert got != expected


def _build_weighted():
    paths = [dict(PATHS[0], weighted=True)]
    ske = build_ske("pathdemo", MESH, BONES, WEIGHTS, ANIMS, 128, 128, paths=paths)
    arm = ske["armature"][0]
    return {
        "bone": [b for b in arm["bone"] if "flow" in b["name"]],   # the anchor bones
        "slot": [s for s in arm["slot"] if s["name"] == "flow"],
        "skin": [s for s in arm["skin"][0]["slot"] if s["name"] == "flow"],
        "path": arm["path"],
    }


def test_path_weighted_golden():
    """v2 (§17): weighted:true → one tangent-aligned anchor bone per on-curve
    point, per-group weights, one bonePose entry per anchor. Hand-verified
    against §17 at authoring time (anchor positions/rotations, weight groups,
    bind matrices), pinned as a regression net."""
    got = _build_weighted()
    expected = json.loads((FIXDIR / "path_weighted_golden_ske.json").read_text())
    assert got == expected


def test_path_weighted_golden_sensitivity():
    got = _build_weighted()
    expected = json.loads((FIXDIR / "path_weighted_golden_ske.json").read_text())
    expected["skin"][0]["display"][0]["bonePose"][1] += 0.01
    assert got != expected
