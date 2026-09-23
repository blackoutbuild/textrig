"""TextRig local MCP server — the rigging loop packaged for any MCP client.

Thin wrapper: NO generator logic here. build -> pipeline/build_rig.sh (or
pipeline/build_pieces_rig.py directly when orchestrator knobs are set — the
shell script doesn't forward them), render -> renderer `pnpm render`,
preview -> renderer `pnpm preview`. Knowledge
(playbook/checklist/format + the robot worked example) is served verbatim
from the repo files at call time, wrapped in an MCP addendum that re-frames
the in-repo instructions for a repo-blind session.

Workspace layout: inputs written by build_rig land in out/mcp/<name>/
(skeleton.json, animations.json, pieces.json, a copy of the source image).
Build artifacts (the DragonBones triple) land in repo out/ — the default
build_rig.sh behavior — so render/preview see them with zero extra plumbing.
"""
import json
import re
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image as PILImage

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "out"
WORKSPACES = OUT / "mcp"
PIPELINE_PY = REPO / "pipeline" / ".venv" / "bin" / "python"
DRAGONBONES_DIR = REPO / "renderer"
PREVIEW_PORT = 3020

BUILD_TIMEOUT_S = 120
RENDER_TIMEOUT_S = 180
PREVIEW_STARTUP_S = 20   # cold pnpm/tsx/vite start takes well over 5s

MAX_FRAMES = 12
MAX_SIZE = 512
MAX_ZOOM = 8

MARKUP_POLL_S = 0.5
# ASCII: the vite mailbox route matches [\w-]+ in JS (ASCII-only) — a Unicode
# name would pass here, then 400 on POST and await would poll forever.
_MARKUP_NAME_RE = re.compile(r"^[\w-]+$", re.ASCII)

mcp = FastMCP("textrig")

GUIDE_FILES = [
    REPO / "docs" / "rigging-playbook.md",
    REPO / "docs" / "inspection-checklist.md",
    REPO / "format" / "README.md",
]

EXAMPLE_DIR = REPO / "examples" / "robot"

# Prepended to the guide: the docs below are written for a Claude working
# INSIDE this repo; a foreign MCP session has none of that access.
MCP_ADDENDUM = """\
# MCP addendum — read this first

You are using TextRig via MCP. The documents below are written for an agent
working inside the TextRig repository; you are not. Re-read every in-repo
instruction through this lens:

- File-path references and shell commands in the docs below do NOT apply.
  You never write `examples/<name>/*.json` files or run `pipeline/build_rig.sh`
  or `pnpm` — the markup JSON documents (skeleton, animations, pieces) are
  passed directly to build_rig as tool arguments, and the server runs the
  pipeline for you.
- Generator knobs (`--fill-blur`, `--bg-tolerance`, `--bg`, `--fill-mode`,
  `--underlap`, `--power`, `--cols`, `--weights-algo`) are build_rig
  parameters: fill_blur, bg_tolerance, bg, fill_mode, underlap, power, cols,
  weights_algo. Set them on the
  build_rig call instead of editing any command line; each knob's
  what/how/when is in the build_rig tool description.
- Any `file` in a piece `source` or `variants` entry must be an ABSOLUTE
  path to a PNG on this machine — the relative paths you will see in the
  worked example refer to repo-internal files you do not have. Variant PNGs
  you generate should be saved beside the source image and referenced by
  absolute path.
- The designed inspection surfaces: the source image you were given, the
  piece-cuts debug view that comes back from build_rig on pieces builds,
  and the filmstrips render() returns — including region close-ups
  (zoom/fx/fy) when a joint, seam or filled zone needs more detail.
  build_rig also states the source image's true WIDTHxHEIGHT in every
  success summary and build error, so coordinates can be estimated by eye
  from the image without measuring anything.
- preview() returns a URL for the HUMAN user to open in their browser.

Loop summary for MCP: rigging_guide topic="core" (this document) -> author
markup JSON -> build_rig -> render() -> judge the filmstrip against the
checklist -> adjust markup or knobs -> build_rig again. For a multi-piece
brief, rigging_guide(topic="pieces") documents polygon cutting and piece
binding, and topic="example" is a complete worked multi-piece rig.
"""

# Served in the guide CORE: sanctions the whole-image path explicitly, so
# single-mesh legitimacy does not have to be inferred from the tool schema.
RIG_TYPE_NOTE = """\
# Choosing the rig type (read before authoring)

- Whole-image deform rig (NO pieces document): the sanctioned conservative
  choice for light-motion briefs (sway, breathing, gentle bob) and for
  characters whose parts form one fused cluster (limbs crossing the body,
  held props, heavy overlaps). One mesh, bones deform it smoothly: no cuts,
  no seams, no occlusion risk. Trade-off: parts cannot move independently
  or change draw order. Author skeleton.json + animations.json only.
- Multi-piece rig (pieces document): needed when the brief requires
  independent piece motion (a limb lifting away from the body), variant
  swaps (blink, mouth), or draw-order changes. Polygon cutting has its own
  trap list: rigging_guide(topic="pieces") documents the polygon/binding
  rules, rigging_guide(topic="example") is a complete worked multi-piece
  rig.

Note: the sliced docs below keep their original section numbering, so you
will see gaps and pointers to sections that are not in this document (§2,
§4, §7, "Piece tracks") — all of those live in rigging_guide(topic="pieces").
"""

GUIDE_TOC = f"""\


{'=' * 70}
# More guide topics (fetch on demand)
{'=' * 70}

This was the CORE guide — everything needed for a whole-image rig. Fetch the
other topics ONLY if your brief needs a multi-piece rig:

- rigging_guide(topic="pieces") — polygon-cutting heuristics (the trap
  list), piece binding types (rigid/swap/deform), depth-change (order)
  tracks, path constraints (a bone chain flowing along a curve), the
  error -> knob map, and the pieces.json track format. Fetch BEFORE
  authoring any pieces.json.
- rigging_guide(topic="example") — a complete worked multi-piece rig (the
  robot): skeleton.json + pieces.json + animations.json exactly as passed
  to build_rig.
"""

PIECES_PREAMBLE = """\
# Multi-piece rig rules (topic "pieces")

Everything below supplements the core guide for briefs that need a
pieces.json: polygon-cutting heuristics, binding types, depth-change (order)
tracks, path constraints, the error -> knob map, and the piece-track format. For a complete
worked multi-piece rig call rigging_guide(topic="example").

Path constraints: skeleton.json accepts an optional `paths` section —
`{"bones": [...], "paths": [{"name", "bone", "points", "chain", ...}]}` — a
bone chain that flows along an authored curve instead of pivoting on a
single joint (hair locks, tails, ribbons). `points` are on-curve image-px
anchors; handles are derived (Catmull-Rom) and the constraint itself has no
keyframe track of its own — motion comes from animating the `bone` field's
owner bone. Add `"weighted": true` and the curve can BEND: the build
generates one anchor bone per point, and a declarative `wave` track in
animations.json (`{"path", "prop": "wave", "amplitude", "period", ...}`)
compiles into a traveling ripple along the chain (hair flows, a tail
whips) — knob semantics in the "Wave tracks" section below. Full field
semantics and the proven authoring pattern (rigid pieces, one per chain
bone) are in the "Path constraints" section below and in
docs/rigging-playbook.md §4. build_rig's `skeleton` parameter accepts both
the bare bone array and the `{"bones", "paths"}` object form — pass the
object form to use paths.
"""

PLAYBOOK_MD, CHECKLIST_MD, FORMAT_MD = GUIDE_FILES

# (file, exact heading line or None for the whole file, until-mode) — see
# _section. Guarded by pipeline/tests/test_mcp_guide_topics.py: renaming a
# doc heading without updating this map fails the suite, not the session.
CORE_SECTIONS = [
    (PLAYBOOK_MD, "# Rigging playbook — author a rig for a fresh PNG", "any"),
    (PLAYBOOK_MD, "## 1. Look first", "same"),
    (PLAYBOOK_MD, "## 3. Skeleton archetypes", "same"),
    (PLAYBOOK_MD, "## 5. Animation vocabulary (amplitudes proven on robot + duck)", "any"),
    (PLAYBOOK_MD, "## 6. Gotchas", "same"),
    (PLAYBOOK_MD, "## 8. Acceptance", "same"),
    (CHECKLIST_MD, None, "same"),
    (FORMAT_MD, "# TextRig format v1 (`*.rig.json`)", "any"),
]
PIECES_SECTIONS = [
    (PLAYBOOK_MD, "## 2. Polygon heuristics (the hard-won ones)", "same"),
    (PLAYBOOK_MD, "## 4. Binding-type decision rules", "same"),
    (PLAYBOOK_MD, "### Depth changes (order tracks)", "same"),
    (PLAYBOOK_MD, "## 7. Error → knob map", "same"),
    (FORMAT_MD, "## Piece tracks (multi-piece rigs, `pieces.json`)", "same"),
    (FORMAT_MD, "## Path constraints (skeleton.json `paths` section)", "same"),
    (FORMAT_MD, "## Wave tracks (animations.json, weighted paths only)", "same"),
]


def _worked_example() -> str:
    """The robot rig's markup served inline — a complete, real pieces.json
    example the docs otherwise reference by repo path."""
    parts = [f"\n\n{'=' * 70}\n# Worked example — a real multi-piece rig (robot)\n{'=' * 70}\n\n"
             "The markup below rigged a robot PNG: bones (skeleton.json), pieces\n"
             "in draw order (pieces.json — rigid + deform + swap variants), and an\n"
             "idle animation (animations.json). These are the exact JSON documents\n"
             "passed to build_rig as the skeleton / pieces / animations arguments.\n"]
    for fname in ("skeleton.json", "pieces.json", "animations.json"):
        content = (EXAMPLE_DIR / fname).read_text().strip()
        parts.append(f"\n## {fname}\n\n```json\n{content}\n```\n")
    return "".join(parts)


def _tail(text: str, n: int = 4000) -> str:
    """Last n chars of a command's output — enough to keep the loud
    validator/writer failure message (they exit right after printing it)
    without dumping unbounded stderr back to the caller."""
    return text[-n:] if len(text) > n else text


def _doc_headings(lines: list[str]) -> list[tuple[int, int]]:
    """(line index, level) of every markdown heading OUTSIDE code fences."""
    fenced = False
    out = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("#"):
            out.append((i, len(line) - len(line.lstrip("#"))))
    return out


def _section(path: Path, heading: str | None, until: str = "same") -> str:
    """Slice one markdown section out of a repo doc at call time — the docs
    stay single-source for the in-repo loop; nothing is duplicated on disk.

    heading=None returns the whole file. Otherwise `heading` is the exact
    heading line. until="same": capture to the next heading of the same or
    higher level (subsections included); until="any": capture to the next
    heading of ANY level (used to take an H1 intro, or an H2 body without
    its H3 subsections). Raises ValueError on a missing heading — the topic
    test turns that into a failing guard when a doc heading is renamed."""
    text = path.read_text()
    if heading is None:
        return text
    lines = text.splitlines(keepends=True)
    headings = _doc_headings(lines)
    starts = [(i, lvl) for i, lvl in headings if lines[i].rstrip() == heading]
    if not starts:
        raise ValueError(f"heading not found in {path}: {heading!r}")
    start, level = starts[0]
    for i, lvl in headings:
        if i > start and (until == "any" or lvl <= level):
            return "".join(lines[start:i])
    return "".join(lines[start:])


def _sections_block(sections: list[tuple[Path, str | None, str]]) -> str:
    """Concatenate doc slices, one FROM banner per source file."""
    parts = []
    last_file = None
    for path, heading, until in sections:
        if path != last_file:
            parts.append(f"\n\n{'=' * 70}\n# FROM: {path.relative_to(REPO)}"
                         f"\n{'=' * 70}\n\n")
            last_file = path
        parts.append(_section(path, heading, until).rstrip("\n") + "\n\n")
    return "".join(parts)


def _guide(topic: str) -> str:
    if topic == "core":
        return (MCP_ADDENDUM + "\n" + RIG_TYPE_NOTE
                + _sections_block(CORE_SECTIONS) + GUIDE_TOC)
    if topic == "pieces":
        return PIECES_PREAMBLE + _sections_block(PIECES_SECTIONS)
    if topic == "example":
        return _worked_example()
    if topic == "all":
        parts = [MCP_ADDENDUM, "\n", RIG_TYPE_NOTE]
        parts += [
            f"\n\n{'=' * 70}\n# FILE: {p.relative_to(REPO)}\n{'=' * 70}\n\n{p.read_text()}"
            for p in GUIDE_FILES
        ]
        parts.append(_worked_example())
        return "".join(parts)
    raise ToolError(f"unknown rigging_guide topic {topic!r} — valid topics: "
                    f"core, pieces, example, all")


def _run(cmd: list[str], cwd: Path, timeout_s: int, timeout_label: str,
         fail_label: str) -> subprocess.CompletedProcess:
    """Run a pipeline/renderer command; non-zero exit or timeout surfaces as a
    ToolError carrying the command's own (tailed) stderr, never a server
    stack trace."""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                 timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{timeout_label} timed out after {timeout_s}s")
    if result.returncode != 0:
        raise ToolError(_tail(result.stderr) or _tail(result.stdout) or
                         f"{fail_label} failed with exit code {result.returncode}")
    return result


def _triple_paths(name: str) -> list[Path]:
    return [OUT / f"{name}_ske.json", OUT / f"{name}_tex.json",
            OUT / f"{name}_tex.png"]


def _remove_triple(name: str) -> None:
    """Drop any (possibly stale) built triple so a failed rebuild can't leave
    the previous build behind for render/preview to pick up silently."""
    for p in _triple_paths(name):
        p.unlink(missing_ok=True)


def _image_dims(image: Path) -> str:
    """'WxHpx' of the source image — ground truth for the session, which has
    no shell to measure with. Soft-fails: dims are a courtesy, never a blocker."""
    try:
        with PILImage.open(image) as im:
            return f"{im.width}x{im.height}px"
    except Exception:
        return "unknown size"


@mcp.tool()
def rigging_guide(topic: str = "core") -> str:
    """START HERE before any rigging: call this with the default
    topic="core". Returns the TextRig rigging guide.

    Topics:
    - "core" (default): conventions, how to choose between a whole-image
      and a multi-piece rig, skeleton archetypes, animation vocabulary, the
      7-point inspection checklist, and the JSON formats. Enough for any
      whole-image (no pieces) rig — most light-motion briefs need nothing
      else.
    - "pieces": polygon-cutting heuristics, piece binding types
      (rigid/swap/deform), draw-order tracks, path constraints (a bone
      chain flowing along a curve), the error->knob map. Fetch BEFORE
      authoring a pieces document.
    - "example": a complete worked multi-piece rig (a real robot) — the
      exact skeleton/pieces/animations JSON passed to build_rig.
    - "all": everything at once (core + pieces + example)."""
    return _guide(topic)


@mcp.tool()
def build_rig(name: str, image_path: str, skeleton: list | dict, animations: dict,
              pieces: dict | None = None, fill_blur: float | None = None,
              bg_tolerance: int | None = None, bg: str | None = None,
              fill_mode: str | None = None, underlap: int | None = None,
              power: float | None = None, cols: int | None = None,
              weights_algo: str | None = None) -> list:
    """Validate + build a DragonBones rig from markup. Call rigging_guide
    first. skeleton/animations/pieces are the JSON documents described there
    (pass the JSON itself, not file paths). skeleton is either the bare bone
    array or the object form {"bones": [...], "paths": [...]} — the latter
    unlocks path constraints and wave tracks. image_path must be an ABSOLUTE
    path to the source image (PNG/JPEG/WebP — anything PIL reads; inputs
    without an alpha channel go through background removal, see the bg /
    bg_tolerance knobs). On validation failure the exact error is
    returned — fix the markup and call again. On success, pieces builds also
    return the piece-cuts + bones debug view as an image — the designed
    place to catch polygon errors while they are still cheap to fix.

    Generator knobs (all optional; defaults are usually right). For each:
    what it does / how to use it / when you need it.
    - bg: background-removal mode for inputs without an alpha channel.
      "auto" (default) keys out a uniform border color; "rembg" runs a
      neural cutout (works on painted/photographic backgrounds). Under
      "auto", inputs that already have an alpha channel skip removal
      entirely — an image with a transparent background is the primary
      input case; "rembg" recuts even those.
    - bg_tolerance: max per-channel distance from the border color that
      still counts as background in bg="auto" (default 24). Raise it when
      auto removal leaves a halo or misses a noisy-but-uniform background;
      lower it when the mask eats into the subject.
    - fill_blur: occlusion-fill blur sigma (pieces builds; default 3.0).
      Raise it when a seam or hole shows behind a moving piece.
    - fill_mode: occlusion-fill strategy (pieces builds; default "auto",
      which picks per zone by depth). "inpaint" forces smooth diffusion
      fill — for a limb-sized reveal that shows a streaked ghost under
      auto; "extend" forces nearest-pixel fill everywhere.
    - underlap: px each piece's region dives UNDER the pieces drawn above
      it (pieces builds; default 30, 0 = off). The band is diffusion-filled
      and clamped inside the solid silhouette, so edge-to-edge cut
      boundaries no longer open holes the moment a piece moves. Raise it
      for big swings; set 0 to reproduce raw edge-to-edge cuts. Opaque
      pixels covered by NO polygon are always absorbed by the nearest
      piece (loud warning) — sloppy gaps between polygons stop dropping
      pixels but still deserve a markup fix.
    - power: IDW weight falloff exponent (whole-image builds; default 4.0;
      only used with weights_algo="idw"). Higher = stiffer, more local bone
      influence.
    - cols: mesh grid columns (whole-image builds; default 20). More
      columns = finer deformation at more vertices. For deform PIECES,
      mesh knobs live in pieces.json per piece, not here.
    - weights_algo: how bone influence over mesh vertices is computed
      (whole-image builds and deform pieces). "heat" (default): diffusion
      inside the silhouette — influence travels through painted pixels only
      and dies out across narrow junctions. "idw": inverse straight-line
      distance — the old behavior, kept for A/B comparison; influence can
      cross empty space and narrow junctions (a raised arm drags the torso;
      a cloak sticks to a nearby arm).
    """
    # Invariant: `pieces is None` selects the whole-image (single mesh) build;
    # any pieces document selects the multi-piece orchestrator. All branching
    # below checks `is not None` for that reason.
    image = Path(image_path)
    if not image.is_absolute():
        raise ToolError(f"image_path must be an absolute path, got: {image_path!r}")
    if not image.is_file():
        raise ToolError(f"image_path does not exist or is not a file: {image_path}")
    if weights_algo not in (None, "idw", "heat"):
        raise ToolError(f'weights_algo must be "idw" or "heat", got {weights_algo!r}')

    dims = _image_dims(image)

    orchestrator_knobs = {"fill_blur": ("--fill-blur", fill_blur),
                          "bg_tolerance": ("--bg-tolerance", bg_tolerance),
                          "bg": ("--bg", bg),
                          "fill_mode": ("--fill-mode", fill_mode),
                          "underlap": ("--underlap", underlap)}
    wholeimage_knobs = {"power": power, "cols": cols}
    if pieces is None:
        bad = [k for k, (_, v) in orchestrator_knobs.items() if v is not None]
        if bad:
            raise ToolError(f"{', '.join(bad)} apply only to pieces builds "
                             f"(pass a pieces document); this is a whole-image build")
    else:
        bad = [k for k, v in wholeimage_knobs.items() if v is not None]
        if bad:
            raise ToolError(f"{', '.join(bad)} apply only to whole-image (no "
                             f"pieces) builds; deform-piece mesh knobs live in "
                             f'pieces.json (per-piece "mesh": {{"cols", "power"}})')

    workspace = WORKSPACES / name
    workspace.mkdir(parents=True, exist_ok=True)

    skel_path = workspace / "skeleton.json"
    anim_path = workspace / "animations.json"
    skel_path.write_text(json.dumps(skeleton, indent=2))
    anim_path.write_text(json.dumps(animations, indent=2))

    image_copy = workspace / image.name
    shutil.copyfile(image, image_copy)

    if pieces is not None:
        pieces_path = workspace / "pieces.json"
        pieces_path.write_text(json.dumps(pieces, indent=2))
        knob_flags = [(flag, v) for flag, v in orchestrator_knobs.values()
                      if v is not None]
        if knob_flags:
            # build_rig.sh doesn't forward orchestrator knobs
            # (playbook §7) — invoke the orchestrator directly, exactly
            # as the playbook prescribes for knob runs.
            cmd = [str(PIPELINE_PY), str(REPO / "pipeline" / "build_pieces_rig.py"),
                   "--image", str(image_copy), "--skeleton", str(skel_path),
                   "--pieces", str(pieces_path), "--animations", str(anim_path),
                   "--out", str(OUT), "--name", name]
            for flag, value in knob_flags:
                cmd += [flag, str(value)]
        else:
            cmd = [str(REPO / "pipeline" / "build_rig.sh"), name, str(image_copy),
                   str(skel_path), str(anim_path),
                   "--pieces", str(pieces_path), "--target", "db"]
    else:
        cmd = [str(REPO / "pipeline" / "build_rig.sh"), name, str(image_copy),
               str(skel_path), str(anim_path),
               "--cols", str(cols if cols is not None else 20), "--target", "db"]
        if power is not None:
            cmd += ["--power", str(power)]

    if weights_algo is not None:
        cmd += ["--weights-algo", weights_algo]

    try:
        result = _run(cmd, cwd=REPO, timeout_s=BUILD_TIMEOUT_S,
                      timeout_label=f"build_rig for {name!r}",
                      fail_label="rig build")
        ske_path = OUT / f"{name}_ske.json"
        if not ske_path.is_file():
            raise ToolError(f"build exited 0 but {ske_path} was not written "
                             f"(stdout: {_tail(result.stdout)})")
    except ToolError as exc:
        _remove_triple(name)   # a failed (re)build must not leave a stale triple
        raise ToolError(f"{exc}\n[source image {image.name}: {dims}]") from None

    piece_count = len(pieces["pieces"]) if pieces is not None else 1
    bone_count = (len(skeleton.get("bones", []))
                  if isinstance(skeleton, dict) else len(skeleton))
    summary = (f"built {name!r} (source image {image.name}: {dims}): "
               f"{bone_count} bone(s), {piece_count} piece(s), "
               f"{len(animations)} animation(s) -> "
               f"out/{name}_ske.json (+ _tex.json, _tex.png)")
    # Successful builds may still print markup-smell warnings (e.g. a deep
    # occlusion zone = invented fill, fill_occlusions WARN) — surface them,
    # they are the pipeline telling the rigger to re-check the polygons.
    warnings = [l for l in result.stderr.splitlines() if "WARNING" in l]
    if warnings:
        summary += "\n" + "\n".join(warnings)
    debug_png = OUT / f"{name}.debug.png"
    if pieces is not None and debug_png.is_file():
        return [summary, Image(path=debug_png),
                "piece cuts + bones debug view — where polygon errors are "
                "cheapest to catch (playbook §2)"]
    return [summary]   # whole-image builds have no debug.png


@mcp.tool()
def render(name: str, animation: str = "idle", frames: int = 8,
           size: int = 256, gif: bool = False, zoom: float | None = None,
           fx: float | None = None, fy: float | None = None) -> list:
    """Render a built rig to a filmstrip (returned as an image). frames<=12,
    size<=512.

    Region close-up (optional): zoom magnifies the auto-fitted view (1 =
    full frame, up to 8); fx/fy aim the viewport inside the rig's bounds
    (0..1, 0 = left/top, default 0.5 = center). Use it when a joint, seam
    or filled zone needs more detail than a full-frame filmstrip gives —
    e.g. zoom=3 fx=0.85 fy=0.2 is a close-up of something near the upper
    right. Same frame/size caps; the pixels just go further."""
    if frames > MAX_FRAMES or size > MAX_SIZE:
        raise ToolError(
            f"render caps exceeded: frames<={MAX_FRAMES} (got {frames}), "
            f"size<={MAX_SIZE} (got {size})")
    region = []
    if any(v is not None for v in (zoom, fx, fy)):
        z = 1.0 if zoom is None else float(zoom)
        x = 0.5 if fx is None else fx
        y = 0.5 if fy is None else fy
        if not 0 < z <= MAX_ZOOM:
            raise ToolError(f"zoom must be in (0, {MAX_ZOOM}], got {z}")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ToolError(f"fx/fy must be in [0, 1], got fx={x} fy={y}")
        region = [str(z), str(x), str(y)]
    if not all(p.is_file() for p in _triple_paths(name)):
        raise ToolError(f"no built rig named {name!r} — build_rig has not "
                         f"succeeded yet (build first, then render)")

    out_name = f"mcp-{name}"
    cmd = ["pnpm", "render", f"out:{name}", animation, out_name, *region,
           "--match-runtime", "--frames", str(frames), "--size", str(size),
           "--out-repo"]
    if gif:
        cmd.append("--gif")

    result = _run(cmd, cwd=DRAGONBONES_DIR, timeout_s=RENDER_TIMEOUT_S,
                  timeout_label=f"render for {name!r}", fail_label="render")

    filmstrip = OUT / f"{out_name}.filmstrip.png"
    if not filmstrip.is_file():
        raise ToolError(f"render exited 0 but {filmstrip} was not written "
                         f"(stdout: {_tail(result.stdout)})")

    summary = (
        f"rendered {name!r} anim={animation!r} frames={frames} size={size}"
        + (f" zoom={region[0]} fx={region[1]} fy={region[2]}" if region else "")
        + "\nQuality bar this filmstrip is designed to be judged against — "
        "the 7-point checklist: static zones; independent piece motion "
        "without joint separation; no revealed holes; draw order; swap "
        "timing; seamless loop; amplitude. A region close-up (zoom/fx/fy) "
        "shows a joint, seam or filled zone at more detail than the full "
        "frame.")
    content = [Image(path=filmstrip), summary]
    if gif and (OUT / f"{out_name}.gif").is_file():
        content.append(f"gif written to out/{out_name}.gif")
    return content


def _preview_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{PREVIEW_PORT}/rigs",
                                     timeout=1) as resp:
            return resp.status == 200
    except (OSError, socket.timeout):
        return False


def _ensure_dev_server(cmd: list[str]) -> None:
    """Start the renderer dev server via `cmd` unless :3020 already answers."""
    if _preview_reachable():
        return
    subprocess.Popen(cmd, cwd=DRAGONBONES_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.monotonic() + PREVIEW_STARTUP_S
    while time.monotonic() < deadline:
        if _preview_reachable():
            return
        time.sleep(0.25)
    raise ToolError(f"preview dev server did not come up on "
                    f":{PREVIEW_PORT} within {PREVIEW_STARTUP_S}s")


def _open_in_browser(url: str) -> None:
    """Best-effort convenience on macOS; the URL is returned either way."""
    try:
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


@mcp.tool()
def preview(name: str) -> str:
    """Live 60fps preview URL for the HUMAN to open in a browser. Starts the
    preview dev server if it is not already running (reuses it otherwise)."""
    _ensure_dev_server(["pnpm", "preview", name])
    return f"http://localhost:{PREVIEW_PORT}/preview.html?name={name}"


def _markup_paths(name: str) -> tuple[Path, Path, Path]:
    return (OUT / f"markup-{name}.session.json",
            OUT / f"markup-{name}.result.json",
            OUT / f"markup-{name}.png")


def _validate_markup(name: str, skeleton, pieces) -> None:
    """Structural sanity only — deep semantics stay build_rig's job (the gate
    must accept markup build_rig would reject on finer points; reviewing such
    markup is what the gate is FOR)."""
    if not isinstance(name, str) or not _MARKUP_NAME_RE.fullmatch(name):
        raise ToolError(f"name must match [A-Za-z0-9_-]+, got {name!r}")
    if name.endswith(("_ske", "_tex")):
        raise ToolError(f"name must not end in _ske/_tex (collides with rig "
                        f"triple naming in out/), got {name!r}")
    bones = skeleton.get("bones") if isinstance(skeleton, dict) else skeleton
    if not isinstance(bones, list) or not bones:
        raise ToolError('skeleton must be a bone array or {"bones": [...]} object')
    names = [b.get("name") for b in bones if isinstance(b, dict)]
    if len(names) != len(bones) or None in names:
        raise ToolError("every bone needs a name")
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ToolError(f"duplicate bone name(s): {sorted(dupes)}")
    roots = [b for b in bones if b.get("parent") is None]
    if len(roots) != 1:
        raise ToolError(f"skeleton needs exactly one root bone, found {len(roots)}")
    known = set(names)
    for b in bones:
        if b.get("parent") is not None and b["parent"] not in known:
            raise ToolError(f"bone {b['name']!r} has unknown parent {b['parent']!r}")
        if not isinstance(b.get("x"), (int, float)) or not isinstance(b.get("y"), (int, float)):
            raise ToolError(f"bone {b['name']!r} needs numeric x/y")
    if pieces is not None:
        plist = pieces.get("pieces") if isinstance(pieces, dict) else None
        if not isinstance(plist, list) or not plist:
            raise ToolError('pieces must be an object with a non-empty "pieces" list')
        for pc in plist:
            poly = (pc.get("source") or {}).get("polygon")
            if poly is not None and len(poly) < 3:
                raise ToolError(f"piece {pc.get('name')!r}: polygon needs >= 3 points")


@mcp.tool()
def open_markup_review(name: str, image_path: str, skeleton: list | dict,
                       pieces: dict | None = None) -> str:
    """Open the interactive markup review page for the HUMAN user: skeleton
    joints and piece-cut boundaries are shown over the source image and can
    be dragged for pixel accuracy; a cut tool splits pieces; a note field
    carries structural wishes back to you. Call BEFORE build_rig whenever cut
    or joint placement matters (multi-piece rigs especially). Give the human
    the returned URL, then call await_markup_review(name) repeatedly until it
    returns the edited markup. skeleton/pieces are the same JSON documents
    build_rig takes. Re-opening the same name replaces the session."""
    image = Path(image_path)
    if not image.is_absolute():
        raise ToolError(f"image_path must be an absolute path, got: {image_path!r}")
    if not image.is_file():
        raise ToolError(f"image_path does not exist or is not a file: {image_path}")
    _validate_markup(name, skeleton, pieces)

    session_path, result_path, png_path = _markup_paths(name)
    OUT.mkdir(parents=True, exist_ok=True)
    size = None
    warning = ""
    try:
        with PILImage.open(image) as im:
            size = [im.width, im.height]
            im.convert("RGBA").save(png_path)
    except Exception:
        # Fallback copies the file verbatim — loudly: the editor's alpha
        # hints degrade, and if PIL couldn't even read it the page may not
        # render it at all.
        shutil.copyfile(image, png_path)
        if size is None:
            warning = ("\nWARNING: source image could not be read at all "
                       "(copied verbatim; imageSize unknown — the editor may "
                       "fail to render it; pass a PNG/JPEG instead)")
        else:
            warning = ("\nWARNING: source image could not be converted to "
                       "RGBA PNG (copied verbatim — the editor's alpha joint "
                       "hints will degrade)")
    result_path.unlink(missing_ok=True)

    session_path.write_text(json.dumps({
        "name": name, "token": uuid.uuid4().hex,
        "image": f"/out/markup-{name}.png", "imageSize": size,
        "skeleton": skeleton, "pieces": pieces}, indent=2))

    _ensure_dev_server(["pnpm", "exec", "vite", "--strictPort"])
    url = f"http://localhost:{PREVIEW_PORT}/markup.html?name={name}"
    _open_in_browser(url)
    return (f"markup review is open — give the human this URL: {url}\n"
            f"Then call await_markup_review({name!r}) repeatedly until it "
            f"returns the edited markup; a 'still editing' reply just means "
            f"call it again." + warning)


@mcp.tool()
def await_markup_review(name: str, timeout_s: int = 50) -> str:
    """Collect the human's edits from an open markup review. Waits up to
    timeout_s (default 50; each call is cheap — keep re-calling while the
    human edits). Returns the edited markup as JSON: {skeleton, pieces,
    meta: {new_pieces, new_bones, user_note}}. Bones listed in
    meta.new_bones were split off existing bones by the human (dblclick on
    a bone axis) and are already valid skeleton members. Pieces listed in
    meta.new_pieces were
    created by the cut tool and carry a COPIED placeholder binding — assign
    each a real bone (extend the skeleton if needed), then re-open the gate
    or proceed to build_rig. meta.user_note is the human's instruction to
    you — honor it."""
    session_path, result_path, _ = _markup_paths(name)
    if not session_path.is_file():
        raise ToolError(f"no open markup review named {name!r} — call "
                        f"open_markup_review first")
    try:
        token = json.loads(session_path.read_text()).get("token")
    except (json.JSONDecodeError, OSError):
        raise ToolError(f"session file for {name!r} is unreadable — re-open "
                        f"the markup review") from None
    stale_seen = False
    deadline = time.monotonic() + max(0, timeout_s)
    while True:
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text())
            except (json.JSONDecodeError, OSError):
                result = None   # mid-write; retry next poll
            if isinstance(result, dict):
                if result.get("token") == token:
                    payload = {"skeleton": result.get("skeleton"),
                               "pieces": result.get("pieces"),
                               "meta": result.get("meta") or {}}
                    new = payload["meta"].get("new_pieces") or []
                    new_bones = payload["meta"].get("new_bones") or []
                    reminder = ("Reminder: honor meta.user_note. "
                                + (f"New piece(s) {', '.join(new)} carry a copied "
                                   f"placeholder binding — assign each a real bone "
                                   f"(extend the skeleton if needed), then re-open "
                                   f"the gate or proceed to build_rig."
                                   if new else "No new pieces were cut.")
                                + (f" New bone(s) {', '.join(new_bones)} were split "
                                   f"off by the human — already in the skeleton; "
                                   f"consider keyframing them."
                                   if new_bones else ""))
                    return json.dumps(payload, indent=2) + "\n\n" + reminder
                stale_seen = True
        if time.monotonic() >= deadline:
            extra = (" (a result from an OLDER review of this name was ignored — "
                     "the human may be editing a stale tab; re-send the URL)"
                     if stale_seen else "")
            return (f"still editing{extra} — no result after {timeout_s}s; "
                    f"call await_markup_review({name!r}) again")
        time.sleep(MARKUP_POLL_S)


if __name__ == "__main__":
    mcp.run()
