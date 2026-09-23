import json, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pieces import load_pieces, split_tracks, validate_piece_tracks

BONES = [
    {"name": "root", "parent": None, "x": 50, "y": 90, "rotation": -90, "length": 0, "skin": False},
    {"name": "arm", "parent": "root", "x": 30, "y": 40, "rotation": -90, "length": 30},
]


def write_manifest(tmp_path, pieces, version=1):
    p = tmp_path / "pieces.json"
    p.write_text(json.dumps({"version": version, "pieces": pieces}))
    return p


def test_valid_rigid_polygon_piece(tmp_path):
    m = write_manifest(tmp_path, [{"name": "body", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [100, 0], [50, 100]]}}])
    out = load_pieces(m, BONES, (100, 100))
    assert out[0]["fill"] == "extend"          # default applied


def test_unknown_bone_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "nope", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_polygon_out_of_bounds_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [200, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_deform_rejected_when_not_allowed(tmp_path):
    m = write_manifest(tmp_path, [{"name": "c", "type": "deform",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100), deform_allowed=False)


def test_variant_file_must_exist(tmp_path):
    m = write_manifest(tmp_path, [{"name": "eye", "bone": "arm", "type": "swap",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
                                   "variants": [{"name": "eye2", "file": "missing.png", "offset": [1, 2]}]}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_split_and_validate_piece_tracks():
    pieces = [{"name": "eye", "type": "swap", "variants": [{"name": "eye2"}]}]
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": [
        {"bone": "arm", "prop": "rotation", "keys": [{"t": 0, "v": 0}, {"t": 2.0, "v": 0}]},
        {"piece": "eye", "prop": "display", "keys": [{"t": 0, "v": "eye"}, {"t": 1.0, "v": "eye2"}, {"t": 1.2, "v": "eye"}]},
    ]}}
    bone_anims, piece_tracks = split_tracks(anims)
    assert len(bone_anims["idle"]["tracks"]) == 1
    assert len(piece_tracks["idle"]) == 1
    validate_piece_tracks(anims, pieces)       # should not raise


def test_display_track_unknown_variant_fails():
    pieces = [{"name": "eye", "type": "swap", "variants": []}]
    anims = {"idle": {"duration": 2.0, "tracks": [
        {"piece": "eye", "prop": "display", "keys": [{"t": 0, "v": "nope"}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_duplicate_piece_names_fail(tmp_path):
    m = write_manifest(tmp_path, [
        {"name": "b", "bone": "root", "type": "rigid",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}},
        {"name": "b", "bone": "arm", "type": "rigid",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}},
    ])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_variants_on_non_swap_piece_fail(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
                                   "variants": [{"name": "b2", "file": "missing.png", "offset": [0, 0]}]}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_bad_fill_value_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
                                   "fill": "sparkle"}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_version_mismatch_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}], version=2)
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_variant_name_collides_with_piece_name(tmp_path):
    # piece B's variant named like piece A -> atlas SubTexture name collision.
    # v.png exists so the test pins the collision check, not file-not-found.
    (tmp_path / "v.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [
        {"name": "body", "bone": "root", "type": "rigid",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}},
        {"name": "eye", "bone": "arm", "type": "swap",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
         "variants": [{"name": "body", "file": "v.png", "offset": [0, 0]}]},
    ])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_variant_name_collides_across_pieces(tmp_path):
    (tmp_path / "v.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [
        {"name": "eyeL", "bone": "arm", "type": "swap",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
         "variants": [{"name": "blink", "file": "v.png", "offset": [0, 0]}]},
        {"name": "eyeR", "bone": "arm", "type": "swap",
         "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
         "variants": [{"name": "blink", "file": "v.png", "offset": [0, 0]}]},
    ])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_variant_name_not_string_fails(tmp_path):
    (tmp_path / "v.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [{"name": "eye", "bone": "arm", "type": "swap",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
                                   "variants": [{"name": 5, "file": "v.png", "offset": [0, 0]}]}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_file_source_happy_path(tmp_path):
    (tmp_path / "part.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [{"name": "hat", "bone": "arm", "type": "rigid",
                                   "source": {"file": "part.png", "offset": [3, 4]}}])
    out = load_pieces(m, BONES, (100, 100))
    assert out[0]["name"] == "hat"
    assert out[0]["fill"] == "extend"
    assert out[0]["base_dir"] == str(tmp_path)


def test_negative_source_offset_fails(tmp_path):
    (tmp_path / "part.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [{"name": "hat", "bone": "arm", "type": "rigid",
                                   "source": {"file": "part.png", "offset": [-100, 50]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_negative_variant_offset_fails(tmp_path):
    (tmp_path / "v.png").write_bytes(b"png")
    m = write_manifest(tmp_path, [{"name": "eye", "bone": "arm", "type": "swap",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]},
                                   "variants": [{"name": "eye2", "file": "v.png", "offset": [0, -1]}]}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_deform_unknown_weight_bone_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "cape", "type": "deform", "bones": ["nope"],
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_deform_mesh_knob_typo_fails(tmp_path):
    # "colss" must fail loud, not silently fall back to the default grid
    m = write_manifest(tmp_path, [{"name": "cape", "type": "deform",
                                   "mesh": {"colss": 8},
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_deform_mesh_non_dict_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "cape", "type": "deform",
                                   "mesh": "oops",
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_deform_mesh_bad_values_fail(tmp_path):
    for mesh in ({"cols": -4}, {"cols": 0}, {"cols": 4.5}, {"cols": True},
                 {"power": "high"}, {"power": 0}):
        m = write_manifest(tmp_path, [{"name": "cape", "type": "deform",
                                       "mesh": mesh,
                                       "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
        with pytest.raises(SystemExit):
            load_pieces(m, BONES, (100, 100))


def test_deform_mesh_valid_knobs_pass(tmp_path):
    m = write_manifest(tmp_path, [{"name": "cape", "type": "deform", "bones": ["arm"],
                                   "mesh": {"cols": 8, "power": 3.0},
                                   "source": {"polygon": [[0, 0], [10, 0], [5, 10]]}}])
    out = load_pieces(m, BONES, (100, 100))
    assert out[0]["mesh"] == {"cols": 8, "power": 3.0}


def test_non_finite_number_in_manifest_fails(tmp_path):
    m = write_manifest(tmp_path, [{"name": "b", "bone": "root", "type": "rigid",
                                   "source": {"polygon": [[float("nan"), 0], [10, 0], [5, 10]]}}])
    with pytest.raises(SystemExit):
        load_pieces(m, BONES, (100, 100))


def test_loop_pop_detected():
    pieces = [{"name": "eye", "type": "swap", "variants": [{"name": "eye2"}]}]
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": [
        {"piece": "eye", "prop": "display",
         "keys": [{"t": 0, "v": "eye"}, {"t": 1.0, "v": "eye2"}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_display_track_needs_key_at_zero():
    # the DragonBones writer requires a key at t=0 (frame durations sum from 0);
    # a display track starting at t>0 is rejected in validation, not the writer.
    pieces = [{"name": "eye", "type": "swap", "variants": [{"name": "eye2"}]}]
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": [
        {"piece": "eye", "prop": "display",
         "keys": [{"t": 0.5, "v": "eye2"}, {"t": 1.0, "v": "eye"}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_alpha_track_needs_key_at_zero():
    pieces = [{"name": "eye", "type": "swap", "variants": []}]
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": [
        {"piece": "eye", "prop": "alpha",
         "keys": [{"t": 0.5, "v": 0.5}, {"t": 2.0, "v": 1.0}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_alpha_track_needs_loop_closing_key_at_duration():
    # alpha → colorFrame terminal frame must land at t=duration for a clean loop.
    pieces = [{"name": "eye", "type": "swap", "variants": []}]
    anims = {"idle": {"duration": 2.0, "loop": True, "tracks": [
        {"piece": "eye", "prop": "alpha",
         "keys": [{"t": 0, "v": 1.0}, {"t": 1.0, "v": 0.5}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_alpha_out_of_range_fails():
    pieces = [{"name": "eye", "type": "swap", "variants": []}]
    anims = {"idle": {"duration": 2.0, "tracks": [
        {"piece": "eye", "prop": "alpha",
         "keys": [{"t": 0, "v": 0.5}, {"t": 1.0, "v": 1.5}, {"t": 2.0, "v": 0.5}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_display_track_on_non_swap_piece_fails():
    pieces = [{"name": "body", "type": "rigid"}]
    anims = {"idle": {"duration": 2.0, "tracks": [
        {"piece": "body", "prop": "display",
         "keys": [{"t": 0, "v": "body"}, {"t": 1.0, "v": "body"}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


def test_split_tracks_both_bone_and_piece_fails():
    anims = {"idle": {"duration": 2.0, "tracks": [
        {"bone": "arm", "piece": "eye", "prop": "rotation",
         "keys": [{"t": 0, "v": 0}]}]}}
    with pytest.raises(SystemExit):
        split_tracks(anims)


def test_split_tracks_neither_bone_nor_piece_fails():
    anims = {"idle": {"duration": 2.0, "tracks": [
        {"prop": "rotation", "keys": [{"t": 0, "v": 0}]}]}}
    with pytest.raises(SystemExit):
        split_tracks(anims)


def test_ease_on_display_key_fails():
    pieces = [{"name": "eye", "type": "swap", "variants": [{"name": "eye2"}]}]
    anims = {"idle": {"duration": 2.0, "loop": False, "tracks": [
        {"piece": "eye", "prop": "display",
         "keys": [{"t": 0, "v": "eye", "ease": "sineInOut"}, {"t": 1.0, "v": "eye2"}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, pieces)


ORDER_PIECES = [
    {"name": "body", "type": "rigid"},
    {"name": "arm", "type": "rigid"},
    {"name": "eye", "type": "rigid"},
]


def test_valid_order_track_passes():
    anims = {"idle": {"duration": 1.5, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": 1}, {"t": 1.5, "v": 0}]}]}}
    validate_piece_tracks(anims, ORDER_PIECES)     # should not raise


def test_order_non_integer_value_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": 1.5}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "must be an integer draw-order shift" in capsys.readouterr().err


def test_order_bool_value_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": True}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "must be an integer draw-order shift" in capsys.readouterr().err


def test_order_ease_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0, "ease": "sineInOut"}, {"t": 1.0, "v": 0}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "order keys are stepped" in capsys.readouterr().err


def test_order_negative_shift_passes():
    anims = {"idle": {"duration": 1.5, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -2}, {"t": 1.5, "v": 0}]}]}}
    validate_piece_tracks(anims, ORDER_PIECES)     # should not raise


def test_order_boundary_shift_both_signs_pass():
    # 3 pieces -> |shift| up to len(pieces)-1 == 2 is valid, either sign.
    for v in (2, -2):
        anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
            {"piece": "arm", "prop": "order",
             "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": v}, {"t": 1.0, "v": 0}]}]}}
        validate_piece_tracks(anims, ORDER_PIECES)     # should not raise


def test_order_negative_out_of_range_fails(capsys):
    # 3 pieces -> v == -3 has |shift| == len(pieces), rejected.
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 0.5, "v": -3}, {"t": 1.0, "v": 0}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "out of range" in capsys.readouterr().err


def test_order_out_of_range_fails(capsys):
    # 3 pieces -> |shift| must be < 3
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": 3}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "out of range" in capsys.readouterr().err


def test_order_loop_pop_at_wrap_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}, {"t": 1.0, "v": 1}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "depth pop at wrap" in capsys.readouterr().err


def test_order_unknown_piece_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "nope", "prop": "order",
         "keys": [{"t": 0.0, "v": 0}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "unknown piece" in capsys.readouterr().err


def test_order_duplicate_track_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order", "keys": [{"t": 0.0, "v": 0}]},
        {"piece": "arm", "prop": "order", "keys": [{"t": 0.0, "v": 1}]},
    ]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "duplicate track" in capsys.readouterr().err


def test_order_missing_key_at_zero_fails(capsys):
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"piece": "arm", "prop": "order",
         "keys": [{"t": 0.5, "v": 1}]}]}}
    with pytest.raises(SystemExit):
        validate_piece_tracks(anims, ORDER_PIECES)
    assert "needs a key at t=0" in capsys.readouterr().err
