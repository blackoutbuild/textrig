# DragonBones weighted-mesh format contract

**Status: PROVEN.** Every field below is traced to the official parser source
(`pixi-dragonbones-runtime@8.0.3`, `lib/parser/ObjectDataParser.mjs` — bundled
but readable; constant strings in `lib/parser/DataParser.mjs`; affine math in
`lib/geom/Matrix.mjs` and `lib/geom/Transform.mjs`) **and** cross-checked against
the official `you_xin/body` sample. It is validated end-to-end by two
hand-authored fixtures that play correctly through the renderer:
`renderer/assets/handmade/` (§1–§8, single weighted mesh — see the
"Worked fixture" section; played on the first attempt, no corrections needed)
and `renderer/assets/figure2/` (§9–§14, multi-slot / image displays /
display swap / animated alpha / multi-region atlas / zero-bone animation —
proven 2026-07-10, also first attempt).

The writer (`pipeline/dragonbones_writer.py`, Task 2) must emit exactly this
shape. The parser does **no schema validation** — malformed data throws deep in
the parser or renders garbage silently, so this contract is the spec.

Target data version: **5.5** (`version` and `compatibleVersion`). Accepted
versions are `["4.0","4.5","5.0","5.5","5.6"]` (`DataParser.DATA_VERSIONS`);
anything else makes `parseDragonBonesData` `console.assert(false,...)` and
**return `null`** (ObjectDataParser `parseDragonBonesData`).

---

## 0. Coordinate system & units (the conventions a naive serializer gets wrong)

- **Y is DOWN.** DragonBones armature space is y-down (screen space). In the
  `you_xin` sample the body extends *upward* into *negative* y (a_body vertices
  reach `y ≈ -1359`, the neck bone bind `ty ≈ -1292`). Our fixture places the
  planted base at `y = 0` and the top edge at `y = -128`.
- **Rotation is in DEGREES in the JSON**, converted to radians by the parser
  (`Transform.DEG_RAD = π/180`). Positive rotation is **clockwise on screen**
  (y-down): `Transform.toMatrix` builds `a=cos θ, b=sin θ, c=-sin(skew+θ),
  d=cos(skew+θ)`, i.e. `(1,0) → (cos θ, sin θ)`, and with +y pointing down that
  is a clockwise visual turn.
- **Affine matrices are 6-float `[a, b, c, d, tx, ty]`** (`Matrix.copyFromArray`).
  A point transforms as `x' = a·x + c·y + tx`, `y' = b·x + d·y + ty`
  (`Matrix.transformPoint`). A pure translation is `[1,0,0,1,tx,ty]`; a pure
  rotation θ is `[cosθ, sinθ, -sinθ, cosθ, 0, 0]`.
- **Armature origin** is the (0,0) of bone/vertex space; auto-fit in the runner
  just centers the local bounds, so absolute placement is free — but bones,
  vertices, `slotPose` and `bonePose` must all share ONE consistent space.

---

## 1. Top-level document

```jsonc
{
  "frameRate": 24,                 // document default fps (fallback 24 if 0/absent)
  "name": "handmade",
  "version": "5.5",                // MUST be in DATA_VERSIONS or parse → null
  "compatibleVersion": "5.5",      // either version OR compatibleVersion must match
  "armature": [ { /* §2 */ } ],
  "textureAtlas": [ ... ]          // OPTIONAL inline; we pass tex.json separately
}
```

- `parseDragonBonesData` reads `version`, `compatibleVersion`, `name`,
  `frameRate`, then iterates `armature[]`. `textureAtlas` may be embedded but we
  keep it in a sibling `_tex.json` and feed it via `parseTextureAtlasData`.
- After parsing, `stage` defaults to the first armature; `armatureNames[]` is
  what `buildArmatureDisplay` consumes.

---

## 2. Armature

```jsonc
{
  "type": "Armature",              // string; "Armature"→0, "Stage"→2, "MovieClip"→1
  "frameRate": 24,                 // per-armature fps; falls back to doc, then 24
  "name": "handmade",
  "aabb": { "x": -32, "y": -128, "width": 64, "height": 128 }, // optional, informational
  "bone":       [ /* §3 */ ],
  "slot":       [ /* §4 */ ],
  "skin":       [ /* §5 */ ],
  "animation":  [ /* §7 */ ],
  "defaultActions": [ { "gotoAndPlay": "bend" } ]  // optional; sets default anim
}
```

- `_parseArmature` order matters only in that bones are parsed and
  `sortBones()`-ed before skins/animations resolve them by name.
- `defaultActions` with `gotoAndPlay: <animName>` sets `defaultAnimation`. It is
  **optional** for our headless runner (the runner explicitly calls
  `display.animation.play(animationNames[0], 0)`), but real DragonBones players
  autostart from it, so emit it.

### Minimum required boilerplate
`version`+`compatibleVersion` (§1), one `armature` with a `name`, `bone[]`,
`slot[]`, `skin[]`, `animation[]`. `type`, per-armature `frameRate`, `aabb`,
`defaultActions`, draw-order (`zOrder`) timelines are all optional. There is **no
`ds`/draw-order requirement** for a single-slot rig.

---

## 3. Bones — parent-local transforms

```jsonc
"bone": [
  { "name": "root" },                                          // identity transform
  { "name": "upper", "parent": "root", "transform": { "x": 0, "y": -64 } }
]
```

- A bone's `transform` is **relative to its parent** (`parent` = parent bone
  name; root bones omit `parent`). Children are linked to parents by name in
  `_parseArmature` (forward refs cached in `_cacheBones`).
- `transform` fields (`_parseTransform`, constant names in `DataParser`):
  | JSON key | meaning |
  |---|---|
  | `x`, `y` | translation (× armature `scale`, default 1) |
  | `skX`, `skY` | **skew-X / skew-Y in degrees.** When `skX == skY` this is a pure **rotation** (rotation = `skY`, skew = `skX - skY = 0`). |
  | `rotate`, `skew` | alternate spelling: `rotation = rotate°`, `skew = skew°`. Parser checks `rotate`/`skew` first, else `skX`/`skY`. |
  | `scX`, `scY` | scaleX / scaleY (default 1) |
  - **Do NOT use a key literally named `rotation`** — it is ignored. Use `skX`+`skY`
    (both equal, for a pure rotation) or `rotate`. The `you_xin` sample uses
    `{"x":..,"y":..,"skX":87.84,"skY":87.84}` for a rotated bone.
- Omitting `transform` entirely = identity (root has none). Inherit flags
  (`inheritTranslation/Rotation/Scale/Reflection`) default true; leave default.

---

## 4. Slots

```jsonc
"slot": [ { "name": "body", "parent": "root" } ]
```

- Minimum = `name` + `parent` (a **bone** name). `displayIndex` (default 0),
  `zIndex`, `color`, `blendMode` are optional.
- **For a weighted (skinned) mesh the slot's parent-bone transform does not move
  the mesh vertices** — skinned vertices are fully determined by the weighted
  bones + `bonePose` (§6). The `you_xin` a_body slot is just
  `{"name":"a_body","parent":"nv"}`. Parent the slot to any real bone (we use
  `root`).

---

## 5. Skin → display list

```jsonc
"skin": [
  { "slot": [
      { "name": "body",
        "display": [ { /* mesh display, §6 */ } ] } ] }
]
```

- One anonymous skin (`name` absent → `"default"`). Each entry keys a **slot by
  name** and lists its `display[]`. `displayIndex` on the slot (default 0) picks
  which display is shown.

---

## 6. Mesh display + weighted skinning (THE core)

```jsonc
{
  "type": "mesh",                  // "mesh"→2; "image"→0, "armature"→1, etc.
  "name": "handmade",              // MUST equal a SubTexture name in the atlas (§8)
  "width": 64, "height": 128,      // informational (not read by parser)
  "vertices":  [ x0,y0, x1,y1, ... ],       // 2N floats, N = vertex count
  "uvs":       [ u0,v0, u1,v1, ... ],       // 2N floats, 0..1, per-vertex, index-aligned
  "triangles": [ i,j,k, ... ],              // 3T ints, vertex indices
  "weights":   [ ... ],                     // packed, see below
  "slotPose":  [ a,b,c,d,tx,ty ],           // 6 floats, present iff weighted
  "bonePose":  [ gIdx,a,b,c,d,tx,ty, ... ]  // 7 floats per mesh-bone, present iff weighted
}
```

Source: `_parseGeometry` (vertices/uvs/triangles/weights) and `_parseMesh`
(records slotPose/bonePose). `_getDisplayType`: `"mesh"` → type 2.

### 6a. vertices / uvs / triangles
- `vertices`: flat `[x,y,...]`, `N = floor(len/2)`. In **slot-local space**
  (transformed by `slotPose` to world — see 6c). With `slotPose = identity`,
  vertices are directly in armature/world space.
- `uvs`: flat `[u,v,...]`, `0..1`, one per vertex, **same index order as
  vertices**. `v=0` is the top of the texture region, `v=1` the bottom.
- `triangles`: flat `[i0,i1,i2,...]`, vertex indices. Winding is irrelevant —
  the mesh renders as a Pixi `MeshSimple` with no back-face culling.
- `edges`/`userEdges` (seen in `you_xin`) are **editor metadata, not read** —
  omit them.

### 6b. `weights` packing — `[boneCount, (globalBoneIdx, weight)…]` per vertex
For each vertex, in vertex order:
```
boneCount_v,  gBoneIdx, weight,  gBoneIdx, weight,  ... (boneCount_v pairs)
```
- `gBoneIdx` is the **global bone index** = index into the armature `bone[]`
  array (`this._rawBones[gBoneIdx]`), NOT an index into this mesh's bone list.
- Total length `= N + 2·(Σ boneCount_v)`. The parser derives the influence count
  as `n = (weights.length − N) / 2` (`_parseGeometry`:
  `n=Math.floor(s.length-i)/2`). **Verified on `you_xin/body/a_body`:** N=74,
  `weights.length`=374 → n=(374−74)/2 = 150 influences. ✓
- Every `gBoneIdx` used here **must also appear in `bonePose`** (the parser does
  `a.indexOf(gBoneIdx)` where `a` is the list of bonePose global indices; a
  missing bone yields `-1` and corrupts the mesh).
- Weights per vertex should sum to 1.0 (not enforced, but required for a sane
  rest pose).

### 6c. `slotPose` and `bonePose` — bind-pose matrices
- **`slotPose`** = 6-float `[a,b,c,d,tx,ty]` = the slot's **world bind matrix**.
  The raw `vertices` are pushed through it to get world bind positions:
  `worldVertex = slotPose · vertex`. Easiest authoring choice: **`slotPose =
  [1,0,0,1,0,0]` (identity)** and put vertices directly in world space — this is
  exactly what `you_xin/body/a_body` does (`slotPose:[1,0,0,1,0,0]`).
- **`bonePose`** = flat array, **7 floats per influencing bone**:
  `[gBoneIdx, a, b, c, d, tx, ty]` repeated. Number of mesh-bones =
  `bonePose.length / 7` (`_=Math.floor(r.length/7)`). **Verified on a_body:**
  `bonePose.length`=70 → 10 bones. ✓ Its `[a..ty]` per bone is that bone's
  **world (global) bind matrix**, e.g. a_body's neck bone entry
  `[27, 0.000524, -1, 1, 0.000524, -36.13, -1292.75]` — a_body's global bind
  matrices, one per weighted bone.

  **CRITICAL invariant:** `bonePose[k]`'s matrix MUST equal the world matrix that
  the bone resolves to from its parent-local `transform` chain in the setup pose.
  At **parse** time the runtime pre-computes, per (vertex, bone) influence, the
  vertex position in that bone's local frame:
  `boneLocal = inverse(bonePoseMatrix) · (slotPose · vertex)`
  (`_parseGeometry`: `helpMatrixA=slotPose; transformPoint(vertex)`, then
  `helpMatrixB=bonePose[7k+1..6]; invert(); transformPoint`). At **runtime** the
  deformed world vertex is `Σ weightᵢ · (boneWorldᵢ · boneLocalᵢ)`. At rest
  `boneWorldᵢ == bonePoseMatrixᵢ`, so it reconstructs `slotPose·vertex` exactly —
  **only if bonePose == the resolved setup-pose world matrix.** A mismatch skews
  the rest pose. (This is the single most dangerous thing to get wrong.)

---

## 7. Animation — bone timelines

```jsonc
"animation": [
  { "duration": 48,               // total length IN FRAMES (at armature frameRate)
    "name": "bend",
    "playTimes": 0,               // OPTIONAL: 0 = loop; default 1. Our runner overrides.
    "bone": [
      { "name": "upper",
        "rotateFrame": [ /* frames */ ],
        "translateFrame": [ ... ],  // optional
        "scaleFrame": [ ... ] } ]   // optional
  }
]
```

- **`duration` is in FRAMES**, not seconds. Seconds = `duration / frameRate`
  (`_parseAnimation`: `r.duration = r.frameCount / frameRate`). Our fixture:
  48 frames / 24 fps = 2.0 s (the runner reported `duration: 2.000s`).
- Each bone timeline keys a bone by `name` and carries any of `translateFrame`
  (x,y), `rotateFrame` (rotate,skew), `scaleFrame` (x,y). Values are **deltas
  applied on top of the bone's setup transform**, not absolutes.

### 7a. Frame arrays
`rotateFrame` entry (`_parseBoneRotateFrame`):
```jsonc
{ "duration": 12,        // frames until next keyframe
  "tweenEasing": 0,      // OR "curve": [x1,y1,x2,y2]; OR omit for stepped
  "rotate": 25,          // DELTA rotation in DEGREES from bind (default 0)
  "skew": 0 }            // optional skew delta in degrees (default 0)
```
`translateFrame`: `{ "duration", tween…, "x", "y" }` (delta px, default 0).
`scaleFrame`: `{ "duration", tween…, "x", "y" }` (default **1**).

> **[Added 2026-07-10, Task 2 — scaleFrame semantics, traced to parser + runtime]**
> `scaleFrame` `x`/`y` are **absolute scale factors relative to the bone's setup
> scale**, NOT additive deltas (this is why the default is `1`, the identity for
> the operation, unlike `rotate`/`translate` whose default `0` marks them as
> additive deltas). Confirmed:
> - Parse (`ObjectDataParser._parseBoneScaleFrame`): reads `x`/`y` with
>   `_frameDefaultValue = 1`, `_frameValueScale = 1` → stored raw.
> - Runtime (`TimelineState.mjs`, `BoneScaleTimelineState.blend`): at full blend
>   weight it does `animationPose.scaleX = resultA` (the frame value), and the
>   bone's final scale is `setup.scaleX · animationPose.scaleX`. With a setup
>   scale of `1` the frame value **is** the resulting scale factor.
>
> **Our mapping:** TextRig `scaleX`/`scaleY` track values are absolute factors
> (neutral `1`) and our exported bones carry no setup scale (`scX`/`scY` omitted →
> `1`). So `scaleFrame.x = our scaleX`, `scaleFrame.y = our scaleY`, copied
> verbatim. (If we ever emit a non-unit rest scale this must become
> `frame / setup` to stay an absolute factor.)

### 7b. Frame durations, last frame, looping (easy to get wrong)
- Per-frame `duration` is **in frames** and the durations of all keyframes
  **sum to the animation `duration`**. Verified on `you_xin` idle: rotateFrame
  durations `[22,22,26,0]` sum to 70 = `duration`.
- The **terminal keyframe carries `duration: 0`** and holds the closing value.
  For a clean loop, that closing value **must equal the first keyframe's value**
  (the runtime wraps frame[last] back to frame[0]). `you_xin` idle: first frame
  rotate 0, terminal `{duration:0}` (rotate defaults 0) → seamless loop.
- Our fixture: `[{d12,rot0},{d12,rot25},{d12,rot0},{d12,rot-25},{d0,rot0}]` —
  durations sum 48, terminal value 0 = first value 0. ✓

### 7c. Easing / tween mapping (for our `sineInOut`)
`_parseTweenFrame` encodes the per-frame tween as:
| JSON | meaning |
|---|---|
| `tweenEasing` omitted | **stepped** (no interpolation) |
| `tweenEasing: 0` | **linear** |
| `tweenEasing: r`, `0 < r ≤ 1` | quadratic **ease-out**, strength r |
| `tweenEasing: r`, `r < 0` | quadratic **ease-in**, strength −r |
| `tweenEasing: r`, `r > 1` | **sine ease-in-out**, strength r−1 (runtime `_getEasingValue` case 5, `lib/animation/BaseTimelineState.mjs`: `s = 0.5*(1 − cos(t·π))` — sine/cosine, NOT quadratic; only the ease-in/ease-out cases 3/4 are quadratic) |
| `curve: [x1,y1,x2,y2,…]` | **cubic Bézier** control points (per segment); the parser samples it (`_samplingEasingCurve`). This is what the DragonBones editor emits for custom curves (`you_xin` uses `curve:[0.2595,0,0.946,0.84]`). |

The strength blends the eased value with linear: `result = (eased − t)·strength + t`
(`_getEasingValue` return). `tweenEasing: 2` encodes strength
`round(100·2 − 100)/100 = 1.0` (`_parseTweenFrame`), i.e. 100% of the sine curve.

**Recommended mapping for our `sineInOut`:** emit `"tweenEasing": 2`. Its
`0.5*(1 − cos(t·π))` at full strength is **mathematically identical to
CSS/textrig `sineInOut`** — our sineInOut maps to `tweenEasing: 2` EXACTLY, not
approximately. (A Bézier `curve` is unnecessary for this; keep curves for easings
DragonBones has no closed form for.) The fixture itself uses `tweenEasing: 0`
(linear) — deliberately the simplest option, and it still reads as a clean bend
because the ±25° keyframes are symmetric.

---

## 8. Texture atlas JSON (`_tex.json`) — single full-image region

```jsonc
{
  "width": 64, "height": 128,      // atlas PNG pixel size
  "name": "handmade",
  "imagePath": "handmade_tex.png",
  "SubTexture": [
    { "name": "handmade",          // MUST equal the mesh display `name`/`path`
      "x": 0, "y": 0, "width": 64, "height": 128 }
  ]
}
```

- `parseTextureAtlasData` reads top-level `width`, `height`, `name`, `imagePath`
  and each `SubTexture`: `x`, `y`, `width`, `height`, `rotated` (bool, default
  false), and optional `frameX/frameY/frameWidth/frameHeight` (trim box — omit
  for an untrimmed region).
- The display is bound to its region by **name**: `SubTexture.name` must equal
  the mesh display's `name` (or `path` if set). One region covering the whole PNG
  = `x:0,y:0,width:PNGw,height:PNGh`, `rotated:false`, no `frame*`.
- Fed to the runtime as `factory.parseTextureAtlasData(texJson, pixiTexture)`
  where `pixiTexture = await Assets.load(png)`.

---

## Worked fixture — `renderer/assets/handmade/` (the proof)

A 64×128 two-tone rectangle: **blue top half (the bending part), orange bottom
half (planted)**, thin dark seam line at the mid-height for legibility. Space:
y-down, origin at the planted base center `(0,0)`, top edge at `y = −128`, seam
at `y = −64`.

**Bones** (parent-local): `root` at origin (identity); `upper` child of root at
the seam `transform:{x:0, y:−64}` (no rotation).

**Mesh**: 3 rows × 3 cols = **9 vertices**, 4 quads = 8 triangles.
Rows at y = 0 (base), −64 (seam), −128 (top); cols at x = −32, 0, +32.
UVs `u=(x+32)/64`, `v=(y+128)/128` (top row → v=0 → texture top → blue).

**Weights** blend across the seam:
- bottom row (v0,v1,v2): 100% `root` (global idx 0) → `[1, 0, 1]`
- seam row (v3,v4,v5): 50/50 → `[2, 0, 0.5, 1, 0.5]`
- top row (v6,v7,v8): 100% `upper` (global idx 1) → `[1, 1, 1]`

Length check: N=9, Σ boneCount = 3·1 + 3·2 + 3·1 = 12, so
`weights.length = 9 + 2·12 = 33`, and `n = (33−9)/2 = 12`. ✓

### Worked `slotPose` / `bonePose` computation
- `slotPose = [1,0,0,1,0,0]` (identity) → mesh `vertices` are already world-space.
- Bone world bind matrices from the transform chain:
  - `root`: parent-local identity → **world = `[1,0,0,1,0,0]`**.
  - `upper`: local `{x:0,y:−64,rot:0}` → `toMatrix` = `[1,0,0,1,0,−64]`; parent
    (root) is identity → **world = `[1,0,0,1,0,−64]`**.
- Therefore `bonePose = [0, 1,0,0,1,0,0,  1, 1,0,0,1,0,−64]`
  (`[gIdx=0, root-world(6)], [gIdx=1, upper-world(6)]`).

**Rest-pose reconstruction check** (seam vertex v3 = (−32,−64), weights
0.5·root + 0.5·upper):
- `slotPose·v3 = (−32,−64)`.
- root: `inv(I)·(−32,−64) = (−32,−64)`; runtime `root_world · that = (−32,−64)`.
- upper: `inv([1,0,0,1,0,−64]) = [1,0,0,1,0,64]`; `·(−32,−64) = (−32,0)` (bone-local);
  runtime `upper_world · (−32,0) = (−32,−64)`.
- Blend: `0.5·(−32,−64) + 0.5·(−32,−64) = (−32,−64)` = original. ✓ Rest pose exact.

**Animation** `bend`: 48 frames @ 24 fps = 2.0 s, `upper.rotateFrame`
`0° → +25° → 0° → −25° → 0°` at frames 0/12/24/36/48, `tweenEasing:0`, terminal
`{duration:0, rotate:0}`.

### Result
Played first try via `cd renderer && pnpm render handmade/handmade bend
handmade`. Runner reported: `armature: handmade`, `anim: bend`,
`duration: 2.000s`, `mesh render-objects: 1`, mesh vertex-buffer checksums
`5/6 distinct` (frames 0 and 5 identical = the 2.0 s loop closing on the capture
span). Filmstrip `out/handmade.filmstrip.png`: orange bottom stays planted while
the blue top swings about the seam (+25° right, back through upright, −25° left,
back to upright), the seam row shears smoothly between the two, UVs correct
(blue up / orange down, no swimming), no garbage. **No corrections were needed —
the contract's numbers were right as authored.**

---

# Multi-slot extensions (Task 1 research — slots, image displays, swap, slot
# alpha, multi-region atlas)

**Status of §9–§14: PROVEN.** Every claim below is
cited to a function in `pixi-dragonbones-runtime@8.0.3`. Cross-checks use the two
official DragonBones samples (not shipped here): `mecha_1002_101d_show_ske.json`
(+`_tex.json`) — 19 slots, **image** displays, a multi-region atlas, one
`armature`-type display — and `you_xin_body/body_ske.json`
(weighted meshes + ffd). Three features had NO sample coverage in either file —
the **`displayFrame`** (swap) timeline (§11), the **`colorFrame`/slot-`alpha`**
timeline (§12), and a **zero-bone-timeline animation** (§14) — and were proven
on **2026-07-10** by the hand-authored fixture
`renderer/assets/figure2/` (multi-slot figure: body/arm/eye slots,
image displays, 4-region atlas), rendered through the renderer and
eye-verified (`out/figure2.filmstrip.png`, `out/figure2-swaponly.filmstrip.png`).
Its `swaponly` animation carries **only** slot timelines (no `bone` key), proving
§11, §12, and §14 in one animation; the `demo` animation additionally exercised
§9 draw order (arm slot above body) and §10 image-display placement/size
alongside a bone rotateFrame. Each formerly UNVERIFIED-vs-sample flag below now
records what the filmstrip showed (the original pass criteria, kept as the
verification record).

Line numbers are unstable (the shipped `.mjs` is minified to 2 lines), so — like
§1–§8 — every citation is by **function name + file**. A beautified working copy
was used to read them.

---

## 9. Slots & draw order

```jsonc
"slot": [                                   // ARMATURE-level slot[] — ORDER = depth
  { "name": "body",  "parent": "root" },    // index 0 → drawn first (bottom)
  { "name": "wing",  "parent": "root" },    // index 1 → drawn above body
  { "name": "badge", "parent": "root",
    "zIndex": 1 }                           // zIndex bumps it to a higher layer
]
```

- `_parseArmature` (ObjectDataParser.mjs) walks the armature's `slot[]` array with
  a running counter: `let t=0; for(const e of r) a.addSlot(this._parseSlot(e, t++))`.
  So a slot's **`zOrder` is its index in `armature.slot[]`** — it is **NOT a JSON
  key**; you set it purely by array position.
- `_parseSlot(e, r)` reads, in order: `displayIndex` (`_getNumber`, default **0**),
  `zOrder = r` (the array index just described), `zIndex` (JSON key `"zIndex"`,
  `_getNumber`, default **0**), `alpha` (JSON key `"alpha"`, `_getNumber`, default
  **1** — a static slot opacity, see §12), `name` (`_getString`), `parent`
  (`_getString` → `this._armature.getBone(...)`, i.e. a **bone** name),
  `blendMode` (string via `_getBlendMode` e.g. `"add"`→1, or a number, default
  0=normal), `color` (if `"color"` present → `_parseColorTransform`, see §12),
  `actions`.
- **Draw order is `1000·zIndex + zOrder`**, ascending = back-to-front. The armature
  sort comparator `Armature._onSortSlots(t,s)` (Armature.mjs) returns
  `1e3*t._zIndex + t._zOrder > 1e3*s._zIndex + s._zOrder ? 1 : -1`; the higher key
  is placed **later** in the sorted `_slots` array → added to the Pixi container
  later → **rendered on top**. Consequences:
  - With `zIndex` omitted (0) on every slot, **depth == `armature.slot[]` array
    order**; later in the array = on top. This is the normal case — emit slots
    back-to-front.
  - `zIndex` is a **coarse layer override** weighted ×1000, so any positive
    `zIndex` lifts a slot above *all* zIndex-0 slots regardless of array position
    (an armature would need >1000 slots for `zOrder` to overflow one `zIndex`
    step). Use it only to force a slot onto a higher layer.
  - There is **no `z` key** and no per-slot `zOrder` JSON key. The static
    `armature.slot[]` order is the **base** draw order; animated reordering
    at runtime is the **§15 zOrder timeline** (`_parseZOrderFrame`, type-1
    animation timeline), whose offsets are shifts *from* this base.
- The **skin**'s `slot[]` order (§5) is irrelevant to depth: it is only a
  by-name lookup that attaches `display[]` to an already-created slot
  (`_parseSkin`: `getSlot(name)`). Depth comes solely from `armature.slot[]`.
- **Cross-check (mecha):** all 19 slots are bare `{"name","parent"}` with no
  `zIndex`/`color` → depth is exactly `armature.slot[]` order. The armature's
  `slot[]` order differs from the skin's `slot[]` order in that file, confirming
  the two are independent.

---

## 10. Image display (non-mesh sprite)

```jsonc
// inside skin → slot[name=body] → display[]
{
  "name": "body",                  // MUST equal a SubTexture name in the atlas (§13),
                                   //   unless "path" is given (see below)
  "transform": { "x": 12, "y": -8, "skX": 0, "skY": 0 },   // OPTIONAL, see space below
  "pivot": { "x": 0.5, "y": 0.5 }  // OPTIONAL; default is center (0.5, 0.5)
}
```
(No `"type"` key needed — image is the default type.)

- `_parseDisplay(e)` resolves the display type from `"type"`: a string goes
  through `_getDisplayType` (`"image"`→0, and **0 is the default** for a missing/
  unknown type), or a raw number. **Type 0 = image** → `ImageDisplayData`.
- Keys read for an image display:
  - `name` (`_getString`, default `""`). Binds the display to its atlas region by
    name (§13).
  - `path` (`_getString`, default `""`). `i.path = a.length>0 ? a : r` — i.e.
    **`path` overrides `name` as the atlas-region key** when present; otherwise the
    region key is `name`. We emit `name` only (omit `path`) and match it to the
    SubTexture name.
  - `pivot` via `_parsePivot(e,i)`: if `"pivot"` present, reads `pivot.x`/`pivot.y`
    (`_getNumber`, each default 0); **if `"pivot"` is absent, default is
    `pivot.x = 0.5, pivot.y = 0.5`** (region center). Values are **normalized
    fractions of the region** (0..1), not pixels.
    **Gotcha — partial pivot:** the 0.5 default applies only when the `"pivot"`
    key is absent entirely. If `"pivot"` is present, each missing axis defaults
    to **0**, not 0.5 — `{"pivot":{"x":0.5}}` gives `(0.5, 0)`, anchoring the
    y-axis at the region's top edge. Supply **both** keys or omit `pivot`
    entirely.
  - `transform` via `_parseTransform(e[TRANSFORM], s.transform, armature.scale)`,
    applied after the type switch: `return ... M.TRANSFORM in e &&
    this._parseTransform(...)`. Same key set as a bone transform (§3): `x,y`,
    `skX/skY` (or `rotate/skew`), `scX/scY`. Omitting `transform` = identity.
- **Pivot space & pixel conversion** (`Slot._updateDisplayData`, Slot.mjs): for a
  textured non-mesh display, `_pivotX = pivot.x · regionWidth · scale`,
  `_pivotY = pivot.y · regionHeight · scale` (region = the SubTexture rectangle).
  So default `0.5/0.5` puts the anchor at the **center of the region**.
- **Transform space = the slot's parent bone's local space.** In
  `Slot._updateDisplayData` the display's `transform` becomes the slot's local
  matrix: `this.global.copyFrom(origin).add(offset).toMatrix(this._localMatrix)`
  where `origin = displayData.transform`. Then
  `Slot._updateGlobalTransformMatrix`:
  `globalTransformMatrix = _localMatrix.concat(parentBone.globalTransformMatrix)`.
  So the sprite is placed by `transform` **relative to the parent bone** (the bone
  named in the slot's `parent`), and the pivot is the point that lands at that
  transformed origin — `PixiSlot._updateTransform` sets the sprite position to
  `x = e.x − (M.a·pivotX + M.c·pivotY)`, `y = e.y − (M.b·pivotX + M.d·pivotY)`
  (pivot subtracted in the display's rotated frame).
- **Texture binding** (`PixiSlot._updateFrame`): the sprite's texture is the
  SubTexture's region texture — `_renderDisplay.texture = textureData.renderTexture`
  — which `PixiTextureAtlasData` builds as a Pixi `Texture` framed to
  `Rectangle(region.x, region.y, region.width, region.height)` (see §13). So an
  image display shows exactly its atlas region.
- **On-screen SIZE = the SubTexture region's `width`×`height`** in armature
  units, scaled by atlas/armature scale and the display transform's `scX`/`scY`.
  **There is NO display width/height key for an image** — `_parseDisplay` case 0
  reads only `name`/`path`/`pivot`/`transform`. Do not copy the informational
  mesh-display `width`/`height` pattern (§6) onto an image display; those keys
  are **silently ignored**. Trace: the region texture is built with
  `frame`/`orig` both sized `region.width × region.height`
  (`PixiTextureAtlasData` `renderTexture` setter), so the sprite's natural size
  is the region's pixel size; `PixiSlot._updateFrame` (non-mesh branch) sets
  `_textureScale = textureData.parent.scale · armatureData.scale`, and
  `PixiSlot._updateTransform` applies
  `scaleX = global.scaleX · _textureScale` (where `global.scaleX` includes the
  display `transform`'s `scX`). With atlas `scale` and armature `scale` at their
  default 1, **1 region pixel = 1 armature unit**.
- **Worked size/placement example:** region
  `{"x":0,"y":0,"width":200,"height":150}`, parent bone at identity, no
  `transform`, no `pivot` (→ default 0.5/0.5). Pivot px =
  `(0.5·200, 0.5·150) = (100, 75)` (`Slot._updateDisplayData`). The sprite's
  origin is offset by the rotated pivot
  (`PixiSlot._updateTransform`: `x = e.x − (M.a·100 + M.c·75) = −100`,
  `y = e.y − (M.b·100 + M.d·75) = −75`), so the sprite spans
  **(−100,−75)..(+100,+75) in armature space** — a 200×150 rect centered on the
  parent bone's origin.
- **Cross-check (mecha):** image displays such as
  `{"name":"mecha_1002_101d_folder/foot_l","transform":{"x":15.95,"y":12.9}}` and
  `{"name":"mecha_1002_101d_folder/upperarm_r","transform":{"x":-28.55,"y":-6.3,"skX":2.43,"skY":2.43}}`
  carry a `transform` and **no `pivot`** → default center pivot. Each slot is
  parented to a same-named bone and the display `transform` offsets the art
  within that bone's frame. ✓

> **Image vs weighted mesh (choosing per part).** A **mesh** display (§6) deforms
> with weighted bones and needs `slotPose`/`bonePose`; an **image** display is a
> rigid sprite moved only by its parent bone + display `transform`. For a
> cut-out part that only translates/rotates/scales as a unit, an image display is
> simpler and cheaper. Both bind to the atlas by `name`.

---

## 11. `displayFrame` timeline — swap / show / hide a slot's display

**PROVEN 2026-07-10 by the `figure2` fixture** (`renderer/assets/figure2/`,
`out/figure2.filmstrip.png` + `out/figure2-swaponly.filmstrip.png`): the eye
slot's texture swapped to a **visibly different region** (green `eye` → red
`eye_closed`) exactly at the swap keyframes, hard-stepped — no cross-fade or
intermediate frame — and swapped back at the return key. (The `value: -1` hide
path remains traced-from-source only — the fixture did not exercise it; the
criterion stands: pixels vanish at the `-1` frame, reappear at the next
non-negative key.)

```jsonc
// A slot needs >1 display in its skin to swap between them:
"skin": [ { "slot": [
  { "name": "mouth",
    "display": [ { "name": "mouth_closed" },     // displayIndex 0
                 { "name": "mouth_open" } ] } ]}],// displayIndex 1

// Swap-only animation (see §14 for the zero-bone-timeline confirmation):
"animation": [ {
  "duration": 24, "name": "talk", "playTimes": 0,
  "slot": [
    { "name": "mouth",
      "displayFrame": [
        { "duration": 6, "value": 0 },   // show display 0 (closed)
        { "duration": 6, "value": 1 },   // swap to display 1 (open)
        { "duration": 6, "value": 0 },
        { "duration": 6, "value": 1 },
        { "duration": 0, "value": 0 }    // terminal; = first value for a clean loop
      ] } ]
} ]
```

- `_parseSlotTimeline(t)` (ObjectDataParser.mjs) resolves the slot by `name`, then
  builds a display timeline: `M.DISPLAY_FRAME in t ?
  _parseTimeline(t,null,"displayFrame",20,0,0,_parseSlotDisplayFrame) :
  _parseTimeline(t,null,"frame",20,0,0,_parseSlotDisplayFrame)`. **Timeline type 20,
  frameValueType 0.** (If you use the legacy key `"frame"` instead of
  `"displayFrame"` it parses identically — we emit `"displayFrame"`.)
- Per-frame parser `_parseSlotDisplayFrame(t,e,r)` calls **`_parseFrame`** (the
  plain, no-tween variant — contrast `_parseTweenFrame`), then:
  `M.VALUE in t ? frameArray[a+1] = _getNumber(t,"value",0)
                : frameArray[a+1] = _getNumber(t,"displayIndex",0)`.
  So each frame's payload is a **display index**, read from **`"value"`
  (preferred) else `"displayIndex"`, default 0**. It also parses per-frame
  `actions`.
- **Stepped, no tween — confirmed.** Runtime `SlotDisplayTimelineState`
  (TimelineState.mjs; wired to timeline type 20 via `AnimationState` `case 20` →
  `SlotDisplayTimelineState`): `_onArriveAtFrame` reads the frame's index and calls
  `target._setDisplayIndex(index, true)`; `_onUpdateFrame` is **empty** → the value
  is applied on frame arrival with **no interpolation** between keys. `tweenEasing`
  is neither read nor meaningful here.
- **Negative `value` = hide — confirmed.** `Slot._setDisplayIndex(t)` stores
  `_displayIndex = t < displayFrames.length ? t : displayFrames.length-1`, so a
  negative `t` is kept as-is. Then `Slot._updateDisplayData` only resolves a
  display when `_displayIndex >= 0 && _displayIndex < _displayFrames.length`; for a
  negative index that guard is false → `_displayFrame`/geometry/texture stay null →
  `_updateDisplay` sets `_display = null` → **nothing is rendered for that slot**.
  So `"value": -1` hides the slot; a later frame with `value: 0` shows it again.
- **Frame durations / loop** follow the same rules as bone timelines (§7b):
  per-frame `duration` is in frames and sums to the animation `duration`; the
  terminal keyframe carries `duration: 0`; for a seamless loop the terminal value
  must equal the first frame's value.
- The index selects from the slot's **skin `display[]`** list (§5) — emit every
  display you intend to swap to. A `null` entry in `display[]` (`addDisplay(t,null)`)
  is a valid "empty" display slot the index can also point at.

---

## 12. Slot color / alpha timeline (and static slot alpha)

**PROVEN 2026-07-10 by the `figure2` fixture** for the animated `colorFrame`
(`renderer/assets/figure2/`, both filmstrips): the body slot visibly
ramped **opaque → half-alpha (`aM: 50`) → opaque** with clearly intermediate
partial-alpha frames (a smooth tween, not a stepped pop), in both a bone-driven
and a zero-bone animation. (The static-`alpha` × animated-`aM` **multiply**
check was not exercised by the fixture — the static slot `"alpha"` key remains
traced-from-source; criterion stands: `"alpha": 0.5` + `aM: 100` renders at
half opacity.)

### 12a. Animated alpha via `colorFrame` (the 5.5 path)

```jsonc
"animation": [ {
  "duration": 24, "name": "fade", "playTimes": 0,
  "slot": [
    { "name": "ghost",
      "colorFrame": [
        { "duration": 12, "tweenEasing": 0, "value": { "aM": 100 } },  // opaque
        { "duration": 12, "tweenEasing": 0, "value": { "aM": 0   } },  // invisible
        { "duration": 0,                      "value": { "aM": 100 } } // terminal = first
      ] } ]
} ]
```

- `_parseSlotTimeline(t)` builds the color timeline alongside the display one:
  `M.COLOR_FRAME in t ? _parseTimeline(t,null,"colorFrame",21,1,1,_parseSlotColorFrame)
                      : _parseTimeline(t,null,"frame",21,1,1,_parseSlotColorFrame)`.
  **Timeline type 21, and it is tween-capable.**
- `_parseSlotColorFrame(t,e,r)` calls **`_parseTweenFrame`** (so `tweenEasing`/
  `curve` from §7c **do** apply — color/alpha interpolates between keys), then reads
  the color object from **`"value"` (preferred) or `"color"`**:
  `_parseColorTransform(value, helpColorTransform)` and stores 8 ints into
  `colorArray`:
  `[round(100·alphaMultiplier), round(100·redMul), round(100·greenMul),
    round(100·blueMul), round(alphaOffset), round(redOffset), round(greenOffset),
    round(blueOffset)]`.
  An **empty `value: {}`** falls through to the shared default color offset
  `(100,100,100,100, 0,0,0,0)` = fully opaque / unchanged.
- `_parseColorTransform(t,e)` key set (all optional):
  | JSON key | field | default | scale at parse |
  |---|---|---|---|
  | `aM` | alphaMultiplier | 100 | `×0.01` → 0..1 |
  | `rM`,`gM`,`bM` | r/g/b multiplier | 100 | `×0.01` → 0..1 |
  | `aO` | alphaOffset | 0 | raw (−255..255) |
  | `rO`,`gO`,`bO` | r/g/b offset | 0 | raw (−255..255) |
  So author multipliers on a **0–100 scale** (100 = ×1.0). For pure alpha animation
  emit only `aM`: **`aM: 100` = fully visible, `aM: 0` = fully transparent.**
- **Runtime** `SlotColorTimelineState` (TimelineState.mjs; `AnimationState` `case 21`
  → `SlotColorTimelineState`): on each frame it reads the 8 `colorArray` values,
  scales the four multipliers back by `×0.01`, and **tweens** them toward the next
  frame (`_onUpdateFrame`: `result = current + difference · tweenProgress`), writing
  the slot's `_colorTransform`. Final displayed alpha =
  `alphaMultiplier · (static slot alpha) · parentGlobalAlpha` (+ `alphaOffset`).
- **Static (non-animated) slot alpha** is the slot-level `"alpha"` key (§9),
  `_parseSlot`: `a.alpha = _getNumber(e,"alpha",1)`. Range 0..1 (NOT 0..100 — this
  is a different key from `aM`). Applied in `Slot._updateAlpha` as
  `_alpha · _parent._globalAlpha`. Use it for a permanently semi-transparent part;
  use `colorFrame` to animate. (Still **UNVERIFIED-vs-sample** — the `figure2`
  fixture did not use a static slot `"alpha"`; the multiply criterion in the §12
  note above stands.)

### 12b. The dedicated alpha timeline is 5.6-only — do NOT use it

There is also an `AlphaTimelineState` (TimelineState.mjs) wired to animation
`timeline`-array entries of `type: 24` (slot alpha) / `type: 60` (bone alpha) via
`AnimationState` `case 24`/`case 60`. These live under the DragonBones **5.6**
`timeline` array, not the 5.5 per-slot `colorFrame`. **For our 5.5 target, animate
alpha with `colorFrame` `value.aM` (§12a); do not emit `type:24` timelines.**

---

## 13. Multi-region texture atlas

```jsonc
{
  "width": 1024, "height": 512,          // atlas PNG size
  "name": "cat", "imagePath": "cat_tex.png",
  "SubTexture": [
    { "name": "body", "x": 0,   "y": 0, "width": 200, "height": 150 },  // untrimmed
    { "name": "wing", "x": 200, "y": 0, "width": 120, "height": 90  }   // untrimmed
    // trimmed example (we do NOT emit this): add frameX/frameY/frameWidth/frameHeight
  ]
}
```

- `parseTextureAtlasData(t,e,r)` (ObjectDataParser.mjs) reads top-level `width`,
  `height`, `name`, `imagePath`, then each `SubTexture`:
  - `name` (`_getString`) — the region key that display `name`/`path` binds to.
  - `x`, `y`, `width`, `height` (`_getNumber`, default 0) → `texture.region`
    (atlas-pixel rectangle).
  - `rotated` (`_getBoolean`, default **false**).
  - `frameWidth`, `frameHeight` (`_getNumber`, default **−1**); `frameX`, `frameY`
    (`_getNumber`, default 0). A trim `frame` rectangle is created **only if
    `frameWidth > 0 && frameHeight > 0`**: `i>0 && s>0 && (n.frame = createRectangle
    (); n.frame.x = frameX; n.frame.y = frameY; n.frame.width = frameWidth;
    n.frame.height = frameHeight)`. **Omitting all `frame*` (our plan, untrimmed) →
    `n.frame` stays null → safe.** ✓
- **UVs are region-relative — confirmed.** `PixiTextureAtlasData` builds each
  SubTexture's `renderTexture = new Texture({ source, frame:
  Rectangle(region.x, region.y, region.width, region.height), orig: same, trim:
  Rectangle(0,0,region.width,region.height), rotate: rotated?groupD8.S:0 })`.
  At render, `PixiSlot._updateFrame` binds the display to that region texture and
  uses the mesh/image `uvs` **verbatim** (`geometry.uvs = p`, with `p[e]=u,
  p[e+1]=v` for unrotated). So a display's `uvs` in `[0..1]` span **its own
  SubTexture region**, not the whole atlas PNG. Each display therefore samples only
  its named region.
- One atlas PNG can hold **many** regions; each slot's display binds to one region
  by `name`. (`frameX/frameY/frameWidth/frameHeight` describe how a trimmed region
  sits inside its original untrimmed bounds — irrelevant to us since we emit
  untrimmed regions with `frame*` omitted.)
- **Rotated regions:** if `rotated:true`, the runtime swaps width/height and sets
  `rotate: groupD8.S`, and `PixiSlot._updateFrame` maps mesh uvs as
  `p[e]=1−v, p[e+1]=u`. **We emit `rotated:false`** (no atlas packer rotation).
- **Cross-check (mecha atlas, 19 SubTextures):** untrimmed regions like
  `{"name":".../forearm_r","x":557,"y":342,"width":125,"height":56}` and
  `.../upperarm_r` **omit `frame*` entirely** — exactly our plan; trimmed regions
  like `{"name":".../hand_r","x":683,"y":297,"width":57,"height":41,"frameX":0,
  "frameY":0,"frameWidth":57,"frameHeight":42}` carry the full `frame*` set. Both
  parse without error, and distinct `x/y` per region confirms multi-region packing
  in one PNG. ✓ (you_xin's atlas likewise mixes trimmed and untrimmed regions.)

---

## 14. Animation with zero bone timelines (swap-only / color-only) — accepted

**Confirmed** that an animation whose only timelines are slot timelines parses and
plays. `_parseAnimation(e)` (ObjectDataParser.mjs) handles each timeline family in
an **independent `if (KEY in e)` block** — `zOrder`, `bone`, `slot`, `ffd`, `ik`,
`timeline` — with **no interdependency and no requirement that `bone` be present**.
If only `"slot"` is present, the loop `for(const e of slot) this._parseSlotTimeline(e)`
runs and the display/color timelines are added via `_animation.addSlotTimeline(...)`.
The animation object is created regardless (name, `duration`, `playTimes` come from
the top level). A `"slot"`-only animation (e.g. the §11 `talk` swap or the §12
`fade`) is valid; it needs no bone track. (**PROVEN 2026-07-10 by the `figure2`
fixture's `swaponly` animation** — 24 frames, no `bone` key at all: it parsed
without error (non-null, duration 1.000s) and played its §11 swap + §12 alpha
ramp while the arm stayed completely static across all filmstrip panels
(`out/figure2-swaponly.filmstrip.png`); that one animation is the proof for
§11, §12, and §14 together.)

---

## 15. Animated draw order — the zOrder timeline

**[PROVEN 2026-07-10 — fixture `renderer/assets/zorder`, filmstrip
probe]** — originally recovered from the shipped, de-minified
`pixi-dragonbones-runtime@8.0.3` lib (`node_modules/.pnpm/pixi-dragonbones-
runtime@8.0.3_.../lib/`) and cross-checked line-for-line against the matching
GitHub tag `v8.0.3` (commit `eb6d827`,
`packages/pixi-dragonbones-runtime/src/…`). Now **fixture-PROVEN**: a minimal
hand fixture (two overlapping 40×40 squares, slot order `[back, front]`, one
`flip` animation whose ONLY timeline is a `zOrder` — no bone/slot track) renders
through the headless renderer and its Pixi children physically re-sort mid-animation.
Probe of `out/zorder-fixture.filmstrip.png` at the overlap-zone center
(panel px `(64,64)`) over the 2 s loop, sampled at t = 0/.4/.8/1.2/1.6/2.0 s:
RGB `red, red, blue, blue, red, red` — the frame-1 `zOrder:[0,1]` (raise `back`
by +1) lifts blue above red for its window `[0.5 s, 1.5 s)`, then the terminal
empty frame restores base (red on top) for a seamless loop; the squares never
move. Corrections the fixture forced: **none** — the traced JSON shape (`zOrder:
{ "frame":[ … ] }`, flat `(slotIndex, offset)` pairs, empty terminal frame =
restore base, §14 zero-bone-timeline acceptance) all held exactly as written.
Note: with endpoint sampling the panel at t = 1.6 s lands in the `[1.5 s, 2.0 s)`
restore window → red (not blue); the flip window is `[0.5 s, 1.5 s)` per the
15/30/15-frame candidate. This section describes how to *animate* the draw order
that §9/§10 pin *statically* (a slot's base `zOrder` = its `armature.slot[]`
list index).

### 15a. Where the timeline lives and its object shape

```jsonc
"animation": [ {
  "duration": 24, "name": "shuffle", "playTimes": 0,
  "zOrder": {                       // ← the zOrder TIMELINE object (per animation)
    "frame": [                      // ← the ONLY key the parser reads
      { "duration": 12, "zOrder": [2, 1] },   // frame 0: move slot#2 up by +1
      { "duration": 12, "zOrder": [2, 1] },   // hold
      { "duration": 0 }                        // terminal: restore base order
    ]
  }
} ]
```

- **One `zOrder` object per animation**, a sibling of `bone`/`slot`/`ffd`/`ik`.
  `_parseAnimation(e)` (ObjectDataParser.mjs) handles it in its own independent
  `M.Z_ORDER in e` block (`M.Z_ORDER === "zOrder"`, DataParser.mjs) — exactly
  the per-family independence documented in §14; **no bone/slot track is
  required** for a zOrder-only animation:
  `this._animation.zOrderTimeline = this._parseTimeline(e[M.Z_ORDER], null,
  M.FRAME, 1, 0, 0, this._parseZOrderFrame)`.
- The **timeline object's frame array lives under key `"frame"`** (`M.FRAME ===
  "frame"`, the 3rd arg above). `_parseTimeline(e, r, a, …)` reads
  `a in e && (r = e[a])` → `e["frame"]`; if `"frame"` is absent or empty the
  call **returns null and the timeline is simply dropped** (ObjectDataParser.mjs
  `_parseTimeline`, the `null === r … 0 === _` early-outs). So this is `zOrder =
  { "frame": [...] }`, **not** a bare array.
- **`TimelineType.ZOrder === 1`** (4th arg; `core/DragonBones`) and **the 5th arg
  is `FrameValueType.Step === 0`** — the value type that carries *no* tweenable
  numeric channel (see 15c).
- **Extra timeline-object keys `"scale"`/`"offset"` are accepted but inert for
  zOrder.** `_parseTimeline` always reads `M.SCALE` (default 1) and `M.OFFSET`
  (default 0) into `timelineArray[A+0] = round(100·scale)` /
  `[A+1] = round(100·offset)`. These scale/offset the *numeric frame value* of
  tweened timelines (bone translate/rotate); a Step timeline has no numeric
  value, so they do nothing here. **We emit neither — omit them.** (Defaults
  land as 100 / 0.)

### 15b. Frame shape and the "restore base order" frame

Each frame is `{ "duration": N, "zOrder": [slotIndex, offset, slotIndex,
offset, …] }` — a flat array of **(slotIndex, zOrderOffset) integer pairs**,
read by `_parseZOrderFrame(rawData, frameStart, frameCount)`
(ObjectDataParser.mjs; GitHub names in parens):

- `slotIndex` (`rawZOrder[i]`) indexes into `armature.sortedSlots` — the **base
  draw order** (slots sorted by their static `zOrder` = `armature.slot[]` list
  index, §9). `zOrderOffset` (`rawZOrder[i+1]`) is a **signed shift** of
  that slot's position (`+` = toward front / higher index).
- A frame whose **`zOrder` key is absent, or is an empty array**, is the
  **restore-to-base-order** frame: `_parseZOrderFrame` writes
  `frameArray[frameOffset+1] = 0` (the count) and stores no permutation. At
  runtime that `0` routes to `_sortZOrder(null, 0)` = reset to base order
  (15e). **Use an empty/absent `zOrder` on the terminal frame to snap the stack
  back to rest** (mirrors §11's `displayFrame` returning to display 0 for a
  seamless loop).
- Pairs are expected in **ascending `slotIndex` order** — the parser walks a
  single rising `originalIndex` cursor and does `while (originalIndex !==
  slotIndex) …` (15d); a descending or duplicate `slotIndex` would desync that
  walk. Emit pairs sorted by `slotIndex`.

### 15c. No tween / no interpolation — confirmed

Draw order is **stepped, never interpolated**. Three independent confirmations:
1. Parse uses `FrameValueType.Step` (`0`), and `_parseZOrderFrame` calls the
   plain **`_parseFrame`** (records only the frame start) — **not**
   `_parseTweenFrame` — so no `tweenEasing`/`curve` is ever read for this
   timeline (contrast the color timeline §12a, which uses `_parseTweenFrame`).
2. The runtime state class **`ZOrderTimelineState`** (TimelineState.mjs) defines
   **`_onUpdateFrame() {}` empty** — there is literally no per-tick blend; all
   work happens once, on frame arrival.
3. Order changes therefore **pop** at each frame boundary. `tweenEasing`/`curve`
   keys on a zOrder frame are silently ignored — do not author them.

### 15d. How the parser precomputes the full slot permutation

`_parseZOrderFrame` does **not** store the raw pairs — it precomputes, per
frame, a **complete permutation of all `slotCount` slots** (`slotCount =
armature.sortedSlots.length`) into `frameArray`:

```
if "zOrder" absent or empty:            // the restore-to-base frame (15b):
    frameArray[base+1] = 0; return      //   count 0 → _sortZOrder(null,0) at runtime
unchanged = int[slotCount - pairs]      // base slots NOT explicitly moved
zOrders   = int[slotCount] filled -1    // final[pos] = base slot index, or -1
originalIndex = 0
for each (slotIndex, zOrderOffset) pair:            // pairs ascending by slotIndex
    while (originalIndex !== slotIndex)              // slots skipped over…
        unchanged[u++] = originalIndex++             // …recorded as "unchanged"
    zOrders[originalIndex + zOrderOffset] = originalIndex++   // moved slot claims its target
while (originalIndex < slotCount)                    // trailing slots…
    unchanged[u++] = originalIndex++                 // …also "unchanged"
// materialize, scanning final positions high→low:
for i = slotCount-1 … 0:
    frameArray[base+2+i] = (zOrders[i] === -1) ? unchanged[--u]   // fill hole from tail
                                               : zOrders[i]        // moved slot
frameArray[base+1] = slotCount                      // the >0 count (see 15b/15e)
```

**Observable effect:** the stored frame is an array `perm[0…slotCount-1]` where
`perm[finalPosition] = base slot index` drawn there (bottom→top). Moved slots
land at their `originalIndex + offset`; every other slot flows into the
remaining holes **in ascending base order** (the `unchanged` list is filled
ascending and consumed from its tail while `i` descends, which re-emits them
ascending).

Worked micro-example — 4 slots `[A,B,C,D]` (base positions 0..3, D on top),
frame `"zOrder": [2, 1]` (raise C by one):
`unchanged=[0,1,3]`, `zOrders=[-1,-1,-1,2]` → materialized **`perm =
[0, 1, 3, 2]`**. Final stack bottom→top = A, B, **D, C** — C jumps above D; A/B
untouched. ✓

**Deterministic collision rule:** if two pairs resolve to the **same** target
`originalIndex + zOrderOffset`, the **later pair wins** (`zOrders[index] = …`
overwrites); the earlier mover is then absent from both `zOrders` and
`unchanged`, so it is **dropped** and some hole is instead back-filled from the
`unchanged` tail — a slot can silently vanish/duplicate in the stack. **Author
distinct targets:** never send two slots to the same absolute position; give
each moved slot an offset that lands it on its own spot.

### 15e. Terminal-frame duration / hold convention

Same as bone and slot timelines (§7/§11): **non-terminal frame `duration`s
accumulate as frame starts, and the terminal frame is stretched to the
animation's `frameCount` regardless of its own `duration`.** In
`_parseTimeline`'s span loop, a non-last frame's span is
`_getNumber(frame, "duration", 1)`, while the last frame (`s === _-1`) gets
`this._animation.frameCount - n` (n = its start). So:
- Frame durations **sum to the animation `duration`** (frames), exactly like
  every other timeline family.
- A **terminal `{ "duration": 0 }` hold is allowed and idiomatic** (its own
  duration is ignored; it holds to the end) — just like the §11 `displayFrame`
  terminal key.
- **Seamless loop:** because order snaps (15c), the **last frame's resolved
  order must equal frame 0's** for a clean loop — most simply, make the terminal
  frame a restore-to-base frame (empty/absent `zOrder`, 15b) *and* start frame 0
  from base order.

### 15f. Runtime application path — Pixi children really re-sort mid-animation

Traced end to end through the shipped runtime:

1. **`AnimationState`** (AnimationState.mjs) borrows a `ZOrderTimelineState`
   into `this._zOrderTimeline` when `animationData.zOrderTimeline !== null`, and
   each `advanceTime` calls `this._zOrderTimeline.update(o)` (guarded by
   `playState <= 0`).
2. **`ZOrderTimelineState._onArriveAtFrame`** (TimelineState.mjs) reads the
   precomputed count and dispatches:
   `frameArray[frameOffset+1] > 0 ? armature._sortZOrder(frameArray,
   frameOffset+2) : armature._sortZOrder(null, 0)` — i.e. apply the permutation
   (starting at `frameOffset+2`, 15d) or **restore base order** (15b).
3. **`Armature._sortZOrder(slotIndices, offset)`** (Armature.mjs) walks
   `armatureData.sortedSlots`; for each final position `n` it reads base index
   `slotIndices[offset+n]` (or `n` when `slotIndices===null` = base order),
   resolves the slot by name, and calls **`slot._setZOrder(n)`**. It sets
   `_slotsDirty = true`.
4. **`Slot._setZOrder(n)`** (Slot.mjs) writes `_zOrder = n` and flags
   `_zOrderDirty = true`.
5. Back in **`Armature.advanceTime`**, after `_animation.advanceTime`, when
   `_slotsDirty` it does `this._slots.sort(Armature._onSortSlots)` (sort key
   `1000·_zIndex + _zOrder`), then each `slot.update()` sees `_zOrderDirty` and
   calls **`_updateZOrder()`**.
6. **`PixiSlot._updateZOrder`** (PixiSlot.mjs) is the payoff:
   `armature.display.addChildAt(this._renderDisplay, this._zOrder)` — it
   **re-inserts the Pixi display child at its new index** in the armature
   container. `addChildAt` on an existing child reorders it, so **Pixi's display
   list is physically re-sorted mid-animation** — the draw order visibly changes
   frame-to-frame. ✓ (This is the observable behavior a later fixture must
   capture on a filmstrip to upgrade §15 to PROVEN.)

---

## 16. Path display + path constraint (v1: a bone chain flows along a curve)

**[PROVEN 2026-07-11 — fixture `renderer/assets/path`, filmstrip
probe]** — originally traced to functions in `pixi-dragonbones-runtime@8.0.3`
(`armature/Constraint.mjs` `PathConstraint`, `parser/ObjectDataParser.mjs`
`_parsePathConstraint`/`_parsePath`/`_parseGeometry`, geometry `case 4`,
`factory/BaseFactory.mjs` `_buildConstraints`/`_getSlotDisplay`,
`core/DataParser.mjs` enum maps) and cross-checked against upstream DragonBonesJS.
Now **fixture-PROVEN**: a minimal hand fixture (three 12×12 squares on chain
bones `c1,c2,c3` of `length:20`, a two-cubic-segment bow through world anchors
(64,100)→(84,60)→(64,20), owner bone `root` swaying ±15°) renders through the
headless renderer; the squares sit ON the bow and sway with the owner. Probe of
`out/path-fixture.filmstrip.png` (8 panels, 2 s loop): blob centroids move
per-panel and return exactly (panel 0 == panel 7, checksums equal — seamless);
bone matrices at rest match pure bezier math to 0.1 px — c2 at
`(75.5, 82.2)` vs predicted `(75.54, 82.16)` (seg-0 cubic at `t = 20/45.4135`),
c3 at `(83.5, 65.4)` vs `(83.51, 65.40)`; under ±15° sway c2 travels 11.0 px vs
the predicted rigid-rotation chord `2·21.2·sin 15° = 11.0`. **Corrections the
fixture forced — three, all load-bearing:** (1) §16b's derived
"weight-to-the-owner-bone" recommendation is WRONG — weighting the geometry to
the animated owner (or using unweighted geometry, which touches the owner the
same way) **freezes the whole armature** via a `_localDirty` deadlock; weight to
a dedicated anchor bone that sorts AFTER the constraint's root bone (§16b/§16c).
(2) `rotateMode:"tangent"` computes a garbage angle in this runtime — a port bug
collapses the tangent to `atan2(endAnchorY, endAnchorX)`, the world-origin
bearing of the current segment's END anchor; use `"chain"` for real
curve-following rotation (§16c). (3) `constantSpeed:false` maps arc position to
the bezier parameter **linearly within a segment** (`t = p/segmentLength`, not
arc-true), so equal spacing is approximate on curved segments (§16b). Everything
else — grouping, padding, lengths semantics, coordinate space, silent-drop
rules, spacing-consumes-bone-length — held exactly as derived.

**Static-params caveat (load-bearing).** DragonBones has **no PathConstraint
timeline type** (no `TimelineType` value, no `PathConstraintTimelineState`, no
`AnimationState` case dispatch — IK has all three, path has none). Params are parsed once in
`PathConstraint.init`. So
`position`/`spacing`/`rotateOffset`/`rotateMix`/`translateMix` are **static**, fixed
at build time. Motion of the chain comes ONLY from animating the **bones that move
the path's control points** — for v1 rigid that is the single owner bone the path
slot is parented to (rotate/translate it and the whole curve sways, chain follows).
"Tween a bone's position ALONG a static curve over the timeline" is not expressible.

A path is three coordinated pieces: (1) a **slot** parented to the owner bone,
(2) a `type:"path"` **display** in the default skin carrying the curve geometry
(§16b), and (3) an **armature-level `"path"` constraint** naming that slot + the
chain bones (§16a).

```jsonc
{
  "bone": [ /* owner bone + chain bones (chain bones need a "length", §16d)
               + geometry ANCHOR bone declared LAST (§16b/§16c — required) */ ],
  "slot": [ { "name": "tailPath", "parent": "hip" } ],          // path slot on owner bone
  "skin": [ { "slot": [ { "name": "tailPath", "display": [ {    // default skin
    "type": "path", "name": "tailPath",
    "closed": false, "constantSpeed": false,
    "lengths": [ /* cumulative arc length per curve segment, §16b */ ],
    "vertexCount": 9, "vertices": [ /* flat control points, §16b */ ], // write the real point count (fixture: 9); parser recomputes from vertices.length and ignores this field, §16b
    "weights": [ /* 1.0 to the anchor bone per point — REQUIRED, §16b */ ],
    "slotPose": [1, 0, 0, 1, 0, 0], "bonePose": [ /* anchor bind, §16b */ ]
  } ] } ] } ],
  "path": [ {                                                    // armature-level array
    "name": "tailFlow", "target": "tailPath", "targetDisplay": "tailPath",
    "bones": ["tail1", "tail2", "tail3"],
    "positionMode": "percent", "spacingMode": "length", "rotateMode": "chain",
    "position": 0, "spacing": 0, "rotateOffset": 0,
    "rotateMix": 1, "translateMix": 1
  } ]
}
```

### 16a. The armature-level `"path"` constraint array

`_parseArmature` iterates `armature["path"][]` (`PATH_CONSTRAINT = "path"`), each
entry through `_parsePathConstraint(e)`; a non-null result is added
(`t && addConstraint(t)`), a null result is **silently discarded**. Keys read
(`_parsePathConstraint`, ObjectDataParser.mjs):

| JSON key | type | default | meaning |
|---|---|---|---|
| `name` | string | `""` | constraint name (`_getString`). |
| `target` | string — a **SLOT** name | **required** | the path slot (`getSlot`). The constraint's *target bone* is set to **this slot's parent bone** (`n.target = r.parent`) — the owner bone. |
| `targetDisplay` | string | `= target` (the slot name) | which display in the **default skin** supplies the path geometry (`defaultSkin.getDisplay(slotName, targetDisplay)`); must exist AND be a `PathDisplayData`. |
| `bones` | string[] — **BONE** names | **required, non-empty** | the constrained chain, parent-first. Each resolvable name is added; the **first resolvable** becomes `root` — the constraint's driver and dirty anchor: `PathConstraint.init` resolves it (`_root = armature.getBone(root.name)`, sets `_root._hasConstraint = true`), the constraint's `update()` is invoked from that bone's own update (`Bone.mjs`: `for (const c of armature._constraints) c._root === this && c.update()`), and the per-frame recompute is gated on `_root._childrenTransformDirty` — see §16c for the freeze failure mode this creates. |
| `positionMode` | `"fixed"`\|`"percent"` | **`percent`** | `_getPositionMode`: `fixed`→0, `percent`→1. Empty/absent → default branch → percent. `percent` multiplies `position` (and, in `_computeBezierCurve`, spacing) by the total path length. |
| `spacingMode` | `"length"`\|`"fixed"`\|`"percent"` | **`length`** | `_getSpacingMode`: `length`→0, `fixed`→1, `percent`→2. See §16d. |
| `rotateMode` | `"tangent"`\|`"chain"`\|`"chainscale"` | **`tangent`** | `_getRotateMode`: `tangent`→0, `chain`→1, `chainscale`→2. `tangent`=each bone aims down the curve tangent; `chain`=preserves inter-bone angles; `chainscale`=also scales bones to the curve (forces per-bone length math, §16d). |
| `position` | number | 0 | start offset along the curve (×pathLength when `positionMode:"percent"`). |
| `spacing` | number | 0 | inter-bone spacing along the curve (§16d). |
| `rotateOffset` | number | 0 | degrees added to each bone's resulting rotation. |
| `rotateMix` | number | 1 | 0..1 blend of the path-driven rotation vs the bone's own. |
| `translateMix` | number | 1 | 0..1 blend of the path-driven translation vs the bone's own. |

**Silent-drop rules** — `_parsePathConstraint` returns `null` (constraint quietly
vanishes, no error) when ANY of:
1. `target` slot not found — `getSlot(...) === null`.
2. armature has no `defaultSkin` (`null`).
3. `targetDisplay` display not found in the default skin, OR found but **not a
   `PathDisplayData`** (`!(i instanceof PathDisplayData)`).
4. `bones` key missing, or an empty array.
Additionally, individual `bones` names that don't resolve (`getBone` null) are
**skipped** with no error; author every name to a real bone (and put the intended
`root` first — it is the first *resolvable* entry).

### 16b. The `type:"path"` display and its geometry

`_parseDisplay` geometry `case 4` (ObjectDataParser.mjs) builds a `PathDisplayData`:

```jsonc
{
  "type": "path",            // DisplayType.Path = 4 (via _getDisplayType or raw int)
  "name": "tailPath",        // display name; = the targetDisplay the constraint looks up
  "closed": false,           // _getBoolean(CLOSED, false)      — loop the curve?
  "constantSpeed": false,    // _getBoolean(CONSTANT_SPEED, false) — see below
  "lengths": [ … ],          // REQUIRED for the constantSpeed:false path (read as e["lengths"] with NO guard — a missing key throws)
  "vertexCount": 0,          // IGNORED by the parser (see below)
  "vertices": [ x0,y0, … ]   // flat control points; geometry via _parsePath → _parseGeometry
}
```

`case 4` copies `lengths` verbatim into `curveLengths`, then `_parsePath` calls the
**shared mesh-geometry parser** `_parseGeometry` on `vertices`. Consequences:
- The JSON `"vertexCount"` field is **not read** — `_parseGeometry` computes
  `vertexCount = floor(vertices.length / 2)` and stores it at `intArray[offset+0]`.
  (`0` in the example is a harmless placeholder; the flat `vertices` length is what
  counts.)
- `triangles`/`uvs` are optional and normally absent for a path (triangleCount → 0).
- `weights`/`slotPose`/`bonePose` follow §6b/§6c exactly and are **REQUIRED for
  v1**: the geometry must be weighted `1.0` to a dedicated anchor bone declared
  AFTER the chain (see the "Unweighted geometry [PINNED: unusable]" bullet and the
  writer rule below — unweighted or owner-weighted geometry freezes the armature).

**Control-point grouping — how `_computeBezierCurve` indexes `vertices`.**
With `t = PathDisplayData`, `o = intArray[t.geometry.offset+0] = vertexCount`:
- `_ = 2·o` (float count), `c = _/6 = vertexCount/3`. `c` is used directly as an
  integer array index with **no `Math.floor`**, so **`vertexCount` MUST be a
  multiple of 3.**
- In the `constantSpeed:false` branch (an authored `lengths` array — what our
  writer emits): `c -= closed ? 1 : 2`; `pathLength = curveLengths[c]`.
- Each curve segment `k` reads an **8-float window** (4 control points) at float
  index `6·k + 2`: `_computeVertices(6*k+2, 8, 0, l)`. So the layout is a **chain of
  cubic béziers with a 6-float (3-control-point) stride per segment and shared
  endpoints** — segment `k`'s 4th point is segment `k+1`'s 1st. `addCurvePosition`
  then evaluates one cubic from those 8 floats (`p0,c1,c2,p1`). This is the standard
  DragonBones/Spine convention, **derived here from the `6*k+2`/`8` index math**, not
  assumed.
- Closed wrap (`k===c`): the closing segment = last-2 points + first-2 points
  (`_computeVertices(_-4,4,0,l)` then `_computeVertices(0,4,4,l)`) — the curve seams
  vertex 0 back to the tail.
- Therefore `curveLengths` (the `lengths` array) holds **cumulative** arc-lengths,
  one per walked segment, last entry = total length; entry count = `c+1` = **open:
  `vertexCount/3 − 1`, closed: `vertexCount/3`**. Equivalently, for `nSeg` visible
  cubic segments: **closed → `vertexCount = 3·nSeg`; open → `vertexCount =
  3·(nSeg+1)`** (open leaves control points 0 and `vertexCount−1` as unused end
  handles — segment 0 starts at point index 1, `6·0+2`). **[PINNED by the
  fixture]**: 2 open segments → 9 control points (18 floats) laid out
  `[hIn₀, a₀, hOut₀, hIn₁, a₁, hOut₁, hIn₂, a₂, hOut₂]` (per-anchor
  handle-in/anchor/handle-out triplets); the end handles at point 0 and point 8
  are never read (the fixture uses mirror handles; values are irrelevant);
  `lengths` = 2 cumulative entries `[45.4135, 90.827]` (each cubic sampled at 64
  points). Rendered bone positions matched this layout's curve math to 0.1 px.
- **Position→parameter mapping (`constantSpeed:false`) [PINNED]**: within a
  segment the solver uses `t = p_local/segLen` **directly as the bezier
  parameter** — linear, NOT arc-length-true. Fixture evidence: the bone at arc
  20 landed at the seg-0 cubic evaluated at `t = 20/45.4135 = 0.4404` exactly
  (`(75.54, 82.16)` predicted, `(75.5, 82.2)` rendered), which is why measured
  inter-bone chords (21.2 px, 18.6 px) straddle the nominal 20 px spacing.
  `constantSpeed:true` (runtime-computed lengths + 10-segment resampling) is the
  arc-true variant; untested.

**Vertex coordinate space (owner-bone-local vs world), and a load-bearing upstream
quirk.** All from `_updatePathVertices(geometryData)`:
- **Owner-bone-local space (unweighted).** When `geometry.weight === null`, each raw
  vertex (× armature `scale`) is pushed through
  **`this._pathSlot.parent.globalTransformMatrix`** — the owner bone's world matrix
  (`x = a·vx + c·vy + tx`, `y = b·vx + d·vy + ty`). Unweighted path vertices are in
  the **owner bone's LOCAL space**, NOT verbatim world.
- **CRITICAL — the unweighted loop is half-length.**
  `for (i = 0, iV = floatOffset; i < vertexCount; i += 2)` runs only `vertexCount/2`
  iterations, filling only the first `vertexCount` of the `2·vertexCount`
  `_pathGlobalVertices` slots — the rest is undefined and the bézier solver reads
  garbage. **Identical in upstream DragonBonesJS** (long-standing upstream quirk, not
  a port bug); real DragonBones exports always emit weighted path geometry. The
  **weighted branch loops correctly** (one control point per iteration, all slots
  filled), reconstructing each point as `Σ weightᵢ · (boneWorldᵢ · boneLocalᵢ)` per
  §6b/§6c.
- **Writer rule (v1 rigid) [PINNED — corrected by the fixture]: emit WEIGHTED
  geometry, every control point weighted `1.0` to a DEDICATED ANCHOR BONE — NOT
  to the owner bone.** The originally derived weight-to-owner shape **freezes
  the entire armature** (fixture round 1: constraint solved once, then every
  frame identical while the animation state kept advancing — see §16c for the
  `_localDirty` deadlock mechanism). The working shape:
  - add a bone (fixture: `"pathBone"`) whose parent is the owner bone, identity
    transform, no slots/children, **declared in `bone[]` AFTER the constraint's
    root bone** (`sortBones` keeps declaration order parent-first, so
    "declared last" ⇒ sorts after the chain — the property §16c's deadlock
    analysis requires);
  - `weights` = `[1, anchorGIdx, 1.0]` per vertex, `slotPose` = identity,
    `bonePose` = `[anchorGIdx, <anchor's world bind matrix>]` (§6c) — with an
    identity-transform anchor that is the owner's world bind matrix;
  - control points are then authored in world/armature space exactly like a §6
    mesh **[coordinate space PINNED: rest render matched world-space curve math
    to 0.1 px]**, and the curve rides rigidly on the owner via the anchor.
- **Unweighted geometry [PINNED: unusable].** A scratch variant with no
  `weights` (vertices in owner-local space) froze identically — the unweighted
  branch calls `updateByConstraint()` on `pathSlot.parent` (the owner), the same
  §16c poison. Its single computed frame *looked* right only because all three
  bones sampled segment 0: the half-length copy loop fills just the first
  `vertexCount` of the `2·vertexCount` float slots (first ⌈vertexCount/2⌉
  points — here floats 0–9 = points 0–4, exactly segment 0's window); any
  position landing in segment 1 reads `undefined` → NaN. Both defects are
  independent; either alone disqualifies unweighted.

### 16c. Runtime behavior

- **Real solver, wired.** `BaseFactory._buildConstraints` `case 1` borrows a
  `PathConstraint` and `init`s it (`case 0` = IK). The constraint updates every tick
  it is dirty: `PathConstraint.update()` first requires the slot's geometry offset to
  match `pathOffset`, then gates the recompute on
  **`this._root._childrenTransformDirty`** (root = the first chain bone, §16a), else
  on `slot._verticesDirty || slot._isBonesUpdate()` (`Slot._isBonesUpdate` scans the
  slot's `_geometryBones` — populated only for **weighted** geometry), else — if
  `this.dirty` is also false — **returns without touching the bones**. When it does
  run, it recomputes `_pathGlobalVertices` from the **live bone matrices**, samples
  the bézier, and **writes each chain bone's `globalTransformMatrix` and `global`**
  (via `bone.global.fromMatrix`). Bones really move; the constraint is authoritative
  over the chain each frame it fires.
- **THE `_localDirty` DEADLOCK [PINNED — fixture round 1 froze on it]: the
  constraint must never call `updateByConstraint()` on a bone that already
  updated earlier in the same tick and needs to rebuild next tick.**
  Mechanism, from `Bone.mjs`: `updateByConstraint()` runs
  `if (_localDirty) { _localDirty = false; if (_transformDirty || parent
  dirty) rebuild; _transformDirty = true }`, and `Bone.update()` only rebuilds
  its matrix when `_localDirty` is true (resetting `_localDirty = true` at the
  end). The constraint fires inside the FIRST CHAIN BONE's update. Every
  geometry bone (and `pathSlot.parent` in the unweighted branch) gets
  `updateByConstraint()` called on it there. If that bone sorts BEFORE the
  first chain bone (owner bones always do — they're ancestors), the call lands
  AFTER the bone's own update: `_localDirty` is cleared, so next tick the
  bone's update sees `_transformDirty` true but `_localDirty` false → skips the
  rebuild → its matrix never re-absorbs `animationPose` → **the owner freezes
  at its first-tick pose and the whole rig with it** (fixture: animation state
  advanced 0→2 s while `root` sat at −15° forever). If the geometry bone sorts
  AFTER the first chain bone instead, the same call arrives BEFORE that bone's
  own update, its parent (the owner, already updated this tick) has
  `_childrenTransformDirty` set, so `updateByConstraint` rebuilds it FRESH and
  the poison never lands — this is why §16b's anchor bone must be declared
  after the chain. Bonus: the anchor's `_childrenTransformDirty` then stays
  true every tick, so `slot._isBonesUpdate()` re-fires the recompute reliably;
  the fixture swayed at every sampled frame (7/8 distinct checksums,
  first == last). Keep the first chain bone a descendant of the owner anyway —
  it costs nothing and keeps the `_root._childrenTransformDirty` gate alive as
  a second trigger.
- **`rotateMode:"tangent"` is BROKEN in this runtime [PINNED — port bug].**
  `addCurvePosition` computes the tangent by subtracting the **cubic**-basis
  partial sum (`u³,3u²t,3ut²`) from the curve point — the difference collapses
  to `t³·(endAnchor)`, so the "tangent" is `atan2(endAnchorY, endAnchorX)`: the
  world-origin bearing of the current segment's END anchor. (Spine's original
  subtracts the **quadratic** basis `u²,2ut,t²` — the de Casteljau tangent
  trick; the port swapped coefficients.) Fixture evidence: c2 and c3 (different
  arc positions, same segment) both rendered at exactly `atan2(60,84) = 35.5°`
  at rest and `37.6°` at −15° sway (the anchor rotated about the pivot to
  `(72.96, 56.18)`, `atan2 = 37.6°` — matches to 0.1°). The angle is constant
  per segment, origin-dependent, and unrelated to the curve direction; at
  position exactly 0 the solver short-circuits to angle 0. Positions are
  unaffected. Fixture evidence above is from the uncommitted `"tangent"`
  scratch variant — the committed fixture (`renderer/assets/path/
  path_ske.json`, as committed) runs `rotateMode:"chain"` by
  default. **`rotateMode:"chain"` works correctly**, verified by that
  committed fixture itself: each bone's rotation = the chord bearing to the
  next sample — c1 rendered −57.1° = `atan2(−17.8, 11.5)` exactly, and all
  rotations tracked the sway ±15°. Writers that care about rotation along
  the curve must emit `"chain"` (or `rotateMix: 0` to keep FK rotation);
  `"chainscale"` is unproven.
- **The path display renders as null, safely.** `BaseFactory._getSlotDisplay`'s
  switch handles only `case 0` (image), `case 1` (armature), `case 2` (mesh) — a
  `type:4` path display falls through and `l` stays `null`, so **nothing is drawn and
  nothing crashes**. The constraint still obtains its control points from the slot's
  `_geometryData` (the type-4 geometry) independent of any rendered display. The path
  slot is purely a geometry carrier; author it with no texture expectation.
- **No PathConstraint timeline type exists** — constraint params are static and all
  motion is bone-driven; full statement and source basis in the intro's
  "Static-params caveat" above.

### 16d. Spacing semantics — `spacingMode:"length"` + `spacing:0`

**Spacing from bone length.** In `PathConstraint.update()`, when
`spacingMode === "length"` (0) — or
whenever `rotateMode === "chainscale"` (2), which forces the same computation — the
per-gap spacing is filled from each chain bone's own length:

```
spaces[0] = 0
for each chain bone t (except the last spaces slot):
    boneLen  = bone._boneData.length            // the setup "length" (§below)
    worldLen = boneLen · sqrt(a² + b²)           // setup length carried to world scale
    spaces[t+1] = (boneLen + spacing) · worldLen / boneLen
```

With **`spacing:0`** this reduces to `spaces[t+1] = worldLen` — i.e. each successive
chain bone is placed one **bone-length** further along the curve, so the bones lie
**head-to-tail, each occupying exactly its own (world-scaled) setup length.** Yes:
`spacingMode:"length"` + `spacing:0` **consumes each chain bone's `length`** as its
spacing. This requires every chain bone to carry a nonzero **`"length"`** (parsed
`bone.length = _getNumber(bone, "length", 0)`, default **0**; `length:0` → division by
`boneLen` → `NaN` → collapsed chain). Emit a `"length"` on each constrained bone
roughly equal to its rest span.

The other modes: `spacingMode:"fixed"` (1) and `spacingMode:"percent"` (2) fill
`spaces[t] = spacing` uniformly (percent additionally scales spacing by the total
path length inside `_computeBezierCurve`, mirroring `positionMode:"percent"`);
neither reads bone length.

## 17. Weighted path geometry — several anchor bones BEND the curve (v2) — PROVEN

Status: **PROVEN** by the hand fixture `renderer/assets/path-weighted/`
(2026-07-15; derivation from `Constraint.mjs` `PathConstraint._updatePathVertices`
weighted branch and `Slot.mjs` `_geometryBones`/`_isBonesUpdate`). Fixture
evidence: 4 anchors, animation keys ONLY `a2` (nothing upstream of the chain
dirty) — 7/8 distinct frames (first=last is loop closure), no freeze, no NaN;
`c1` square static to 0.00 px and `c2`'s to 0.08 px (exactly the derived
locality: `a2`'s group touches only segments 1–2); `c3`'s measured trajectory
matches the solver-math prediction of its base to **≤0.18 screen px (~0.04
armature px) residual across all 8 frames** after a per-axis affine fit
(scale/pivot constants absorbed).

v1 (§16) weights every control point `1.0` to ONE anchor bone → the curve is
rigid; motion = swaying the owner. v2 splits the weighting: **each on-curve
point (with its two handles) rides its OWN anchor bone**, so animating the
anchors deforms the curve and the constrained chain shows a traveling wave.

### 17a. JSON shape (delta over §16b)

Same `type:"path"` display; only `weights`/`bonePose` change:

- `weights`: per control point `[1, anchorGIdx_j, 1.0]` where `j` = the
  point's GROUP (a group = `[hIn_j, anchor_j, hOut_j]`, §16b layout) — i.e.
  point `k` belongs to group `j = floor(k/3)`. N on-curve points → N groups →
  N distinct anchor bones. Multi-bone `weights` arrays parse through the same
  shared mesh-geometry path as §6b (already exercised by deform meshes); the
  new claim is only their use under a PATH display.
- `bonePose`: one `[anchorGIdx_j, <world bind matrix>]` entry per anchor.
- Anchor bones: parent = owner bone, rest transform placing each anchor AT
  its on-curve point (bind matrix = that world position), `length` 0, no
  slots/children, **all declared AFTER the constraint's chain bones** — the
  §16b/§16c placement rule, unchanged: anchors must sort after the chain to
  avoid the `_localDirty` freeze.

### 17b. Derived runtime behavior (what the fixture must confirm)

- **Per-frame skinned evaluation.** The weighted branch of
  `_updatePathVertices` reconstructs every control point as
  `Σ weightᵢ · (boneWorldMatrixᵢ · bindLocalᵢ)` from LIVE matrices (each
  geometry bone gets `updateByConstraint()` first). Moving one anchor
  translates its group's 3 points rigidly; between groups the cubics bend
  smoothly. Handles ride their group's anchor, so C1 continuity at an
  anchor is preserved under translate (both its handles move with it) but
  the curve is only C0 across a bent segment boundary pair — expected to
  read fine at wave amplitudes; the fixture's eyes-check owns this call.
- **Dirty propagation WITHOUT a dirty chain root.** `update()` recomputes
  vertices when `_root._childrenTransformDirty` is false but
  `slot._isBonesUpdate()` is true — the latter scans `_geometryBones`
  (populated from `weight.bones` for weighted geometry only) for any bone
  with `_childrenTransformDirty`. An animation keying ONLY anchor bones —
  nothing upstream of the chain — must therefore still bend the curve.
  **This branch has never been exercised** (v1's fixture animated the
  owner, making the whole subtree dirty every frame); it is the
  load-bearing unknown of v2, and the fixture keys ONLY one mid-curve
  anchor for exactly this reason.
- **Static `lengths` under a bending curve.** `curveLengths` is authored
  (constantSpeed:false) and never recomputed, so position→segment mapping
  keeps using REST arc lengths while the live curve bends. Bone spacing is
  therefore rest-length-approximate under deformation — stable (no length
  feedback), and negligible at wave amplitudes ≪ curve length. Writer knob
  docs must not promise arc-true spacing on a bent curve.

### 17c. Fixture protocol + what the run PINNED

`renderer/assets/path-weighted/`: 4 on-curve points (12 control
points, 3 segments, cumulative `lengths[3] = [29.610, 55.837, 85.447]`),
anchors `a0..a3` parented to `root` at the points, chain `c1,c2,c3`
(length 20 each, §16d), animation keys ONLY `a2` translate (±12 px x
ping-pong). Run:
`pnpm render path-weighted/path-weighted wavebend path-weighted --frames 8 --size 256`.

**[PINNED by the fixture]:**

- **Anchor-only dirtiness DOES drive the constraint.** With the chain root
  never dirty, `slot._isBonesUpdate()` alone triggers `_updatePathVertices`
  every frame — the v1 freeze does not reappear with N anchors declared
  after the chain.
- **Bending is segment-local.** Moving anchor `j` translates its 3-point
  group rigidly and reshapes only the (≤2) cubic segments whose 8-float
  windows include those points — bones parked on other segments hold
  bit-still (c1: 0.00 px across frames).
- **`rotateMode:"chain"` places bones head-to-tail, not at nominal arc
  points.** Per the solver loop, bone `t`'s translation = the running
  point `M`, which chain mode advances to the PREVIOUS bone's TIP
  (`M += (boneLen·(cosθ·a − sinθ·b) − h)·rotateMix`), not to
  `positions[t+1]`. Consequences: joints never separate while the curve
  bends (why chain mode suits strand pieces), and a bent curve shifts
  downstream bone BASES off the nominal arc positions. c3's measured
  base trajectory matched this math to ~0.04 armature px over 8 frames;
  a naive "base = curve(arc)" model mispredicts it.
- Capture phases of the 8-frame filmstrip sample the animation at
  `(k+0.5)/8` of the duration (frames 3 and 4 of a symmetric ping-pong
  are equal) — relevant when checking fixture numerics, not a runtime
  property.

---

## 18. Slot deform (ffd) timelines

**Status: PROVEN** by the hand fixture `renderer/assets/deform/`
(2026-07-15). An UNWEIGHTED 3×3-vertex mesh (`square_mesh`, 9 verts / 8 tris, no
`weights`/`slotPose`/`bonePose` — exactly the shape the flow-morph writer will
emit) plays a `bend` animation (90 f @ 24 fps) that runs, in parallel on the
same `square` slot: an `ffd` timeline (rest → displace ONE corner vertex by
`(+12,−8)` via `offset:4, vertices:[12,−8]` → rest), a `root` bone `rotateFrame`
±5° sway, and a `colorFrame` alpha ramp `aM 100→40→100`. Rendered through the
renderer (`pnpm render deform/deform bend deform-fixture --frames 9 --size 128
--span 3.75 --out-repo`, `out/deform-fixture.filmstrip.png`). **Corrections the
fixture forced: NONE** — the DERIVED JSON shape below played on the first
attempt. The four risks the spec flagged all cleared:

1. **Unweighted mesh + ffd does NOT freeze the armature** (the §16 `_localDirty`
   deadlock is a **path-constraint**-specific hazard, absent for a plain
   root-parented unweighted mesh). Evidence: the whole square visibly rotates
   through every panel of the filmstrip — the parallel bone sway runs the full
   clip while the corner deforms. No plan-B (weight-1.0-to-root) variant was
   needed. `distinct mesh vertex states: 8/9` (frame 0 == frame 8 = loop
   closure).
2. **Tween between ffd keys interpolates** (both forms). Vertex-buffer checksum
   sum ramps **linearly** on the `tweenEasing:0` key (`0.002, 1.002, 2.002,
   3.002, 3.999` — even steps, intermediate frames differ from both endpoints)
   then **decelerates** on the `curve:[0.25,0.1,0.25,1.0]` key (`3.999, 2.362,
   0.789, 0.158, 0.002` — nonlinear), proving §18d: both `tweenEasing` AND
   `curve` are parsed and applied to deform frames, exactly like §7c.
3. **ffd + slot `colorFrame` coexist on one slot** (both registered via
   `addSlotTimeline` under the same slot name, §18a) — the square darkens toward
   mid-animation (alpha ramp) *while* its corner deforms, neither breaking the
   other.
4. **`offset` + zero-compression honored exactly.** At the mid keyframe the
   vertex-buffer checksum is `3.999 ≈ rest(0.002) + (12 + −8)` — i.e. the sum of
   all vertex deltas equals the single authored `(+12,−8)` displacement, so
   exactly ONE vertex moved and every other stayed at `(0,0)`. Visually only the
   top-right (green) corner pulls; the other three hold.

**Caveats found:** (a) the renderer's `meshVertexChecksum` reflects **only the
ffd-deformed mesh-local vertex buffer** for an unweighted mesh — the bone/slot
rigid transform (the ±5° sway) is applied at the Pixi container level and is
NOT baked into that buffer, which is *why* the checksum is clean evidence of
deform-only motion (and, separately, why risk 1's freeze probe must be read off
the FILMSTRIP, not the checksum). (b) The spike's default sample span is
`min(2.0 s, duration)`; this 3.75 s clip needs `--span 3.75` (or
`--match-runtime`) to capture the full deform-out-and-back and see the loop
close. Everything else in §18a–§18f below is traced to
`pixi-dragonbones-runtime@8.0.3` (parse: `lib/parser/ObjectDataParser.mjs` —
the `M.FFD in e` block in `_parseAnimation`, `_parseSlotDeformFrame`,
`_parseGeometry`; constants `M.FFD==="ffd"` in `lib/parser/DataParser.mjs`;
runtime blend: `DeformTimelineState` / `MutilpleValueTimelineState` in
`lib/animation/{TimelineState,BaseTimelineState}.mjs`; dispatch:
`lib/animation/AnimationState.mjs` `case 22`; geometry reconstruction:
`lib/armature/Slot.mjs` `_geometryBones` + `lib/pixi/PixiSlot.mjs`
`_updateMesh`) and held as written.

FFD ("free-form deformation") is DragonBones' name for **per-vertex mesh
deformation over time** — it animates the `vertices` of a `type:"mesh"` display
(§6) or `type:"path"` display (§16b/§17) directly, on top of (or instead of)
bone-driven motion. It is a **sibling of `bone`/`slot`/`zOrder`/`ik`** inside an
`animation` entry (§14's per-family independence applies: an `ffd`-only
animation with no `bone` key is valid).

### 18a. The animation-level `"ffd"` array and mesh matching

```jsonc
"animation": [ {
  "duration": 24, "name": "wobble", "playTimes": 0,
  "ffd": [ {
    "skin": "default",          // OPTIONAL, default "default" (M.DEFAULT_NAME)
    "slot": "body",              // REQUIRED — an armature slot NAME (§4/§9)
    "name": "bodyMesh",          // REQUIRED — a display NAME inside that skin+slot (§5/§6)
    "frame": [ /* deform keyframes, §18b */ ]
  } ]
} ]
```

- `_parseAnimation` handles `M.FFD in e` (`M.FFD === "ffd"`) as its own
  independent block, iterating `e[M.FFD]`. For each entry:
  ```
  let t=D._getString(e,M.SKIN,M.DEFAULT_NAME);
  const r=D._getString(e,M.SLOT,""), a=D._getString(e,M.NAME,"");
  if(0===t.length&&(t=M.DEFAULT_NAME),
     this._slot=this._armature.getSlot(r),
     this._mesh=this._armature.getMesh(t,r,a),
     null===this._slot||null===this._mesh) continue;
  const i=this._parseTimeline(e,null,M.FRAME,22,2,0,this._parseSlotDeformFrame);
  null!==i&&this._animation.addSlotTimeline(r,i);
  ```
  So each `ffd` entry reads three string keys — `skin` (`M.SKIN`, default/empty
  → `"default"`), `slot` (`M.SLOT`, default `""`), `name` (`M.NAME`, default
  `""`) — then resolves the target via **`getMesh(skin, slot, name)` =
  `getSkin(skin).getDisplay(slot, name)`** (`ArmatureData.getMesh`,
  `model/ArmatureData.mjs`): `skin` must equal a `skin[]` entry's `name` (or the
  anonymous default skin, §5), `slot` must equal that skin's keyed slot `name`,
  `name` must equal a `display[]` entry's `name` inside that slot.
- **Mesh-matching failure is silent.** If `getSlot(slot)` returns `null` (no
  armature-level slot with that name, §4/§9) **or** `getMesh(...)` returns
  `null` (skin/slot/name don't resolve to a display, or `getSkin` itself returns
  `null`) the `continue` **drops the whole `ffd` entry with no error, no
  warning** — the animation still parses and plays, just without this
  deformation. Get `skin`/`slot`/`name` wrong and the mesh silently never
  deforms.
- **The actual keyframes live under key `"frame"`**, not `"ffd"` — `M.FRAME`
  is the 3rd arg to `_parseTimeline` (`e,null,M.FRAME,22,2,0,...)`), which reads
  `e["frame"]` (same key name as the generic `zOrder` timeline object, §15a; the
  `ffd` ARRAY ELEMENT plays the same "one object with skin/slot/name identity
  fields + a `frame` array" role that the `zOrder` timeline OBJECT plays for
  draw order). If `"frame"` is absent or empty, `_parseTimeline` returns `null`
  and the whole entry is dropped (no timeline registered, no error) — same rule
  as every other timeline family (§15a's `_parseTimeline` early-out).
- **Timeline type 22, frame value type 2 (float)** — the 4th/5th args to
  `_parseTimeline`. Registration is via **`addSlotTimeline(slotName,
  timeline)`**, the identical registration call used for `displayFrame`/
  `colorFrame` (§11/§12) — a slot can carry a display-swap timeline, a
  color timeline, AND a deform timeline simultaneously, all registered under
  the same slot name.
- **Runtime dispatch (`AnimationState`, `case 22`)** additionally re-matches the
  timeline to a **live `DisplayFrame`** by comparing **geometry offset**, not
  name — `n.getGeometryData().offset===h` where `h` is the mesh's
  `geometry.offset` baked into the timeline's own metadata (§18b). It scans
  **ALL** of the slot's `displayFrameCount` display frames (active or not) and
  binds to the first one whose geometry offset matches — the binding is to the
  authored DisplayFrame itself, independent of which display is currently
  shown. `DeformTimelineState.blend` then keeps writing that DisplayFrame's
  `deformVertices` every tick regardless of the swap state; but the **render**
  path only reflects it while that display is the slot's active one —
  `PixiSlot._updateMesh` reads `this._displayFrame.deformVertices` (the ACTIVE
  frame's array), and `blend`'s `_verticesDirty` flag is likewise gated on
  `slot._geometryData === displayFrame.getGeometryData()`. So a §11
  display-swap does not break the timeline: the deltas keep tracking in the
  background and show up the moment the authored display becomes active again.
  Practical rule unchanged: **the deform is visually inert while a different
  display is shown** — only deform a display during the spans it is actually
  displayed (or accept the hidden-but-tracking behavior).

- **§18a re-match PROVEN END-TO-END (2026-07-15)** by the hand fixture
  `renderer/assets/deform-swap/` (`out/deform-swap-fixture.filmstrip.png`).
  ONE slot (`square`) carries TWO unweighted mesh displays (`sq0`, `sq1`,
  distinct atlas regions so a swap is unmistakable), a stepped `displayFrame`
  timeline `[v0 30f, v1 30f, v0 30f, v0 0f]` (swap → and back), AND a **separate
  `ffd` entry per display** (each `slot:"square"`, `name:"sq0"`/`"sq1"`),
  animating a different corner. Rendered results (`--span 3.75`, 16 samples;
  mesh-vertex-buffer checksum = the ACTIVE display's deformed local verts, base
  grid sums to 0 so the checksum reads pure deform):
  1. **The swap is a HARD CUT — zero blend at any speed.** Colors flip in a
     single frame; the checksum jumps discontinuously at the swap frames (`f30`:
     +12.8 → −14.0; `f60`: +8.4 → +16.0) with no intermediate value. This is the
     property the flow-morph stepped-display scheme relies on to make the old
     alpha-ramp double-exposure structurally impossible.
  2. **Each display's `ffd` applies to IT, before and after the swap.** `sq0`'s
     x-corner deform runs only in its own windows (`[0,30]`, `[60,90]`), `sq1`'s
     y-corner deform only in `[30,60]` — neither leaks onto the other.
  3. **An inactive display's `deformVertices` keep tracking, so its pose is
     CORRECT the instant it becomes active** (the §18a re-match, confirmed
     visually + numerically). `sq1`'s ffd ramps `(0,+14)→(0,−14)` across `[0,30]`
     while `sq1` is HIDDEN; at `f30` it becomes active and renders **already at
     `−14`**, not at a rest pose — exactly the behavior the morph compiler needs,
     where each display holds a nonzero half-offset pose during the span before
     it is shown. `mesh render-objects: 1` confirms only the active display's
     mesh is in the container tree (hidden displays are removed, not merely
     invisible), which is why the checksum is clean per-display evidence.

  **Verdict: `one slot + K mesh displays + stepped displayFrame + per-display
  ffd` is fully supported in this port** — the foundation the flow-deform-morph
  stepped-swap restructure builds on.

### 18b. Frame fields, defaults, and zero-compression

```jsonc
{ "duration": 8,             // frames until next keyframe (default 1, like every other family)
  "tweenEasing": 0,          // OR "curve": [...]; OR omit for stepped — identical to §7c
  "offset": 10,              // OPTIONAL float offset into the flat vertex-delta array, default 0
  "vertices": [ 3.5, -2.0 ] }  // OPTIONAL flat [dx0,dy0,dx1,dy1,...] deltas, default: omitted ⇒ all-zero this frame
```

`_parseSlotDeformFrame(t, e, r)` (`t`=raw frame JSON, `e`=this frame's start
position in frames, `r`=its span — the same generic `(rawData, frameStart,
frameSpan)` signature every frame parser gets from `_parseTimeline`'s loop):

```
const a=this._frameFloatArray.length,
      i=this._parseTweenFrame(t,e,r),                 // duration+tween, same as §7c
      s=M.VERTICES in t?t[M.VERTICES]:null,             // "vertices" or null
      n=D._getNumber(t,M.OFFSET,0),                      // "offset", default 0
      o=this._intArray[this._mesh.geometry.offset+0],    // mesh vertexCount N
      h=this._mesh.geometry.weight;                       // weight data, or null
```

- `duration`/`tweenEasing`/`curve` are parsed by the **same `_parseTweenFrame`**
  used by bone/color frames — see §18d, identical semantics to §7c.
- **Every frame writes a fixed-length float block, regardless of how much
  `vertices` supplies** — this is the "zero-compression": a frame only needs to
  list the coordinates that actually move; every other float in the block is
  filled with `0` (no displacement).
  - **Unweighted mesh** (`h===null`): block length = **`2·N`** (N = the mesh's
    vertex count, `intArray[mesh.geometry.offset+0]`) — one `(dx,dy)` pair per
    mesh vertex, in vertex order (`this._frameFloatArray.length+=2*o`).
  - **Weighted mesh** (`h!==null`): block length = **`2·h.count`**
    (`h.count` = the SAME total-influence count as §6b's `n = (weights.length −
    N)/2`, i.e. `Σ boneCount_v` over all vertices) —
    `this._frameFloatArray.length+=2*h.count`. One `(dx,dy)` pair **per
    (vertex, influencing-bone) pair**, walked in the same vertex-then-bone
    order as the `weights` packing (§6b): a vertex influenced by 2 bones gets
    the SAME source delta written out twice (once per influence, transformed
    differently per bone — §18c).
- **Reading `vertices`/`offset` into that block** — the parser walks the flat
  vertex-coordinate index `t = 0, 2, 4, …, 2N−2` (note: **always `2·N`
  iterations, indexed by VERTEX position, even for a weighted mesh** — the fan-
  out to multiple influences happens per-vertex inside the loop, §18c):
  ```
  _ = (t<n || t-n>=s.length) ? 0 : s[t-n];
  m = (t+1<n || t+1-n>=s.length) ? 0 : s[t+1-n];
  ```
  i.e. **`offset` is a scalar FLOAT index into the flat delta array** (same
  convention as mesh `vertices` itself), and any vertex coordinate whose flat
  index falls outside `[offset, offset+vertices.length)` defaults to **`0`**.
  Worked example: to move only vertex 5 of an N-vertex mesh by `(dx,dy)`, emit
  `"offset": 10, "vertices": [dx, dy]` (10 = 5×2); every other vertex gets
  `(0,0)` this frame. Omitting `vertices` entirely (`s===null`) is equivalent to
  `offset:0, vertices:[]` — an all-zero (no-deformation) frame, e.g. for a
  rest/closing keyframe.
- **Frames are stored contiguously with this constant stride** in the shared
  `frameFloatArray` — every frame in one `ffd` timeline appends the SAME block
  length (2N or 2·h.count), so frame `k`'s data starts exactly `k·blockLength`
  floats after frame 0's. This is what lets the runtime index frame `k`'s block
  by `frameIndex·valueCount` (§18e) without per-frame offset bookkeeping.
- **Duration/terminal-frame/loop conventions are identical to every other
  timeline family** (§7b/§11/§15e): per-frame `duration` sums to the animation
  `duration`; a terminal `{"duration":0}` hold is idiomatic; for a seamless
  loop the terminal frame's resolved deltas must equal frame 0's — trivially
  achieved by omitting `vertices` on both the first and the terminal frame if
  the rest pose itself is the loop's neutral point.
- **`inheritDeform`** (mesh display key, `M.INHERIT_DEFORM`, default **`true`**,
  parsed in `_parseDisplay` geometry `case 2`): if a mesh display sets
  `"inheritDeform": false`, its vertices ignore any matching `ffd` timeline at
  render time even if parse-side matching (§18a) succeeded — `PixiSlot._updateMesh`
  gates the whole deform-add on `i = t.length>0 && a.inheritDeform` (§18c). Leave
  it at its default `true` (or omit it) to let `ffd` timelines apply.

### 18c. Weighted vs. unweighted geometry — two different coordinate spaces (both ways)

**Parse time** (`_parseSlotDeformFrame`, continued):

```
if(null!==h){                                    // WEIGHTED mesh — ONCE, before the vertex loop:
  const t=this._weightSlotPose[l];                // raw slotPose JSON, cached at skin-parse time
  this._helpMatrixA.copyFromArray(t,0);           // helpMatrixA = slotPose
  ...
}
for(let t=0;t<2*o;t+=2)                            // per flat vertex coordinate pair; _,m ← s[t-n]/s[t+1-n] (§18b bounds-check)
  if(...,null!==h){
    const t=this._weightBonePoses[l],               // REBINDS t (shadows the loop var): raw bonePose JSON
          e=this._intArray[f++];                     // this vertex's bone-influence count
    this._helpMatrixA.transformPoint(_,m,this._helpPoint,!0);   // delta: slotPose, NO translation
    _=this._helpPoint.x, m=this._helpPoint.y;
    for(let r=0;r<e;++r){
      const e=this._intArray[f++];                   // REBINDS e: LOCAL bone index (into this mesh's bonePose list)
      this._helpMatrixB.copyFromArray(t,7*e+1);       // t here = _weightBonePoses[l] — that bone's world bind matrix
      this._helpMatrixB.invert();
      this._helpMatrixB.transformPoint(_,m,this._helpPoint,!0); // delta: inverse(bonePose), NO translation
      this._frameFloatArray[a+A++]=this._helpPoint.x;
      this._frameFloatArray[a+A++]=this._helpPoint.y;
    }
  } else {                                           // UNWEIGHTED mesh
    this._frameFloatArray[a+t]=_, this._frameFloatArray[a+t+1]=m; // raw JSON delta, no transform at all
  }
```

The 4th argument `!0` (`true`) to `transformPoint` is DragonBones' matrix API's
"delta"/"ignore-translation" flag — confirming the deltas are transformed as
**vectors**, not points (translation components of `slotPose`/`bonePose`
are dropped; only rotation/scale/skew apply).

- **Weighted geometry: the task's hint is CONFIRMED.** Each vertex's raw JSON
  delta `(dx,dy)` is first rotated/scaled into world space by `slotPose`
  (linear part only), then — separately per influencing bone — rotated/scaled
  into that BONE's LOCAL space by the inverse of the bone's `bonePose` bind
  matrix (linear part only). The mesh's `weights` per-vertex bone list (§6b)
  determines how many transformed copies are written (one per influence).
- **Unweighted geometry: the task's hint is CONFIRMED.** The delta is stored
  verbatim, in the mesh's own flat coordinate space — no `slotPose` transform,
  no bone transform, nothing. (There is no `slotPose`/`bonePose` to apply: an
  unweighted mesh display carries neither key.)
- **Render time closes the loop** (`PixiSlot._updateMesh`, `lib/pixi/PixiSlot.mjs`)
  — this is where `deformVertices` (the array `DeformTimelineState.blend`
  writes into, §18e) actually gets folded into the final vertex buffer:
  - **Weighted branch** (`s=geometryData.weight!==null`):
    ```
    for each vertex a:
      boneCount = intArray[p++]
      for each influence l:
        bone = geometryBones[ intArray[p++] ]        // LIVE Bone
        rMat = bone.globalTransformMatrix              // LIVE world matrix
        wgt  = floatArray[y++]                          // this influence's weight (§6b)
        bx = floatArray[y++]*scale                       // BIND-LOCAL x (§6c rest data)
        by = floatArray[y++]*scale                       // BIND-LOCAL y
        if (inheritDeform) { bx += deformVertices[m++]; by += deformVertices[m++] }   // ← ffd delta ADDED HERE, in bone-local space
        vertices.x += (rMat.a*bx + rMat.c*by + rMat.tx) * wgt
        vertices.y += (rMat.b*bx + rMat.d*by + rMat.ty) * wgt
    ```
    The stored deform delta is added directly to the **bind-local** coordinate
    — the exact same space it was converted into at parse time — and the SAME
    live bone world matrix that reconstructs the rest pose (§6c) then carries
    the (bind-local + delta) point into world space, weighted-summed across
    influences. This is why the parser front-loads the bone-local conversion:
    at render time deform is a plain per-influence addition, no further matrix
    work.
  - **Unweighted branch** (`s===null`):
    ```
    for each vertex pair a=0,2,4,...:
      x = floatArray[h+a]*scale, y = floatArray[h+a+1]*scale     // mesh-local rest xy
      if (inheritDeform) { x += deformVertices[a]; y += deformVertices[a+1] }   // ← ffd delta ADDED HERE, in mesh-local space
      vertices[a]=x, vertices[a+1]=y   // (or transformed by a Surface parent — not our case)
    ```
    Again the delta is added in the exact space it was stored in (mesh-local),
    then the whole mesh moves as a rigid unit via the slot's own transform
    (outside `_updateMesh`) — unweighted deform reshapes the mesh but does NOT
    skin per-vertex to different bones.
  - Both branches gate the addition on `i = deformVertices.length>0 &&
    geometryData.inheritDeform` — `deformVertices.length` is 0 whenever no
    `ffd` timeline matched this display (§18a/§18e), so a display with no
    matching timeline renders its plain rest pose, unaffected.

### 18d. Tween forms — identical to §7c

`_parseSlotDeformFrame` calls **the same `_parseTweenFrame`** used by bone
translate/rotate/scale frames and the §12a color frame — there is only one
tween-encoding function in the parser. The full §7c table applies verbatim:
`tweenEasing` omitted → stepped; `0` → linear; `0<r≤1` → quadratic ease-out
strength r; `r<0` → quadratic ease-in strength `−r`; `r>1` → sine ease-in-out
strength `r−1`; `"curve":[...]` → sampled cubic Bézier. Deform values DO
interpolate smoothly between keyframes under a tween (confirmed by the shared
runtime base, §18e) and hold with a hard step when no tween is given.

### 18e. Runtime blend mechanics (`DeformTimelineState`)

```
class DeformTimelineState extends MutilpleValueTimelineState {
  init(t,e,i){
    super.init(t,e,i);
    if(null!==this._timelineData){
      const t=this._animationData.frameIntOffset+this._timelineArray[this._timelineData.offset+3];
      const intArr=this._animationData.parent.parent.frameIntArray;
      this._valueOffset=this._animationData.frameFloatOffset;
      this._valueCount=intArr[t+2];
      this._deformCount=intArr[t+1];
      this._deformOffset=intArr[t+3];
      this._sameValueOffset=intArr[t+4];      // (+65536 if negative — 16-bit wraparound artifact from the shared binary-format code path)
      ...
      this._valueArray=this._animationData.parent.parent.frameFloatArray;
    }
  }
  blend(t){
    ... for each l in 0..deformCount:
      value = l<deformOffset ? valueArray[sameValueOffset+l]
            : l<deformOffset+valueCount ? _rd[l-deformOffset]
            : valueArray[sameValueOffset+l-valueCount];
      deformVertices[l] = dirty>1 ? deformVertices[l]+value*blendWeight : value*blendWeight;
  }
}
```

- `_valueCount`/`_deformCount`/`_deformOffset`/`_sameValueOffset` all come from
  **one shared 5-int metadata record**, written ONCE by `_parseSlotDeformFrame`
  **only when parsing frame 0** (`if(0===e){...}`, `e`=frameStart, so this is
  the "is this the timeline's first frame" check):
  ```
  const t=this._frameIntArray.length; this._frameIntArray.length+=5;
  this._frameIntArray[t+0]=this._mesh.geometry.offset;              // → intArr[t+0], read by AnimationState case 22 for display-matching (§18a)
  this._frameIntArray[t+1]=this._frameFloatArray.length-a;          // → this._deformCount = block length just written for frame 0
  this._frameIntArray[t+2]=this._frameFloatArray.length-a;          // → this._valueCount  = SAME block length (duplicated)
  this._frameIntArray[t+3]=0;                                        // → this._deformOffset = always 0 for JSON-sourced data
  this._frameIntArray[t+4]=a-this._animation.frameFloatOffset;      // → this._sameValueOffset = frame 0's float block start
  this._timelineArray[this._timeline.offset+3]=t-this._animation.frameIntOffset;   // pointer to this record
  ```
  Because our writer (and the reference JSON parser) always emits `deformOffset
  = 0` and `deformCount === valueCount` (both are literally the same computed
  expression), in `blend()`'s three-way branch **the first arm (`l<deformOffset`,
  i.e. `l<0`) is dead, and the third arm (`l≥valueCount`) is unreachable since
  `deformCount===valueCount`** — every index resolves through the middle arm,
  `_rd[l]`, the live per-tick tween buffer. (The dead branches exist to support
  the DragonBones **binary** format's own compression scheme, where a
  `deformOffset>0` can point unchanged low/high index ranges back at frame 0's
  stored block instead of re-storing them — irrelevant to our JSON writer,
  which already gets its own, simpler zero-compression at the per-frame
  `vertices`/`offset` level, §18b.)
- `_rd` (the tween buffer) is filled by the shared `MutilpleValueTimelineState`
  base (`lib/animation/BaseTimelineState.mjs`) exactly like every other
  float-valued timeline (bone translate/rotate/scale): on arriving at a
  keyframe it reads that frame's `valueCount`-float block at
  `valueOffset+frameValueOffset+frameIndex·valueCount` and, if tweened,
  precomputes the delta to the NEXT frame's block (or back to frame 0's block
  if this is the last frame — the wraparound that makes looping tween-through
  work); each tick it linearly interpolates by the eased tween progress. This
  is the SAME generic mechanism §7's bone frames use — deform frames are "just"
  N-float tweened values instead of 2.
- `deformVertices[l]` (per slot's active `DisplayFrame`, sized `2N` or
  `2·weight.count` by `DisplayFrame.updateDeformVertices`, `lib/armature/Slot.mjs`)
  is what `PixiSlot._updateMesh` reads (§18c) to fold the animated delta into
  the render vertex buffer. `dirty>1` handles the case of MULTIPLE additive
  ffd timelines blending onto the same mesh (animation layering) — out of
  scope for a single-track writer, but the `+=` accumulation confirms deform
  timelines are additive under blending, matching bone timelines' additive
  model.

### 18f. Summary for the writer

- Emit one `ffd` entry per (skin, slot, mesh-name) you want to deform, each
  with its own `frame[]` array of the SAME general shape as every other timeline
  family: `duration` (frames, sums to animation duration), optional
  `tweenEasing`/`curve` (§18d), and `offset`+`vertices` for the payload.
- Only encode the vertices that actually move each frame — everything else
  defaults to `(0,0)` — but remember the payload space differs by mesh type:
  **unweighted → plain per-vertex `(dx,dy)` in the mesh's own flat coordinate
  space** (the same space its rest `vertices` array is authored in); **weighted
  → the parser needs `slotPose`/`bonePose` (already required for §6c's rest
  pose) to convert your per-vertex delta into bone-local space at PARSE time —
  author `vertices`/`offset` in the same flat per-VERTEX order as the rest mesh
  regardless of weighting; the parser fans a vertex's single delta out to all
  of its bone influences automatically.**
- `skin`/`slot`/`name` must match exactly (§5/§6 display naming); a mismatch is
  silently dropped both at parse time (§18a) and, separately, at runtime if the
  targeted display isn't the slot's currently active one (§18a's `case 22`
  geometry-offset match).
- Leave `inheritDeform` at its default (omit it) so the mesh actually shows the
  animated deltas.

---

