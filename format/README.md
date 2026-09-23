# TextRig format v1 (`*.rig.json`)

Coordinates: image pixels, origin top-left, **y down**. Rotation: degrees,
positive = clockwise on screen. A bone points from `(x, y)` along its
rotation for `length` px; rotation `-90` points up.

```jsonc
{
  "version": 1,
  "texture": "duck.png",              // file next to the rig json
  "imageSize": [1024, 1024],
  "meshes": [{                        // v1: always exactly one mesh
    "vertices": [[x, y], ...],        // rest pose, image pixels
    "uvs": [[u, v], ...],             // 0..1
    "triangles": [[a, b, c], ...],    // indices into vertices
    "weights": [                      // one entry per vertex
      { "bones": [1, 2], "w": [0.7, 0.3] }   // ≤4 influences, w sums to 1
    ]
  }],
  "bones": [                          // ordered parent-first; exactly one root
    { "name": "root", "parent": null, "x": 512, "y": 900,
      "rotation": -90, "length": 0, "skin": false }
    // "skin": false → bone never receives weights (default true)
  ],
  "animations": {
    "idle": {
      "duration": 3.0, "loop": true,
      "tracks": [{
        "bone": "head",
        "prop": "rotation",           // rotation | x | y | scaleX | scaleY | alpha
        "keys": [{ "t": 0.0, "v": -6, "ease": "sineInOut" }, { "t": 3.0, "v": -6 }]
      }]
    }
  }
}
```

Semantics:
- Bones store the **absolute** rest pose. Runtimes derive parent-local
  transforms at load time.
- Track values: rotation/x/y are **deltas added to rest** (neutral 0);
  scaleX/scaleY are **absolute factors** (neutral 1 — e.g. `1.03` = +3%,
  and `0.03` would collapse the bone to 3%). x/y deltas act in parent-bone
  space. Local compose order per bone:
  `T(restX+dx, restY+dy) · R(restRot+drot) · S(sx, sy)`.
- `ease` on a key shapes the segment **to the next key**. Allowed:
  `linear, sineIn, sineOut, sineInOut, quadIn, quadOut, quadInOut, backOut`.
  Missing ease = linear. Time before first key / after last key holds that
  key's value; for seamless loops put a final key at `t = duration`
  **whose value equals the first key's value** (during looped playback
  t=duration wraps to 0, the final key only shapes the approach).
- `alpha` tracks are allowed on the root bone only and set whole-mesh alpha.
- Skinning: linear blend. `v' = Σ wᵢ · (Worldᵢ · InvRestWorldᵢ) · v`.

## Piece tracks (multi-piece rigs, `pieces.json`)

Coverage note: opaque source pixels covered by NO piece polygon are absorbed
by the nearest piece at build (loud `seams: WARNING`, components ≥32px²), and
every polygon piece dives `--underlap` px (default 30) under the pieces drawn
above it, diffusion-filled — edge-to-edge cut boundaries neither drop pixels
nor open holes in motion.

Multi-piece builds (`--pieces`) additionally allow piece tracks in
`animations.json` — a track has exactly one of `bone`|`piece`, never both:

```jsonc
{
  "piece": "arm_r",
  "prop": "order",                  // display | alpha | order
  "keys": [{ "t": 0.0, "v": 0 }, { "t": 0.5, "v": 1 }, { "t": 1.6, "v": 0 }]
}
```

- `display` — `swap` pieces only. `v` is a variant name (or the piece's own
  name for the base image). Stepped, no `ease`; loop anims need the last
  value equal to the first (validated).
- `alpha` — piece opacity, `v` in `[0, 1]`. Tweened: `ease` allowed on keys
  (default linear). Requires a loop-closing key at `t = duration` (validated);
  its value equaling the first key's is the author's responsibility — the
  validator does not check it.
- `order` — integer draw-order shift relative to the piece's position in
  `pieces.json` (0 = base, positive = toward viewer). Stepped, no `ease`;
  loop anims need the last value equal to the first (validated).
  `|shift|` must be `< piece count` AND the shifted position must stay within
  the piece list (writer fails loud otherwise — "out-of-range position").
- **DragonBones target only.** The legacy own-runtime (`assemble.py`, the
  `*.rig.json` above) is single-mesh and never sees piece tracks.

## Path constraints (skeleton.json `paths` section)

`skeleton.json` may be a bare bone array (as above) — or an object
`{"bones": [...], "paths": [...]}`. `bones` is the same array either way;
`paths` is an optional list, each entry a bone chain that flows along an
authored curve instead of pivoting on a single joint (hair locks, tails,
ribbons):

```jsonc
{
  "bones": [ /* ... */ ],
  "paths": [{
    "name": "hair_flow",                   // unique; no collision with a bone/slot name
    "bone": "head",                        // owner bone — carries the curve
    "points": [[695, 200], [740, 295], [778, 375], [800, 430]],
    "chain": ["hair1", "hair2", "hair3"]   // bones that ride the curve, parent-first off the owner
  }]
}
```

- `points` are **on-curve anchors**, not bezier handles — 2 or more
  ordered `[x, y]` pairs in image pixels, each inside the image bounds,
  with no two consecutive points coinciding. Handles are derived
  automatically (Catmull-Rom through the anchors, converted to cubic
  bezier segments) — there is no handle field to author.
- **Constraint parameters are static.** The port has no PathConstraint
  keyframe timeline (contract §16c) — the curve itself never animates
  directly. Motion comes from animating the `bone` field (the owner):
  rotate or move it and the curve, and everything riding it, follows.
- **`"weighted": true` (optional, default false) makes the curve BENDABLE**
  (contract §17): the writer generates one anchor bone per `points` entry —
  `<name>_a0` … `<name>_a{N-1}`, children of the owner, each resting AT its
  point, rotated to the local curve tangent — and the curve's geometry rides
  them. Animating the anchors bends the curve; the chain follows the bent
  shape with no joint separation (`rotateMode:"chain"` keeps bones
  head-to-tail). Two ways to animate them:
  - the `wave` track in animations.json (see "Wave tracks" below) —
    compiled into anchor keys at build time;
  - ordinary bone tracks on the anchor names directly (`x`/`y`/`rotation`
    deltas; NOTE x/y offsets live in the OWNER bone's frame — DragonBones
    translate timelines add in parent space, and every anchor's parent is
    the owner).
  A non-weighted path stays byte-identical to v1 output.
- **`chain` bones need a real `length`.** `spacingMode:"length"` (the
  writer's fixed choice) places each chain bone one bone-length further
  along the curve — it consumes each bone's own `length` as its spacing
  (contract §16d). A chain bone with `length` ≤ 0 is rejected loud
  (`validate_paths`); give each one roughly its true rest span.
- `rotateMode` defaults to `"chain"` (each bone's rotation follows the
  local curve bearing — the verified-correct mode). `"tangent"` is
  accepted but is a known port bug in this runtime — the angle collapses
  to a constant per segment, unrelated to curve direction; `"chainscale"`
  is unproven. All three remain valid values to set.
- `closed` is not supported in v1 — `closed: true` is rejected loud (open
  curves only).
- Unknown keys in a path entry, an unknown owner `bone`, an unknown or
  duplicate `chain` bone, fewer than 2 `points`, or an out-of-bounds point
  are all rejected loud by `validate_paths` before any build work happens.
- **DragonBones target only.** The legacy own-runtime target (`assemble.py`,
  the `*.rig.json` format above) has no path solver — a skeleton with a
  `paths` section fails that target loud; build with `--target db`.
- Deform pieces weighted onto chain bones are not a proven combination in
  this runtime (produced NaN geometry in testing) — the proven pattern is
  one **rigid** piece per chain bone.

## Wave tracks (animations.json, weighted paths only)

A traveling wave along a weighted path is authored declaratively — one
track keyed by `path` instead of `bone`/`piece`:

```jsonc
{ "path": "hair_flow", "prop": "wave",
  "amplitude": 12,            // px, peak offset — required, > 0
  "period": 1.5,              // seconds per cycle — required, > 0
  "wavelength": 1.0,          // fraction of curve arc length (1.0 = one full
                              // wave over the whole curve), default 1.0
  "phase": 0.0,               // fraction of a cycle, [0..1), default 0
  "direction": "root-to-tip", // | "tip-to-root", default root-to-tip
  "envelope": "linear",       // | "flat" | [[u, gain], ...], default linear
  "axis": "normal" }          // | "tangent", default normal
```

The build expands it into per-frame `x`/`y` keys on the path's anchor
bones (`wave_compiler.py`): anchor j at normalized rest arc position `u`
gets `amplitude · env(u) · sin(2π(t/period − s·u/wavelength + phase))`
along the curve's local normal (`axis:"normal"`) or tangent
(`"tangent"`). Semantics of the knobs:

- `envelope` scales amplitude along the curve: `"linear"` = 0 at the root
  growing to 1 at the tip (the root stays pinned — hair does not detach
  from the head); `"flat"` = full amplitude everywhere; a breakpoint list
  `[[u, gain], ...]` (u ascending in [0,1]) is interpolated linearly and
  clamped at the ends.
- `direction` sets which way crests travel; `phase` shifts where the wave
  starts in its cycle (useful to de-sync two paths).
- Loud guards: a wave on a missing or non-weighted path; non-positive
  `amplitude`/`period`/`wavelength`; a looping animation whose `duration`
  is not a whole number of `period`s (breaks the seamless loop; the error
  suggests the nearest valid periods); a `duration` that is not a whole
  number of frames at the build fps.
- Hand-authored tracks on the same anchor bones remain legal (the compiler
  is sugar, not a cage); a wave colliding with hand keys on the same
  anchor/prop is rejected as a duplicate track after expansion.
- Bone spacing along a BENT curve is rest-length-approximate: the solver
  maps arc positions against the authored rest lengths (contract §17b) —
  negligible at wave amplitudes small relative to the curve length.

Typical use: a weighted `hair_flow` path plus a `flow` animation = head
sway + hair ripple.

## Morph pieces (`type: "morph"`, `pieces.json`)

A morph piece turns a run of already-prepared keyframe PNGs (video-derived
or otherwise) into per-vertex FFD deform timelines driven by optical flow —
the video→rig conveyor's answer to bone-LBS melt/tear on fast, large-travel
motion (see `docs/dragonbones-format-contract.md` §18). Coexists
with rigid/swap/deform in one manifest:

```jsonc
{ "name": "body", "type": "morph",
  "mesh": { "cols": 14 },
  "loop": "pingpong",              // or "forward" (loop closed by the frames themselves)
  "frames": [
    { "file": "frames/q01.png", "t": 0.0,   "key": true },
    { "file": "frames/g01.png", "t": 0.083, "key": false },   // guide frame
    { "file": "frames/q02.png", "t": 0.25,  "key": true },
    // ...
  ] }
```

- **Frames are files the agent prepares, not raw video.** Each `file` is an
  already-extracted PNG, aligned to one common canvas (same size/origin
  across the whole `frames` list) with alpha already keyed (rembg or
  equivalent). Slicing a video into these frames — and picking which ones
  are keys vs guides — is the agent's job (extract by source frame number, e.g. ffmpeg
  `select='eq(n,N)'`, never by an fps filter); automating that selection is
  out of scope here.
- `key: true` — **keyframes**: go into the atlas and own their own mesh
  segment; cuts happen between consecutive keyframes. The first and last
  keyframes define the loop.
- `key: false` — **guide frames**: feed the optical-flow chain in short
  steps between two keyframes (several short hops track motion far more
  reliably than one long jump) and carry **zero weight** otherwise — never
  shipped into the atlas, no mesh/segment of their own.
- `t` values are seconds on the single animation timeline the morph
  participates in (see the MVP constraint below); a `"pingpong"` loop
  mirrors them around the last key.
- `loop`:
  - `"pingpong"` — plays forward through the keys, then back. Requires the
    animation's `duration` to equal `2 × (last key's t)`.
  - `"forward"` — the frames themselves close the loop (first key frame ≈
    last key frame). Requires `duration >= last key's t`; a validator warns
    (does not block the build) if the first/last frames differ beyond a
    threshold.
- **Single-animation MVP constraint.** A manifest containing a morph piece
  may define **exactly one** animation in `animations.json` — the morph
  piece's synthesized timelines go there. A second animation is a **loud
  build error**; lifting this constraint is out of scope for this feature.
- **A morph piece needs no tracks of its own in `animations.json`.** The
  compiler synthesizes everything from the `frames` list: per-vertex FFD
  deform timelines (a two-sided morph — each pair of neighboring keyframes
  travels toward a shared midpoint shape computed from forward+backward
  optical flow, so the two never diverge) plus the stepped-alpha cuts that
  swap between keyframe meshes at each midpoint. Other pieces in the same
  manifest (rigid props, etc.) keep authoring normal tracks as usual;
  hybrid rigid+morph rigs are mechanically supported but have no worked
  example yet.
- **DragonBones target only** (`--target db`) — like piece tracks and path
  constraints above, the legacy own-runtime target (`assemble.py`, the
  `*.rig.json` format) has no FFD deform timeline support; a manifest with
  a morph piece fails that target loud.
