# Examples

Each folder holds a source image plus the markup an agent would pass to
`build_rig`:

| Example | Rig type | Shows |
|---|---|---|
| `manager/` | multi-piece, polygon-cut (10 pieces, 12 bones) | one flat PNG cut into limbs, a `deform` torso on `spine` + `chest` for breathing, `"fill": "none"` on pieces that reach past their seam, `--underlap 18`; animations `idle`, `idle_2`, `idle_3` |
| `character-layers/` | multi-piece from file layers (17 pieces, 11 bones) | a hand-separated layered export used as-is: every piece is a `source.file` layer with `"fill": "none"`, built with `--underlap 0`; `deform` hair and torso, `swap` eyelids and cigarette; 11 animations (`idle`, `doze`, `drowsy1`–`3`, `falling`, `asleep`, `wake`, `perk`, `stir`, `stir2`) |
| `robot/` | multi-piece (7 pieces) | rigid limbs, a `deform` hose bridging two bones, an eye-glow `swap` variant |

Build and render them from the repo root:

```sh
# one flat PNG, polygon cuts
pipeline/build_rig.sh manager examples/manager/source.png \
  examples/manager/skeleton.json examples/manager/animations.json \
  --pieces examples/manager/pieces.json --underlap 18
(cd renderer && pnpm render out:manager idle manager --match-runtime --frames 8 --gif --out-repo)

# hand-separated layers, used as pieces
pipeline/build_rig.sh character-layers examples/character-layers/source.png \
  examples/character-layers/skeleton.json examples/character-layers/animations.json \
  --pieces examples/character-layers/pieces.json --underlap 0
(cd renderer && pnpm render out:character-layers doze character-layers --match-runtime --frames 8 --gif --out-repo)

# robot
pipeline/build_rig.sh robot examples/robot/source.png \
  examples/robot/skeleton.json examples/robot/animations.json \
  --pieces examples/robot/pieces.json
(cd renderer && pnpm render out:robot idle robot --match-runtime --frames 8 --gif --out-repo)
```

Notes:

- `manager`: `--underlap 18` (instead of the default 30) keeps the white
  shirt from smearing across the shoulder seams. The legs and upper arms are
  `"fill": "none"` and their polygons reach well past the hip and elbow seams,
  so real art, not diffusion fill, sits under those joints.
- `character-layers`: `layers/` holds the layer PNGs cropped to their bounds.
  Each `offset` is the layer's top-left corner on the 1008×1792 canvas.
  `source.png` is the layers composited at rest. With file-layer pieces
  nothing is cut from it; it sets the canvas. The eye pieces use the half-lid
  layer as their base display, drawn over open eyes painted on `head`.
  `animations.json` is dense (keys every few frames) because it was generated
  by a script.
- Whole-image rigs pass `--cols 20` in place of `--pieces`. Variant and
  layer `file` paths in `pieces.json` resolve relative to the manifest when you
  build from the shell. Through MCP they must be absolute paths.
