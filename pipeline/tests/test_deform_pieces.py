"""TDD for Task 12: deform pieces in the multi-piece orchestrator.

A deform piece run end-to-end through build_pieces_rig must emit a weighted
mesh display whose:
  - vertices are WORLD-space (piece-local mesh + piece offset, applied by the
    writer's _deform_display),
  - uvs are region-relative (mesh built over the cutout → uvs relative to the
    cutout == the atlas region),
  - weights reference GLOBAL bone indices (into the armature bone array),
  - bonePose entries equal from_trs of each referenced bone's absolute rest.

Uses a FILE-source deform piece so the piece offset is known exactly and the
expected mesh can be recomputed from the same layer alpha (numeric assertions
in the style of test_dragonbones_writer.py).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_pieces_rig  # noqa: E402
import mesh_gen  # noqa: E402
from dragonbones_writer import from_trs, _round, pack_weights  # noqa: E402
from weights_gen import compute_weights  # noqa: E402

W, H = 60, 80
CAPE_OFFSET = (5, 40)
CAPE_W, CAPE_H = 16, 12

BONES = [
    {"name": "root", "parent": None, "x": 30, "y": 70, "rotation": -90, "length": 0, "skin": False},
    {"name": "arm", "parent": "root", "x": 30, "y": 45, "rotation": -90, "length": 20},
]

ANIMS = {
    "idle": {
        "duration": 1.0, "loop": True,
        "tracks": [
            {"bone": "arm", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]},
        ],
    }
}

PIECES = {
    "version": 1,
    "pieces": [
        {"name": "cape", "type": "deform", "bones": ["arm"],
         "source": {"file": "cape.png", "offset": list(CAPE_OFFSET)}},
    ],
}


def _write_inputs(tmp_path):
    src = np.zeros((H, W, 4), np.uint8)
    src[50:75, 5:55] = [200, 50, 50, 255]          # some opaque content
    Image.fromarray(src, "RGBA").save(tmp_path / "src.png")
    cape = np.zeros((CAPE_H, CAPE_W, 4), np.uint8)
    cape[:, :] = [10, 200, 10, 255]                # fully opaque cape layer
    Image.fromarray(cape, "RGBA").save(tmp_path / "cape.png")
    (tmp_path / "skeleton.json").write_text(json.dumps(BONES))
    (tmp_path / "animations.json").write_text(json.dumps(ANIMS))
    (tmp_path / "pieces.json").write_text(json.dumps(PIECES))


def _args(tmp_path, out_dir):
    return [
        "--image", str(tmp_path / "src.png"),
        "--skeleton", str(tmp_path / "skeleton.json"),
        "--pieces", str(tmp_path / "pieces.json"),
        "--animations", str(tmp_path / "animations.json"),
        "--out", str(out_dir), "--name", "hero",
    ]


def test_deform_piece_end_to_end(tmp_path):
    _write_inputs(tmp_path)
    out_dir = tmp_path / "out"
    build_pieces_rig.main(_args(tmp_path, out_dir))

    ske = json.loads((out_dir / "hero_ske.json").read_text())
    arm_doc = ske["armature"][0]
    slot = next(s for s in arm_doc["skin"][0]["slot"] if s["name"] == "cape")
    disp = slot["display"][0]
    assert disp["type"] == "mesh" and disp["name"] == "cape"

    # Recompute the expected mesh from the same cutout alpha (file layer == cutout).
    cape_alpha = np.array(Image.open(tmp_path / "cape.png").convert("RGBA"))[:, :, 3]
    mesh = mesh_gen.build_mesh(cape_alpha, target_cols=16)
    ox, oy = CAPE_OFFSET

    # WORLD-space vertices: piece-local mesh vertices + piece offset.
    expected_verts = [c + (ox if i % 2 == 0 else oy)
                      for i, c in enumerate(v for pt in mesh["vertices"] for v in pt)]
    assert disp["vertices"] == expected_verts

    # region-relative uvs, all inside [0, 1].
    expected_uvs = [c for uv in mesh["uvs"] for c in uv]
    assert disp["uvs"] == expected_uvs
    assert all(0.0 <= u <= 1.0 for u in disp["uvs"])

    # GLOBAL bone indices: only_bones={"arm"} → every influence is arm == index 1.
    packed = disp["weights"]
    idx = 0
    while idx < len(packed):
        count = packed[idx]
        for k in range(count):
            assert packed[idx + 1 + 2 * k] == 1          # global index of "arm"
        idx += 1 + 2 * count

    # bonePose == from_trs of each referenced bone's absolute rest (arm only).
    assert disp["bonePose"] == [1, *[_round(v) for v in from_trs(30, 45, -90)]]

    # region carries the full untrimmed cutout size.
    assert disp["width"] == CAPE_W and disp["height"] == CAPE_H


# --------------------------------------------------------------------------- #
# World-space weights (2 skin bones, NO subset): the IDW split between the two
# bones depends on where the vertices sit on the CANVAS, so this test fails if
# the orchestrator computes weights over piece-LOCAL vertices (drops +ox/+oy).
# --------------------------------------------------------------------------- #
BONES2 = [
    {"name": "root", "parent": None, "x": 30, "y": 70, "rotation": -90, "length": 0, "skin": False},
    {"name": "top", "parent": "root", "x": 5, "y": 42, "rotation": 0, "length": 20},
    {"name": "bot", "parent": "root", "x": 5, "y": 54, "rotation": 0, "length": 20},
]

ANIMS2 = {
    "idle": {
        "duration": 1.0, "loop": True,
        "tracks": [
            {"bone": "top", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]},
        ],
    }
}

PIECES2 = {
    "version": 1,
    "pieces": [
        {"name": "cape", "type": "deform", "mesh": {"cols": 4},
         "source": {"file": "cape.png", "offset": list(CAPE_OFFSET)}},
    ],
}


def test_deform_weights_computed_in_world_space(tmp_path):
    # Cape spans world y 40..52 BETWEEN the two bone segments (y=42 and y=54):
    # top rows weight to "top", bottom rows to "bot", the middle row blends
    # ~50/50. Piece-local vertices (y 0..12) would sit far ABOVE both bones and
    # produce a completely different per-vertex split.
    _write_inputs(tmp_path)
    (tmp_path / "skeleton.json").write_text(json.dumps(BONES2))
    (tmp_path / "animations.json").write_text(json.dumps(ANIMS2))
    (tmp_path / "pieces.json").write_text(json.dumps(PIECES2))
    out_dir = tmp_path / "out"
    # This test's expectation is computed via compute_weights (idw) below, so
    # pin the orchestrator to idw explicitly — heat is now the default.
    build_pieces_rig.main(_args(tmp_path, out_dir) + ["--weights-algo", "idw"])

    ske = json.loads((out_dir / "hero_ske.json").read_text())
    disp = ske["armature"][0]["skin"][0]["slot"][0]["display"][0]

    cape_alpha = np.array(Image.open(tmp_path / "cape.png").convert("RGBA"))[:, :, 3]
    mesh = mesh_gen.build_mesh(cape_alpha, target_cols=4)
    ox, oy = CAPE_OFFSET
    world = [[x + ox, y + oy] for x, y in mesh["vertices"]]
    expected = compute_weights(world, BONES2, p=4.0)

    # exact per-vertex weight VALUES, not just indices
    assert disp["weights"] == pack_weights(expected)

    # sanity that the values are informative (would catch a degenerate fixture):
    # at least one vertex genuinely blends both bones ...
    assert any(sorted(vw["bones"]) == [1, 2] and max(vw["w"]) < 0.95 for vw in expected)
    # ... and the split flips with canvas y: top-most vertex favors "top" (1),
    # bottom-most favors "bot" (2).
    def w_of(vw, gidx):
        return dict(zip(vw["bones"], vw["w"])).get(gidx, 0.0)
    top_i = min(range(len(world)), key=lambda i: world[i][1])
    bot_i = max(range(len(world)), key=lambda i: world[i][1])
    assert w_of(expected[top_i], 1) > 0.9 and w_of(expected[bot_i], 2) > 0.9


def test_deform_error_names_the_piece(tmp_path, capsys):
    # No skin bones at all → compute_weights raises ValueError; the orchestrator
    # must prefix it with the offending piece name.
    _write_inputs(tmp_path)
    no_skin = [
        {"name": "root", "parent": None, "x": 30, "y": 70, "rotation": -90, "length": 0, "skin": False},
        {"name": "limb", "parent": "root", "x": 30, "y": 45, "rotation": -90, "length": 20, "skin": False},
    ]
    (tmp_path / "skeleton.json").write_text(json.dumps(no_skin))
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 1.0, "loop": True, "tracks": [
            {"bone": "limb", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]}]}}))
    (tmp_path / "pieces.json").write_text(json.dumps({
        "version": 1, "pieces": [
            {"name": "cape", "type": "deform",
             "source": {"file": "cape.png", "offset": list(CAPE_OFFSET)}}]}))
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_args(tmp_path, tmp_path / "out"))
    assert "piece cape" in capsys.readouterr().err


def test_deform_piece_unknown_bone_fails(tmp_path):
    _write_inputs(tmp_path)
    bad = {"version": 1, "pieces": [
        {"name": "cape", "type": "deform", "bones": ["nope"],
         "source": {"file": "cape.png", "offset": list(CAPE_OFFSET)}}]}
    (tmp_path / "pieces.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_args(tmp_path, tmp_path / "out"))


def test_weights_algo_heat_smoke(tmp_path):
    # --weights-algo heat must build end-to-end and produce valid weights
    # (same output contract as IDW: normalized, global bone indices).
    _write_inputs(tmp_path)
    out_dir = tmp_path / "out"
    build_pieces_rig.main(_args(tmp_path, out_dir) + ["--weights-algo", "heat"])
    ske = json.loads((out_dir / "hero_ske.json").read_text())
    # the deform display exists and is weighted (weights array non-empty)
    disp = [d for sk in ske["armature"][0]["skin"][0]["slot"]
            for d in sk["display"] if d.get("weights")]
    assert disp, "no weighted deform display in the heat build"
