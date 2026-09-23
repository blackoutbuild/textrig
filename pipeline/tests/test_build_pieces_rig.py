"""Smoke test for build_pieces_rig.py — the Task 10 multi-piece orchestrator.

Self-contained tiny fixture (not the golden figure2/duck assets): a 60x80 RGBA
source with two opaque colored blocks, a 2-bone skeleton, a 2-piece manifest
(one polygon rigid piece, one polygon swap piece with a variant PNG), and an
idle animation with one bone-rotation track, one piece-display track, and one
piece-alpha track.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_pieces_rig  # noqa: E402

W, H = 60, 80

BONES = [
    {"name": "root", "parent": None, "x": 30, "y": 70, "rotation": -90, "length": 0, "skin": False},
    {"name": "limb", "parent": "root", "x": 30, "y": 20, "rotation": -90, "length": 20},
]

ANIMS = {
    "idle": {
        "duration": 1.0,
        "loop": True,
        "tracks": [
            {"bone": "limb", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]},
            {"piece": "eye", "prop": "display",
             "keys": [{"t": 0, "v": "eye"}, {"t": 0.5, "v": "eye_blink"}, {"t": 1.0, "v": "eye"}]},
            {"piece": "body", "prop": "alpha",
             "keys": [{"t": 0, "v": 1.0}, {"t": 0.5, "v": 0.8}, {"t": 1.0, "v": 1.0}]},
        ],
    }
}


def _make_image():
    arr = np.zeros((H, W, 4), np.uint8)
    arr[50:75, 5:55] = [200, 50, 50, 255]      # "body" block
    arr[5:25, 10:30] = [50, 50, 200, 255]      # "eye" block
    return arr


def _write_common(tmp_path):
    Image.fromarray(_make_image(), "RGBA").save(tmp_path / "src.png")
    (tmp_path / "skeleton.json").write_text(json.dumps(BONES))
    (tmp_path / "animations.json").write_text(json.dumps(ANIMS))
    variant = np.zeros((20, 20, 4), np.uint8)
    variant[:, :] = [10, 200, 10, 255]
    Image.fromarray(variant, "RGBA").save(tmp_path / "eye_blink.png")


def _write_pieces(tmp_path, pieces_doc):
    (tmp_path / "pieces.json").write_text(json.dumps(pieces_doc))


VALID_PIECES = {
    "version": 1,
    "pieces": [
        {"name": "body", "bone": "root", "type": "rigid",
         "source": {"polygon": [[5, 50], [55, 50], [55, 75], [5, 75]]}},
        {"name": "eye", "bone": "limb", "type": "swap",
         "source": {"polygon": [[10, 5], [30, 5], [30, 25], [10, 25]]},
         "variants": [{"name": "eye_blink", "file": "eye_blink.png", "offset": [10, 5]}]},
    ],
}


def _args(tmp_path, out_dir, name="critter"):
    return [
        "--image", str(tmp_path / "src.png"),
        "--skeleton", str(tmp_path / "skeleton.json"),
        "--pieces", str(tmp_path / "pieces.json"),
        "--animations", str(tmp_path / "animations.json"),
        "--out", str(out_dir),
        "--name", name,
    ]


def test_build_pieces_rig_end_to_end(tmp_path):
    _write_common(tmp_path)
    _write_pieces(tmp_path, VALID_PIECES)
    out_dir = tmp_path / "out"

    build_pieces_rig.main(_args(tmp_path, out_dir))

    ske_path = out_dir / "critter_ske.json"
    tex_json_path = out_dir / "critter_tex.json"
    tex_png_path = out_dir / "critter_tex.png"
    debug_path = out_dir / "critter.debug.png"
    for p in (ske_path, tex_json_path, tex_png_path, debug_path):
        assert p.is_file(), p

    ske = json.loads(ske_path.read_text())
    armature = ske["armature"][0]
    assert armature["name"] == "critter"
    assert [s["name"] for s in armature["slot"]] == ["body", "eye"]

    tex = json.loads(tex_json_path.read_text())
    assert len(tex["SubTexture"]) == 3          # body + eye + eye_blink

    # debug overlay must cover the full source canvas
    with Image.open(debug_path) as dbg:
        assert dbg.size == (W, H)
    # atlas must actually carry pixels (not an all-transparent placeholder)
    atlas = np.array(Image.open(tex_png_path).convert("RGBA"))
    assert atlas.any()

    pieces_dir = out_dir / "critter.pieces"
    assert pieces_dir.is_dir()
    cutouts = {p.name for p in pieces_dir.glob("*.png")}
    assert "body.png" in cutouts and "eye.png" in cutouts


def test_deform_piece_succeeds_end_to_end(tmp_path):
    # A deform piece now flows all the way through: mesh slot in the ske, a debug
    # overlay covering the full canvas, and a cutout PNG.
    _write_common(tmp_path)
    deform_pieces = {
        "version": 1,
        "pieces": [
            {"name": "body", "bone": "root", "type": "rigid",
             "source": {"polygon": [[5, 50], [55, 50], [55, 75], [5, 75]]}},
            {"name": "cape", "type": "deform", "bones": ["limb"],
             "source": {"polygon": [[10, 5], [30, 5], [30, 25], [10, 25]]}},
        ],
    }
    _write_pieces(tmp_path, deform_pieces)
    out_dir = tmp_path / "out"
    # animations reference 'eye' (a swap piece) which no longer exists here; use a
    # bone-only animation for this fixture.
    (tmp_path / "animations.json").write_text(json.dumps({
        "idle": {"duration": 1.0, "loop": True, "tracks": [
            {"bone": "limb", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]}]}}))

    build_pieces_rig.main(_args(tmp_path, out_dir))

    ske = json.loads((out_dir / "critter_ske.json").read_text())
    cape_slot = next(s for s in ske["armature"][0]["skin"][0]["slot"] if s["name"] == "cape")
    assert cape_slot["display"][0]["type"] == "mesh"

    with Image.open(out_dir / "critter.debug.png") as dbg:
        assert dbg.size == (W, H)
    assert (out_dir / "critter.pieces" / "cape.png").is_file()


def test_deform_piece_unknown_bone_rejected(tmp_path):
    _write_common(tmp_path)
    deform_pieces = {
        "version": 1,
        "pieces": [
            {"name": "cape", "type": "deform", "bones": ["ghost"],
             "source": {"polygon": [[5, 50], [55, 50], [55, 75], [5, 75]]}},
        ],
    }
    _write_pieces(tmp_path, deform_pieces)
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_args(tmp_path, out_dir))


@pytest.mark.parametrize("mode", ["auto", "extend", "inpaint"])
def test_fill_mode_forwarded_to_fill_occlusions(tmp_path, monkeypatch, mode):
    # --fill-mode must reach fill_occlusions.fill_occlusions as the `mode` kwarg
    # (follows the same forwarding contract as --fill-blur/--bg-tolerance/--bg).
    _write_common(tmp_path)
    _write_pieces(tmp_path, VALID_PIECES)
    out_dir = tmp_path / "out"

    calls = []
    real_fill_occlusions = build_pieces_rig.fill_occlusions.fill_occlusions

    def spy(built_pieces, **kwargs):
        calls.append(kwargs)
        return real_fill_occlusions(built_pieces, **kwargs)

    monkeypatch.setattr(build_pieces_rig.fill_occlusions, "fill_occlusions", spy)

    build_pieces_rig.main(_args(tmp_path, out_dir) + ["--fill-mode", mode])

    assert len(calls) == 1
    assert calls[0].get("mode") == mode


def test_fill_mode_defaults_to_auto(tmp_path, monkeypatch):
    _write_common(tmp_path)
    _write_pieces(tmp_path, VALID_PIECES)
    out_dir = tmp_path / "out"

    calls = []
    real_fill_occlusions = build_pieces_rig.fill_occlusions.fill_occlusions

    def spy(built_pieces, **kwargs):
        calls.append(kwargs)
        return real_fill_occlusions(built_pieces, **kwargs)

    monkeypatch.setattr(build_pieces_rig.fill_occlusions, "fill_occlusions", spy)

    build_pieces_rig.main(_args(tmp_path, out_dir))

    assert len(calls) == 1
    assert calls[0].get("mode") == "auto"


def test_fill_mode_invalid_value_rejected(tmp_path):
    _write_common(tmp_path)
    _write_pieces(tmp_path, VALID_PIECES)
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_args(tmp_path, out_dir) + ["--fill-mode", "bogus"])


MW, MH = 64, 64


def _morph_square(shift):
    arr = np.zeros((MH, MW, 4), np.uint8)
    arr[20:40, 10 + shift:30 + shift, :3] = [80, 160, 240]
    arr[20:40, 10 + shift:30 + shift, 3] = 255
    return arr


def _write_morph_common(tmp_path):
    Image.fromarray(_morph_square(0), "RGBA").save(tmp_path / "frame0.png")
    Image.fromarray(_morph_square(4), "RGBA").save(tmp_path / "frame1.png")
    Image.fromarray(_morph_square(8), "RGBA").save(tmp_path / "frame2.png")
    (tmp_path / "skeleton.json").write_text(json.dumps([
        {"name": "root", "parent": None, "x": 32, "y": 32,
         "rotation": -90, "length": 0, "skin": False},
    ]))


MORPH_PIECES = {
    "version": 1,
    "pieces": [
        {"name": "body", "type": "morph", "loop": "pingpong", "mesh": {"cols": 6},
         "frames": [
             {"file": "frame0.png", "t": 0, "key": True},
             {"file": "frame1.png", "t": 0.1, "key": False},
             {"file": "frame2.png", "t": 0.25, "key": True},
         ]},
    ],
}

MORPH_ANIMS = {"win": {"duration": 0.5, "loop": True, "tracks": []}}


def _morph_args(tmp_path, out_dir, name="morphrig"):
    return [
        "--image", str(tmp_path / "frame0.png"),  # unused source canvas for morph-only rigs
        "--skeleton", str(tmp_path / "skeleton.json"),
        "--pieces", str(tmp_path / "pieces.json"),
        "--animations", str(tmp_path / "animations.json"),
        "--out", str(out_dir),
        "--name", name,
    ]


def test_morph_piece_end_to_end(tmp_path):
    _write_morph_common(tmp_path)
    (tmp_path / "pieces.json").write_text(json.dumps(MORPH_PIECES))
    (tmp_path / "animations.json").write_text(json.dumps(MORPH_ANIMS))
    out_dir = tmp_path / "out"

    build_pieces_rig.main(_morph_args(tmp_path, out_dir))

    ske_path = out_dir / "morphrig_ske.json"
    tex_json_path = out_dir / "morphrig_tex.json"
    tex_png_path = out_dir / "morphrig_tex.png"
    debug_path = out_dir / "morphrig.debug.png"
    for p in (ske_path, tex_json_path, tex_png_path, debug_path):
        assert p.is_file(), p
    with Image.open(debug_path) as dbg:
        assert dbg.size == (MW, MH)

    ske = json.loads(ske_path.read_text())
    armature = ske["armature"][0]
    skin_slots = {s["name"]: s for s in armature["skin"][0]["slot"]}
    # ONE slot, TWO mesh displays (K=2 key frames): display 0 = piece name, then
    # the __k1 variant. Contract §11+§18 stepped display swap.
    assert set(skin_slots) == {"body"}
    displays = skin_slots["body"]["display"]
    assert [d["name"] for d in displays] == ["body", "body__k1"]
    for display in displays:
        assert display["type"] == "mesh"
        assert "weights" not in display

    [anim] = armature["animation"]
    assert anim["name"] == "win"
    # both ffd timelines target the SHARED slot "body", one per display name
    assert {tl["slot"] for tl in anim["ffd"]} == {"body"}
    assert {tl["name"] for tl in anim["ffd"]} == {"body", "body__k1"}
    # a stepped displayFrame timeline on the shared slot; NO colorFrame alpha
    disp_tls = [tl for tl in anim["slot"] if "displayFrame" in tl]
    assert len(disp_tls) == 1 and disp_tls[0]["name"] == "body"
    assert not any("colorFrame" in tl for tl in anim["slot"])

    tex = json.loads(tex_json_path.read_text())
    assert len(tex["SubTexture"]) == 2   # only the two KEY frames are packed; the guide is not

    pieces_dir = out_dir / "morphrig.pieces"
    cutouts = {p.name for p in pieces_dir.glob("*.png")}
    assert cutouts == {"body.png", "body__k1.png"}


def test_morph_piece_two_animations_rejected(tmp_path):
    _write_morph_common(tmp_path)
    (tmp_path / "pieces.json").write_text(json.dumps(MORPH_PIECES))
    (tmp_path / "animations.json").write_text(json.dumps({
        "win": {"duration": 0.5, "loop": True, "tracks": []},
        "lose": {"duration": 0.5, "loop": True, "tracks": []},
    }))
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_morph_args(tmp_path, out_dir))


def test_missing_variant_file_fails(tmp_path):
    _write_common(tmp_path)
    bad_pieces = {
        "version": 1,
        "pieces": [
            {"name": "body", "bone": "root", "type": "rigid",
             "source": {"polygon": [[5, 50], [55, 50], [55, 75], [5, 75]]}},
            {"name": "eye", "bone": "limb", "type": "swap",
             "source": {"polygon": [[10, 5], [30, 5], [30, 25], [10, 25]]},
             "variants": [{"name": "eye_blink", "file": "nope.png", "offset": [10, 5]}]},
        ],
    }
    _write_pieces(tmp_path, bad_pieces)
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit):
        build_pieces_rig.main(_args(tmp_path, out_dir))
