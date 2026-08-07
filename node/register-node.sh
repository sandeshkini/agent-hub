#!/usr/bin/env bash
# Run this machine's agent-hub node registrar (heartbeats into the OWUI machine selector).
# Requires python3 (stdlib only) and a node.env next to this script (see node.env.example).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$DIR/node.env" ]; then
  echo "!! $DIR/node.env not found — copy node.env.example to node.env and fill it in." >&2
  exit 1
fi

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "!! python3 not found" >&2; exit 1; }

echo "== agent-hub node registrar (Ctrl-C to stop) =="
exec "$PY" "$DIR/registrar.py"
