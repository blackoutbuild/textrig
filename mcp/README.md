# TextRig local MCP server

A local stdio MCP server that packages the TextRig rigging loop (look →
mark up → build → inspect → iterate) as six tools. An agent with only this
server can rig an image from a prose request. It needs no repo access and no
files. The server is a thin wrapper and **contains no generator logic**. Its
tools shell out to `pipeline/build_rig.sh` and to the `renderer/` pnpm
scripts, the same entry points you can run by hand from the repo.

## Setup

From the repo root:

```sh
./install.sh
```

## Connect

```sh
claude mcp add textrig -- /absolute/path/to/textrig/mcp/serve.sh
```

Other MCP clients: register `/absolute/path/to/textrig/mcp/serve.sh` as a
stdio server command (no arguments, no environment variables).

`serve.sh` resolves the repo root from its own location and runs
`mcp/server.py` over stdio with the venv's Python.

## Tools

- **`rigging_guide(topic="core")`** — the rigging guide, layered by topic so
  a light brief doesn't pay for cutting docs it won't use. `core` (default):
  MCP addendum, rig-type decision note (whole-image vs multi-piece),
  skeleton archetypes, animation vocabulary, the 7-point inspection
  checklist, and the JSON format reference — enough for any whole-image rig.
  `pieces`: polygon-cutting heuristics, binding types, order tracks, the
  error→knob map (fetch before authoring a pieces document). `example`: the
  `examples/robot/` skeleton/pieces/animations JSON served inline. `all`:
  everything concatenated (the old single-blob behavior). Sections are
  sliced from `docs/rigging-playbook.md`, `docs/inspection-checklist.md`,
  and `format/README.md` at call time — single-source, guarded by
  `pipeline/tests/test_mcp_guide_topics.py`. Call `core` first; every other
  tool's description points back to it.
- **`build_rig(name, image_path, skeleton, animations, pieces?, fill_blur?,
  bg_tolerance?, bg?, fill_mode?, underlap?, power?, cols?, weights_algo?)`** —
  validates and builds a DragonBones rig. `skeleton`/`animations`/`pieces`
  are JSON values (not file paths) per the formats `rigging_guide` describes.
  `image_path` must be an **absolute** path to the source image on the same
  machine (PNG/JPEG/WebP — anything PIL reads; alpha-less inputs go through
  background removal). Every success summary and build error also states the
  source image's true `WIDTHxHEIGHT` (the session has no shell to measure
  with). Optional generator knobs: `fill_blur`, `bg_tolerance`, `bg`,
  `fill_mode` for pieces builds (forwarded to `pipeline/build_pieces_rig.py`,
  invoked directly because `build_rig.sh` doesn't forward them; `fill_mode=inpaint` forces smooth diffusion fill of occluded zones,
  `auto` (default) picks per zone), and `power`, `cols` for whole-image
  builds (`cols` defaults to 20). Each knob is documented in the tool
  description as what-it-does / how-to-use / when-you-need-it. On
  validation failure the loud validator/writer error is returned verbatim as
  the tool error — fix the markup and call again. On success, writes the
  DragonBones triple (`out/<name>_ske.json` + `_tex.json` + `_tex.png`); on
  failure any previously built triple of the same name is removed so a stale
  rig can't be rendered by accident. Successful pieces builds also return the
  `out/<name>.debug.png` piece-cuts + bones view as an inline image (check
  polygons before animating); whole-image builds return just the summary.
  Any `file` in a piece `source`/`variants` entry must likewise be an
  absolute path on the same machine.
- **`render(name, animation="idle", frames=8, size=256, gif=false, zoom?, fx?, fy?)`** —
  renders a built rig to a filmstrip, returned as an inline image (plus a gif
  file on request). Inspect it against the checklist with your own eyes.
  Optional `zoom`/`fx`/`fy` render a region close-up (zoom 1–8; fx/fy aim
  the viewport, 0..1, default center) — the lens for joints, seams and
  filled zones. The result text names the 7-point checklist as the quality
  bar the filmstrip is designed against.
  Capped at `frames<=12`, `size<=512` (tool-result image budget). Refuses
  with an explicit "build_rig has not succeeded yet" error when no built
  triple exists for `name`.
- **`preview(name)`** — ensures the live preview dev server is running
  (reuses it if already up on `:3020`, otherwise starts it) and returns the
  preview URL, for a **human** to open in a browser (60fps, not for the
  agent to "look" at).
- **`open_markup_review(name, image_path, skeleton, pieces?)`** — the
  human-review gate for markup accuracy. Writes a session (source image +
  skeleton + pieces), ensures the dev server, and returns a
  `markup.html?name=<name>` URL for the **human**: they drag joints and
  piece-boundary vertices (shared boundaries move both polygons at once),
  split pieces with a cut stroke, and leave a free-text note. Call BEFORE
  build_rig whenever joint or cut placement matters — pixel accuracy from
  the human's hands is cheaper than render-and-iterate. Re-opening the same
  name replaces the session (stale tabs can't satisfy the new gate — results
  are token-matched).
- **`await_markup_review(name, timeout_s=50)`** — collects the human's
  edits: blocks up to `timeout_s`, returns `{skeleton, pieces, meta:
  {new_pieces, new_bones, user_note}}` as JSON once the human presses Done. A
  "still editing" reply is not an error — call it again. Pieces in
  `meta.new_pieces` were cut in the editor and carry a copied placeholder
  binding: assign each a real bone (extend the skeleton if needed), then
  re-open the gate or proceed to build_rig. `meta.user_note` is the human's
  instruction to the agent.

## Workspace layout

`build_rig` writes its inputs (skeleton.json, animations.json, pieces.json,
a copy of the source image) to `out/mcp/<name>/`. Build artifacts (the
DragonBones triple, debug PNGs, filmstrips) land in the repo's normal `out/`
directory — the default `build_rig.sh` / `pnpm render --out-repo` behavior —
so `render` and `preview` see them with zero extra plumbing. `out/` is
disposable and gitignored.

## Caps and timeouts

- `render`: `frames <= 12`, `size <= 512`; requests over the cap are
  rejected before any subprocess runs.
- `build_rig` subprocess timeout: 120s.
- `render` subprocess timeout: 180s.
- `preview` startup wait: 20s (cold pnpm/tsx/vite start).
- `await_markup_review`: polls the result file every 0.5s for up to
  `timeout_s` (default 50 — under common MCP client tool timeouts; re-call
  freely while the human edits).

## Errors

Every shelled command's non-zero exit surfaces as an MCP tool error
(`isError: true`) carrying the command's stderr (tail, if long) — never a
stack trace of the server itself.
