"""TDD for dragonbones_writer.py's flow-deform-morph writer additions (Task 7):
unweighted morphframe mesh displays + `ffd` (contract §18) timeline emission.

Ground truth for every emitted shape is
`renderer/assets/deform/deform_ske.json` (rendered-correct, contract
§18) and `docs/dragonbones-format-contract.md` §18a-§18f. Upstream data shape
(frame_pieces/ffd_timelines/alpha_tracks) comes from `morph_compiler.expand_morph`
(Task 6) — see `pipeline/tests/test_morph_compiler.py` for its own fixtures;
the coverage test here reuses the same synthetic `_sq` piece.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dragonbones_writer as dw  # noqa: E402
import morph_compiler  # noqa: E402
from dragonbones_writer import build_ske_multipiece  # noqa: E402

BONES = [{"name": "root", "parent": None, "x": 0, "y": 0, "rotation": 0, "length": 0, "skin": False}]


def _morphframe_piece(name, offset=(5, 10)):
    return {"name": name, "type": "morphframe", "offset": offset, "variants": [],
            "mesh_data": {"vertices": [[0, 0], [4, 0], [0, 4], [4, 4]],
                          "uvs": [[0, 0], [1, 0], [0, 1], [1, 1]],
                          "triangles": [[0, 1, 2], [1, 3, 2]]}}


# ---------------------------------------------------------------------------
# A. unweighted morphframe mesh display
# ---------------------------------------------------------------------------
def test_morphframe_display_unweighted():
    built = [_morphframe_piece("body__k0", offset=(5, 10))]
    ske = build_ske_multipiece("t", BONES, built, {}, {}, [
        {"name": "body__k0", "x": 0, "y": 0, "width": 4, "height": 4}])
    disp = ske["armature"][0]["skin"][0]["slot"][0]["display"][0]
    assert disp["type"] == "mesh"
    assert disp["name"] == "body__k0"
    assert "weights" not in disp
    assert "slotPose" not in disp
    assert "bonePose" not in disp
    # world vertices = piece-local + offset (5, 10)
    assert disp["vertices"] == [5, 10, 9, 10, 5, 14, 9, 14]
    assert disp["uvs"] == [0, 0, 1, 0, 0, 1, 1, 1]
    assert disp["triangles"] == [0, 1, 2, 1, 3, 2]
    assert disp["width"] == 4 and disp["height"] == 4


def test_morphframe_display_rotated_parent_bone_local_space():
    # An UNWEIGHTED mesh display's vertices live in the slot's parent bone's
    # LOCAL space at runtime — nothing compensates the bone's world transform
    # (the weighted path does it via slotPose/bonePose, which morphframes drop).
    # Morphframe geometry is authored in IMAGE space, so the writer must emit
    # v_local = inv(world_bind(owner)) · v_image — the same §6c bind math
    # _deform_display bakes into bonePose. Owner root: rotation -90 at (2, 4)
    # → inv = [0, 1, -1, 0, 4, -2] → v_local = (4 - y_img, x_img - 2).
    bones = [{"name": "root", "parent": None, "x": 2, "y": 4, "rotation": -90,
              "length": 0, "skin": False}]
    built = [_morphframe_piece("body__k0", offset=(5, 10))]
    ske = build_ske_multipiece("t", bones, built, {}, {}, [
        {"name": "body__k0", "x": 0, "y": 0, "width": 4, "height": 4}])
    disp = ske["armature"][0]["skin"][0]["slot"][0]["display"][0]
    # image verts (5,10) (9,10) (5,14) (9,14) → local, concrete numbers:
    assert disp["vertices"] == [-6, 3, -6, 7, -10, 3, -10, 7]
    # and the same numbers via the writer's own bind-matrix math (§6c):
    inv = dw.mat_invert(dw.from_trs(2, 4, -90))
    expected = []
    for x, y in [(5, 10), (9, 10), (5, 14), (9, 14)]:
        lx, ly = dw.mat_apply(inv, x, y)
        expected.extend([dw._round(lx), dw._round(ly)])
    assert disp["vertices"] == expected
    assert "weights" not in disp and "slotPose" not in disp and "bonePose" not in disp


# ---------------------------------------------------------------------------
# A2. image-space global -> bone-track parent-local mapping (motion decomp)
# ---------------------------------------------------------------------------
def test_image_delta_to_bone_local_root_owner_is_identity():
    # a root-owned bone's translate is in armature space == image space
    bones = [{"name": "root", "parent": None, "x": 3, "y": 7, "rotation": -90, "length": 0}]
    assert dw.image_delta_to_bone_local(bones, "root", 5, 0) == (5, 0)
    assert dw.image_delta_to_bone_local(bones, "root", 0, 9) == (0, 9)


def test_image_delta_to_bone_local_carrier_under_minus90_root():
    # carrier parented to a root rotated -90: image (dx,dy) -> (-dy, dx).
    # A pure VERTICAL image jump of -40 (body moves up) must map to x_key=+40.
    bones = [{"name": "root", "parent": None, "x": 179, "y": 600, "rotation": -90, "length": 30},
             {"name": "carrier", "parent": "root", "x": 179, "y": 450, "rotation": -90, "length": 150}]
    x, y = dw.image_delta_to_bone_local(bones, "carrier", 0.0, -40.0)
    assert abs(x - 40.0) < 1e-9 and abs(y - 0.0) < 1e-9
    # horizontal image jump +12 -> y_key=+12
    x2, y2 = dw.image_delta_to_bone_local(bones, "carrier", 12.0, 0.0)
    assert abs(x2 - 0.0) < 1e-9 and abs(y2 - 12.0) < 1e-9


# ---------------------------------------------------------------------------
# B. ffd timeline emission
# ---------------------------------------------------------------------------
OWNER_IDENT = {"p": BONES[0]}


def test_ffd_offsets_rotate_into_bone_local_space():
    # FFD deltas are VECTORS: they add to the mesh-local vertices at render
    # time, so they must be rotated into the owner bone's local frame by the
    # LINEAR part of the inverse bind matrix — NO translation (a delta is not
    # a point). Owner rot -90: image-space (5, 0) → local (0, 5).
    owner = {"name": "root", "parent": None, "x": 2, "y": 4, "rotation": -90, "length": 0}
    off = np.zeros((2, 2))
    off[0] = [5.0, 0.0]
    tracks = [{"piece": "p", "keys": [
        {"t": 0.0, "offsets": np.zeros((2, 2))},
        {"t": 0.5, "offsets": off},
        {"t": 1.0, "offsets": np.zeros((2, 2))}]}]
    frames = dw.build_ffd_timelines("anim", tracks, {"p": owner}, fps=30, total_frames=30)
    mid = frames[0]["frame"][1]
    # rotated vector: dx'=(0,5) — translation of the bind matrix must NOT leak in
    assert mid["vertices"] == [0, 5]
    assert mid.get("offset", 0) == 0


def test_ffd_block_emitted():
    built = [_morphframe_piece("body__k0"), _morphframe_piece("body__k1", offset=(0, 0))]
    subtex = [{"name": "body__k0", "x": 0, "y": 0, "width": 4, "height": 4},
              {"name": "body__k1", "x": 8, "y": 0, "width": 4, "height": 4}]
    anims = {"win": {"duration": 1.0, "loop": True, "tracks": []}}
    ffd_timelines = [
        {"piece": "body__k0", "keys": [
            {"t": 0.0, "offsets": np.zeros((4, 2))},
            {"t": 0.5, "offsets": np.array([[0, 0], [0, 0], [3.0, -2.0], [0, 0]])},
            {"t": 1.0, "offsets": np.zeros((4, 2))}]},
        {"piece": "body__k1", "keys": [
            {"t": 0.0, "offsets": np.zeros((4, 2))},
            {"t": 1.0, "offsets": np.zeros((4, 2))}]},
    ]
    ske = build_ske_multipiece("t", BONES, built, anims, {}, subtex, ffd_timelines=ffd_timelines)
    anim = ske["armature"][0]["animation"][0]
    assert "ffd" in anim
    by_slot = {e["slot"]: e for e in anim["ffd"]}
    assert set(by_slot) == {"body__k0", "body__k1"}

    e0 = by_slot["body__k0"]
    assert e0["slot"] == "body__k0" and e0["name"] == "body__k0"
    frames = e0["frame"]
    assert frames[0]["vertices"] == []                 # rest key: all-zero
    mid = frames[1]
    assert mid["offset"] % 2 == 0
    assert len(mid["vertices"]) % 2 == 0
    assert sum(f["duration"] for f in frames) == anim["duration"] == 30

    e1 = by_slot["body__k1"]
    assert all(f["vertices"] == [] for f in e1["frame"])   # never moves
    assert sum(f["duration"] for f in e1["frame"]) == 30


def test_ffd_zero_compression():
    # only vertex 3 (flat index 6/7) moves.
    offsets_mid = np.zeros((5, 2))
    offsets_mid[3] = [3.5, -2.0]
    tracks = [{"piece": "p", "keys": [
        {"t": 0.0, "offsets": np.zeros((5, 2))},
        {"t": 0.5, "offsets": offsets_mid},
        {"t": 1.0, "offsets": np.zeros((5, 2))}]}]
    frames = dw.build_ffd_timelines("anim", tracks, OWNER_IDENT, fps=30, total_frames=30)
    mid = frames[0]["frame"][1]
    assert mid["offset"] == 6
    assert mid["vertices"] == [3.5, -2.0]


def test_ffd_distinct_keys_never_merge():
    J = 1 / 30 + 0.001
    off_a = np.zeros((2, 2))
    off_b = np.zeros((2, 2))
    off_b[0] = [5.0, 0.0]
    # two keys 1/30+0.001 apart with DIFFERENT values must survive as distinct
    # frames — the compiler's minimum spacing guarantees >=1-frame separation.
    tracks = [{"piece": "p", "keys": [
        {"t": 0.0, "offsets": off_a},
        {"t": J, "offsets": off_b},
        {"t": 1.0, "offsets": off_a}]}]
    frames = dw.build_ffd_timelines("anim", tracks, OWNER_IDENT, fps=30, total_frames=30)
    fr = frames[0]["frame"]
    assert len(fr) == 3
    assert sum(f["duration"] for f in fr) == 30

    # a synthetic sub-frame collision (two DIFFERENT-valued keys quantizing to
    # the SAME output frame) indicates a bug upstream — fail loud.
    collide_tracks = [{"piece": "p", "keys": [
        {"t": 0.0, "offsets": off_a},
        {"t": 0.0001, "offsets": off_b},   # both round to frame 0 at 30fps
        {"t": 1.0, "offsets": off_a}]}]
    with pytest.raises(SystemExit):
        dw.build_ffd_timelines("anim", collide_tracks, OWNER_IDENT, fps=30, total_frames=30)


def test_ffd_keys_must_start_and_end_on_loop():
    tracks = [{"piece": "p", "keys": [
        {"t": 0.1, "offsets": np.zeros((2, 2))},
        {"t": 1.0, "offsets": np.zeros((2, 2))}]}]
    with pytest.raises(ValueError, match="t=0"):
        dw.build_ffd_timelines("anim", tracks, OWNER_IDENT, fps=30, total_frames=30)

    tracks2 = [{"piece": "p", "keys": [
        {"t": 0.0, "offsets": np.zeros((2, 2))},
        {"t": 0.9, "offsets": np.zeros((2, 2))}]}]
    with pytest.raises(ValueError, match="t=duration"):
        dw.build_ffd_timelines("anim", tracks2, OWNER_IDENT, fps=30, total_frames=30)


# ---------------------------------------------------------------------------
# C. frame-quantized end-to-end: ONE stepped display track, exactly one display
#    active per frame, switches land on the cut frames (no colorFrame alpha).
# ---------------------------------------------------------------------------
def _sq(shift):
    img = np.zeros((64, 64, 4), np.uint8)
    img[22:42, 10 + shift:30 + shift, :3] = 200
    img[22:42, 10 + shift:30 + shift, 3] = 255
    return img


def _morph_piece(loop="pingpong"):
    return {"name": "body", "type": "morph", "loop": loop, "mesh": {"cols": 6},
            "frames": [
                {"file": "a.png", "t": 0.0, "key": True},
                {"file": "g.png", "t": 0.1, "key": False},
                {"file": "b.png", "t": 0.25, "key": True}],
            "frame_images": [_sq(0), _sq(4), _sq(8)]}


MORPH_ANIMS = {"win": {"duration": 0.5, "loop": True, "tracks": []}}


def _subtex(fp):
    """Atlas SubTexture stubs for every display of the single morphframe piece."""
    names = [fp[0]["name"]] + [v["name"] for v in fp[0]["variants"]]
    return [{"name": n, "x": i * 70, "y": 0, "width": 64, "height": 64}
            for i, n in enumerate(names)]


def _eval_displayframe(frames, frame_idx):
    """The active display index at integer output frame `frame_idx` (stepped
    displayFrame, contract §11: hold until the next frame arrives)."""
    acc = 0
    for f in frames:
        if f["duration"] == 0 or frame_idx < acc + f["duration"]:
            return f["value"]
        acc += f["duration"]
    return frames[-1]["value"]


def test_display_track_one_active_per_frame_and_no_alpha():
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(_morph_piece(), MORPH_ANIMS)
    subtex = _subtex(fp)
    ske = build_ske_multipiece("t", BONES, fp, MORPH_ANIMS, {"win": list(tracks)},
                               subtex, ffd_timelines=ffd)
    anim = ske["armature"][0]["animation"][0]
    total_frames = anim["duration"]
    # exactly ONE slot carries a displayFrame timeline (the shared morph slot),
    # and NO colorFrame alpha timelines remain (single display => no coverage).
    disp_tls = [tl for tl in anim["slot"] if "displayFrame" in tl]
    assert len(disp_tls) == 1
    assert disp_tls[0]["name"] == "body"
    assert not any("colorFrame" in tl for tl in anim["slot"])
    frames = disp_tls[0]["displayFrame"]
    assert sum(f["duration"] for f in frames) == total_frames   # contiguous, no gaps
    ndisp = 1 + len(fp[0]["variants"])
    # one valid display index active at every output frame
    for f in range(total_frames + 1):
        idx = _eval_displayframe(frames, f)
        assert 0 <= idx < ndisp, f"bad display index at frame {f}"


def test_display_track_forward_loop_closes_on_display_0():
    fwd_anims = {"win": {"duration": 0.3, "loop": True, "tracks": []}}
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(_morph_piece("forward"), fwd_anims)
    subtex = _subtex(fp)
    ske = build_ske_multipiece("t", BONES, fp, fwd_anims, {"win": list(tracks)},
                               subtex, ffd_timelines=ffd)
    anim = ske["armature"][0]["animation"][0]
    total_frames = anim["duration"]
    frames = [tl for tl in anim["slot"] if "displayFrame" in tl][0]["displayFrame"]
    assert sum(f["duration"] for f in frames) == total_frames
    # forward loop: the timeline wraps back to display 0 at the end
    assert frames[-1]["value"] == 0
    ndisp = 1 + len(fp[0]["variants"])
    for f in range(total_frames + 1):
        assert 0 <= _eval_displayframe(frames, f) < ndisp
