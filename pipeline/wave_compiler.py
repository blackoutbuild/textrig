"""Wave compiler (path v2): expand declarative `wave` tracks into ordinary
per-frame anchor-bone translate tracks at build time.

A wave track in animations.json:

    { "path": "hair_flow", "prop": "wave",
      "amplitude": 15,            # px, peak offset — REQUIRED, > 0
      "period": 1.5,              # seconds per cycle — REQUIRED, > 0
      "wavelength": 1.0,          # fraction of curve arc length, > 0
      "phase": 0.0,               # fraction of a cycle, [0..1)
      "direction": "root-to-tip", # | "tip-to-root"
      "envelope": "linear",       # | "flat" | [[u, gain], ...]
      "axis": "normal" }          # | "tangent"

Anchor j of the path (`<path>_a{j}`, contract §17) at normalized rest arc
position u_j gets, per output frame at time t:

    offset_j(t) = amplitude * env(u_j) * sin(2*pi*(t/period - s*u_j/wavelength + phase))
    s = +1 root-to-tip, -1 tip-to-root

The offset points along the curve's local normal (`axis:"normal"`) or tangent
(`axis:"tangent"`) at the point — a WORLD direction. DragonBones translate
timelines add offsets in the bone's PARENT space, and every anchor's parent is
the path's owner bone, so the world offset vector is rotated into the owner's
frame and emitted as a paired x+y track per anchor (shared key grid, linear
ease — dense per-frame sampling reproduces the exact waveform).

Expansion runs BEFORE assemble.validate_animations: downstream validators and
timeline emission see only ordinary bone tracks. Anchor bones remain directly
keyable by hand — this compiler is sugar, not a cage; a wave track colliding
with hand keys on the same anchor is caught by the duplicate-track check
after expansion.
"""
import math

from assemble import fail
from path_math import path_anchor_frames

_WAVE_KEYS = {"path", "prop", "amplitude", "period", "wavelength", "phase",
              "direction", "envelope", "axis"}
_DIRECTIONS = {"root-to-tip": 1.0, "tip-to-root": -1.0}
_AXES = {"normal", "tangent"}


def _env_fn(env, where):
    if env == "linear":
        return lambda u: u
    if env == "flat":
        return lambda u: 1.0
    if isinstance(env, list) and env:
        pts = []
        for bp in env:
            if (not isinstance(bp, list) or len(bp) != 2
                    or any(not isinstance(c, (int, float)) or not math.isfinite(c) for c in bp)):
                fail(f"{where}: malformed envelope breakpoint {bp!r} (want [u, gain])")
            pts.append((float(bp[0]), float(bp[1])))
        us = [u for u, _ in pts]
        if any(u2 <= u1 for u1, u2 in zip(us, us[1:])):
            fail(f"{where}: envelope u values must be strictly increasing")
        if us[0] < 0 or us[-1] > 1:
            fail(f"{where}: envelope u values must lie in [0, 1]")

        def interp(u):
            if u <= pts[0][0]:
                return pts[0][1]
            if u >= pts[-1][0]:
                return pts[-1][1]
            for (u1, g1), (u2, g2) in zip(pts, pts[1:]):
                if u <= u2:
                    return g1 + (g2 - g1) * (u - u1) / (u2 - u1)
            return pts[-1][1]
        return interp
    fail(f"{where}: envelope must be \"linear\", \"flat\" or a [[u, gain], ...] list")


def _positive(tr, fld, where, required=False, default=None):
    if fld not in tr:
        if required:
            fail(f"{where}: missing required knob {fld!r}")
        return default
    v = tr[fld]
    if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        fail(f"{where}: {fld} must be a finite number > 0 (got {v!r})")
    return float(v)


def _expand_one(aname, anim, tr, paths_by_name, bones_by_name, fps):
    where = f"{aname}: wave track on path {tr.get('path')!r}"
    unknown = set(tr) - _WAVE_KEYS
    if unknown:
        fail(f"{where}: unknown keys {sorted(unknown)}")
    p = paths_by_name.get(tr.get("path"))
    if p is None:
        fail(f"{where}: no such path in skeleton.json `paths`")
    if not p.get("weighted"):
        fail(f"{where}: path is not weighted — a wave needs one anchor bone per "
             f"curve point to bend the curve; set \"weighted\": true on the path "
             f"(contract §17)")

    amplitude = _positive(tr, "amplitude", where, required=True)
    period = _positive(tr, "period", where, required=True)
    wavelength = _positive(tr, "wavelength", where, default=1.0)
    phase = tr.get("phase", 0.0)
    if not isinstance(phase, (int, float)) or not math.isfinite(phase) or not (0 <= phase < 1):
        fail(f"{where}: phase must be a fraction of a cycle in [0, 1) (got {phase!r})")
    direction = tr.get("direction", "root-to-tip")
    if direction not in _DIRECTIONS:
        fail(f"{where}: direction must be one of {sorted(_DIRECTIONS)}")
    axis = tr.get("axis", "normal")
    if axis not in _AXES:
        fail(f"{where}: axis must be one of {sorted(_AXES)}")
    env = _env_fn(tr.get("envelope", "linear"), where)
    s = _DIRECTIONS[direction]

    duration = anim["duration"]
    total_frames = duration * fps
    if abs(total_frames - round(total_frames)) > 1e-6:
        fail(f"{where}: duration {duration}s does not land on the {fps}fps frame "
             f"grid — the wave is sampled per frame; use a duration that is a "
             f"whole number of frames")
    total_frames = round(total_frames)
    if anim.get("loop", True):
        cycles = duration / period
        if abs(cycles - round(cycles)) > 1e-6 or round(cycles) < 1:
            k = max(1, round(cycles))
            near = sorted({duration / k for k in {max(1, k - 1), k, k + 1}}, reverse=True)
            fail(f"{where}: a looping animation needs a whole number of wave "
                 f"cycles in its duration (duration {duration}s / period {period}s "
                 f"= {cycles:.4f}); nearest seamless periods: "
                 f"{', '.join(f'{p:.6g}s' for p in near)}")

    owner = bones_by_name[p["bone"]]
    owner_rad = math.radians(owner["rotation"])
    cos_r, sin_r = math.cos(owner_rad), math.sin(owner_rad)
    ts = [i / fps for i in range(total_frames + 1)]
    tracks = []
    for j, (tan_deg, u) in enumerate(path_anchor_frames(p["points"])):
        tan = math.radians(tan_deg)
        if axis == "normal":
            wx, wy = -math.sin(tan), math.cos(tan)     # world: tangent rotated +90°
        else:
            wx, wy = math.cos(tan), math.sin(tan)      # world: along the curve
        # world direction -> owner-local (anchor's parent space): rotate by -owner
        dx, dy = cos_r * wx + sin_r * wy, -sin_r * wx + cos_r * wy
        gain = amplitude * env(u)
        offs = [gain * math.sin(2 * math.pi * (t / period - s * u / wavelength + phase))
                for t in ts]
        bone = f"{p['name']}_a{j}"
        tracks.append({"bone": bone, "prop": "x",
                       "keys": [{"t": t, "v": o * dx} for t, o in zip(ts, offs)]})
        tracks.append({"bone": bone, "prop": "y",
                       "keys": [{"t": t, "v": o * dy} for t, o in zip(ts, offs)]})
    return tracks


def expand_wave_tracks(anims, paths, bones, fps=30):
    """Return `anims` with every `{"path": ..., "prop": "wave"}` track replaced
    by per-frame x+y tracks on the path's generated anchor bones. Animations
    without wave tracks pass through untouched. All misuse fails loud."""
    paths_by_name = {p["name"]: p for p in (paths or [])}
    bones_by_name = {b["name"]: b for b in bones}
    out = {}
    for aname, anim in anims.items():
        tracks = anim.get("tracks", [])
        if not any("path" in tr or tr.get("prop") == "wave" for tr in tracks):
            out[aname] = anim
            continue
        new_tracks = []
        for tr in tracks:
            if "path" not in tr and tr.get("prop") != "wave":
                new_tracks.append(tr)
                continue
            if tr.get("prop") != "wave":
                fail(f"{aname}: track has a \"path\" key but prop "
                     f"{tr.get('prop')!r} — the only path-level track is prop "
                     f"\"wave\"")
            if "path" not in tr:
                fail(f"{aname}: prop \"wave\" track needs a \"path\" key naming "
                     f"a weighted path from skeleton.json")
            new_tracks.extend(_expand_one(aname, anim, tr, paths_by_name,
                                          bones_by_name, fps))
        out[aname] = dict(anim, tracks=new_tracks)
    return out
