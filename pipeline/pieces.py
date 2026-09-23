"""pieces.json manifest: load, validate loud, normalize. Piece tracks for animations."""
import json
import math
import sys
from pathlib import Path

PIECE_TYPES = {"rigid", "swap", "deform", "morph"}
PIECE_PROPS = {"display", "alpha", "order"}
MORPH_LOOPS = {"pingpong", "forward"}
# `bone` is ALLOWED on a morph piece (optional owner bone the slot parents to
# and the synthesized global-motion track rides; defaults to root). `bones`
# (deform weight recipients) stays forbidden — a morphframe mesh is unweighted.
MORPH_FORBIDDEN_FIELDS = {"polygon", "fill", "bones", "variants"}


def fail(msg: str) -> None:
    print(f"pieces: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _check_offset(off, ctx):
    if (not isinstance(off, list) or len(off) != 2
            or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in off)):
        fail(f"{ctx}: offset must be [x, y] finite numbers")
    if any(v < 0 for v in off):
        fail(f"{ctx}: offset {off} has negative component — layer offsets are "
             f"canvas-pixel positions and must be >= 0")


def _check_mesh(mesh, name):
    if not isinstance(mesh, dict):
        fail(f"piece {name}: mesh must be an object with cols/power knobs, "
             f"got {mesh!r}")
    unknown = set(mesh) - {"cols", "power"}
    if unknown:
        fail(f"piece {name}: unknown mesh knob(s) {sorted(unknown)} "
             f"(want cols|power)")
    cols = mesh.get("cols")
    if cols is not None and (isinstance(cols, bool)
                             or not isinstance(cols, int) or cols <= 0):
        fail(f"piece {name}: mesh.cols must be a positive integer, got {cols!r}")
    power = mesh.get("power")
    if power is not None and (isinstance(power, bool)
                              or not isinstance(power, (int, float))
                              or not math.isfinite(power) or power <= 0):
        fail(f"piece {name}: mesh.power must be a positive number, got {power!r}")


def _validate_morph(p, name, base_dir):
    """Validate+normalize a morph piece in place: resolves frame file paths to
    entry['path'], enforces t-monotonic/key-frame/loop rules. Does not touch
    cutting-only fields (rejected by the caller)."""
    forbidden = MORPH_FORBIDDEN_FIELDS & set(p)
    if forbidden:
        fail(f"piece {name}: {sorted(forbidden)} not allowed on morph pieces "
             f"(cutting-only fields)")
    loop = p.get("loop", "pingpong")
    if loop not in MORPH_LOOPS:
        fail(f"piece {name}: loop must be one of {sorted(MORPH_LOOPS)}, got {loop!r}")
    _check_mesh(p.get("mesh", {}), name)
    frames = p.get("frames")
    if not isinstance(frames, list) or not frames:
        fail(f"piece {name}: morph pieces need a non-empty frames[] list")
    prev_t = None
    key_count = 0
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict):
            fail(f"piece {name}: frame {i} must be an object")
        file = fr.get("file")
        if not file or not isinstance(file, str):
            fail(f"piece {name}: frame {i} missing/empty file")
        fp = base_dir / file
        if not fp.is_file():
            fail(f"piece {name}: frame {i} file not found: {fp}")
        t = fr.get("t")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            fail(f"piece {name}: frame {i} t must be a finite number, got {t!r}")
        if prev_t is not None and t <= prev_t:
            fail(f"piece {name}: frame {i} t={t} not strictly increasing "
                 f"after {prev_t} — frames must be time-ordered")
        prev_t = t
        key = fr.get("key")
        if not isinstance(key, bool):
            fail(f"piece {name}: frame {i} key must be true/false, got {key!r}")
        if key:
            key_count += 1
        fr["path"] = str(fp)
    if key_count < 2:
        fail(f"piece {name}: morph needs at least 2 key frames (got {key_count}) "
             f"— key frames own segments and cuts happen between them")
    if not frames[0].get("key"):
        fail(f"piece {name}: first frame must be a key frame (defines the loop start)")
    if not frames[-1].get("key"):
        fail(f"piece {name}: last frame must be a key frame (defines the loop end)")
    return loop


def _check_source(src, name, base_dir, image_size):
    w, h = image_size
    if not isinstance(src, dict) or len(set(src) & {"polygon", "file"}) != 1:
        fail(f"piece {name}: source must have exactly one of polygon|file")
    if "polygon" in src:
        poly = src["polygon"]
        if not isinstance(poly, list) or len(poly) < 3:
            fail(f"piece {name}: polygon needs >= 3 points")
        for pt in poly:
            if (not isinstance(pt, list) or len(pt) != 2
                    or any(not math.isfinite(c) for c in pt)):
                fail(f"piece {name}: bad polygon point {pt}")
            if not (0 <= pt[0] <= w and 0 <= pt[1] <= h):
                fail(f"piece {name}: polygon point {pt} outside image {w}x{h} "
                     f"(fix the polygon coordinates)")
    else:
        if not (base_dir / src["file"]).is_file():
            fail(f"piece {name}: source file not found: {base_dir / src['file']}")
        _check_offset(src.get("offset"), f"piece {name} source")


def load_pieces(path, bones, image_size, deform_allowed=True):
    """Parse + validate pieces.json. Returns normalized piece list (fill default
    applied, variants normalized). File paths are validated relative to the
    manifest's directory; loading the actual images is cut_pieces' job."""
    path = Path(path)
    base_dir = path.parent
    with open(path) as f:
        doc = json.load(f, parse_constant=lambda c: fail(f"non-finite number {c} in {path}"))
    if doc.get("version") != 1:
        fail(f"unsupported pieces.json version {doc.get('version')!r} (want 1)")
    pieces = doc.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        fail("pieces.json: empty or missing pieces[]")
    bone_names = {b["name"] for b in bones}
    skin_bones = {b["name"] for b in bones if b.get("skin", True)}
    seen = set()
    out = []
    for p in pieces:
        name = p.get("name")
        if not name or not isinstance(name, str):
            fail("piece with missing/empty name")
        if name in seen:
            fail(f"duplicate piece name {name!r}")
        seen.add(name)
        ptype = p.get("type")
        if ptype not in PIECE_TYPES:
            fail(f"piece {name}: bad type {ptype!r} (want rigid|swap|deform|morph)")
        if ptype == "deform" and not deform_allowed:
            fail(f"piece {name}: deform pieces are phase 2 — not enabled yet")
        if ptype == "morph" and not deform_allowed:
            fail(f"piece {name}: morph pieces are phase 2 — not enabled yet")
        if ptype == "morph":
            loop = _validate_morph(p, name, base_dir)
            if "bone" in p and p["bone"] not in bone_names:
                fail(f"piece {name}: unknown owner bone {p['bone']!r}")
            out.append({**p, "loop": loop, "base_dir": str(base_dir)})
            continue
        if ptype in ("rigid", "swap"):
            if p.get("bone") not in bone_names:
                fail(f"piece {name}: unknown or missing owner bone {p.get('bone')!r}")
            if "bones" in p or "mesh" in p:
                fail(f"piece {name}: bones/mesh knobs are deform-only")
        else:  # deform
            if "bone" in p:
                fail(f"piece {name}: deform pieces use 'bones' (weight recipients), not 'bone'")
            for b in p.get("bones", []):
                if b not in skin_bones:
                    fail(f"piece {name}: weight bone {b!r} not a known skin bone")
            _check_mesh(p.get("mesh", {}), name)
        _check_source(p.get("source"), name, base_dir, image_size)
        variants = p.get("variants", [])
        if variants and ptype != "swap":
            fail(f"piece {name}: variants are swap-only")
        for v in variants:
            vn = v.get("name")
            if not vn or not isinstance(vn, str):
                fail(f"piece {name}: variant with missing/empty name")
            if vn in seen:
                fail(f"piece {name}: variant name {vn!r} collides with another piece/variant "
                     f"name — atlas SubTexture names must be globally unique")
            seen.add(vn)
            if not (base_dir / v.get("file", "")).is_file():
                fail(f"piece {name}: variant {vn}: file not found: {base_dir / v.get('file', '')}")
            _check_offset(v.get("offset"), f"piece {name} variant {vn}")
        fill = p.get("fill", "extend")
        if fill not in ("extend", "none"):
            fail(f"piece {name}: fill must be extend|none")
        out.append({**p, "fill": fill, "variants": variants, "base_dir": str(base_dir)})
    return out


def split_tracks(anims):
    """Split animations into (bone_anims, piece_tracks). bone_anims keeps the
    original shape (feedable to assemble.validate_animations); piece_tracks is
    {anim_name: [piece tracks]}."""
    bone_anims, piece_tracks = {}, {}
    for aname, a in anims.items():
        bt = [t for t in a["tracks"] if "bone" in t]
        pt = [t for t in a["tracks"] if "piece" in t]
        for t in a["tracks"]:
            if ("bone" in t) == ("piece" in t):
                fail(f"{aname}: track must have exactly one of bone|piece: {t}")
        bone_anims[aname] = {**a, "tracks": bt}
        piece_tracks[aname] = pt
    return bone_anims, piece_tracks


def validate_piece_tracks(anims, pieces):
    by_name = {p["name"]: p for p in pieces}
    _, piece_tracks = split_tracks(anims)
    for aname, tracks in piece_tracks.items():
        dur = anims[aname]["duration"]
        loop = anims[aname].get("loop", True)
        seen = set()
        for tr in tracks:
            pname, prop = tr["piece"], tr["prop"]
            if pname not in by_name:
                fail(f"{aname}: unknown piece {pname!r}")
            if prop not in PIECE_PROPS:
                fail(f"{aname}: bad piece prop {prop!r} (want display|alpha|order)")
            if (pname, prop) in seen:
                fail(f"{aname}: duplicate track {pname}.{prop}")
            seen.add((pname, prop))
            keys = tr["keys"]
            if not keys:
                fail(f"{aname}: empty keys on {pname}.{prop}")
            ts = [k["t"] for k in keys]
            if any(t2 <= t1 for t1, t2 in zip(ts, ts[1:])):
                fail(f"{aname}: key times not strictly increasing on {pname}.{prop}")
            # The DragonBones writer requires a key at t=0 for all piece props
            # (per-frame durations must sum from frame 0), and additionally a
            # loop-closing key at t=duration for alpha (colorFrame terminal frame).
            # Enforce here with friendly messages so bad input dies in validation.
            if ts[0] != 0:
                fail(f"{aname}: {pname}.{prop} needs a key at t=0 (the animation "
                     f"start) — add a keyframe at t=0")
            if ts[-1] > dur:
                fail(f"{aname}: key time {ts[-1]} past duration {dur} on {pname}.{prop}")
            if prop == "display":
                piece = by_name[pname]
                if piece["type"] not in ("swap", "morphframe"):
                    fail(f"{aname}: display track on non-swap piece {pname}")
                valid = {pname} | {v["name"] for v in piece.get("variants", [])}
                for k in keys:
                    if k.get("ease"):
                        fail(f"{aname}: display keys are stepped — no ease allowed ({pname})")
                    if k["v"] not in valid:
                        fail(f"{aname}: display value {k['v']!r} not in {sorted(valid)} ({pname})")
                if loop and keys[-1]["v"] != keys[0]["v"]:
                    fail(f"{aname}: loop anim: last display value {keys[-1]['v']!r} != "
                         f"first value {keys[0]['v']!r} — visible pop at wrap ({pname})")
            elif prop == "order":
                for k in keys:
                    if k.get("ease"):
                        fail(f"{aname}: order keys are stepped — no ease allowed ({pname})")
                    v = k["v"]
                    if isinstance(v, bool) or not isinstance(v, int):
                        fail(f"{aname}: order value {v!r} must be an integer draw-order shift ({pname})")
                    if abs(v) >= len(pieces):
                        fail(f"{aname}: order shift {v} out of range — |shift| must be < "
                             f"{len(pieces)} (piece count) ({pname})")
                if loop and keys[-1]["v"] != keys[0]["v"]:
                    fail(f"{aname}: loop anim: last order value {keys[-1]['v']} != first "
                         f"{keys[0]['v']} — depth pop at wrap ({pname})")
            else:  # alpha
                if ts[-1] != dur:
                    fail(f"{aname}: {pname}.alpha needs a loop-closing key at "
                         f"t=duration ({dur}) — add a keyframe at t={dur}")
                for k in keys:
                    if not (0.0 <= k["v"] <= 1.0):
                        fail(f"{aname}: alpha {k['v']} out of [0,1] on {pname}")
