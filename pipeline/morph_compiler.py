"""Morph compiler.

Unfolds a `type: "morph"` piece (a small stack of keyframe images with
per-frame times on ONE animation timeline) into the three things the writer
and atlas packer consume uniformly:

  1. frame_pieces  — a list with exactly ONE `morphframe` piece carrying K mesh
     DISPLAYS (display 0 = the piece name, displays 1..K-1 = `variants` named
     `<name>__k{i}`), one unweighted mesh (contract §18) per key frame. No
     weights_data (FFD offsets are plain mesh-space xy).
  2. ffd_timelines — a per-vertex deform timeline per DISPLAY, each targeting
     (shared slot, its own display name): zero at that key's own pole, half-way
     to the common midpoint shape at each cut boundary (Beier-Neely two-sided
     morphing expressed as FFD offsets).
  3. display_tracks — a list with exactly ONE stepped `display` track: a
     displayFrame timeline (contract §11) switching the shared slot between its
     K displays at the MID-SEGMENT CUT times (the cut between key k and k+1 is
     (T(k)+T(k+1))/2). By construction EXACTLY ONE display is active at any
     instant — zero overlap at any playback speed — so there is no
     double-exposure (the alpha-ramp scheme this replaces could show two copies
     at low speed). At each cut both meshes sit on the SAME midpoint shape
     (flow_gen's two-sided blend) and the incoming display's FFD pose is correct
     the instant it becomes active (§18a, proven by the deform-swap fixture), so
     the hard cut shows only a texture change, never a silhouette pop.

Transition geometry
--------------------
For segment i (key i -> key i+1) flow_gen.segment_offsets returns half_a
(offsets moving key i's OWN vertices half-way to the midpoint M) and half_b
(offsets moving key i+1's OWN vertices half-way back to the same M). half_a is
piece i's "toward-next" pose; half_b is piece (i+1)'s "toward-prev" pose. Each
segment's flow is computed ONCE and, for ping-pong, reused mirrored in time on
the backward pass (never recomputed). Note: the compiler passes two
INDEPENDENTLY-built meshes as verts_a/verts_b (each key frame's own grid), so
flow_gen's per-index "halves meet at M" semantics apply to the silhouettes,
not to index pairs — each half independently lands its own mesh on the common
midpoint shape.

All FFD interpolation is LINEAR. An eased FFD key would arrive at the cut at a
different speed than its partner departs on the far side, producing a visible
velocity hitch exactly at the (max-velocity) cut; constant morph speed across
the cut is what keeps the two-sided seam invisible.

Stepped display-swap scheme (replaces the earlier 1-frame alpha ramps, which
the user rejected because at low playback speed the ramp frame was visible as a
double-exposure). One displayFrame timeline holds display k over [prev_cut,
next_cut] and hard-switches to display k±1 exactly at each cut. Because a single
slot shows ONE display at a time, there is never any overlap to expose — the
swap is invisible not because it is brief but because it is instantaneous and
lands where both silhouettes already coincide. On the ping-pong backward pass
the same switches replay mirrored in time (cut_b = dur - cut_f). See contract
§11 (stepped displayFrame) and §18a (inactive displays keep their FFD pose
tracking, so the incoming one is already on the midpoint shape when shown).
"""
import sys

import numpy as np

import flow_gen
import mesh_gen

_DEFAULT_COLS = 14


def fail(msg: str) -> None:
    print(f"morph_compiler: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _dedup_ffd(keys):
    """keys: list of (t, offsets ndarray), any order. Sort by t and drop keys
    within 1e-9s of the previous one (collisions only occur where the two keys
    hold the same value by construction)."""
    keys = sorted(keys, key=lambda kv: kv[0])
    out = []
    for t, off in keys:
        if out and abs(out[-1][0] - t) < 1e-9:
            out[-1] = (t, off)
            continue
        out.append((t, off))
    return [{"t": t, "offsets": off} for t, off in out]


_GLOBAL_EPS = 0.5        # image px: max |global| below this => no bone track
_LOOP_TOL = 1.0          # forward loop: |accumulated global at last key| warn


def _bone_global_keys(loop, T, cut_f, dur, P, G_cut, name):
    """Synthesize the global-motion bone keys in IMAGE space (list of
    (t, gx, gy), piecewise-linear, loop-closing) from the accumulated per-pole
    globals P[k] and per-cut globals G_cut[i]. Returns None when no segment has a
    global component (max |global| < _GLOBAL_EPS) — the mesh alone is fine then.

    pingpong: the forward pass poles/cuts, plus a time-mirror of every interior
    key (the same key frames retrace in reverse), so the track is symmetric about
    the turn T[-1]=dur/2 and returns to P[0]=0 at dur (loop closes automatically).
    forward: poles/cuts up to the last pole, then a loop-closing key at dur equal
    to P[0]=0; a non-closed body path (|P[-1]| > _LOOP_TOL) warns loudly (the
    frames themselves did not return the figure to its start)."""
    K = len(T)
    keys = [(T[0], P[0])]
    for i in range(K - 1):
        keys.append((cut_f[i], G_cut[i]))
        keys.append((T[i + 1], P[i + 1]))
    if loop == "pingpong":
        turn = T[K - 1]
        mirror = []
        for t, v in keys:
            if t <= 1e-9 or abs(t - turn) < 1e-9:
                continue                       # loop start (mirror is dur) / turn
            mirror.append((dur - t, v))
        keys = keys + mirror + [(dur, P[0])]
    else:  # forward
        if abs(keys[-1][0] - dur) < 1e-9:
            keys[-1] = (dur, P[0])             # last pole is at dur: close on P[0]
        else:
            keys.append((dur, P[0]))           # hold+wrap to the loop start
        if float(np.linalg.norm(P[K - 1])) > _LOOP_TOL:
            print(f"morph_compiler: WARNING: piece {name}: forward loop does not "
                  f"close — accumulated global at last key is "
                  f"{P[K - 1].round(2).tolist()}px (expected ~0); the body does "
                  f"not return to its start, the wrap will pop", file=sys.stderr)
    keys.sort(key=lambda kv: kv[0])
    # drop keys colliding in time (same global by construction at a shared time)
    out = []
    for t, v in keys:
        if out and abs(out[-1][0] - t) < 1e-9:
            out[-1] = (t, v)
            continue
        out.append((t, v))
    if max(float(np.linalg.norm(v)) for _, v in out) < _GLOBAL_EPS:
        return None
    return {"keys": [(float(t), float(v[0]), float(v[1])) for t, v in out]}


def expand_morph(piece, anims, fps=30, owner_bone_name=None):
    """Expand a morph piece into
    (frame_pieces, ffd_timelines, display_tracks, bone_translation).

    piece: a morph dict validated by pieces._validate_morph, PLUS
    piece["frame_images"] (list of HxWx4 uint8 RGBA arrays, one per frames
    entry, on a shared canvas). anims: the animations dict (name -> {duration,
    loop, tracks}); exactly ONE animation must be defined (MVP constraint).
    owner_bone_name: the bone the morph slot is parented to; the synthesized
    global-motion bone track lands on it, and the slot/geometry are compensated
    into its local frame by the writer. When None the slot parents to root
    (the writer's default) and the bone track (if any) targets root.

    Motion decomposition (standard codec architecture)
    --------------------------------------------------
    DIS optical flow cannot track flat untextured fills (solid clothing): during
    a GLOBAL whole-body translation the well-tracked silhouette edges read the
    true displacement while the interior lags, and content_gate zeros the
    unobservable interior — so the mesh alone would move the edges but pin the
    interior, a shear that reads as a warp wave traveling through the figure
    (and WHERE the untrackable regions fall depends on pixel scale, so the wave
    moved with build resolution). The fix: per segment, extract the dominant
    rigid translation (median over moving verts) onto the owner BONE as one exact
    uniform vector; only the residual `half - g` stays on the mesh FFD, gated. The
    interior then rides the global exactly instead of warping behind the edges.

    `bone_translation` is the synthesized global motion in IMAGE space:
    `{"keys": [(t, gx, gy), ...]}` (piecewise-linear, delta-from-rest, loop-
    closing) or None when no segment has a global component. build_pieces_rig
    rotates it into the owner bone's parent frame (image_delta_to_bone_local)
    and folds it into the bone animation. See the decomposition math in the body."""
    name = piece["name"]

    # MVP constraint: the morph's synthesized timelines need exactly one home.
    if len(anims) != 1:
        fail(f"morph piece {name}: exactly ONE animation must be defined when a "
             f"morph piece exists (got {len(anims)}: {sorted(anims)}) — the "
             f"morph's FFD + alpha timelines go on that single animation; "
             f"lifting this is out of MVP scope")
    (_aname, anim), = anims.items()
    dur = float(anim["duration"])
    loop = piece.get("loop", "pingpong")

    frames = piece["frames"]
    frame_images = piece.get("frame_images")
    if not isinstance(frame_images, list) or len(frame_images) != len(frames):
        fail(f"morph piece {name}: frame_images (len "
             f"{0 if frame_images is None else len(frame_images)}) must align "
             f"1:1 with frames (len {len(frames)}) — the orchestrator attaches it")

    # FIRST step, before meshing/flow/decomposition: stabilize extraction jitter
    # (Wan video + rembg + LANCZOS resize inject a small global shift between
    # consecutive frames). Everything downstream — meshes, DIS flow, the global
    # decomposition — is built on the STABILIZED frames, so a genuine global
    # wobble never becomes a traveling deform wave or a swap-cadence shake. Real
    # motion (large shift, or a collapsed static support) is passed through
    # untouched; see flow_gen.register_frames.
    frame_images, _reg_corr = flow_gen.register_frames(frame_images)
    _reg_mag = [float(np.hypot(c[0], c[1])) for c in _reg_corr]
    _reg_nz = sum(1 for m in _reg_mag if m > 1e-6)
    print(f"morph_compiler: registration: piece {name}: stabilized "
          f"{_reg_nz}/{len(_reg_mag)} frames (max correction "
          f"{max(_reg_mag, default=0.0):.2f}px)", file=sys.stderr)

    key_pos = [idx for idx, fr in enumerate(frames) if fr.get("key")]
    K = len(key_pos)
    if K < 2:
        fail(f"morph piece {name}: need >= 2 key frames (got {K})")
    T = [float(frames[p]["t"]) for p in key_pos]
    last = T[-1]

    tol = 1.0 / fps
    if last > dur + 1e-6:
        fail(f"morph piece {name}: last key t={last} exceeds animation duration "
             f"{dur} — the morph timeline runs off the end of the animation")
    if loop == "pingpong":
        if abs(dur - 2.0 * last) > tol + 1e-9:
            fail(f"morph piece {name}: ping-pong loop requires duration == "
                 f"2 * last_key_t; last_key_t={last}, 2*last_key_t={2 * last}, "
                 f"animation duration={dur} (tolerance {tol:.4f}s = 1/fps) — set "
                 f"the animation duration to {2 * last} or adjust the last key t")
    else:  # forward
        if dur + 1e-6 < last:
            fail(f"morph piece {name}: forward loop requires duration >= "
                 f"last_key_t (the last frame holds to the end); last_key_t="
                 f"{last}, animation duration={dur}")

    cols = (piece.get("mesh") or {}).get("cols") or _DEFAULT_COLS

    # 1. ONE frame piece carrying K mesh DISPLAYS (contract §11 + §18, proven
    # end-to-end by the deform-swap fixture): display 0's name IS the piece name
    # (its atlas region), displays 1..K-1 are `variants` named `<name>__k{i}`.
    # A stepped displayFrame timeline (built below) switches between them at the
    # cut times — exactly one display active at any instant, so the old
    # alpha-ramp double-exposure is structurally impossible.
    disp_names = [name if i == 0 else f"{name}__k{i}" for i in range(K)]
    frame_meshes = []
    verts = []
    key_displays = []      # {name, img, offset, mesh_data} per key frame
    for i, p in enumerate(key_pos):
        img = frame_images[p]
        mesh = mesh_gen.build_mesh(img[:, :, 3], target_cols=cols)
        frame_meshes.append(mesh)
        verts.append(np.asarray(mesh["vertices"], np.float32))
        key_displays.append({"name": disp_names[i], "img": img,
                             "offset": (0, 0), "mesh_data": mesh})
    morph_piece = {
        "name": name, "type": "morphframe",
        "img": key_displays[0]["img"], "offset": (0, 0),
        "mesh_data": key_displays[0]["mesh_data"],
        "variants": key_displays[1:],
    }
    if owner_bone_name is not None:
        # parent the slot to the owner bone (carrier): the synthesized global
        # bone track rides it, and _morph_display / build_ffd_timelines
        # compensate geometry+residuals into its local frame.
        morph_piece["bone"] = owner_bone_name
    frame_pieces = [morph_piece]

    # 2. per-segment two-sided half-offsets, computed ONCE each, decomposed into
    # a shared global bone translation + a per-display gated RESIDUAL FFD.
    # toward_next[i] is the residual applied on display i (i -> midpoint M_i);
    # toward_prev[i+1] the residual applied on display i+1 (i+1 -> the same M_i).
    canvas_w = int(frame_images[key_pos[0]].shape[1])
    move_thresh = canvas_w / 240.0     # resolution-scaled "vertex is moving"
    toward_next = [None] * K
    toward_prev = [None] * K
    seg_ga = [np.zeros(2) for _ in range(K - 1)]   # A-side half-global (i -> M_i)
    seg_gb = [np.zeros(2) for _ in range(K - 1)]   # B-side half-global (i+1 -> M_i)
    for i in range(K - 1):
        chain = frame_images[key_pos[i]:key_pos[i + 1] + 1]  # keys + inner guides
        seg = flow_gen.segment_offsets(chain, verts[i], verts[i + 1])
        for w in seg["warnings"]:
            print(f"morph_compiler: WARNING: piece {name} segment "
                  f"{i}->{i + 1}: {w}", file=sys.stderr)
        # Laplacian-smooth each half-offset field over its OWN mesh's adjacency
        # (half_a rides verts[i]'s grid, half_b rides verts[i+1]'s) — irons out
        # flow discontinuities at a motion boundary into a contour zigzag,
        # without touching global translation (constant fields are a fixed
        # point). deadband + patch radius scale with canvas width.
        first_img, last_img = chain[0], chain[-1]
        gate_a = flow_gen.content_gate(first_img, last_img, verts[i])
        gate_b = flow_gen.content_gate(first_img, last_img, verts[i + 1])
        sm_a = flow_gen.smooth_offsets(seg["half_a"],
                                       frame_meshes[i]["triangles"], canvas_w=canvas_w)
        sm_b = flow_gen.smooth_offsets(seg["half_b"],
                                       frame_meshes[i + 1]["triangles"], canvas_w=canvas_w)
        # Motion decomposition. A vertex is "moving" where its content actually
        # changed (gate) AND its smoothed offset clears the motion threshold; the
        # dominant (median) translation over those verts is the segment's rigid
        # half-global. It goes on the bone; the residual `sm - g` stays on the
        # mesh, GATED so the unobservable interior (gate≈0) keeps residual 0 and
        # rides the bone's global exactly — no shear, no traveling warp. The same
        # local-change gate drives both sides, so static content stays coincident
        # and the two-sided midpoint seam holds.
        moving_a = (gate_a > 0.5) & (np.linalg.norm(sm_a, axis=1) > move_thresh)
        moving_b = (gate_b > 0.5) & (np.linalg.norm(sm_b, axis=1) > move_thresh)
        g_a = flow_gen.dominant_translation(sm_a, moving_a)
        g_b = flow_gen.dominant_translation(sm_b, moving_b)
        seg_ga[i] = g_a
        seg_gb[i] = g_b
        toward_next[i] = (sm_a - g_a) * gate_a[:, None]
        toward_prev[i + 1] = (sm_b - g_b) * gate_b[:, None]

    # 3. accumulate the global along the timeline. D_i = g_a - g_b is the
    # segment's rigid translation (blends both sides: g_a≈+D/2 from A, g_b≈-D/2
    # from B). P[k] = cumulative global at pole k (delta-from-rest, image space);
    # G_cut[i] = P[i] + g_a = P[i+1] + g_b is the SHARED bone value at cut c_i —
    # the two definitions agree BY CONSTRUCTION (D = g_a - g_b), so a single bone
    # track serves both displays at the swap and neither side pops (the invariant
    # the design had to close). Meshes are re-centered by P[k] below so the bone
    # can carry a nonzero global at the poles while each pole still shows its
    # authored key frame (base = native - P[k], bone = P[k] -> world = native).
    D = [seg_ga[i] - seg_gb[i] for i in range(K - 1)]
    P = [np.zeros(2)]
    for i in range(K - 1):
        P.append(P[-1] + D[i])
    G_cut = [P[i] + seg_ga[i] for i in range(K - 1)]

    # segment cuts (mid-points) and their ping-pong mirrors
    cut_f = [(T[i] + T[i + 1]) / 2.0 for i in range(K - 1)]  # index 0..K-2
    cut_b = [dur - c for c in cut_f]

    # re-center each display's emitted geometry by its cumulative global P[k].
    # verts[]/gates/residuals were computed on the NATIVE positions above and are
    # unaffected (residuals are deltas). Display 0 has P[0]=0 (unchanged).
    for k in range(K):
        if float(np.linalg.norm(P[k])) > 1e-12:
            vk = np.asarray(key_displays[k]["mesh_data"]["vertices"], float) - P[k]
            key_displays[k]["mesh_data"]["vertices"] = vk.tolist()

    # 4. synthesize the global-motion bone track (IMAGE space, piecewise-linear
    # through poles P[k] and cuts G_cut[i], loop-closing). build_pieces_rig
    # rotates it into the owner bone's parent frame.
    bone_translation = _bone_global_keys(loop, T, cut_f, dur, P, G_cut, name)

    Z = [np.zeros((len(verts[i]), 2)) for i in range(K)]

    def TN(i):
        return toward_next[i]

    def TP(i):
        return toward_prev[i]

    # --- FFD timelines ----------------------------------------------------
    ffd_timelines = []
    for i in range(K):
        if loop == "pingpong":
            if i == 0:
                raw = [(0.0, Z[0]), (cut_f[0], TN(0)), (cut_b[0], TN(0)), (dur, Z[0])]
            elif i == K - 1:
                # top piece: single window through the turn, pole T(i)=dur/2
                raw = [(0.0, TP(i)), (cut_f[i - 1], TP(i)), (T[i], Z[i]),
                       (cut_b[i - 1], TP(i)), (dur, TP(i))]
            else:
                raw = [(0.0, TP(i)), (cut_f[i - 1], TP(i)), (T[i], Z[i]),
                       (cut_f[i], TN(i)), (cut_b[i], TN(i)), (dur - T[i], Z[i]),
                       (cut_b[i - 1], TP(i)), (dur, TP(i))]
        else:  # forward — no mirror; loop is closed by the frames themselves
            if i == 0:
                raw = [(0.0, Z[0]), (cut_f[0], TN(0)), (dur, Z[0])]
            elif i == K - 1:
                # holds at its pole to the end; invisible before its window
                raw = [(0.0, Z[i]), (cut_f[i - 1], TP(i)), (T[i], Z[i]), (dur, Z[i])]
            else:
                raw = [(0.0, TP(i)), (cut_f[i - 1], TP(i)), (T[i], Z[i]),
                       (cut_f[i], TN(i)), (dur, TP(i))]
        # Each FFD timeline targets the SHARED slot (the morph piece name) and
        # its OWN display name. Keys outside a display's active window hold the
        # cut pose; per contract §18a the runtime keeps those inactive
        # deformVertices tracking so the pose is correct the instant the display
        # becomes active (proven by the deform-swap fixture).
        ffd_timelines.append({"piece": name, "display": disp_names[i],
                              "keys": _dedup_ffd(raw)})

    # --- stepped display track (replaces the alpha-ramp cuts) --------------
    # ONE display timeline switches the shared slot between its K displays at the
    # cut times: forward 0->1->...->K-1, and (ping-pong) back K-1->...->0. Exactly
    # one display is active at any instant BY CONSTRUCTION — zero overlap at any
    # playback speed, so the low-speed double-exposure the user rejected cannot
    # occur. The switches are hard (stepped displayFrame, §11) and land on the
    # same cut frames as the FFD half-offset poses, so silhouette is continuous.
    dkeys = [(0.0, disp_names[0])]
    for i in range(K - 1):
        dkeys.append((cut_f[i], disp_names[i + 1]))          # forward switches
    if loop == "pingpong":
        for i in range(K - 2, -1, -1):
            dkeys.append((cut_b[i], disp_names[i]))          # backward switches
    else:  # forward: hold the last display to the end, hard-wrap to display 0
        dkeys.append((dur, disp_names[0]))                   # loop-closing key
    display_track = {"piece": name, "prop": "display",
                     "keys": [{"t": t, "v": v} for t, v in dkeys]}

    return frame_pieces, ffd_timelines, [display_track], bone_translation
