"""wave_compiler.expand_wave_tracks (path v2, contract §17).

The math cases use a STRAIGHT VERTICAL path (all tangents -90°, u spacing
uniform) so expected offsets are hand-computable: the curve normal is world
(+1, 0) — sin(-90°) = -1 — and with an unrotated owner the world offset IS
the parent-local offset, x tracks carry the sine, y tracks are zero.
"""
import math

import pytest

import wave_compiler
from assemble import validate_animations
from wave_compiler import expand_wave_tracks

BONES = [
    {"name": "root", "parent": None, "x": 64, "y": 100, "rotation": 0, "length": 20},
    {"name": "c1", "parent": "root", "x": 64, "y": 80, "rotation": -90, "length": 20},
    {"name": "c2", "parent": "c1", "x": 64, "y": 60, "rotation": -90, "length": 20},
]
PATHS = [{"name": "flow", "bone": "root", "weighted": True,
          "points": [[64, 100], [64, 60], [64, 20]], "chain": ["c1", "c2"]}]
FPS = 8  # keys at i/8 s — math cases sample nice grid times like 0.25


def wave_anim(**over):
    tr = {"path": "flow", "prop": "wave", "amplitude": 10, "period": 1.0}
    tr.update(over)
    return {"w": {"duration": 2.0, "loop": True, "tracks": [tr]}}


def test_pass_through_without_wave_tracks():
    anims = {"idle": {"duration": 1.0, "loop": True, "tracks": [
        {"bone": "root", "prop": "rotation",
         "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]}]}}
    assert expand_wave_tracks(anims, PATHS, BONES, fps=FPS) == anims
    assert expand_wave_tracks(anims, [], BONES, fps=FPS)["idle"] is anims["idle"]


def test_expansion_shape_and_grid():
    out = expand_wave_tracks(wave_anim(), PATHS, BONES, fps=FPS)
    tracks = out["w"]["tracks"]
    # 3 anchors x (x + y) tracks, dense per-frame keys 0..duration inclusive
    assert [(t["bone"], t["prop"]) for t in tracks] == [
        ("flow_a0", "x"), ("flow_a0", "y"),
        ("flow_a1", "x"), ("flow_a1", "y"),
        ("flow_a2", "x"), ("flow_a2", "y")]
    for t in tracks:
        assert len(t["keys"]) == 2 * FPS + 1
        assert t["keys"][0]["t"] == 0 and t["keys"][-1]["t"] == 2.0
    # expanded output passes the ordinary animation validator (with paths)
    validate_animations(out, BONES, paths=PATHS)


def test_expansion_math_straight_vertical_path():
    out = expand_wave_tracks(wave_anim(), PATHS, BONES, fps=FPS)
    by = {(t["bone"], t["prop"]): t["keys"] for t in out["w"]["tracks"]}
    # tangent -90° -> world normal (-sin, cos) = (1, 0); owner rotation 0 ->
    # all offset lives in x, y is exactly zero everywhere
    for j in range(3):
        assert all(abs(k["v"]) < 1e-9 for k in by[(f"flow_a{j}", "y")])
    # root anchor: linear envelope gain = u0 = 0 -> pinned
    assert all(k["v"] == 0 for k in by[("flow_a0", "x")])
    # tip anchor (u=1): offset(t) = 10 * sin(2pi*(t/1 - 1/1)) = 10*sin(2pi*t)
    for k in by[("flow_a2", "x")]:
        assert k["v"] == pytest.approx(10 * math.sin(2 * math.pi * k["t"]), abs=1e-9)
    # middle anchor (u=0.5, straight line): gain 5, phase shifted by half a turn
    # (u comes from sampled arc lengths — exact only to ~1e-9 on a straight line)
    for k in by[("flow_a1", "x")]:
        assert k["v"] == pytest.approx(
            5 * math.sin(2 * math.pi * (k["t"] - 0.5)), abs=1e-6)


def test_direction_flips_phase_sign():
    fwd = expand_wave_tracks(wave_anim(), PATHS, BONES, fps=FPS)
    rev = expand_wave_tracks(wave_anim(direction="tip-to-root"), PATHS, BONES, fps=FPS)
    get = lambda o, b: {(t["bone"], t["prop"]): t["keys"] for t in o["w"]["tracks"]}[(b, "x")]
    # u=0.5 anchor: sin(2pi(t - 0.5)) vs sin(2pi(t + 0.5)) — here equal magnitude,
    # so compare at u=1 via loop phase: rev tip = 10*sin(2pi*(t+1)) = fwd tip
    # -> distinguish at the middle anchor quarter-period sample instead
    t_q = 0.25
    f = next(k["v"] for k in get(fwd, "flow_a1") if k["t"] == t_q)
    r = next(k["v"] for k in get(rev, "flow_a1") if k["t"] == t_q)
    assert f == pytest.approx(5 * math.sin(2 * math.pi * (t_q - 0.5)), abs=1e-6)
    assert r == pytest.approx(5 * math.sin(2 * math.pi * (t_q + 0.5)), abs=1e-6)


def test_axis_tangent_offsets_along_curve():
    out = expand_wave_tracks(wave_anim(axis="tangent"), PATHS, BONES, fps=FPS)
    by = {(t["bone"], t["prop"]): t["keys"] for t in out["w"]["tracks"]}
    # tangent -90° -> world dir (0, -1): x ~zero (cos(-pi/2) dust), y = -offset
    assert all(abs(k["v"]) < 1e-9 for k in by[("flow_a2", "x")])
    for k in by[("flow_a2", "y")]:
        assert k["v"] == pytest.approx(-10 * math.sin(2 * math.pi * k["t"]), abs=1e-9)


def test_owner_rotation_maps_world_to_parent_local():
    # owner rotated -90°: world (1, 0) in owner-local = rotate by +90 ->
    # dx = cos(-90)*1 + sin(-90)*0 = 0; dy = -sin(-90)*1 + cos(-90)*0 = 1
    bones = [dict(BONES[0], rotation=-90)] + BONES[1:]
    out = expand_wave_tracks(wave_anim(), PATHS, bones, fps=FPS)
    by = {(t["bone"], t["prop"]): t["keys"] for t in out["w"]["tracks"]}
    assert all(abs(k["v"]) < 1e-9 for k in by[("flow_a2", "x")])
    for k in by[("flow_a2", "y")]:
        assert k["v"] == pytest.approx(10 * math.sin(2 * math.pi * k["t"]), abs=1e-9)


def test_envelope_flat_and_custom():
    flat = expand_wave_tracks(wave_anim(envelope="flat"), PATHS, BONES, fps=FPS)
    by = {(t["bone"], t["prop"]): t["keys"] for t in flat["w"]["tracks"]}
    # flat: the root anchor waves at full amplitude too
    assert any(abs(k["v"]) > 9.9 for k in by[("flow_a0", "x")])
    # custom: clamp below first breakpoint, interpolate between
    custom = expand_wave_tracks(
        wave_anim(envelope=[[0.5, 0.0], [1.0, 2.0]]), PATHS, BONES, fps=FPS)
    byc = {(t["bone"], t["prop"]): t["keys"] for t in custom["w"]["tracks"]}
    assert all(k["v"] == 0 for k in byc[("flow_a0", "x")])   # u=0 clamps to gain 0
    assert all(abs(k["v"]) < 1e-6 for k in byc[("flow_a1", "x")])  # u~0.5 -> gain ~0
    assert any(abs(k["v"]) > 19 for k in byc[("flow_a2", "x")])  # u=1 -> gain 2


def test_loop_closure_first_equals_last():
    out = expand_wave_tracks(wave_anim(phase=0.25, wavelength=0.5), PATHS, BONES, fps=FPS)
    for t in out["w"]["tracks"]:
        assert t["keys"][0]["v"] == pytest.approx(t["keys"][-1]["v"], abs=1e-6)


@pytest.mark.parametrize("mutate,msg", [
    (dict(path="nope"), "no such path"),
    (dict(amplitude=0), "amplitude"),
    (dict(amplitude=None), "amplitude"),
    (dict(period=-1), "period"),
    (dict(wavelength=0), "wavelength"),
    (dict(phase=1.0), "phase"),
    (dict(direction="sideways"), "direction"),
    (dict(axis="diagonal"), "axis"),
    (dict(envelope="bumpy"), "envelope"),
    (dict(envelope=[[0.9, 1], [0.1, 1]]), "strictly increasing"),
    (dict(envelope=[[0, 1], [1.5, 1]]), "lie in [0, 1]"),
    (dict(swing=3), "unknown keys"),
])
def test_wave_guards(mutate, msg, capsys):
    with pytest.raises(SystemExit):
        expand_wave_tracks(wave_anim(**mutate), PATHS, BONES, fps=FPS)
    assert msg in capsys.readouterr().err


def test_wave_requires_weighted_path(capsys):
    paths = [dict(PATHS[0], weighted=False)]
    with pytest.raises(SystemExit):
        expand_wave_tracks(wave_anim(), paths, BONES, fps=FPS)
    assert 'weighted' in capsys.readouterr().err


def test_wave_missing_required_knob(capsys):
    anims = {"w": {"duration": 2.0, "loop": True, "tracks": [
        {"path": "flow", "prop": "wave", "amplitude": 10}]}}
    with pytest.raises(SystemExit):
        expand_wave_tracks(anims, PATHS, BONES, fps=FPS)
    assert "period" in capsys.readouterr().err


def test_loop_guard_suggests_nearest_periods(capsys):
    with pytest.raises(SystemExit):
        expand_wave_tracks(wave_anim(period=0.7), PATHS, BONES, fps=FPS)
    err = capsys.readouterr().err
    assert "whole number of wave cycles" in err and "0.666667s" in err


def test_non_loop_animation_skips_cycle_guard():
    anims = wave_anim(period=0.7)
    anims["w"]["loop"] = False
    out = expand_wave_tracks(anims, PATHS, BONES, fps=FPS)  # no raise
    assert len(out["w"]["tracks"]) == 6


def test_duration_off_frame_grid_rejected(capsys):
    anims = wave_anim()
    anims["w"]["duration"] = 1.01
    with pytest.raises(SystemExit):
        expand_wave_tracks(anims, PATHS, BONES, fps=FPS)
    assert "frame grid" in capsys.readouterr().err


def test_path_key_with_wrong_prop_rejected(capsys):
    anims = {"w": {"duration": 2.0, "loop": True, "tracks": [
        {"path": "flow", "prop": "rotation", "keys": []}]}}
    with pytest.raises(SystemExit):
        expand_wave_tracks(anims, PATHS, BONES, fps=FPS)
    assert 'prop' in capsys.readouterr().err


def test_wave_collides_with_hand_keys_on_anchor(capsys):
    anims = wave_anim()
    anims["w"]["tracks"].append(
        {"bone": "flow_a1", "prop": "x",
         "keys": [{"t": 0, "v": 0}, {"t": 2.0, "v": 0}]})
    out = expand_wave_tracks(anims, PATHS, BONES, fps=FPS)
    with pytest.raises(SystemExit):
        validate_animations(out, BONES, paths=PATHS)
    assert "duplicate track" in capsys.readouterr().err


def test_validator_rejects_anchor_tracks_without_weighted_path(capsys):
    anims = {"w": {"duration": 1.0, "loop": True, "tracks": [
        {"bone": "flow_a0", "prop": "x",
         "keys": [{"t": 0, "v": 0}, {"t": 1.0, "v": 0}]}]}}
    paths = [dict(PATHS[0], weighted=False)]
    with pytest.raises(SystemExit):
        validate_animations(anims, BONES, paths=paths)
    assert "unknown bone" in capsys.readouterr().err
