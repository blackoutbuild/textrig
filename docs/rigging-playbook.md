# Rigging playbook — author a rig for a fresh PNG

You are about to author `skeleton.json` + `pieces.json` + `animations.json` for
an image you have never seen, then drive it through the self-check loop. This is
the distilled markup knowledge from the robot and duck reference rigs; where a
rule came from a concrete case, that case is named in parentheses. Read it
once, then start looking.

Conventions everywhere: image pixels, origin top-left, **y down**, rotation in
degrees positive = clockwise, **-90 = up**. Joints INSIDE the silhouette. Format semantics live in `format/README.md`; the loop protocol lives
in `docs/inspection-checklist.md`.

---

## 1. Look first

Before authoring anything, render a coordinate grid over the source so you can
read polygon coordinates straight off the image:

```sh
pipeline/.venv/bin/python -c "from PIL import Image, ImageDraw; im=Image.open('SRC.png').convert('RGBA'); d=ImageDraw.Draw(im); [d.line([(x,0),(x,im.height)], fill=(255,0,0,128)) or d.text((x+2,2),str(x),fill=(255,0,0,255)) for x in range(0,im.width,100)]; [d.line([(0,y),(im.width,y)], fill=(255,0,0,128)) or d.text((2,y+2),str(y),fill=(255,0,0,255)) for y in range(0,im.height,100)]; im.save('SRC.grid.png')"
```

Read `SRC.grid.png` and inventory, in words, before touching JSON:

- **Silhouette** — the full opaque outline; note where it ends (pixels owned by
  no piece will drop from the render — see §2).
- **Moving parts** — limbs, head, tail, jaw: anything that should articulate.
- **Static base** — feet/treads/pedestal that must stay planted (checklist #1).
- **See-through gaps** — holes in the silhouette that show background or another
  part through them (claw-finger gaps on the robot). These are traps — §2.
- **Baked effects** — glow, rim light, motion blur already painted into the flat
  image. A glow that bleeds through a gap becomes a moving artifact (§2); a rim
  on un-owned pixels is the loss you knowingly accept.

---

## 2. Polygon heuristics (the hard-won ones)

- **Cut along anatomical / mechanical necks** — shoulder joints, hips, the base
  of the neck, wrist, jaw hinge. The cut line is where two parts pivot relative
  to each other (robot arm at shoulder, leg at hip).
- **Joints inside the silhouette.** A bone's origin must sit on opaque pixels of
  the part it drives.
- **Overlap pieces 2–4px at the joint.** The cut is `polygon ∧ alpha`, so a
  small overlap costs nothing and prevents a gap opening at the seam under
  rotation (robot pauldrons keep torso overlap through the swing,
  checklist #2).
- **DO NOT enclose a see-through gap that shows background or another piece.**
  Polygon cutting assigns those pixels to your piece, so when it moves they
  travel with it and double / smear against the backdrop — the robot claw gaps
  baked body-glow into small green discs that swam near the palms at sway
  extremes). Route the polygon boundary along the solid
  material and leave the gap OUT; accept a static hole rather than a moving
  artifact.
- **Hug your OWN visible silhouette — never claim a neighbor's territory
  across a gap.** If piece B (an arm) hangs BESIDE piece A (the torso) with
  background between them, A's polygon must stop at A's visible edge, NOT
  sweep under B "to be safe". Every pixel A claims under B must be invented
  by occlusion fill, and no fill can invent a limb-sized area of never-drawn
  art — the robot's original body polygon claimed the whole hanging arm's
  footprint and a `wave` that lifted the arm revealed an arm-shaped ghost
  (fixed by re-cutting body/leg_r polygons along true silhouettes; the
  revealed area is now honestly transparent). Overlap into a
  neighbor is legitimate ONLY at the joint contact zone (shoulder socket,
  boot under a resting fist) — small, near the cut, where fill is plausible.
- **Cover the full silhouette, or knowingly accept rim loss.** Pixels owned by
  NO piece drop from the render (rim glow below the robot claws, strips between
  the treads). Negligible at rest; decide deliberately.
- **When a deform piece overlays another piece, single-own the pixels with a
  matching NOTCH.** Cut the deform piece's boundary just ABOVE the occluder's
  outline so the *outline stays with the static piece* (it never tears), then cut
  the identical polyline as a notch out of the lower piece so no pixel is owned
  twice — otherwise the lower piece's static copy doubles the image under the
  deforming mesh (robot hoses trace just above the dome outline; the body
  polygon carries the matching notch). See the `hoses` + `body` pair in
  `examples/robot/pieces.json` for the worked polyline.
- **A base-plate piece behind static occluders will fire deep-fill
  warnings — expected when the occluders never move.** A backdrop piece
  spanning the full silhouette behind several rigid pieces (the halo disc
  behind `body`/`arm_r` in a reaper character rig) trips
  `fill_occlusions` deep-zone WARNINGs (>16px, "invented content", §7) for
  every region the foreground pieces cover, since the plate's own pixels
  under them are never drawn. When those occluders are themselves static
  rigid pieces with no motion track, the filled zones are unrevealable —
  probe the rendered filmstrip to confirm (reaper: two such zones, 178px
  body / 86px arm, confirmed 0px ever exposed) — and the warning is safe to
  ship past. Carving the occluders' shapes out of the base-plate polygon
  avoids the warning but opens real holes the moment any occluder DOES
  move; the durable fix is splitting dedicated pieces for whatever sits on
  the plate (out of scope on reaper v1, where nothing on it moves). This
  does not soften deep-fill warnings in general — a warning under a piece
  that DOES move (or might) still deserves the usual look (§7).

Debug the cuts on `out/<name>.debug.png` (polygons + bones + labels over the
source) BEFORE animating; an empty piece after `∧-alpha` means the polygon fell
off the silhouette (inspection-checklist §4).

---

## 3. Skeleton archetypes

Bone points FROM the joint ALONG the part: `(x,y)` is the joint, `rotation` is
the direction the part extends (-90 = up), `length` is roughly the part's reach.
Order parent-first; **exactly one root** (format/README). The root usually sits
at the hips/base with `skin: false` so it never receives weights; `length` is
cosmetic (duck uses 0, robot 60).

**Humanoid / mech mascot** — root at hips → spine → {head, arm_l, arm_r} off the
spine, {leg_l, leg_r} off the root. 7 bones. The worked reference is
`examples/robot/skeleton.json`: `root(455,720,-90)`, `spine(455,640,-90,len220)`,
`head(575,320,-90)`, arms at the shoulder joints pointing down-and-out
(`arm_l rot99`, `arm_r rot80`), legs at the hips (`rot90`).

**Compact critter** — root at base → body → neck → head → beak, plus a tail off
the body. 6 bones. A reference shape:
root(470,950) → body(470,900,-90) → neck(490,715,-95) → head(485,560,-88) →
beak(630,450,rot6); tail(360,800,rot150) branches off body. Note the chain runs
up the neck, and side parts (tail, beak) branch with their own rotations.

Place each joint by reading its coordinate off the grid image (§1).

---

## 4. Binding-type decision rules

Set `type` per piece (pieces.json):

- **`rigid`** (default) — anything that moves as a rigid unit on one bone: body,
  arms, legs, head, treads. `{ "bone": ..., "type": "rigid", "source": {polygon
  or file+offset} }`.
- **`swap`** — for discrete state changes (eye on/off, mouth open/closed).
  Variants are EXTRA input PNGs listed under `variants`, each with a `name`,
  `file`, and `offset` (top-left corner position in source pixels). Driven by a
  `display` track (§5). Robot eye + `eye_glow` variant.
- **`deform`** — ONLY for soft / bridging parts: hoses, cables, capes, a
  breathing plate. Weighted to the 2+ bones it bridges via a `bones` subset;
  `mesh: { cols: N, power: 2 }` — **power 2 is the proven default** (robot
  hoses bridge spine+head, power 2, no smear). Do not reach for deform on
  anything that could be rigid.
- **`morph`** — for a run of real source frames (a filmed gesture, a
  two-pose transition brief) where pixels should travel between actual
  images instead of a single drawing being nudged by bones. Per-vertex FFD
  deform timelines driven by optical flow; no bones, no weights. See
  `format/README.md` "Morph pieces" for the schema.

**Morph vs deform vs cut — pick by what "moving" means for this part:**
- **Multiple SOURCE FRAMES exist and pixels should travel between them**
  (a video-extracted gesture, a two-frame-or-more transition) →
  **`morph`**. The mesh warps by optical flow between real images you
  provide; nothing is invented, and any content change (a prop appearing,
  a pose swap) rides the mid-segment cut between keyframes rather than
  being drawn by the generator.
- **One static image, a subtle single-pose motion** (breathing, idle sway,
  a soft bridge like a hose/cape) → **`deform`** (bone-weighted mesh, §4
  above).
- **Discrete parts with real joints, or a state that flips between drawn
  variants** → **cut + bones** (`rigid` pieces on a skeleton, §3) plus
  `swap` for the variant flip.

**Draw order = pieces list order, back to front**. Deform bridges
usually go FIRST/behind (robot: hoses first). Occlusion fill between overlapping
pieces is automatic (edge-extend + blur); `"fill": "none"` opts out.

Ready-made layer PNGs and Claude-eye polygons may mix in one manifest.

### Morph pieces (video-derived keyframes)

- **Extract frames by SOURCE FRAME NUMBER, never by fps-filter timestamps.**
  An `fps=N` ffmpeg filter samples by timestamp, which can silently drift a
  few frames off the frames you think you picked (a gappy guide chain then
  looks like an optical-flow failure). Address every frame by its exact
  source frame number (`select='eq(n,N)'`).
- Guide frames are cheap — add more of them between two keyframes on any
  segment with large or fast pixel travel; they cost nothing in the atlas
  (§ format/README "Morph pieces").

### Path-constrained chains (hair, tails, ribbons)

For a part that should flow along a curve rather than pivot on a single
joint — a hair lock, a tail, a ribbon — author a `paths` entry in
`skeleton.json` (object form: `{"bones": [...], "paths": [...]}`; field
semantics in `format/README.md`'s "Path constraints" section) instead of a
plain pivoting bone:

- **3–5 anchors along the medial axis, inside the silhouette.** Trace
  on-curve points (image px) along the part's natural centerline — the
  same eye-off-the-grid instinct as reading bone joints (§1), but for a
  curve instead of pivot points. The path's `bone` field names the owner —
  usually the parent the part hangs off (`head` for hair); animate THAT
  bone to move the whole curve, since the constraint parameters themselves
  never keyframe (format/README.md).
- **Chain bones need honest lengths.** Each bone in `chain` gets spacing
  along the curve equal to its own `length` (contract §16d) — size it to
  the bone's real rest span, same instinct as any other bone (§3), not a
  placeholder; `length: 0` collapses the chain (rejected loud by
  `validate_paths`).
- **One rigid piece per chain bone.** Slice the hair into
  `hair1`/`hair2`/`hair3`, each a small rigid polygon on its own chain
  bone — the proven pattern.
  Deform pieces weighted onto path-chain bones are not a proven
  combination in this runtime (produced NaN geometry); reach for rigid
  slices instead.
- **Rigid sway vs traveling wave.** A plain path sways as one stiff curve
  (owner-bone animation — v1). For rippling motion — hair that *flows*,
  a tail that whips — set `"weighted": true` on the path and add a `wave`
  track to the animation (knobs in format/README.md "Wave tracks"):

  ```jsonc
  { "path": "hair_flow", "prop": "wave",
    "amplitude": 12, "period": 1.5 }
  ```

  Proven starting point (reaper hair, strand ~300 px long): amplitude
  **8–15 px**, period **1–2 s**, one wavelength over the curve, the default
  linear envelope (root pinned — otherwise the part detaches from the
  body). Combine freely with owner sway: the sway moves the whole curve,
  the wave ripples it. Keep `duration` a whole number of periods — the
  build rejects a broken loop loudly and suggests the nearest valid
  periods.

---

## 5. Animation vocabulary (amplitudes proven on robot + duck)

Author `tracks` as deltas from rest (rotation/x/y neutral 0; scaleX/scaleY are
ABSOLUTE factors, neutral 1). Proven amplitudes:

- **Idle sway** (spine/body rotation): **±2–3°** (robot spine 2.5°, duck body
  1.5°).
- **Limb swing** (arms, neck): **±3° shipped; up to ±5° for livelier idles**
  (the robot's livelier idle used ±5°, its lazy-arms idle ±3°; duck neck/head
  ±3–5°; counter-swing the two arms: arm_l −3, arm_r +3).
- **Head counter-tilt**: **−1.5 .. −3°**, opposite the body's lean (robot).
- **Secondary parts** (tail, antenna): looser, **±10°** with a snap-back
  (duck tail 10° then −4° quadInOut).
- **"Heavy" reading** (request "heavier / more alive"): longer period (**4.0s**
  vs 3.0), plus a downward **settle** (`spine` y-delta **+9px on a ~900px-tall
  figure** — +4px was sub-pixel and read as no weight),
  plus **lagged extremes** — limb/head peaks land ~0.3s AFTER the body's
  (robot arms peak t=2.3, head t=2.4 vs body t=2.0).
- **Swap pulses**: quick = **0.25s** window, slow = **0.8s**.

**Always:**

- Key at **t=0 AND t=duration on every bone track**, with the final value equal
  to the first (seamless loop; format/README, validated pre-build — the
  validator names the track + both values if you miss it).
- **`sineInOut`** ease for organic motion (robot + duck use it throughout);
  `quadInOut`/`backOut` for snaps.
- **Display tracks**: first key at t=0, stepped (never interpolates); on loops the
  last value must equal the first (robot eye returns to `eye`).
- **Avoid keys closer than 1/30s** — the pipeline rejects frame collisions.

Worked references: `examples/robot/animations.json` (heavy idle: 4s, settle 9px,
lagged arms/head, one 0.8s glow).

### Depth changes (order tracks)

Multi-piece rigs (`pieces.json`) can shift a piece's draw depth mid-animation
with an `order` piece track — DragonBones target only (format/README.md,
Piece tracks section). Use it when a limb must cross IN FRONT of the body it
normally sits behind: keep the piece's **base order = its resting arrangement**
(usually `0` — unchanged from `pieces.json`), raise it ONLY for the crossing
window, and always return to `0` before the window closes. Typical shift is
**+1..+2** — enough to clear the one piece it needs to pass in front of, no
more. E.g. a wave — an arm crossing the torso for the middle of a 2s loop:

```jsonc
{ "piece": "arm_r", "prop": "order",
  "keys": [{ "t": 0.0, "v": 0 }, { "t": 0.5, "v": 1 }, { "t": 1.6, "v": 0 }] }
```

Two authoring hazards, both writer-fatal (`pipeline/dragonbones_writer.py
build_zorder_frames`), quoted so you recognize them from the error text:

- **Two pieces shifted to the same target position in one frame** →
  `"pieces {a} and {b} collide at zOrder frame t={t}s (both target slot
  position {target})"`. Fix: stagger the shifts (e.g. `+1` vs `+2`) or move
  one piece's window so the two never overlap in time.
- **Shifting past the ends of the piece list** → `"piece {p} order shift
  {shift} moves slot {si} to out-of-range position {target} (must be
  0..{count-1})"`. Fix: shrink the shift — `pieces.py` only checks
  `|shift| < piece count`, not that the *landing* position is in range, so a
  piece near either end of `pieces.json` can still overflow.

---

## 6. Gotchas

| Trap | Reality | Source |
|---|---|---|
| scaleX/scaleY values | ABSOLUTE factors: `1.03` = +3%; `0.03` collapses the bone to 3% | format/README |
| rotation up | **-90 = up**; positive = clockwise | format/README |
| coordinates | **y grows DOWNWARD** | format/README |
| validator errors | the message NAMES the fixing knob — READ it, don't guess | build errors |
| variant off-canvas | a variant PNG may extend past the canvas — that is legal | pieces format |
| eye / swap variant | generate by cropping the piece polygon and `ImageEnhance.Brightness(1.6)` | robot eye_glow ×1.6 |
| seamless loop | final key value must EQUAL the first; loop wraps t=duration→0 | format/README |
| rotation key arcs | DB tweens rotation the SHORT way (Δ normalized to ±180°); a segment of ≥180° plays BACKWARD — insert a waypoint key so every segment stays <180° | robot jump |

---

## 7. Error → knob map

When the filmstrip shows a defect, escalate cheapest-first
(markup → knobs → keyframes; inspection-checklist §3):

> **Knob invocation note**: `--fill-blur`, `--bg-tolerance`, `--bg` exist ONLY
> on `pipeline/build_pieces_rig.py` — `pipeline/build_rig.sh` rejects them with
> "unknown arg". To turn a knob, invoke the orchestrator directly
> (`pipeline/.venv/bin/python pipeline/build_pieces_rig.py --image … --skeleton …
> --pieces … --animations … --out out --name <name> --fill-blur 5`) or extend
> the wrapper's arg loop; `build_rig.sh` alone runs orchestrator defaults.

- **Seam / hole visible behind a moving piece** → this is now handled
  automatically: every polygon piece dives `--underlap` px (default 30) under
  the pieces drawn above it, clamped ≥6px inside the SOLID silhouette (an
  underlap crossing into transparency gets diffusion-filled too and floats as
  a smudge once the mover leaves — robot shoulder), and the band is
  diffusion-inpainted. Escalate with 1) a bigger `--underlap` for big swings,
  2) fill blur (`--fill-blur`), 3) if a visible seam survives, that is the
  limit of automatic fill — report it rather than hiding it.
  `--underlap 0` reproduces raw edge-to-edge cuts for A/B. Opaque pixels
  covered by NO polygon are absorbed by the nearest piece with a loud
  `seams: WARNING` — read it; the auto-assignment is nearest-pixel, not
  smart, so a warned gap on a joint line deserves a real markup fix.
- **Limb sweeps THROUGH the body / limbs end up crossed instead of spread** →
  a rotation segment of ≥180°: the writer emits raw key deltas (no `clockwise`
  field yet) and DB normalizes each tween to ±180°, so a 185° arc plays as
  −175° through the body. Insert an intermediate key (waypoint) so every
  segment stays <180° (robot jump: −25 → 70 → 160).
- **Dark ghost / streaky silhouette revealed under a LIFTED piece** (e.g. a wave
  `order` track raises an arm, exposing a smeared arm-outline + rim-glow streaks
  baked into the piece below) → this is the *deep* occlusion case, now handled
  automatically: `fill_occlusions` runs in `mode="auto"`, and any occluded
  component deeper than `DEEP_OCCLUSION_PX` (16px) gets a harmonic **diffusion**
  inpaint instead of nearest-pixel extend — boundary armor tones blend smoothly,
  no streaks. Thin seams (≤16px) stay byte-identical extend. Force it with
  `--fill-mode inpaint` on `build_pieces_rig.py` (or `fill_mode="inpaint"` on the
  MCP `build_rig` tool); `--fill-mode extend` reproduces the old streaky
  behavior for A/B. If content-level detail is needed under a very large
  lift (a whole hidden limb, not just a fill), no fill can invent it — cut
  the polygons differently or supply a layer PNG for the hidden part.
- **Green/colored patch revealed under a lifted piece that `--fill-mode` does NOT
  change** → it is not occlusion fill at all: the lower piece's polygon scooped
  up a faint baked *glow/aura* from the source (any source pixel with `alpha>8`
  inside the polygon becomes valid piece content — `cut_pieces.py`), and a mover
  above it was hiding that content at rest. This is a §2 markup trap ("do not
  enclose a see-through gap / baked glow"): trim the lower piece's polygon off the
  glow, or accept it. Diagnose by forcing `--fill-mode inpaint` — if the patch is
  byte-identical, it is captured source content, not fill (robot `leg_r` top
  corners, revealed by the `wave` arm lift).
- **Mush / smear at a deform joint, or the body inflates / drags near a
  moving limb (whole-image or deform piece)** → heat diffusion is now the
  DEFAULT weights algorithm (`weights_gen.py`, `build_pieces_rig.py`): it
  diffuses influence inside the silhouette only, so this class of failure
  should not appear out of the box. If it still does, or you need the old
  behavior for an A/B, `--weights-algo idw` on `build_rig.sh` (forwarded to
  both whole-image and pieces builds; MCP knob: `weights_algo="idw"`)
  restores IDW (straight-line distance; leaks across empty space and narrow
  junctions, mush at any reasonable `--power`).
- **Mask ate the symbol on a no-alpha input** → `--bg-tolerance`, then
  `--bg rembg`, then a manual silhouette polygon override.
- **Flow inconsistency warning (morph piece "may tear here")** → the
  forward and backward optical-flow estimates disagreed above threshold on
  that region — add guide frames between the named keyframes (steadies the
  flow chain with smaller per-hop travel) or insert an extra keyframe at
  the warned segment. Advisory, not blocking — always eyeball the rendered
  morph at that segment before shipping past it.
- **Atlas exceeds 8192px on either side (BLACK render)** → loud build
  error, not a warning — the GPU texture limit. Downscale the source
  frames, reduce the number of keyframes, or split the rig into more
  pieces so each atlas stays under the limit.
- **Atlas exceeds 4096px on either side (warning only)** → builds and
  renders fine here, but is older-GPU risk in the field; consider
  downscaling the source frames unless you know the target hardware.
- **Piece empty after the cut** → the polygon fell off the silhouette; fix it
  against `out/<name>.debug.png` (inspection-checklist §4).
- **Double image / smear under a deform piece** → missing NOTCH in the lower
  piece; single-own the pixels (§2).
- **Chain ignores the curve entirely (bones sit at their plain rest/FK
  pose)** → the path constraint was silently dropped — DragonBones drops a
  `path` constraint silently on an unresolved `path`/`slot`/`targetDisplay`
  name (contract §16a); check the chain/owner bone names against the build
  summary.
- **Segments bunch up near the curve start instead of spreading along it**
  → chain bone `length` — spacing consumes each chain bone's own length
  (§16d); give each chain bone an honest rest-span length, not a
  placeholder.
- **Pieces on the chain all rotate to the same fixed, curve-unrelated
  angle** → `rotateMode:"tangent"` is a known port bug (contract §16c) —
  the angle collapses to a per-segment constant unrelated to curve
  direction; use the default `"chain"` instead (`"chainscale"` untested).
- **Chain frozen at rest, or the piece vanishes** → either a deform piece
  weighted onto chain bones (unproven — use rigid slices, one per chain
  bone, §4) or a chain bone with `length: 0` (older rigs; `validate_paths`
  now rejects this loud).
- **Hair/tail sways stiffly as one piece instead of rippling** → the curve
  is rigid by construction without `"weighted": true` on the path — set it
  and add a `wave` track (§4; format/README "Wave tracks"). The reverse —
  ripple reads as chaotic wobble → lower `amplitude` or lengthen `period`;
  crests crawl the wrong way → flip `direction`.
- **Wave's strand root visibly slides off the body** → the envelope: the
  default `"linear"` pins the root (gain 0 at u=0); `"flat"` (or a custom
  envelope with a nonzero first gain) waves the attachment point itself —
  intended only for free-floating parts (ribbons, banners).

---

## 8. Acceptance

Drive the render through **`docs/inspection-checklist.md`** — the 7 yes/no items,
fast/quality modes, the ≤4-iteration budget, one change per iteration, and which
artifact to inspect for which suspicion. Deliver only when every item is "yes",
or the budget is exhausted (then deliver WITH a shortfall report). Verify with
your own eyes on the filmstrip (and numeric probes for timing), never by trusting
a report.
