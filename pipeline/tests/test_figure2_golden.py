"""Golden test — the pipeline-generated `_ske.json` for `figure2` must match the
hand-authored, RENDER-PROVEN fixture `renderer/assets/figure2/figure2_ske.json`.

figure2 is the ground truth for the extended DragonBones contract (§9–§14): it
was authored by hand and plays correctly through the renderer (eye-verified
filmstrips). This test drives the WHOLE Python pipeline in-process from TextRig
inputs (skeleton.json + pieces.json + animations.json in ./fixtures/figure2/)
and asserts the emitted `_ske.json` reproduces figure2 field-for-field, modulo a
small, closed set of DELIBERATE, contract-legitimate differences.

Coordinate-space crux
---------------------
figure2 was authored in ARMATURE space with the origin at the body base center
(y-up-is-negative). TextRig inputs are IMAGE pixels (origin top-left, y-down, no
negatives). We place the armature origin at image (24, 80) — the tight bounding
canvas is 44×80 — so every piece/bone lands at a non-negative image coordinate.

Because DragonBones child-bone transforms and image-display transforms are all
expressed PARENT-LOCAL / OWNER-BONE-LOCAL, the uniform image-space shift cancels
out of them: `arm`/`head` bone transforms and every display transform come out
EXACTLY equal to figure2's. The shift survives in exactly ONE place — the ROOT
bone's transform, which figure2 leaves at identity (origin at 0,0) but we emit as
`{x:24, y:80}` (the armature origin's image position). Per contract §0 absolute
armature placement is free (the runner auto-fits), so this is a benign difference.

Sanctioned differences (the ONLY places generated ≠ figure2)
------------------------------------------------------------
(a) ROOT bone transform: figure2 `{"name":"root"}` (identity) vs generated
    `{"name":"root","transform":{"x":24,"y":80}}`  — the armature-origin shift.
(b) ATLAS layout: SubTexture x/y and atlas width/height/imagePath differ (our
    shelf packer lays regions out differently than figure2's hand atlas). The
    `_ske.json` references regions ONLY by name, so this never touches the ske;
    it is compared on the `_tex.json` side by region (name, width, height) set.

(c) THIRD DIFFERENCE — aabb — RATIFIED 2026-07-10 as a standing exclusion.
    figure2 carries an armature `aabb` `{x:-24,y:-80,width:48,height:80}`; the
    writer emits none (neither the single-mesh nor multipiece path ever has).
    `aabb` is OPTIONAL / informational per contract §2, and figure2's is
    hand-authored NON-TIGHT (width 48, whereas the actual tight piece bounds are
    only 44 wide, x∈[-24,20]) — so the writer could not reproduce this exact box
    even if it computed one, and emitting a computed aabb just to shrink this
    exclusion list would be YAGNI. Decision: keep as an explicit documented
    exclusion, pinned by
    test_no_other_structural_differences_than_the_sanctioned_set so the
    exclusion set can never widen silently.
"""
import copy
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cut_pieces
import pack_atlas
import pieces as pieces_mod
import dragonbones_writer as dbw
from assemble import validate_animations, validate_bones

FIX = Path(__file__).resolve().parent / "fixtures" / "figure2"
GT_DIR = Path(__file__).resolve().parents[2] / "renderer" / "assets" / "figure2"
assert GT_DIR.is_dir(), (
    f"figure2 ground-truth renderer assets not found at {GT_DIR} — "
    f"if they moved, update GT_DIR in this test")
CANVAS_W, CANVAS_H = 44, 80
FPS = 24


def _materialize_layers(dst: Path) -> None:
    """Crop figure2_tex.png into per-piece PNGs + copy pieces.json into `dst`, so
    file-sourced pieces resolve their layers relative to that manifest dir. The
    crop rectangles come from figure2_tex.json itself (never hardcoded — a stale
    rect would silently crop garbage, since pixel content is not asserted
    downstream), so the cut pixels are byte-identical to the atlas regions
    (fill_occlusions skipped)."""
    gt_tex = json.loads((GT_DIR / "figure2_tex.json").read_text())
    atlas = np.array(Image.open(GT_DIR / "figure2_tex.png").convert("RGBA"))
    for s in gt_tex["SubTexture"]:
        x, y, w, h = s["x"], s["y"], s["width"], s["height"]
        Image.fromarray(atlas[y:y + h, x:x + w]).save(dst / f"{s['name']}.png")
    shutil.copy(FIX / "pieces.json", dst / "pieces.json")


def _build(tmp_path):
    """Run the whole pipeline in-process → (ske, tex, built, subtex)."""
    _materialize_layers(tmp_path)
    skeleton = json.loads((FIX / "skeleton.json").read_text())
    animations = json.loads((FIX / "animations.json").read_text())

    pieces = pieces_mod.load_pieces(tmp_path / "pieces.json", skeleton, (CANVAS_W, CANVAS_H))
    bone_anims, piece_tracks = pieces_mod.split_tracks(animations)

    # Same loud gate the CLI would apply. Piece-only anims leave empty bone-track
    # lists, which validate_animations accepts.
    validate_bones(skeleton, CANVAS_W, CANVAS_H)
    validate_animations(bone_anims, skeleton)
    pieces_mod.validate_piece_tracks(animations, pieces)

    # File-sourced pieces ignore the source image content; cut_all only needs its
    # shape. A transparent canvas keeps figure2's hand pixels byte-identical
    # (no occlusion fill — fill:"none" on every piece).
    src = np.zeros((CANVAS_H, CANVAS_W, 4), np.uint8)
    built = cut_pieces.cut_all(src, pieces)

    images = []
    for p in built:
        images.append((p["name"], p["img"]))
        for v in p["variants"]:
            images.append((v["name"], v["img"]))
    atlas_img, subtex = pack_atlas.compose_atlas(images)

    ske = dbw.build_ske_multipiece("figure2", skeleton, built, bone_anims,
                                   piece_tracks, subtex, fps=FPS)
    tex = dbw.build_tex_multi("figure2", atlas_img.shape[1], atlas_img.shape[0],
                              "figure2_tex.png", subtex)
    return ske, tex, built, subtex


# --------------------------------------------------------------------------- #
# Recursive compare with float tolerance; records the JSON path of every diff. #
# --------------------------------------------------------------------------- #
def _diff(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: missing in generated (figure2={b[k]!r})")
            elif k not in b:
                out.append(f"{path}.{k}: extra in generated ({a[k]!r})")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, bool) or isinstance(b, bool):
        # bool == int in Python (True == 1), so also flag a type drift
        if type(a) is not type(b) or a != b:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > 1e-4:
            out.append(f"{path}: {a} vs {b}")
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")


def test_frame_times_round_exactly():
    """The display swap key times MUST round to figure2's exact frames (the JSON
    can only store decimals; verify the writer's round(t*fps) lands right)."""
    animations = json.loads((FIX / "animations.json").read_text())
    got = {}
    for aname, a in animations.items():
        for tr in a["tracks"]:
            if tr.get("prop") == "display":
                got[aname] = [round(k["t"] * FPS) for k in tr["keys"]]
    assert got["demo"] == [0, 20, 28, 48]      # figure2 demo eye displayFrame
    assert got["swaponly"] == [0, 8, 16, 24]   # figure2 swaponly eye displayFrame


def test_root_transform_is_the_armature_origin_in_image_space(tmp_path):
    """Sanctioned difference (a): the sole bone-transform divergence is the root,
    whose generated transform equals the armature origin's image position."""
    ske, _, _, _ = _build(tmp_path)
    gt = json.loads((GT_DIR / "figure2_ske.json").read_text())
    gen_root = ske["armature"][0]["bone"][0]
    gt_root = gt["armature"][0]["bone"][0]
    assert gen_root == {"name": "root", "transform": {"x": 24, "y": 80}}
    assert gt_root == {"name": "root"}          # figure2 root is identity
    # every OTHER bone matches figure2 exactly (translation-invariant parent-local)
    assert ske["armature"][0]["bone"][1:] == gt["armature"][0]["bone"][1:]


def test_atlas_regions_match_by_name_and_size(tmp_path):
    """Sanctioned difference (b): atlas x/y layout is free, but the region set —
    (name, width, height) — must match figure2's, and the ske must reference
    regions ONLY by name (no atlas coords leak into the ske)."""
    ske, tex, _, _ = _build(tmp_path)
    gt_tex = json.loads((GT_DIR / "figure2_tex.json").read_text())
    gen_regions = {(s["name"], s["width"], s["height"]) for s in tex["SubTexture"]}
    gt_regions = {(s["name"], s["width"], s["height"]) for s in gt_tex["SubTexture"]}
    assert gen_regions == gt_regions

    region_names = {s["name"] for s in tex["SubTexture"]}
    for slot in ske["armature"][0]["skin"][0]["slot"]:
        for disp in slot["display"]:
            assert disp["name"] in region_names            # bound by name
            # image displays carry only name(+transform); no atlas x/y/width/height
            assert not ({"x", "y"} & set(disp)), disp


def test_generated_ske_matches_figure2_field_for_field(tmp_path):
    """The whole-document golden: after removing exactly the sanctioned
    differences, generated `_ske.json` must equal figure2 field-for-field —
    slots, displays, transforms, every timeline frame, playTimes, defaultActions,
    frameRate, version, names."""
    ske, _, _, _ = _build(tmp_path)
    gt = json.loads((GT_DIR / "figure2_ske.json").read_text())

    gen = copy.deepcopy(ske)
    ref = copy.deepcopy(gt)

    # (a) drop the root-transform shift.
    root = gen["armature"][0]["bone"][0]
    assert root["name"] == "root" and root.get("transform") == {"x": 24, "y": 80}
    root.pop("transform")

    # (c) drop figure2's hand-authored, non-reproducible informational aabb —
    #     ratified third exclusion, see module docstring.
    assert "aabb" in ref["armature"][0]
    ref["armature"][0].pop("aabb")

    diffs = []
    _diff(gen, ref, "ske", diffs)
    assert not diffs, "generated _ske.json diverges from figure2:\n" + "\n".join(diffs)


def test_no_other_structural_differences_than_the_sanctioned_set(tmp_path):
    """Guard: with NOTHING removed, the raw ske diff must be EXACTLY the two
    ske-level entries (root transform + aabb); the third sanctioned difference —
    atlas layout — lives on the _tex.json side and never touches the ske. If a
    NEW difference appears, this fails loud so the golden can't silently drift."""
    ske, _, _, _ = _build(tmp_path)
    gt = json.loads((GT_DIR / "figure2_ske.json").read_text())
    diffs = []
    _diff(ske, gt, "ske", diffs)
    assert set(diffs) == {
        "ske.armature[0].aabb: missing in generated "
        "(figure2={'x': -24, 'y': -80, 'width': 48, 'height': 80})",
        "ske.armature[0].bone[0].transform: extra in generated ({'x': 24, 'y': 80})",
    }, diffs
