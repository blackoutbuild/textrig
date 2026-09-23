# Working in this repo

TextRig turns a flat PNG into a DragonBones rig. An AI agent authors the
markup JSON and the code does the geometry.

Layout:
- `mcp/server.py`: the MCP server (thin wrapper, no generator logic).
- `pipeline/`: Python build pipeline. `build_rig.sh` is the entry point for
  whole-image builds and `build_pieces_rig.py` for multi-piece builds.
  `dragonbones_writer.py` emits the rig.
- `renderer/`: Vite + Pixi 8 + pixi-dragonbones-runtime, with headless
  filmstrip/GIF rendering (`pnpm render`), live preview (`pnpm preview`) and
  the markup review editor (`markup.html`).
- `docs/rigging-playbook.md`, `docs/inspection-checklist.md`,
  `format/README.md`: the rigging knowledge. `rigging_guide` slices these
  files at call time by exact heading, so if you rename a heading, update
  `CORE_SECTIONS`/`PIECES_SECTIONS` in `mcp/server.py`
  (`pipeline/tests/test_mcp_guide_topics.py` fails until you do).
- `docs/dragonbones-format-contract.md`: the exact DragonBones JSON shape
  the writer emits, with a source for every field.
- `examples/`: worked rigs. `renderer/assets/`: hand-authored golden fixtures
  used by the tests.

Commands:
- Setup: `./install.sh`
- Tests: `pipeline/.venv/bin/pytest pipeline/tests -q` and `cd renderer && pnpm test`
- `out/` is disposable build output (gitignored).

Conventions: image pixels, y down, rotation in degrees, -90 = up. scaleX and
scaleY are absolute factors. A seamless loop needs the last key to equal the
first.
