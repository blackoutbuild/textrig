"""Golden test — the pipeline-generated zOrder timeline for `zorder` must match
the hand-authored, RENDER-PROVEN fixture
`renderer/assets/zorder/zorder_ske.json`.

zorder is the ground truth for contract §15 (animated draw order): it was
authored by hand and plays correctly through the renderer (eye-verified
filmstrip, probe sequence red/red/blue/blue/red/red — see contract §15
"PROVEN" note). This test drives the WHOLE Python pipeline in-process from
TextRig inputs (skeleton.json + pieces.json + animations.json in
./fixtures/zorder/) and asserts the emitted `_ske.json`'s `zOrder` timeline
reproduces the hand fixture's EXACTLY.

Unlike test_figure2_golden.py, this is NOT a whole-document golden: the
zorder fixture's bones/atlas/display transforms were authored independently
(different canvas, different piece placement) and are not expected to match
byte-for-byte. The only claim under test is the thing zorder actually proves:
given per-piece `order` tracks, the writer (`dragonbones_writer.build_zorder_frames`)
emits the same (duration, zOrder-pairs) frame sequence DragonBones itself was
proven to interpret correctly. The base slot order (`["back", "front"]`) is
asserted too since it is the precondition the zOrder pairs are indices into.

Fill note
---------
`pieces.json` uses polygon-cut pieces from a flat `source.png` where the red
square is painted OVER the blue square in their 20x20 overlap (that IS the
correct rest-frame look). Cutting `back`'s polygon straight out of that flat
raster would bake red into part of its own texture (the overlap corner) —
wrong, because during `flip` the writer's zOrder timeline draws `back` ON TOP
of `front` for 1s, and that corner must show blue, not baked-in red. Fixed by
leaving `fill` at its pieces.py default (`"extend"`, not `"none"`): the
orchestrator's `fill_occlusions` pass (skipped by the figure2 fixture, but NOT
skipped here) repaints `back`'s occluded corner by nearest-valid-pixel
extension from `back`'s own unoccluded (blue) pixels before the atlas is
packed, so the cutout is a clean solid blue square. This test builds the
pipeline in-process up to and including `fill_occlusions`, matching the real
`build_pieces_rig.py` orchestrator order (cut -> fill_occlusions -> atlas ->
write), so the golden ske reflects the same pixel content the CLI would ship.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cut_pieces
import fill_occlusions
import pack_atlas
import pieces as pieces_mod
import dragonbones_writer as dbw
from assemble import validate_animations, validate_bones

FIX = Path(__file__).resolve().parent / "fixtures" / "zorder"
GT_PATH = (Path(__file__).resolve().parents[2] / "renderer"
           / "assets" / "zorder" / "zorder_ske.json")
assert GT_PATH.is_file(), (
    f"zorder ground-truth renderer asset not found at {GT_PATH} — "
    f"if it moved, update GT_PATH in this test")
FPS = 30    # matches the hand fixture's frameRate (needed so key times t=0/0.5/1.5
            # round to the exact frames 0/15/45 the hand fixture's durations imply)


def _build(animations=None):
    """Run the whole pipeline in-process (mirrors build_pieces_rig.py's order:
    cut -> fill_occlusions -> atlas -> write) -> ske dict. `animations`
    overrides the fixture's animations.json (used by the sensitivity test)."""
    skeleton = json.loads((FIX / "skeleton.json").read_text())
    if animations is None:
        animations = json.loads((FIX / "animations.json").read_text())
    src = np.array(Image.open(FIX / "source.png").convert("RGBA"))
    h, w = src.shape[:2]

    pieces = pieces_mod.load_pieces(FIX / "pieces.json", skeleton, (w, h))
    bone_anims, piece_tracks = pieces_mod.split_tracks(animations)

    validate_bones(skeleton, w, h)
    validate_animations(bone_anims, skeleton)   # flip has zero bone tracks — accepted
    pieces_mod.validate_piece_tracks(animations, pieces)

    built = cut_pieces.cut_all(src, pieces)
    fill_occlusions.fill_occlusions(built)      # repaints back's occluded corner blue

    images = []
    for p in built:
        images.append((p["name"], p["img"]))
        for v in p["variants"]:
            images.append((v["name"], v["img"]))
    atlas_img, subtex = pack_atlas.compose_atlas(images)

    ske = dbw.build_ske_multipiece("zorder", skeleton, built, bone_anims,
                                   piece_tracks, subtex, fps=FPS)
    return ske


def test_source_png_regenerates_byte_identical(tmp_path):
    """make_source_png.py is deterministic — the committed source.png must be
    exactly what it produces (no drift between the script and the fixture)."""
    import runpy
    committed = (FIX / "source.png").read_bytes()
    script = FIX / "make_source_png.py"
    # Run the generator with cwd = tmp_path so it doesn't clobber the committed
    # fixture; it writes next to itself (Path(__file__).parent), so copy+patch.
    text = script.read_text()
    scratch = tmp_path / "make_source_png.py"
    scratch.write_text(text)
    runpy.run_path(str(scratch), run_name="__main__")
    generated = (tmp_path / "source.png").read_bytes()
    assert generated == committed


def test_base_slot_order_is_back_then_front():
    """Precondition for the zOrder pairs' meaning: back=index 0, front=index 1
    (contract §9, list order = draw order)."""
    ske = _build()
    slots = ske["armature"][0]["slot"]
    assert [s["name"] for s in slots] == ["back", "front"]


def test_zorder_timeline_matches_hand_proven_fixture():
    """The golden assertion: the generated `flip` animation's zOrder timeline
    must equal the hand-proven fixture's zOrder timeline exactly (frame count,
    durations, and (slotIndex, offset) pairs)."""
    ske = _build()
    gt = json.loads(GT_PATH.read_text())

    gen_anim = ske["armature"][0]["animation"][0]
    gt_anim = gt["armature"][0]["animation"][0]

    assert gen_anim["name"] == "flip" == gt_anim["name"]
    assert gen_anim["zOrder"] == gt_anim["zOrder"], (
        f"generated zOrder timeline diverges from the hand-proven fixture:\n"
        f"generated: {gen_anim['zOrder']}\n"
        f"expected:  {gt_anim['zOrder']}")


def test_zorder_timeline_would_catch_a_wrong_pairing():
    """Sensitivity: the golden equality reacts to real generator output — the
    SAME pipeline run with shifted key times (0.7/1.3 instead of 0.5/1.5,
    i.e. durations 21/18/21 instead of 15/30/15) must NOT match the fixture."""
    animations = json.loads((FIX / "animations.json").read_text())
    keys = animations["flip"]["tracks"][0]["keys"]
    assert [k["t"] for k in keys] == [0.0, 0.5, 1.5], "fixture drifted — update this test"
    keys[1]["t"], keys[2]["t"] = 0.7, 1.3
    ske = _build(animations)
    gt = json.loads(GT_PATH.read_text())
    gen_frames = ske["armature"][0]["animation"][0]["zOrder"]["frame"]
    gt_frames = gt["armature"][0]["animation"][0]["zOrder"]["frame"]
    assert gen_frames != gt_frames
