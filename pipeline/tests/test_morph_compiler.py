import numpy as np
import pytest
import flow_gen
import mesh_gen
import morph_compiler
import pieces as pieces_mod


def _sq(shift):
    img = np.zeros((64, 64, 4), np.uint8)
    img[22:42, 10 + shift:30 + shift, :3] = 200
    img[22:42, 10 + shift:30 + shift, 3] = 255
    return img


def _piece(loop="pingpong"):
    return {"name": "body", "type": "morph", "loop": loop,
            "mesh": {"cols": 6},
            "frames": [
                {"file": "a.png", "t": 0.0, "key": True},
                {"file": "g.png", "t": 0.1, "key": False},
                {"file": "b.png", "t": 0.25, "key": True}],
            "frame_images": [_sq(0), _sq(4), _sq(8)]}


ANIMS = {"win": {"duration": 0.5, "loop": True, "tracks": []}}


def _eval(keys, t):
    ks = sorted(keys, key=lambda k: k["t"])
    if t <= ks[0]["t"]:
        return ks[0]["v"]
    for a, b in zip(ks, ks[1:]):
        if a["t"] <= t <= b["t"]:
            if b["t"] == a["t"]:
                return b["v"]
            f = (t - a["t"]) / (b["t"] - a["t"])
            return a["v"] + (b["v"] - a["v"]) * f
    return ks[-1]["v"]


def _eval_step(keys, t):
    """Step (hold-previous) evaluation of a stepped display track."""
    ks = sorted(keys, key=lambda k: k["t"])
    v = ks[0]["v"]
    for k in ks:
        if k["t"] <= t + 1e-9:
            v = k["v"]
    return v


def _displays(fp):
    """(names, meshes) per display of the single morphframe piece."""
    assert len(fp) == 1
    piece = fp[0]
    names = [piece["name"]] + [v["name"] for v in piece["variants"]]
    meshes = [piece["mesh_data"]] + [v["mesh_data"] for v in piece["variants"]]
    return names, meshes


# ---- given-in-spec tests -------------------------------------------------

def test_expand_counts():
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(_piece(), ANIMS)
    assert len(fp) == 1                       # ONE morphframe piece, K displays
    assert fp[0]["type"] == "morphframe"
    assert "weights_data" not in fp[0]
    names, _ = _displays(fp)
    assert names == ["body", "body__k1"]      # display 0 = piece name
    assert len(ffd) == 2                      # one ffd timeline per display
    assert [tl["display"] for tl in ffd] == ["body", "body__k1"]
    assert all(tl["piece"] == "body" for tl in ffd)   # all share the slot
    assert len(tracks) == 1                   # ONE stepped display track
    assert tracks[0]["prop"] == "display"


def test_frame_piece_shape():
    fp, _, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    p = fp[0]
    assert p["offset"] == (0, 0)
    assert len(p["variants"]) == 1            # K=2 → display 0 + one variant
    assert p["img"].shape == (64, 64, 4)
    assert "vertices" in p["mesh_data"]
    v = p["variants"][0]
    assert v["name"] == "body__k1" and v["offset"] == (0, 0)
    assert v["img"].shape == (64, 64, 4)
    assert "vertices" in v["mesh_data"]


def test_ffd_zero_at_own_pole():
    fp, ffd, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    keys0 = {round(k["t"], 3): k for k in ffd[0]["keys"]}
    assert np.allclose(keys0[0.0]["offsets"], 0)


def test_ffd_v_matches_own_mesh():
    fp, ffd, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    _, meshes = _displays(fp)
    for mesh, tl in zip(meshes, ffd):
        v = len(mesh["vertices"])
        for k in tl["keys"]:
            assert np.asarray(k["offsets"]).shape == (v, 2)


def test_decomposition_bone_carries_global_mesh_residual_small():
    """The square translates as a rigid body: motion decomposition puts the
    segment's ~half-shift on the BONE and leaves the mesh residual near zero
    (the whole point — the interior rides the global instead of warping)."""
    fp, ffd, _, bt = morph_compiler.expand_morph(_piece(), ANIMS)
    assert bt is not None, "a whole-body translation must produce a bone track"
    kd = {round(t, 3): (gx, gy) for t, gx, gy in bt["keys"]}
    gx_cut = kd[0.125][0]                          # image +x global at the cut
    assert gx_cut > 1.5, "the bone must carry the rigid half-translation (+x)"
    # the mesh residual at the cut is now SMALL — the global left the mesh
    keys0 = {round(k["t"], 3): k for k in ffd[0]["keys"]}
    res_mean = np.abs(np.asarray(keys0[0.125]["offsets"])[:, 0]).mean()
    assert res_mean < gx_cut, "residual must be smaller than the bone's global"
    assert res_mean < 1.5, "residual should be near zero for a rigid translation"


def test_exactly_one_display_active_every_instant():
    # A stepped display track is a step function: at ANY instant exactly one of
    # the K displays is shown — zero overlap by construction (the whole point of
    # replacing the alpha ramps). We assert the shown value is always a real
    # display name across the loop.
    fp, _, tracks, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    names, _ = _displays(fp)
    dt = tracks[0]
    for t in np.linspace(0, 0.5, 101):
        assert _eval_step(dt["keys"], t) in names, f"no display at t={t}"


def test_two_animations_fail_loud():
    with pytest.raises(SystemExit):
        morph_compiler.expand_morph(
            _piece(), {"a": ANIMS["win"], "b": ANIMS["win"]})


# ---- extended tests ------------------------------------------------------

def test_ffd_times_strictly_increasing():
    _, ffd, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    for tl in ffd:
        ts = [k["t"] for k in tl["keys"]]
        assert all(b > a for a, b in zip(ts, ts[1:])), ts
        assert ts[0] == 0.0
        assert abs(ts[-1] - 0.5) < 1e-9


def test_ffd_loop_closes():
    # every piece: first FFD value == last FFD value (seamless loop)
    _, ffd, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    for tl in ffd:
        assert np.allclose(tl["keys"][0]["offsets"], tl["keys"][-1]["offsets"])


def test_ffd_pingpong_symmetry():
    # piece 0 (bottom): its toward-next value at cut_f(0) equals the mirrored
    # value at cut_b(0) = duration - cut_f(0).
    _, ffd, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    keys0 = {round(k["t"], 6): k for k in ffd[0]["keys"]}
    cut_f0 = 0.125
    cut_b0 = 0.5 - cut_f0
    assert np.allclose(keys0[round(cut_f0, 6)]["offsets"],
                       keys0[round(cut_b0, 6)]["offsets"])


def test_display_track_passes_validate():
    fp, _, tracks, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    anims = {"win": {"duration": 0.5, "loop": True, "tracks": list(tracks)}}
    # validate_piece_tracks reads the morphframe piece's name/type/variants.
    pieces_mod.validate_piece_tracks(anims, fp)  # must not sys.exit


def test_display_track_shape():
    fp, _, tracks, _ = morph_compiler.expand_morph(_piece(), ANIMS)
    names, _ = _displays(fp)
    assert len(tracks) == 1
    dt = tracks[0]
    assert dt["prop"] == "display" and dt["piece"] == "body"
    ts = [k["t"] for k in dt["keys"]]
    assert ts[0] == 0.0
    assert all(b > a for a, b in zip(ts, ts[1:]))     # strictly increasing
    assert dt["keys"][0]["v"] == "body"               # display 0 at t=0
    assert dt["keys"][-1]["v"] == dt["keys"][0]["v"]  # pingpong closes on display 0
    assert all(k["v"] in names for k in dt["keys"])
    assert not any("ease" in k for k in dt["keys"])   # stepped, no ease


# ---- forward loop --------------------------------------------------------

FWD_ANIMS = {"win": {"duration": 0.3, "loop": True, "tracks": []}}


def test_forward_no_backward_keys_and_covers():
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(_piece("forward"), FWD_ANIMS)
    names, _ = _displays(fp)
    # no backward pass: piece 0's FFD has no mirrored re-pose — fewer keys.
    assert len(ffd[0]["keys"]) < 8
    dt = tracks[0]
    # one display shown at every instant across the forward timeline
    for t in np.linspace(0, 0.3, 121):
        assert _eval_step(dt["keys"], t) in names, f"no display at t={t}"
    # forward closes with a hard-wrap key back to display 0 at t=duration
    assert dt["keys"][-1]["t"] == 0.3
    assert dt["keys"][-1]["v"] == "body"


def test_forward_display_passes_validate():
    fp, _, tracks, _ = morph_compiler.expand_morph(_piece("forward"), FWD_ANIMS)
    anims = {"win": {"duration": 0.3, "loop": True, "tracks": list(tracks)}}
    pieces_mod.validate_piece_tracks(anims, fp)


def test_forward_ffd_loop_closes():
    _, ffd, _, _ = morph_compiler.expand_morph(_piece("forward"), FWD_ANIMS)
    for tl in ffd:
        assert np.allclose(tl["keys"][0]["offsets"], tl["keys"][-1]["offsets"])


# ---- loud failures -------------------------------------------------------

def test_pingpong_duration_mismatch_fails():
    with pytest.raises(SystemExit):
        morph_compiler.expand_morph(
            _piece(), {"win": {"duration": 0.4, "loop": True, "tracks": []}})


def test_forward_duration_too_short_fails():
    with pytest.raises(SystemExit):
        morph_compiler.expand_morph(
            _piece("forward"), {"win": {"duration": 0.2, "loop": True, "tracks": []}})


# ---- K=3: exercises the genuine middle-piece branch ---------------------

def _piece3(loop="pingpong"):
    return {"name": "body", "type": "morph", "loop": loop, "mesh": {"cols": 6},
            "frames": [
                {"file": "a.png", "t": 0.0, "key": True},
                {"file": "b.png", "t": 0.25, "key": True},
                {"file": "c.png", "t": 0.5, "key": True}],
            "frame_images": [_sq(0), _sq(6), _sq(12)]}


ANIMS3 = {"win": {"duration": 1.0, "loop": True, "tracks": []}}   # 2 * 0.5


def test_k3_middle_piece_pingpong():
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(_piece3(), ANIMS3)
    names, _ = _displays(fp)
    assert names == ["body", "body__k1", "body__k2"]
    # middle display 1: strictly increasing FFD times, loop closes, zero at pole
    mid = ffd[1]
    assert mid["display"] == "body__k1"
    ts = [k["t"] for k in mid["keys"]]
    assert all(b > a for a, b in zip(ts, ts[1:])), ts
    assert np.allclose(mid["keys"][0]["offsets"], mid["keys"][-1]["offsets"])
    pole = {round(k["t"], 3): k for k in mid["keys"]}[0.25]  # T(1)
    assert np.allclose(pole["offsets"], 0)
    # ping-pong symmetry on the middle display: cut_f(1)=0.375 mirrors cut_b(1)=0.625
    km = {round(k["t"], 6): k for k in mid["keys"]}
    assert np.allclose(km[0.375]["offsets"], km[0.625]["offsets"])
    # one display shown at every instant
    dt = tracks[0]
    for t in np.linspace(0, 1.0, 201):
        assert _eval_step(dt["keys"], t) in names, t


def test_k3_display_validates_both_loops():
    for loop, dur in (("pingpong", 1.0), ("forward", 0.6)):
        fp, ffd, tracks, _bt = morph_compiler.expand_morph(
            _piece3(loop), {"win": {"duration": dur, "loop": True, "tracks": []}})
        anims = {"win": {"duration": dur, "loop": True, "tracks": list(tracks)}}
        pieces_mod.validate_piece_tracks(anims, fp)
        for tl in ffd:
            ts = [k["t"] for k in tl["keys"]]
            assert all(b > a for a, b in zip(ts, ts[1:])), (loop, ts)
            assert np.allclose(tl["keys"][0]["offsets"], tl["keys"][-1]["offsets"])
        names, _ = _displays(fp)
        for t in np.linspace(0, dur, 201):
            assert _eval_step(tracks[0]["keys"], t) in names, (loop, t)


# ---- shape at fade-in (the highest-risk invariant) -----------------------

def _eval_ffd(keys, t):
    """Linear interpolation between FFD keys, like the runtime would."""
    if t <= keys[0]["t"]:
        return np.asarray(keys[0]["offsets"], float)
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            f = (t - a["t"]) / (b["t"] - a["t"])
            oa = np.asarray(a["offsets"], float)
            ob = np.asarray(b["offsets"], float)
            return oa + (ob - oa) * f
    return np.asarray(keys[-1]["offsets"], float)


def _recompute_residual(chain, va, vb, tri_a, tri_b):
    """Recompute a segment's decomposed toward-next / toward-prev RESIDUALS
    exactly as the compiler does (smooth w/ canvas scaling, gate, subtract the
    median global). Returns (residual_a, residual_b)."""
    seg = flow_gen.segment_offsets(chain, va, vb)
    W = int(chain[0].shape[1])
    mt = W / 240.0
    ga = flow_gen.content_gate(chain[0], chain[-1], va)
    gb = flow_gen.content_gate(chain[0], chain[-1], vb)
    sm_a = flow_gen.smooth_offsets(seg["half_a"], tri_a, canvas_w=W)
    sm_b = flow_gen.smooth_offsets(seg["half_b"], tri_b, canvas_w=W)
    g_a = flow_gen.dominant_translation(
        sm_a, (ga > 0.5) & (np.linalg.norm(sm_a, axis=1) > mt))
    g_b = flow_gen.dominant_translation(
        sm_b, (gb > 0.5) & (np.linalg.norm(sm_b, axis=1) > mt))
    return (sm_a - g_a) * ga[:, None], (sm_b - g_b) * gb[:, None]


def test_shape_at_swap_incoming_display_holds_its_decomposed_residual():
    """THE locked invariant, now under motion decomposition: at every display
    switch the INCOMING display's FFD pose equals its DECOMPOSED cut residual
    (`(smoothed_half - g) * gate`), NOT zeros and NOT the old full half-offset.
    Combined with the shared bone track carrying the global, this puts the
    incoming display on the common midpoint silhouette the instant it appears
    (contract §18a) — the hard cut stays invisible."""
    piece = _piece3()
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(piece, ANIMS3)
    names, meshes = _displays(fp)
    K = len(meshes)
    dur = ANIMS3["win"]["duration"]
    T = [fr["t"] for fr in piece["frames"] if fr["key"]]
    key_pos = [i for i, fr in enumerate(piece["frames"]) if fr["key"]]
    imgs = piece["frame_images"]
    # NB: meshes are re-centered by the compiler; recompute the flow on the
    # NATIVE key-frame meshes (independent of re-centering, which shifts geometry
    # but not the flow-derived offsets).
    nverts = [np.asarray(mesh_gen.build_mesh(imgs[p][:, :, 3], target_cols=6)["vertices"],
                         np.float32) for p in key_pos]
    tris = [m["triangles"] for m in meshes]
    segs = []
    for j in range(K - 1):
        chain = imgs[key_pos[j]:key_pos[j + 1] + 1]
        ra, rb = _recompute_residual(chain, nverts[j], nverts[j + 1], tris[j], tris[j + 1])
        segs.append({"half_a": ra, "half_b": rb})
    cut_f = [(T[j] + T[j + 1]) / 2 for j in range(K - 1)]
    ffd_by_disp = {tl["display"]: tl for tl in ffd}

    found = 0
    for k in tracks[0]["keys"]:
        t, dname = k["t"], k["v"]
        d = names.index(dname)
        exp = None
        for j, cf in enumerate(cut_f):
            if abs(t - cf) < 1e-6 and d == j + 1:
                exp = segs[j]["half_b"]         # forward switch-in, toward-prev
            if abs(t - (dur - cf)) < 1e-6 and d == j:
                exp = segs[j]["half_a"]         # backward switch-in, toward-next
        if exp is None:
            continue                            # pole key (t=0 display 0) — no cut
        v_cut = _eval_ffd(ffd_by_disp[dname]["keys"], t)
        assert np.allclose(v_cut, exp, atol=1e-6), \
            f"display {dname}: FFD at swap {t} is not its decomposed cut residual"
        found += 1
    # K=3 pingpong switch-ins that land on a cut: 0->1, 1->2, 2->1, 1->0
    assert found == 4


# ---- guides actually influence flow -------------------------------------

def _piece_big(with_guide):
    frames = [{"file": "a.png", "t": 0.0, "key": True}]
    imgs = [_sq(0)]
    if with_guide:
        frames.append({"file": "g.png", "t": 0.1, "key": False})
        imgs.append(_sq(15))
    frames.append({"file": "b.png", "t": 0.25, "key": True})
    imgs.append(_sq(30))
    return {"name": "body", "type": "morph", "loop": "pingpong",
            "mesh": {"cols": 6}, "frames": frames, "frame_images": imgs}


def test_guides_influence_flow():
    _, ffd_g, _, _ = morph_compiler.expand_morph(_piece_big(True), ANIMS)
    _, ffd_n, _, _ = morph_compiler.expand_morph(_piece_big(False), ANIMS)
    g = np.asarray({round(k["t"], 3): k for k in ffd_g[0]["keys"]}[0.125]["offsets"])
    n = np.asarray({round(k["t"], 3): k for k in ffd_n[0]["keys"]}[0.125]["offsets"])
    # the guided chain accumulates the big shift in two steps; the raw pair
    # under-reads it — the mid offsets must differ measurably.
    assert not np.allclose(g, n, atol=0.5)


# ---- motion decomposition (fully-textured, DIS-trackable everywhere) -------

def _tex_sq(shift):
    """64x64 RGBA, a 24px fully-TEXTURED square at 10+shift — DIS tracks its
    interior everywhere, so the whole body reads one rigid translation."""
    rng = np.random.default_rng(5)
    img = np.zeros((64, 64, 4), np.uint8)
    tex = rng.integers(60, 255, (24, 24, 3), np.uint8)
    img[20:44, 10 + shift:34 + shift, :3] = tex
    img[20:44, 10 + shift:34 + shift, 3] = 255
    return img


def _tex_piece(loop="pingpong", shifts=(0, 5, 10)):
    frames = [{"file": "a.png", "t": 0.0, "key": True},
              {"file": "g.png", "t": 0.1, "key": False},
              {"file": "b.png", "t": 0.25, "key": True}]
    return {"name": "body", "type": "morph", "loop": loop, "mesh": {"cols": 8},
            "frames": frames, "frame_images": [_tex_sq(s) for s in shifts]}


def test_bone_track_carries_full_travel_residual_small():
    """A rigid 10px translation: the bone track's peak (accumulated global at
    the pole) is the FULL travel, and the mesh FFD residual is near zero."""
    fp, ffd, _, bt = morph_compiler.expand_morph(_tex_piece(), ANIMS)
    assert bt is not None
    peak = max(abs(gx) for _, gx, _ in bt["keys"])
    assert 7.0 < peak < 13.0, f"bone peak {peak} should be ~10px full travel"
    # residual across every FFD key is small — the global left the mesh
    res_max = max(np.abs(np.asarray(k["offsets"])).max()
                  for tl in ffd for k in tl["keys"])
    assert res_max < 2.0, f"residual {res_max} should be near zero for rigid motion"


def test_bone_track_loop_closes_and_is_symmetric():
    _, _, _, bt = morph_compiler.expand_morph(_tex_piece(), ANIMS)
    keys = {round(t, 6): (gx, gy) for t, gx, gy in bt["keys"]}
    dur = ANIMS["win"]["duration"]
    # first key at t=0 and last at t=dur both return to the loop start (0,0)
    assert np.allclose(keys[0.0], [0.0, 0.0])
    assert np.allclose(keys[round(dur, 6)], [0.0, 0.0])
    # ping-pong symmetry: the value at cut_f(0)=0.125 mirrors cut_b(0)=0.375
    assert np.allclose(keys[0.125], keys[round(dur - 0.125, 6)])


def test_meshes_recentered_by_cumulative_global():
    """Each display's emitted geometry is shifted by -P[k] (its cumulative
    global). Display 0 (P[0]=0) is unchanged; the peak display is shifted by the
    full accumulated travel so the bone (P[k]) can restore it to native."""
    piece = _tex_piece()
    fp, _, _, bt = morph_compiler.expand_morph(piece, ANIMS)
    _, meshes = _displays(fp)
    key_pos = [i for i, fr in enumerate(piece["frames"]) if fr["key"]]
    native = [np.asarray(mesh_gen.build_mesh(piece["frame_images"][p][:, :, 3],
                                             target_cols=8)["vertices"], float)
              for p in key_pos]
    d0 = np.asarray(meshes[0]["vertices"], float)
    np.testing.assert_allclose(d0, native[0])            # display 0 unchanged
    # display 1 (the peak key) is shifted by a constant ≈ -P[1] = -(full travel)
    shift = np.asarray(meshes[1]["vertices"], float) - native[1]
    assert np.allclose(shift, shift[0], atol=1e-6)        # a CONSTANT shift
    peak = max(abs(gx) for _, gx, _ in bt["keys"])
    assert abs(-shift[0][0] - peak) < 1.0                 # matches the bone peak


def test_forward_bone_track_closes_at_zero():
    """A forward loop must return the accumulated global to 0 at t=dur (seamless
    loop). Uses a there-and-back shift so the body ends where it started."""
    fp, _, _, bt = morph_compiler.expand_morph(
        _tex_piece("forward", shifts=(0, 8, 0)), FWD_ANIMS)
    if bt is not None:      # there-and-back may still net a small global
        assert np.allclose(bt["keys"][0][1:], [0.0, 0.0])
        assert np.allclose(bt["keys"][-1][1:], [0.0, 0.0])
        assert abs(bt["keys"][-1][0] - FWD_ANIMS["win"]["duration"]) < 1e-9


def test_pure_local_segment_has_no_bone_track():
    """No global motion (a stationary square) → no bone track at all (the mesh
    FFD alone is correct; decomposition must not invent a global)."""
    _, _, _, bt = morph_compiler.expand_morph(
        _tex_piece(shifts=(0, 0, 0)), ANIMS)
    assert bt is None


def test_registration_runs_first_and_stabilizes_frames(capsys, monkeypatch):
    """expand_morph runs flow_gen.register_frames as the FIRST step on
    frame_images, and everything downstream builds on the stabilized frames."""
    seen = {}
    real = flow_gen.register_frames

    def spy(frames, max_jitter=None):
        seen["n"] = len(frames)
        seen["first_id"] = id(frames[0])
        out, corr = real(frames, max_jitter=max_jitter)
        seen["out_first_id"] = id(out[0])
        return out, corr

    monkeypatch.setattr(flow_gen, "register_frames", spy)
    p = _piece()
    orig_first_id = id(p["frame_images"][0])
    fp, ffd, tracks, _bt = morph_compiler.expand_morph(p, ANIMS)
    # called with the raw frame_images
    assert seen["n"] == len(p["frames"])
    assert seen["first_id"] == orig_first_id
    # loud-but-informational log line on stderr
    err = capsys.readouterr().err
    assert "registration" in err and "stabilized" in err
    # still produces a valid morphframe piece
    assert fp[0]["type"] == "morphframe"


def test_downstream_meshes_built_from_registered_frames(monkeypatch):
    """CONSUMPTION: the meshes (and thus flow/decomposition) are built from the
    OUTPUT of register_frames, not the raw frame_images. We stub registration to
    translate every frame by a known +5px in x; the resulting morphframe mesh
    silhouette must move ~+5px versus the identity-registration build."""
    import cv2

    def build_first_mesh_centroid(shift_x):
        def stub(frames, max_jitter=None):
            M = np.array([[1, 0, shift_x], [0, 1, 0]], np.float32)
            out = [cv2.warpAffine(f, M, (f.shape[1], f.shape[0]),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(0, 0, 0, 0)) for f in frames]
            return out, [np.zeros(2) for _ in frames]
        monkeypatch.setattr(flow_gen, "register_frames", stub)
        fp, _, _, _ = morph_compiler.expand_morph(_piece(), ANIMS)
        v = np.asarray(fp[0]["mesh_data"]["vertices"], float)
        return v[:, 0].mean()

    cx0 = build_first_mesh_centroid(0.0)
    cx5 = build_first_mesh_centroid(5.0)
    assert cx5 - cx0 == pytest.approx(5.0, abs=1.0), \
        f"downstream mesh not built from registered frames: {cx0} -> {cx5}"
