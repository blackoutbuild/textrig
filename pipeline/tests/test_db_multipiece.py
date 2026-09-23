"""TDD for dragonbones_writer.py multi-piece armature (Stage 1).

Ground truth for every emitted shape is
`renderer/assets/figure2/figure2_ske.json` (rendered-correct) and
`docs/dragonbones-format-contract.md` §9–§14. Where the pre-research plan
hypotheses disagreed with the fixture, the fixture wins — noted inline.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragonbones_writer import (
    build_ske_multipiece, build_slot_timelines, build_tex_multi, image_display_transform,
)

BONES = [
    {"name": "root", "parent": None, "x": 50, "y": 90, "rotation": -90, "length": 0, "skin": False},
    {"name": "arm", "parent": "root", "x": 30, "y": 40, "rotation": -135, "length": 30},
]


def _piece(name, bone, ptype="rigid", offset=(10, 20), size=(40, 60), variants=()):
    img = np.zeros((size[1], size[0], 4), np.uint8); img[:, :, 3] = 255
    return {"name": name, "type": ptype, "bone": bone, "fill": "extend",
            "img": img, "offset": offset, "mask_full": np.ones((100, 100), bool),
            "variants": list(variants)}


def test_slots_in_manifest_order_and_parents():
    built = [_piece("body", "root"), _piece("arm", "arm")]
    ske = build_ske_multipiece("t", BONES, built, {}, {}, [
        {"name": "body", "x": 0, "y": 0, "width": 40, "height": 60},
        {"name": "arm", "x": 44, "y": 0, "width": 40, "height": 60}])
    arm_doc = ske["armature"][0]
    assert [s["name"] for s in arm_doc["slot"]] == ["body", "arm"]
    assert arm_doc["slot"][1]["parent"] == "arm"


def test_image_display_center_maps_back_to_world():
    # inverse check: transform must place the region center at offset+size/2 in world
    t = image_display_transform((10, 20), (40, 60), BONES[1])   # owner = arm
    r = math.radians(BONES[1]["rotation"])
    cx = BONES[1]["x"] + t["x"] * math.cos(r) - t["y"] * math.sin(r)
    cy = BONES[1]["y"] + t["x"] * math.sin(r) + t["y"] * math.cos(r)
    assert abs(cx - 30) < 1e-6 and abs(cy - 50) < 1e-6           # 10+40/2, 20+60/2


def test_image_display_omits_type_and_counterrotates():
    # figure2 image displays carry NO "type" key (image = default type 0).
    built = [_piece("arm", "arm")]
    ske = build_ske_multipiece("t", BONES, built, {}, {}, [
        {"name": "arm", "x": 0, "y": 0, "width": 40, "height": 60}])
    disp = ske["armature"][0]["skin"][0]["slot"][0]["display"][0]
    assert "type" not in disp                         # image is default
    assert disp["name"] == "arm"
    # owner arm bone rotation -135 → display counter-rotates skX=skY=135 to stay
    # world-axis-aligned at rest.
    assert disp["transform"]["skX"] == 135 and disp["transform"]["skY"] == 135


def test_swap_slot_has_two_displays_and_display_timeline():
    v = {"name": "eye2", "img": np.zeros((8, 8, 4), np.uint8), "offset": (12, 22)}
    built = [_piece("eye", "arm", ptype="swap", variants=[v])]
    piece_tracks = {"idle": [{"piece": "eye", "prop": "display",
                              "keys": [{"t": 0, "v": "eye"}, {"t": 1.0, "v": "eye2"},
                                       {"t": 1.5, "v": "eye"}]}]}
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": []}}
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, [
        {"name": "eye", "x": 0, "y": 0, "width": 8, "height": 8},
        {"name": "eye2", "x": 12, "y": 0, "width": 8, "height": 8}])
    skin_slot = ske["armature"][0]["skin"][0]["slot"][0]
    assert len(skin_slot["display"]) == 2
    # the variant display must be placed with ITS OWN offset/size, not the base's
    assert skin_slot["display"][1]["transform"] == \
        image_display_transform((12, 22), (8, 8), BONES[1])
    assert skin_slot["display"][1]["transform"] != skin_slot["display"][0]["transform"]
    anim = ske["armature"][0]["animation"][0]
    slot_tl = anim["slot"][0]
    frames = slot_tl["displayFrame"]
    assert [f["value"] for f in frames] == [0, 1, 0]
    assert sum(f["duration"] for f in frames) == 60              # 2.0s @ 30fps


def test_zero_bone_animation_omits_bone_key():
    # figure2 'swaponly' animation carries only slot timelines and NO "bone" key.
    built = [_piece("eye", "arm", ptype="swap",
                    variants=[{"name": "eye2", "img": np.zeros((8, 8, 4), np.uint8),
                               "offset": (0, 0)}])]
    piece_tracks = {"idle": [{"piece": "eye", "prop": "display",
                              "keys": [{"t": 0, "v": "eye"}, {"t": 1.0, "v": "eye2"},
                                       {"t": 2.0, "v": "eye"}]}]}
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": []}}
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, [
        {"name": "eye", "x": 0, "y": 0, "width": 8, "height": 8},
        {"name": "eye2", "x": 12, "y": 0, "width": 8, "height": 8}])
    anim = ske["armature"][0]["animation"][0]
    assert "bone" not in anim
    assert anim["slot"] and anim["name"] == "idle"


def test_alpha_colorframe_shape():
    # figure2 body colorFrame: [{duration,tweenEasing,value:{aM}}, ..., {duration:0,value}]
    built = [_piece("body", "root")]
    piece_tracks = {"idle": [{"piece": "body", "prop": "alpha",
                              "keys": [{"t": 0, "v": 1.0},
                                       {"t": 1.0, "v": 0.5, "ease": "linear"},
                                       {"t": 2.0, "v": 1.0}]}]}
    tls = build_slot_timelines("idle", piece_tracks["idle"], built, 30, 60)
    assert tls[0]["colorFrame"] == [
        {"duration": 30, "tweenEasing": 0, "value": {"aM": 100}},
        {"duration": 30, "tweenEasing": 0, "value": {"aM": 50}},
        {"duration": 0, "value": {"aM": 100}},
    ]


def test_alpha_without_loop_closing_key_fails():
    built = [_piece("body", "root")]
    tracks = [{"piece": "body", "prop": "alpha",
               "keys": [{"t": 0, "v": 1.0}, {"t": 1.0, "v": 0.5}]}]   # ends at frame 30, not 60
    with pytest.raises(ValueError):
        build_slot_timelines("idle", tracks, built, 30, 60)


def test_display_key_not_at_zero_fails():
    built = [_piece("eye", "arm", ptype="swap")]
    piece_tracks = {"idle": [{"piece": "eye", "prop": "display",
                              "keys": [{"t": 0.5, "v": "eye"}]}]}
    anims = {"idle": {"duration": 2.0, "tracks": []}}
    with pytest.raises(ValueError):
        build_slot_timelines("idle", piece_tracks["idle"], built, 30, 60)


def test_display_key_past_duration_fails():
    # a key at t=3.0 in a 2.0s (60-frame) animation would make the terminal
    # hold duration negative — must fail loud even when called directly
    # (bypassing pieces.validate_piece_tracks).
    built = [_piece("eye", "arm", ptype="swap",
                    variants=[{"name": "eye2", "img": np.zeros((8, 8, 4), np.uint8),
                               "offset": (0, 0)}])]
    tracks = [{"piece": "eye", "prop": "display",
               "keys": [{"t": 0, "v": "eye"}, {"t": 3.0, "v": "eye2"}]}]
    with pytest.raises(ValueError, match="past the animation duration"):
        build_slot_timelines("idle", tracks, built, 30, 60)


def test_two_rigid_pieces_share_owner_bone():
    # the common real case: two cut-out parts riding the same bone
    built = [_piece("torso", "root"), _piece("head", "root", offset=(50, 0), size=(20, 20))]
    ske = build_ske_multipiece("t", BONES, built, {}, {}, [
        {"name": "torso", "x": 0, "y": 0, "width": 40, "height": 60},
        {"name": "head", "x": 44, "y": 0, "width": 20, "height": 20}])
    arm_doc = ske["armature"][0]
    assert [s["name"] for s in arm_doc["slot"]] == ["torso", "head"]
    assert [s["parent"] for s in arm_doc["slot"]] == ["root", "root"]
    assert len(arm_doc["skin"][0]["slot"]) == 2


def test_build_tex_multi_shape():
    subtex = [{"name": "a", "x": 2, "y": 2, "width": 8, "height": 8}]
    assert build_tex_multi("t", 64, 32, "t_tex.png", subtex) == {
        "width": 64, "height": 32, "name": "t", "imagePath": "t_tex.png",
        "SubTexture": subtex}


def _deform_piece(name, offset=(5, 10)):
    return {"name": name, "type": "deform", "bones": ["arm"], "fill": "extend",
            "offset": offset, "variants": [],
            "mesh_data": {"vertices": [[0, 0], [4, 0], [0, 4]],
                          "uvs": [[0, 0], [1, 0], [0, 1]],
                          "triangles": [[0, 1, 2]]},
            "weights_data": [{"bones": [1], "w": [1.0]},
                             {"bones": [1], "w": [1.0]},
                             {"bones": [1], "w": [1.0]}]}


def test_multipiece_doc_routes_order_tracks():
    # order tracks must produce a top-level animation["zOrder"] timeline and
    # must NOT be handed to build_slot_timelines (which would treat "order"
    # as an unknown/alpha-like prop). An animation with no order tracks gets
    # no "zOrder" key at all.
    built = [_piece("body", "root"), _piece("hat", "root", offset=(50, 0), size=(20, 20))]
    subtex = [{"name": "body", "x": 0, "y": 0, "width": 40, "height": 60},
              {"name": "hat", "x": 44, "y": 0, "width": 20, "height": 20}]
    anims = {"wave": {"duration": 2.0, "loop": True, "tracks": []},
             "idle": {"duration": 1.0, "loop": True, "tracks": []}}
    piece_tracks = {
        "wave": [{"piece": "body", "prop": "order",
                  "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 1}, {"t": 1.5, "v": 0}]}],
    }
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, subtex)
    by_name = {a["name"]: a for a in ske["armature"][0]["animation"]}

    wave = by_name["wave"]
    assert wave["zOrder"] == {"frame": [
        {"duration": 15},
        {"duration": 30, "zOrder": [0, 1]},
        {"duration": 15},
    ]}
    assert "slot" not in wave     # the order track produced NO slot timeline

    idle = by_name["idle"]
    assert "zOrder" not in idle


def test_multipiece_doc_drops_all_zero_order_timeline():
    # an order track that never leaves 0 would emit a zOrder timeline of only
    # empty (restore-base) frames — pure noise; drop the timeline entirely.
    built = [_piece("body", "root"), _piece("hat", "root", offset=(50, 0), size=(20, 20))]
    subtex = [{"name": "body", "x": 0, "y": 0, "width": 40, "height": 60},
              {"name": "hat", "x": 44, "y": 0, "width": 20, "height": 20}]
    anims = {"wave": {"duration": 2.0, "loop": True, "tracks": []}}
    piece_tracks = {
        "wave": [{"piece": "hat", "prop": "order",
                  "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": 0}, {"t": 2.0, "v": 0}]}],
    }
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, subtex)
    assert "zOrder" not in ske["armature"][0]["animation"][0]


def test_order_and_display_tracks_coexist_on_one_piece():
    # a swap piece may carry BOTH a display track (slot timeline) and an order
    # track (zOrder timeline) in the same animation — they route to separate
    # timelines and neither eats the other.
    variant = {"name": "hat_up", "img": np.zeros((20, 20, 4), np.uint8), "offset": (50, 0)}
    built = [_piece("body", "root"),
             _piece("hat", "root", ptype="swap", offset=(50, 0), size=(20, 20),
                    variants=[variant])]
    subtex = [{"name": "body", "x": 0, "y": 0, "width": 40, "height": 60},
              {"name": "hat", "x": 44, "y": 0, "width": 20, "height": 20},
              {"name": "hat_up", "x": 44, "y": 24, "width": 20, "height": 20}]
    anims = {"wave": {"duration": 2.0, "loop": True, "tracks": []}}
    piece_tracks = {"wave": [
        {"piece": "hat", "prop": "display",
         "keys": [{"t": 0.0, "v": "hat"}, {"t": 1.0, "v": "hat_up"}, {"t": 2.0, "v": "hat"}]},
        {"piece": "hat", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -1}, {"t": 1.5, "v": 0}]},
    ]}
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, subtex)
    wave = ske["armature"][0]["animation"][0]
    assert wave["zOrder"] == {"frame": [
        {"duration": 15},
        {"duration": 30, "zOrder": [1, -1]},
        {"duration": 15},
    ]}
    slot_tl = {tl["name"]: tl for tl in wave["slot"]}
    assert "displayFrame" in slot_tl["hat"]          # display survived routing
    assert all("order" not in str(f) for f in slot_tl["hat"]["displayFrame"])


def test_order_track_on_deform_piece_emits_zorder():
    # order tracks are piece-type-agnostic: a deform piece is an ordinary slot,
    # so shifting its depth works exactly like a rigid piece.
    built = [_piece("body", "root"), _deform_piece("cape")]
    subtex = [{"name": "body", "x": 0, "y": 0, "width": 40, "height": 60},
              {"name": "cape", "x": 44, "y": 0, "width": 4, "height": 4}]
    anims = {"flap": {"duration": 2.0, "loop": True, "tracks": []}}
    piece_tracks = {"flap": [
        {"piece": "cape", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -1}, {"t": 1.5, "v": 0}]},
    ]}
    ske = build_ske_multipiece("t", BONES, built, anims, piece_tracks, subtex)
    assert ske["armature"][0]["animation"][0]["zOrder"] == {"frame": [
        {"duration": 15},
        {"duration": 30, "zOrder": [1, -1]},
        {"duration": 15},
    ]}


def test_deform_piece_emits_weighted_mesh_display():
    built = [_deform_piece("cape", offset=(5, 10))]
    ske = build_ske_multipiece("t", BONES, built, {}, {}, [
        {"name": "cape", "x": 0, "y": 0, "width": 4, "height": 4}])
    arm_doc = ske["armature"][0]
    # deform slots parent to root (weighted mesh vertices are determined by weights)
    assert arm_doc["slot"][0]["parent"] == "root"
    disp = arm_doc["skin"][0]["slot"][0]["display"][0]
    assert disp["type"] == "mesh" and disp["name"] == "cape"
    # world vertices = piece-local + offset (5, 10)
    assert disp["vertices"] == [5, 10, 9, 10, 5, 14]
    # region-relative uvs verbatim
    assert disp["uvs"] == [0, 0, 1, 0, 0, 1]
    assert disp["triangles"] == [0, 1, 2]
    assert disp["slotPose"] == [1, 0, 0, 1, 0, 0]
    # packed weights use GLOBAL bone indices; bonePose covers every referenced bone
    assert disp["weights"] == [1, 1, 1.0, 1, 1, 1.0, 1, 1, 1.0]
    assert len(disp["bonePose"]) % 7 == 0 and disp["bonePose"][0] == 1
    assert disp["width"] == 4 and disp["height"] == 4
