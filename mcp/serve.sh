#!/usr/bin/env bash
# stdio MCP entry: claude mcp add textrig -- /abs/path/to/mcp/serve.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/pipeline/.venv/bin/python"
[ -x "$PY" ] || { echo "pipeline/.venv missing — run ./install.sh first" >&2; exit 1; }
exec "$PY" "$DIR/mcp/server.py"
