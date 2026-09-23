#!/usr/bin/env bash
# One-command setup for TextRig. Safe to re-run: every step is idempotent.
#
#   ./install.sh
#
# Steps:
#   1. Python venv at pipeline/.venv (Python 3.10+)
#   2. pip install pipeline + MCP server requirements into it
#   3. download the background-removal model weights (u2net, ~170 MB, one time)
#   4. pnpm install in renderer/ + the headless Chromium used for rendering
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/pipeline/.venv"
PY_BIN="${PYTHON:-python3}"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v "$PY_BIN" >/dev/null || die "python3 not found (set PYTHON=/path/to/python3.10+)"
"$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ required, found $("$PY_BIN" --version 2>&1)"
command -v node >/dev/null || die "Node.js 18+ not found"
command -v pnpm >/dev/null || die "pnpm not found (npm i -g pnpm, or: corepack enable)"

say "Python venv: $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade --quiet pip

say "Python dependencies"
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/pipeline/requirements.txt" -r "$ROOT/mcp/requirements.txt"

say "Model weights (background removal)"
"$VENV/bin/python" "$ROOT/pipeline/fetch_models.py"

say "Renderer dependencies (pnpm)"
(cd "$ROOT/renderer" && pnpm install --frozen-lockfile)

say "Headless Chromium for the renderer (playwright)"
(cd "$ROOT/renderer" && pnpm exec playwright install chromium)

chmod +x "$ROOT/mcp/serve.sh" "$ROOT/pipeline/build_rig.sh"

cat <<EOF

TextRig is installed.

Connect it to Claude Code:
  claude mcp add textrig -- "$ROOT/mcp/serve.sh"

Any other MCP client (stdio):
  { "mcpServers": { "textrig": { "command": "$ROOT/mcp/serve.sh" } } }
EOF
