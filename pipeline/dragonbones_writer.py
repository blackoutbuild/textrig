"""Serialize our rig inputs → a valid DragonBones `<name>_ske.json` + `<name>_tex.json` (+ copied png).

Inputs are IDENTICAL to assemble.py (mesh.json, skeleton.json, weights.json,
animations.json, texture png) — our formats do not change. The DragonBones parser
does NO schema validation, so we reuse assemble.py's loud validators BEFORE
serializing. The exact emitted shape is pinned by docs/dragonbones-format-contract.md.

Coordinate space / slotPose choice
----------------------------------
Our mesh vertices are in IMAGE PIXEL space (origin top-left, y-DOWN, absolute) —
which is exactly the space DragonBones treats as armature/world space. So the
armature space IS our image space: bones are placed at their absolute image
coordinates, bonePose bind matrices are `from_trs(absolute)`, and the mesh
vertices are emitted verbatim with **slotPose = identity `[1,0,0,1,0,0]`** (the
contract's worked-example choice: identity slotPose ⇒ `vertices` are already in
world space). No coordinate flip or offset is applied anywhere; every element
(bones, vertices, slotPose, bonePose) shares the one image/world space.

Bone transforms
---------------
Our skeleton stores ABSOLUTE rest bones (y-down, degrees, scale-free). DragonBones
bone `transform` is parent-local. We port runtime/src/skeleton.ts `restLocal`:
`local = inverse(parent_world) · child_absolute` for translation, and
`rotation_local = child_rotation − parent_rotation` (valid because rests are
scale-free). Emitted as `{x, y, skX=skY=rotation°}` (skX==skY ⇒ pure rotation).
`bonePose` matrices are each bone's absolute world bind matrix `from_trs(x,y,rot)`
— which the parser must be able to reconstruct from the parent-local chain, and
does exactly, since parent_local∘recompose is an exact inverse.

Easing mapping (our ease names → DragonBones tween), per contract §7c
--------------------------------------------------------------------
Closed forms DragonBones represents exactly (scalar `tweenEasing`):
  linear     → tweenEasing 0
  quadIn     → tweenEasing -1   (quadratic ease-in,  full strength)
  quadOut    → tweenEasing  1   (quadratic ease-out, full strength)
  sineInOut  → tweenEasing  2   (0.5·(1−cos(t·π)) — identical to CSS sineInOut)
No closed form → cubic-Bézier `curve: [x1,y1,x2,y2]` (Penner/CSS control points):
  sineIn     → [0.47,  0,     0.745, 0.715]
  sineOut    → [0.39,  0.575, 0.565, 1.0  ]
  quadInOut  → [0.455, 0.03,  0.515, 0.955]
  backOut    → [0.175, 0.885, 0.32,  1.275]   (y>1 overshoot; sampled fine)
Any ease name outside this table raises (fail loud, never guess).

Scale-track semantics
---------------------
DragonBones `scaleFrame` x/y are ABSOLUTE factors relative to the bone's setup
scale (default 1 = identity), NOT additive deltas — traced to the parser +
runtime in docs/dragonbones-format-contract.md §7a [Added 2026-07-10]. Our
`scaleX/scaleY` track values are absolute factors (neutral 1) and our bones carry
no setup scale, so we copy them verbatim into `scaleFrame`.

Timeline coverage
-----------------
Each exported track must have a key at t=0 AND a key at t=duration. This is
STRICTER than assemble.py / format/README.md (which merely recommend the
loop-closing final key): the loop convention is enforced as a hard rule at this
stage, because otherwise the DragonBones per-frame durations would not sum to
the animation duration. Additionally, two keys closer than 1/fps (which would
round to the same output frame → a non-terminal zero-duration frame → NaN in
DragonBones' progress division) are rejected loud.
"""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Reuse assemble.py's loud validators (import, do NOT copy-paste). assemble.py is
# a sibling module; it only runs on `__main__`, so importing is side-effect free.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble  # noqa: E402
import wave_compiler  # noqa: E402
from assemble import (  # noqa: E402
    load_json, validate_animations, validate_bones, validate_mesh, validate_paths,
    validate_weights,
)


def fail(msg: str) -> None:
    print(f"dragonbones_writer: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# Errors raised while running under THIS tool should say so — rebind the alias
# the imported validators resolve at call time. assemble.py itself is untouched
# (standalone runs still print "assemble: ERROR:").
assemble.fail = fail

FRAME_RATE = 30
DATA_VERSION = "5.5"

# ease name → DragonBones tween fragment (merged into each frame). See docstring.
_EASE_SCALAR = {"linear": 0, "quadIn": -1, "quadOut": 1, "sineInOut": 2}
_EASE_CURVE = {
    "sineIn": [0.47, 0.0, 0.745, 0.715],
    "sineOut": [0.39, 0.575, 0.565, 1.0],
    "quadInOut": [0.455, 0.03, 0.515, 0.955],
    "backOut": [0.175, 0.885, 0.32, 1.275],
}


# --------------------------------------------------------------------------- #
# 2x3 affine matrices as np.array([a, b, c, d, tx, ty]); x' = a·x + c·y + tx.  #
# Mirrors runtime/src/mat2d.ts exactly.                                        #
# --------------------------------------------------------------------------- #
def from_trs(x, y, rot_deg, sx=1.0, sy=1.0):
    r = np.radians(rot_deg)
    cos, sin = np.cos(r), np.sin(r)
    return np.array([cos * sx, sin * sx, -sin * sy, cos * sy, x, y], dtype=float)


def mat_apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def mat_invert(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise ValueError("mat_invert: singular matrix")
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return np.array([ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty)], dtype=float)


def mat_mul(A, B):
    return np.array([
        A[0] * B[0] + A[2] * B[1],
        A[1] * B[0] + A[3] * B[1],
        A[0] * B[2] + A[2] * B[3],
        A[1] * B[2] + A[3] * B[3],
        A[0] * B[4] + A[2] * B[5] + A[4],
        A[1] * B[4] + A[3] * B[5] + A[5],
    ], dtype=float)


# --------------------------------------------------------------------------- #
# Bones                                                                        #
# --------------------------------------------------------------------------- #
def world_bind_matrix(bone):
    """Absolute world bind matrix (scale-free) of a rest bone."""
    return from_trs(bone["x"], bone["y"], bone["rotation"])


def parent_local(bone, parent):
    """Our absolute rest bone → parent-local (x, y, rotation°). Ported from
    runtime/src/skeleton.ts buildSkeleton restLocal."""
    if parent is None:
        return (bone["x"], bone["y"], bone["rotation"])
    inv = mat_invert(world_bind_matrix(parent))
    lx, ly = mat_apply(inv, bone["x"], bone["y"])
    return (lx, ly, bone["rotation"] - parent["rotation"])


def image_delta_to_bone_local(bones, owner_name, dx, dy):
    """Rotate an image-space translation `(dx, dy)` into `owner_name`'s translate
    frame — the space a DragonBones translate timeline adds in, which is the
    OWNER bone's PARENT frame (format/README: "x/y deltas act in parent-bone
    frame"; every bone's translate is expressed in its parent's local axes).

    Used to convert a synthesized global-motion vector (morph motion
    decomposition) into bone-track x/y keys. Only the LINEAR part of the parent's
    inverse bind matrix applies — a translation delta is a vector, not a point.

    A root-owned bone (parent is None) lives directly in armature space, which IS
    our image space, so the mapping is the identity. A bone parented to a root
    that rotates image by -90° (our standard carrier chain) reduces to
    `(x, y) = (-dy, dx)` — exactly the win1-deform prototype's hand mapping."""
    by = {b["name"]: b for b in bones}
    owner = by[owner_name]
    parent_name = owner.get("parent")
    if not parent_name:
        return (dx, dy)
    inv = mat_invert(world_bind_matrix(by[parent_name]))
    return (inv[0] * dx + inv[2] * dy, inv[1] * dx + inv[3] * dy)


def _round(v, nd=6):
    r = round(float(v), nd)
    return int(r) if r == int(r) else r


def bones_to_db(bones, length_bones=frozenset()):
    """`length_bones` names bones that MUST carry an explicit `length` in the
    emitted bone data. Path-constraint chain bones need it: the runtime's
    `spacingMode:"length"` divides by each chain bone's setup length (contract
    §16d; `_getNumber(bone, "length", 0)` → default 0 → division by zero → NaN
    → collapsed, invisible chain). Length is cosmetic for every other bone, so
    it is omitted there to keep path-free rigs byte-identical to their goldens."""
    by_name = {b["name"]: b for b in bones}
    out = []
    for b in bones:
        parent = None if b["parent"] is None else by_name[b["parent"]]
        entry = {"name": b["name"]}
        if b["parent"] is not None:
            entry["parent"] = b["parent"]
        lx, ly, lrot = parent_local(b, parent)
        transform = {}
        if _round(lx) != 0 or _round(ly) != 0:
            transform["x"], transform["y"] = _round(lx), _round(ly)
        if _round(lrot) != 0:
            transform["skX"] = transform["skY"] = _round(lrot)
        if transform:
            entry["transform"] = transform
        if b["name"] in length_bones:
            entry["length"] = _round(b["length"])
        out.append(entry)
    return out


def _path_chain_bones(paths):
    """Names of every bone driven as a path-constraint chain link — these
    require an emitted `length` (contract §16d, see `bones_to_db`)."""
    return {b for p in (paths or []) for b in p.get("chain", [])}


# --------------------------------------------------------------------------- #
# Weights + bonePose                                                           #
# --------------------------------------------------------------------------- #
def pack_weights(weights):
    """Our per-vertex {bones, w} → DragonBones packed [count, gIdx, w, ...]."""
    packed = []
    for vw in weights:
        packed.append(len(vw["bones"]))
        for gidx, w in zip(vw["bones"], vw["w"]):
            packed.extend([gidx, w])
    return packed


def referenced_bones(weights):
    refs = set()
    for vw in weights:
        refs.update(vw["bones"])
    return sorted(refs)


def build_bonepose(bones, referenced):
    """[gIdx, a, b, c, d, tx, ty] per referenced bone (world bind matrix)."""
    bp = []
    for gidx in referenced:
        bp.append(gidx)
        bp.extend(_round(v) for v in world_bind_matrix(bones[gidx]))
    return bp


# --------------------------------------------------------------------------- #
# Easing + timelines                                                           #
# --------------------------------------------------------------------------- #
def ease_to_db(name):
    if name in _EASE_SCALAR:
        return {"tweenEasing": _EASE_SCALAR[name]}
    if name in _EASE_CURVE:
        return {"curve": list(_EASE_CURVE[name])}
    raise ValueError(f"no DragonBones mapping for easing {name!r}")


# channel spec: DB frame key -> (our prop, db field, neutral default)
_CHANNELS = {
    "rotateFrame": [("rotation", "rotate", 0.0)],
    "translateFrame": [("x", "x", 0.0), ("y", "y", 0.0)],
    "scaleFrame": [("scaleX", "x", 1.0), ("scaleY", "y", 1.0)],
}


def _channel_frames(comps, fps, total_frames):
    """comps: list of (track_or_None, db_field, default). Emit DB frame list."""
    present = [t for t, _, _ in comps if t is not None]
    ref = present[0]["keys"]
    ref_times = [k["t"] for k in ref]
    ref_eases = [k.get("ease", "linear") for k in ref]
    # paired components (x+y / sx+sy) must share one time+easing grid (DB has one
    # tween per frame for the whole channel) — fail loud instead of guessing.
    for t, _, _ in comps:
        if t is None:
            continue
        if [k["t"] for k in t["keys"]] != ref_times or [k.get("ease", "linear") for k in t["keys"]] != ref_eases:
            raise ValueError(f"paired channel components on bone {t['bone']!r} disagree on key times/easing")
    frame_times = [round(t * fps) for t in ref_times]
    if frame_times[0] != 0:
        raise ValueError(f"track on {ref[0]!r}: DragonBones export needs a key at t=0")
    if frame_times[-1] != total_frames:
        raise ValueError("track needs a key at t=duration (loop-closing frame)")
    # Keys closer than 1/fps round to the same frame → a non-terminal
    # {"duration": 0} frame, which NaNs DragonBones' per-frame progress division.
    for i in range(len(frame_times) - 1):
        if frame_times[i + 1] == frame_times[i]:
            raise ValueError(
                f"keys at t={ref_times[i]} and t={ref_times[i + 1]} collapse to "
                f"the same frame at {fps}fps")

    n = len(ref)
    frames = []
    for i in range(n):
        terminal = i == n - 1
        frame = {"duration": 0 if terminal else frame_times[i + 1] - frame_times[i]}
        if not terminal:
            frame.update(ease_to_db(ref_eases[i]))
        for t, field, default in comps:
            frame[field] = _round(t["keys"][i]["v"] if t is not None else default)
        frames.append(frame)
    return frames


def build_bone_timelines(bone_name, tracks, fps, total_frames):
    by_prop = {tr["prop"]: tr for tr in tracks}
    if "alpha" in by_prop:
        raise ValueError("alpha tracks have no DragonBones bone-timeline channel (Stage 0b)")
    tl = {"name": bone_name}
    for frame_key, spec in _CHANNELS.items():
        comps = [(by_prop.get(prop), field, default) for prop, field, default in spec]
        if any(t is not None for t, _, _ in comps):
            tl[frame_key] = _channel_frames(comps, fps, total_frames)
    return tl


def build_animation(name, anim, fps):
    total_frames = round(anim["duration"] * fps)
    by_bone = {}
    for tr in anim["tracks"]:
        by_bone.setdefault(tr["bone"], []).append(tr)
    bone_timelines = [build_bone_timelines(b, trs, fps, total_frames) for b, trs in by_bone.items()]
    play_times = 0 if anim.get("loop", True) else 1  # DB: 0 = loop forever, 1 = play once
    return {"duration": total_frames, "name": name, "playTimes": play_times, "bone": bone_timelines}


# --------------------------------------------------------------------------- #
# Document assembly                                                            #
# --------------------------------------------------------------------------- #
def build_ske(name, mesh, bones, weights, anims, tex_w, tex_h, fps=FRAME_RATE, paths=None):
    verts = [c for v in mesh["vertices"] for c in v]
    uvs = [c for uv in mesh["uvs"] for c in uv]
    tris = [i for tri in mesh["triangles"] for i in tri]
    refs = referenced_bones(weights)
    display = {
        "type": "mesh", "name": name, "width": tex_w, "height": tex_h,
        "vertices": verts, "uvs": uvs, "triangles": tris,
        "weights": pack_weights(weights),
        "slotPose": [1, 0, 0, 1, 0, 0],
        "bonePose": build_bonepose(bones, refs),
    }
    animations = [build_animation(aname, a, fps) for aname, a in anims.items()]
    all_bones = list(bones)
    slots = [{"name": name, "parent": bones[0]["name"]}]
    skin_slots = [{"name": name, "display": [display]}]
    constraints = []
    if paths:
        extra_bones, path_slots, path_skin_slots, constraints = build_path_elements(paths, bones)
        all_bones = all_bones + extra_bones
        slots = slots + path_slots
        skin_slots = skin_slots + path_skin_slots
    armature = {
        "type": "Armature", "frameRate": fps, "name": name,
        "bone": bones_to_db(all_bones, _path_chain_bones(paths)),
        "slot": slots,
        "skin": [{"slot": skin_slots}],
        "animation": animations,
    }
    if animations:
        armature["defaultActions"] = [{"gotoAndPlay": animations[0]["name"]}]
    if constraints:
        armature["path"] = constraints
    return {
        "frameRate": fps, "name": name,
        "version": DATA_VERSION, "compatibleVersion": DATA_VERSION,
        "armature": [armature],
    }


def build_tex(name, tex_w, tex_h, png_name):
    return {
        "width": tex_w, "height": tex_h, "name": name, "imagePath": png_name,
        "SubTexture": [{"name": name, "x": 0, "y": 0, "width": tex_w, "height": tex_h}],
    }


# --------------------------------------------------------------------------- #
# Multi-piece armature (Stage 1): image / swap / deform slots, slot timelines. #
# Every emitted shape is pinned to docs/dragonbones-format-contract.md §9–§14  #
# and the proven fixture renderer/assets/figure2/figure2_ske.json.   #
# The single-mesh path above is untouched.                                     #
# --------------------------------------------------------------------------- #
def image_display_transform(offset, size, owner_bone):
    """Image display transform (contract §10). Places the region CENTER (the
    default DragonBones pivot 0.5/0.5) at `offset + size/2` in world space by
    expressing it in the owner bone's local frame, and counter-rotates the
    sprite by the bone's world rotation so the art stays world-axis-aligned at
    rest. Our bones store ABSOLUTE rest, so the bone's rest world rotation is
    its own `rotation`."""
    cx, cy = offset[0] + size[0] / 2.0, offset[1] + size[1] / 2.0
    inv = mat_invert(world_bind_matrix(owner_bone))
    lx, ly = mat_apply(inv, cx, cy)
    t = {"x": _round(lx), "y": _round(ly)}
    rot = _round(-owner_bone["rotation"])
    if rot != 0:
        t["skX"] = t["skY"] = rot
    return t


def _image_display(region_name, offset, size, owner_bone):
    """Image (sprite) display. Contract §10: image is the DEFAULT display type —
    figure2 image displays carry NO `type` key, so we emit none. `width`/`height`
    are silently ignored for image displays and are NOT emitted. Identity
    transforms are omitted (same convention as bones)."""
    d = {"name": region_name}
    tr = image_display_transform(offset, size, owner_bone)
    if any(v != 0 for v in tr.values()):
        d["transform"] = tr
    return d


def build_slot_timelines(anim_name, tracks, built_pieces, fps, total_frames):
    """Piece display/alpha tracks → DragonBones slot timelines (contract §11–§12).

    - display → `displayFrame` (type 20, stepped): frame `value` = index into the
      slot's skin display list `[piece, *variants]`. The terminal frame HOLDS to
      the end of the animation (`total_frames − last_frame`); when the last key
      sits at t=duration this is a natural `duration: 0` (figure2's case).
      A key at t=0 is required; a key at t=duration is NOT (the hold covers it),
      but keys PAST t=duration are rejected (negative terminal hold).
    - alpha → `colorFrame` (type 21, tween-capable): `value = {"aM": 0..100}`,
      `tweenEasing`/`curve` on every non-terminal frame, terminal frame carries
      `duration: 0` and NO tween. Frame key order matches figure2:
      `duration, tweenEasing, value`. Requires a loop-closing key at t=duration.
    """
    by_piece_name = {p["name"]: p for p in built_pieces}
    out = {}
    for tr in tracks:
        p = by_piece_name[tr["piece"]]
        tl = out.setdefault(tr["piece"], {"name": tr["piece"]})
        keys = tr["keys"]
        frame_times = [round(k["t"] * fps) for k in keys]
        if frame_times[0] != 0:
            raise ValueError(f"{anim_name}: {tr['piece']}.{tr['prop']} needs a key at t=0")
        for a, b in zip(frame_times, frame_times[1:]):
            if b == a:
                raise ValueError(f"{anim_name}: {tr['piece']}.{tr['prop']} keys collapse "
                                 f"to one frame at {fps}fps")
        if tr["prop"] == "display":
            if frame_times[-1] > total_frames:
                raise ValueError(
                    f"{anim_name}: {tr['piece']}.{tr['prop']} key at t={keys[-1]['t']} "
                    f"is past the animation duration ({total_frames / fps}s) — the "
                    f"terminal hold would get a negative duration")
            display_names = [p["name"]] + [v["name"] for v in p["variants"]]
            frames = []
            for i, k in enumerate(keys):
                dur = (frame_times[i + 1] - frame_times[i]) if i + 1 < len(keys) \
                    else total_frames - frame_times[i]
                frames.append({"duration": dur, "value": display_names.index(k["v"])})
            tl["displayFrame"] = frames
        else:  # alpha
            if frame_times[-1] != total_frames:
                raise ValueError(f"{anim_name}: {tr['piece']}.alpha needs a loop-closing "
                                 f"key at t=duration")
            frames = []
            for i, k in enumerate(keys):
                terminal = i == len(keys) - 1
                frame = {"duration": 0 if terminal else frame_times[i + 1] - frame_times[i]}
                if not terminal:
                    frame.update(ease_to_db(k.get("ease", "linear")))
                frame["value"] = {"aM": int(round(k["v"] * 100))}
                frames.append(frame)
            tl["colorFrame"] = frames
    return list(out.values())


def _deform_display(piece, region, bones):
    """Per-piece weighted mesh display (contract §6, §13). Vertices are pushed to
    world space (piece-local mesh + canvas offset) with `slotPose = identity`;
    uvs are region-relative (already 0..1 within the piece's own SubTexture);
    weights carry GLOBAL bone indices and every referenced bone appears in
    `bonePose`."""
    mesh, weights = piece["mesh_data"], piece["weights_data"]
    ox, oy = piece["offset"]
    verts = [c + (ox if i % 2 == 0 else oy) for i, c in
             enumerate(v for pt in mesh["vertices"] for v in pt)]
    uvs = [c for uv in mesh["uvs"] for c in uv]
    tris = [i for t in mesh["triangles"] for i in t]
    refs = referenced_bones(weights)
    return {
        "type": "mesh", "name": piece["name"],
        "width": region["width"], "height": region["height"],
        "vertices": verts, "uvs": uvs, "triangles": tris,
        "weights": pack_weights(weights),
        "slotPose": [1, 0, 0, 1, 0, 0],
        "bonePose": build_bonepose(bones, refs),
    }


def _morph_display(piece, region, owner_bone):
    """Per-piece UNWEIGHTED mesh display for a morph key frame (contract §18,
    proven by renderer/assets/deform/deform_ske.json). Same geometry
    emission as `_deform_display` (vertices/uvs/triangles placed at the piece's
    canvas offset) MINUS weights/slotPose/bonePose — an unweighted mesh carries
    none of those; its `ffd` timeline (`build_ffd_timelines`) animates
    `vertices` directly at render time instead of bone weighting.

    Coordinate space: an unweighted mesh's vertices live in the slot's PARENT
    BONE's local space at runtime — the slot inherits the bone's world
    transform raw, with NO slotPose/bonePose compensation (that pair exists
    only on weighted meshes, §6c). Our morphframe geometry is authored in
    IMAGE space, so emit `v_local = inv(world_bind(owner)) · v_image` — the
    same bind math the weighted path bakes into bonePose. At rest the bone's
    world matrix equals its bind matrix, so the composition reconstructs the
    image placement exactly (and the mesh then rides the bone rigidly)."""
    mesh = piece["mesh_data"]
    ox, oy = piece["offset"]
    inv = mat_invert(world_bind_matrix(owner_bone))
    verts = []
    for x, y in mesh["vertices"]:
        lx, ly = mat_apply(inv, x + ox, y + oy)
        verts.extend([_round(lx), _round(ly)])
    uvs = [c for uv in mesh["uvs"] for c in uv]
    tris = [i for t in mesh["triangles"] for i in t]
    return {
        "type": "mesh", "name": piece["name"],
        "width": region["width"], "height": region["height"],
        "vertices": verts, "uvs": uvs, "triangles": tris,
    }


def _trim_ffd_offsets(flat):
    """Leading+trailing zero-run compression (contract §18b): `flat` is a
    frame's full-length `(dx,dy,dx,dy,...)` delta block (2N floats for an
    unweighted mesh). Returns `(offset, trimmed_floats)`, or `None` if every
    coordinate is zero (no deformation this frame — the caller emits an empty
    `"vertices": []`, matching the fixture's rest/terminal frames). The trim
    is VERTEX-PAIR aligned (offset even, payload even-length) — coordinate-
    granular offsets are parser-legal but the fixture and the editor emit
    pair-aligned blocks, so we match that shape."""
    n = len(flat)
    lo = 0
    while lo < n and flat[lo] == 0:
        lo += 1
    if lo == n:
        return None
    hi = n
    while flat[hi - 1] == 0:
        hi -= 1
    lo -= lo % 2
    hi += (hi - lo) % 2
    return lo, flat[lo:hi]


def _ffd_offsets_local(offsets, inv):
    """Rotate image-space FFD deltas into the owner bone's local frame. Deltas
    are VECTORS added to the mesh-local vertices at render time (§18c), so only
    the LINEAR part of the inverse bind matrix applies — no translation (the
    same convention as the parser's `transformPoint(..., true)` delta flag)."""
    off = np.asarray(offsets, float)
    return np.stack([inv[0] * off[:, 0] + inv[2] * off[:, 1],
                     inv[1] * off[:, 0] + inv[3] * off[:, 1]], axis=1)


def build_ffd_timelines(anim_name, ffd_tracks, owner_by_piece, fps, total_frames):
    """morph_compiler `ffd_timelines` -> DragonBones `animation.ffd[]` entries
    (contract §18a/§18b). One entry per DISPLAY: `slot` = the shared morph slot
    (`tr["piece"]`), `name` = that display's own name (`tr["display"]`, defaulting
    to the slot name for a single-display piece). K displays share one slot, each
    with its own ffd timeline (§11+§18a, proven by the deform-swap fixture). Key
    times quantize to the SAME fps grid as every other timeline
    family. All FFD interpolation is LINEAR (morph_compiler's own contract —
    a tween mismatch across a mid-segment cut would produce a velocity hitch
    exactly at the cut), so every non-terminal frame carries `tweenEasing: 0`;
    the terminal frame carries none (matches the deform fixture).

    `owner_by_piece` maps piece name -> the OWNER (slot parent) bone dict.
    Offsets arrive in IMAGE space and are rotated into the owner bone's local
    frame (`_ffd_offsets_local`) — the SAME space `_morph_display` emits the
    rest vertices in; using different conventions for the two would skew every
    deform (see that docstring for why unweighted geometry is bone-local).

    Quantization guarantees (Task 6 review — requirements, not suggestions):
      1. Two adjacent keys with DIFFERENT values must never collapse onto the
         same output frame. The compiler's `J = 1/fps + 0.001` key spacing
         guarantees >=1-frame separation between any two keys that actually
         differ, so a collision here means a real bug upstream — fail loud via
         `fail()` (SystemExit), not a catchable ValueError: this is an
         invariant violation, not a user-authoring error for a caller to
         recover from.
      2. The terminal key at t=duration must survive emission: `frame_times[-1]`
         is required to equal `total_frames`, and even if it merges with an
         identical-valued predecessor (guarantee 3) the surviving frame carries
         the same closing value.
      3. Near-equal (same-value) FFD keys MAY merge — collapsing two identical
         values loses nothing.
    """
    out = []
    for tr in ffd_tracks:
        slot_name = tr["piece"]                       # the shared slot
        disp_name = tr.get("display", slot_name)      # the display within it
        inv = mat_invert(world_bind_matrix(owner_by_piece[slot_name]))
        keys = tr["keys"]
        frame_times = [round(k["t"] * fps) for k in keys]
        if frame_times[0] != 0:
            raise ValueError(f"{anim_name}: ffd {disp_name!r} needs a key at t=0")
        if frame_times[-1] != total_frames:
            raise ValueError(
                f"{anim_name}: ffd {disp_name!r} needs a loop-closing key at t=duration")

        merged_times, merged_keys = [frame_times[0]], [keys[0]]
        for ft, k in zip(frame_times[1:], keys[1:]):
            if ft == merged_times[-1]:
                prev = np.asarray(merged_keys[-1]["offsets"], float)
                cur = np.asarray(k["offsets"], float)
                if not np.allclose(prev, cur, atol=1e-6):
                    fail(f"{anim_name}: ffd {disp_name!r} keys at t={merged_keys[-1]['t']} "
                         f"and t={k['t']} collapse to the same frame at {fps}fps with "
                         f"DIFFERENT values — this indicates authoring at sub-frame "
                         f"spacing, a bug upstream (the compiler's key spacing must "
                         f"guarantee >=1-frame separation between distinct values)")
                continue  # same value: merge, keep the first (guarantee 3)
            merged_times.append(ft)
            merged_keys.append(k)

        n = len(merged_keys)
        frames = []
        for i in range(n):
            terminal = i == n - 1
            local = _ffd_offsets_local(merged_keys[i]["offsets"], inv)
            flat = [_round(v) for v in local.reshape(-1)]
            trimmed = _trim_ffd_offsets(flat)
            frame = {"duration": 0 if terminal else merged_times[i + 1] - merged_times[i]}
            if not terminal:
                frame["tweenEasing"] = 0
            if trimmed is None:
                frame["vertices"] = []
            else:
                lo, vals = trimmed
                if lo != 0:
                    frame["offset"] = lo
                frame["vertices"] = vals
            frames.append(frame)
        out.append({"skin": "default", "slot": slot_name, "name": disp_name, "frame": frames})
    return out


def build_zorder_frames(anim_name, order_tracks, built_pieces, fps, total_frames):
    """Merge per-piece `order` tracks into ONE DragonBones zOrder timeline
    (contract §15). Frames are absolute snapshots of the deviation set:
    at every time where any track's offset changes, emit (slotIndex, offset)
    pairs for ALL pieces currently shifted, ascending slot index. Stepped —
    a key holds until the piece's next key. Terminal frame holds to the end
    (displayFrame §11 precedent)."""
    slot_index = {p["name"]: i for i, p in enumerate(built_pieces)}
    steps = {}
    for tr in order_tracks:
        ft = [round(k["t"] * fps) for k in tr["keys"]]
        for a, b in zip(ft, ft[1:]):
            if a == b:
                raise ValueError(f"{anim_name}: {tr['piece']}.order keys collapse "
                                 f"to one frame at {fps}fps")
        steps[tr["piece"]] = list(zip(ft, (int(k["v"]) for k in tr["keys"])))
    change_times = sorted({t for s in steps.values() for t, _ in s})

    def offset_at(pname, t):
        off = 0
        for ft, v in steps[pname]:
            if ft <= t:
                off = v
        return off

    frames = []
    for i, t in enumerate(change_times):
        pairs = []
        targets = {}
        for pname in sorted(steps, key=slot_index.__getitem__):
            off = offset_at(pname, t)
            if off != 0:
                si = slot_index[pname]
                target = si + off
                if not (0 <= target < len(built_pieces)):
                    # pieces.py only bounds |shift| < piece count; the shifted
                    # POSITION can still fall outside the stack, which the §15d
                    # parser (a JS array) accepts silently and then corrupts.
                    raise ValueError(
                        f"{anim_name}: piece {pname!r} order shift {off} moves slot {si} to "
                        f"out-of-range position {target} (must be 0..{len(built_pieces) - 1})")
                if target in targets:
                    raise ValueError(
                        f"{anim_name}: pieces {targets[target]!r} and {pname!r} collide "
                        f"at zOrder frame t={t / fps}s (both target slot position {target})")
                targets[target] = pname
                pairs.extend([si, off])
        dur = (change_times[i + 1] - t) if i + 1 < len(change_times) else total_frames - t
        frame = {"duration": dur}
        if pairs:
            frame["zOrder"] = pairs
        frames.append(frame)
    return frames


# --------------------------------------------------------------------------- #
# Path constraints (v1 rigid) — contract §16. A path is a slot parented to an #
# owner bone, a type:"path" display carrying curve geometry weighted to a     #
# dedicated anchor bone (§16b — weighting to the owner or leaving the         #
# geometry unweighted deadlocks the armature, §16c), and an armature-level    #
# "path" constraint tying a bone chain to that curve. PROVEN by the hand      #
# fixture renderer/assets/path.                                    #
# --------------------------------------------------------------------------- #
import path_math  # noqa: E402
from path_math import catmull_rom_to_bezier, path_anchor_frames  # noqa: E402


def bezier_arc_lengths(segs, samples=64):
    """Cumulative per-segment arc lengths (path_math), rounded with the
    writer's output convention — this is what lands in the emitted JSON."""
    return path_math.bezier_arc_lengths(segs, samples, rounder=_round)


def build_path_elements(paths, bones, taken_slot_names=frozenset()):
    """skeleton `paths` section (validated by assemble.validate_paths) ->
    (extra_bones, slots, skin_slots, constraints) for the DragonBones
    armature. `bones` is the EXISTING bone list (before any path's anchor is
    appended) — returned `extra_bones` must be appended AFTER it; the weight
    global-bone-indices baked into the geometry below assume exactly that
    placement (§16b/§16c: an anchor sorting after the constrained chain is
    what avoids the `_localDirty` deadlock).

    `taken_slot_names` are slot names already used by non-path pieces (Stage 1
    multi-piece armatures) — a path name colliding with one is rejected, same
    as a collision with any existing (or another path's generated anchor)
    bone name."""
    by_name = {b["name"]: b for b in bones}
    bone_names = set(by_name)
    taken_names = set(taken_slot_names)
    anchor_offset = len(bones)

    extra_bones, slots, skin_slots, constraints = [], [], [], []
    for p in paths:
        name = p["name"]
        if name in taken_names:
            raise ValueError(f"path {name!r}: slot name collides with an existing slot/path")
        if name in bone_names:
            raise ValueError(f"path {name!r}: slot name collides with an existing bone")
        taken_names.add(name)

        owner = by_name[p["bone"]]
        points = p["points"]
        n = len(points)
        segs = catmull_rom_to_bezier(points)
        lengths = bezier_arc_lengths(segs)

        # [hIn, anchor, hOut] per on-curve anchor (§16b). The first anchor's
        # hIn and the last anchor's hOut are unused end-handle placeholders
        # (the parser never reads them, contract §16b) — duplicate the anchor
        # point itself, the simplest valid filler.
        verts = []
        for j in range(n):
            pt = points[j]
            h_in = segs[j - 1][2] if j > 0 else pt
            h_out = segs[j][1] if j < n - 1 else pt
            verts.append(h_in)
            verts.append(pt)
            verts.append(h_out)
        vertex_count = 3 * n
        vertices = [_round(c) for v in verts for c in v]

        if p.get("weighted"):
            # v2 (§17): one anchor bone per on-curve point; each point group
            # [hIn, pt, hOut] rides its own anchor, so animating anchors BENDS
            # the curve. Anchors sit AT their point, rotated to the local
            # tangent (local +X = along the curve, +Y = the curve normal —
            # wave offsets are plain local translate keys).
            path_anchors = []
            weights = []
            bone_pose = []
            frames = path_anchor_frames(points)
            for j in range(n):
                aname = f"{name}_a{j}"
                if aname in bone_names:
                    raise ValueError(
                        f"path {name!r}: generated anchor bone {aname!r} collides with an existing bone")
                bone_names.add(aname)
                pt = points[j]
                rot = frames[j][0]
                gidx = anchor_offset + len(extra_bones)
                anchor_bone = {
                    "name": aname, "parent": owner["name"],
                    "x": pt[0], "y": pt[1], "rotation": rot, "length": 0,
                }
                extra_bones.append(anchor_bone)
                path_anchors.append(gidx)
                bone_pose.extend([gidx] + [_round(v) for v in world_bind_matrix(anchor_bone)])
            for j in range(n):
                for _ in range(3):
                    weights.extend([1, path_anchors[j], 1.0])
        else:
            anchor_name = f"{name}_anchor"
            if anchor_name in bone_names:
                raise ValueError(
                    f"path {name!r}: generated anchor bone {anchor_name!r} collides with an existing bone")
            bone_names.add(anchor_name)
            anchor_gidx = anchor_offset + len(extra_bones)
            anchor_bone = {
                "name": anchor_name, "parent": owner["name"],
                "x": owner["x"], "y": owner["y"], "rotation": owner["rotation"], "length": 0,
            }
            extra_bones.append(anchor_bone)
            weights = []
            for _ in range(vertex_count):
                weights.extend([1, anchor_gidx, 1.0])
            bone_pose = [anchor_gidx] + [_round(v) for v in world_bind_matrix(anchor_bone)]

        slots.append({"name": name, "parent": owner["name"]})
        display = {
            "type": "path", "name": name,
            "closed": bool(p.get("closed", False)),
            "constantSpeed": False,
            "lengths": lengths,
            "vertexCount": vertex_count,
            "vertices": vertices,
            "weights": weights,
            "slotPose": [1, 0, 0, 1, 0, 0],
            "bonePose": bone_pose,
        }
        skin_slots.append({"name": name, "display": [display]})

        constraints.append({
            "name": name, "target": name, "targetDisplay": name,
            "bones": list(p["chain"]),
            "positionMode": "percent", "spacingMode": "length",
            "rotateMode": p.get("rotateMode", "chain"),
            "position": p.get("position", 0), "spacing": p.get("spacing", 0),
            "rotateOffset": p.get("rotateOffset", 0),
            "rotateMix": p.get("rotateMix", 1), "translateMix": p.get("translateMix", 1),
        })
    return extra_bones, slots, skin_slots, constraints


def build_ske_multipiece(name, bones, built_pieces, anims, piece_tracks, subtex,
                         fps=FRAME_RATE, paths=None, ffd_timelines=None):
    """Multi-piece armature document.

    - `bones`: our absolute rest bones (same as the single-mesh path).
    - `built_pieces`: cut_pieces output in manifest (= draw) order; each has
      `type`, `img`+`offset`+`variants` (image/swap), `mesh_data`+`weights_data`
      (deform), or `mesh_data`+`variants` (morphframe — K unweighted mesh
      displays in one slot, contract §11+§18; each variant carries its own
      `name`/`img`/`offset`/`mesh_data`; morph_compiler.expand_morph).
    - `anims`: bone-side animations (pieces.split_tracks output).
    - `piece_tracks`: {anim_name: [piece display/alpha tracks]}.
    - `subtex`: SubTexture dicts from pack_atlas (region sizes for deform).
    - `ffd_timelines`: OPTIONAL morph_compiler.expand_morph `ffd_timelines`
      output — per-morphframe-piece vertex-deform keys. Emitted onto the
      SINGLE animation object (matches morph_compiler's own MVP constraint of
      exactly one animation whenever a morph piece exists; passing this with
      more than one animation is a caller error, rejected loud).

    Slot ARRAY ORDER is draw order (contract §9), back-to-front == manifest order.
    """
    by_name = {b["name"]: b for b in bones}
    regions = {s["name"]: s for s in subtex}
    root = bones[0]["name"]
    slots, skin_slots = [], []
    owner_by_piece = {}
    for p in built_pieces:
        # deform/morphframe pieces have no single owner bone (weighted meshes
        # are fixed by weights+bonePose; unweighted morphframe meshes are
        # authored in image space and converted into the owner's local frame
        # by _morph_display) → parent slot to root.
        owner = by_name[p["bone"]] if p.get("bone") else by_name[root]
        owner_by_piece[p["name"]] = owner
        slots.append({"name": p["name"], "parent": owner["name"]})
        if p["type"] == "deform":
            displays = [_deform_display(p, regions[p["name"]], bones)]
        elif p["type"] == "morphframe":
            # One slot, K unweighted mesh displays (contract §11+§18): display 0
            # is the piece itself, each variant is another key frame's mesh. The
            # stepped displayFrame track swaps between them; each display's own
            # ffd timeline deforms it (build_ffd_timelines).
            displays = [_morph_display(p, regions[p["name"]], owner)]
            for v in p["variants"]:
                displays.append(_morph_display(v, regions[v["name"]], owner))
        else:
            size = (p["img"].shape[1], p["img"].shape[0])
            displays = [_image_display(p["name"], p["offset"], size, owner)]
            for v in p["variants"]:
                vsize = (v["img"].shape[1], v["img"].shape[0])
                displays.append(_image_display(v["name"], v["offset"], vsize, owner))
        skin_slots.append({"name": p["name"], "display": displays})
    animations = []
    for aname, a in anims.items():
        anim_doc = build_animation(aname, a, fps)
        if not anim_doc["bone"]:
            del anim_doc["bone"]            # contract §14 / figure2: omit empty bone list
        ptracks = piece_tracks.get(aname, [])
        order_tracks = [t for t in ptracks if t["prop"] == "order"]
        slot_tls = build_slot_timelines(aname, [t for t in ptracks if t["prop"] != "order"],
                                        built_pieces, fps, anim_doc["duration"])
        if slot_tls:
            anim_doc["slot"] = slot_tls
        if order_tracks:
            zframes = build_zorder_frames(aname, order_tracks, built_pieces, fps,
                                          anim_doc["duration"])
            if any("zOrder" in f for f in zframes):
                anim_doc["zOrder"] = {"frame": zframes}
            # else: every offset is 0 at every change time → a timeline of only
            # restore-base frames is a no-op; omit it entirely.
        animations.append(anim_doc)
    if ffd_timelines:
        if len(animations) != 1:
            raise ValueError(
                f"ffd_timelines requires exactly one animation (matches "
                f"morph_compiler's MVP constraint); got {len(animations)}: "
                f"{[a['name'] for a in animations]}")
        anim_doc = animations[0]
        ffd_frames = build_ffd_timelines(anim_doc["name"], ffd_timelines, owner_by_piece,
                                         fps, anim_doc["duration"])
        if ffd_frames:
            anim_doc["ffd"] = ffd_frames
    all_bones = list(bones)
    constraints = []
    if paths:
        taken = {s["name"] for s in slots}
        extra_bones, path_slots, path_skin_slots, constraints = build_path_elements(
            paths, bones, taken_slot_names=taken)
        all_bones = all_bones + extra_bones
        slots = slots + path_slots
        skin_slots = skin_slots + path_skin_slots
    armature = {
        "type": "Armature", "frameRate": fps, "name": name,
        "bone": bones_to_db(all_bones, _path_chain_bones(paths)),
        "slot": slots,
        "skin": [{"slot": skin_slots}],
        "animation": animations,
    }
    if animations:
        armature["defaultActions"] = [{"gotoAndPlay": animations[0]["name"]}]
    if constraints:
        armature["path"] = constraints
    return {"frameRate": fps, "name": name,
            "version": DATA_VERSION, "compatibleVersion": DATA_VERSION,
            "armature": [armature]}


def build_tex_multi(name, atlas_w, atlas_h, png_name, subtex):
    """Multi-region atlas descriptor (contract §13). `subtex` = pack_atlas
    SubTexture dicts (untrimmed: name/x/y/width/height, no frame*)."""
    return {"width": atlas_w, "height": atlas_h, "name": name,
            "imagePath": png_name, "SubTexture": subtex}


def _run_or_fail(fn):
    """Run fn(), translating build/validation exceptions into the loud fail()
    exit. THE loud-failure contract for CLI entry points — main() and the
    Stage 1 orchestrator both inherit it from here (never copy-paste it)."""
    try:
        return fn()
    except (KeyError, TypeError) as e:
        fail(f"malformed input: {type(e).__name__}: {e}")
    except ValueError as e:
        fail(str(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--skeleton", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--animations", required=True)
    ap.add_argument("--texture", required=True)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", required=True, help="asset base name (<name>_ske.json …)")
    args = ap.parse_args()

    def body():
        mesh = load_json(args.mesh)
        bones, paths = assemble.load_skeleton(args.skeleton)
        weights = load_json(args.weights)
        anims = load_json(args.animations)
        w, h = Image.open(args.texture).size
        if mesh["imageSize"] != [w, h]:
            fail(f"mesh imageSize {mesh['imageSize']} != texture size [{w}, {h}]")

        # Same loud gate as assemble.py — DragonBones validates nothing.
        validate_bones(bones, w, h)
        validate_mesh(mesh)
        validate_weights(weights, len(mesh["vertices"]), bones)
        validate_paths(paths, bones, w, h)
        anims = wave_compiler.expand_wave_tracks(anims, paths, bones, fps=FRAME_RATE)
        validate_animations(anims, bones, paths=paths)

        ske = build_ske(args.name, mesh, bones, weights, anims, w, h, paths=paths)
        png_name = f"{args.name}_tex.png"
        tex = build_tex(args.name, w, h, png_name)

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.name}_ske.json").write_text(json.dumps(ske))
        (out / f"{args.name}_tex.json").write_text(json.dumps(tex))
        tex_dst = out / png_name
        if tex_dst.resolve() != Path(args.texture).resolve():
            shutil.copy(args.texture, tex_dst)
        print(f"dragonbones_writer: {len(bones)} bones, {len(mesh['vertices'])} vertices, "
              f"{len(mesh['triangles'])} tris, animations: {', '.join(anims)} "
              f"-> {out}/{args.name}_ske.json (+_tex.json,+_tex.png)")

    _run_or_fail(body)


if __name__ == "__main__":
    main()
