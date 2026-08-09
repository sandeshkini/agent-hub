#!/usr/bin/env bash
# E10.7 — pin remote agent-hub nodes into THIS hub's OWUI registry so they stay in the machine selector.
#
# The node registry is in-memory (a node goes offline after ~90s of silence, evicted after ~1h, and it's
# cleared when open-webui restarts). Remote nodes we reach over Pangolin's register.* front-door don't push
# to us (OWUI isn't exposed SSO-free — by design), so the HUB re-registers them here on a loop: health-check
# each node's front-door, and (only if healthy) POST its manifest to localhost OWUI. Keeps OWUI fully private.
#
# Config (in the agent-hub .env, gitignored):
#   NODE_HUB_TOKEN      — bearer that gates /api/v1/nodes/register
#   PINNED_NODES_JSON   — JSON array of { "health": "<url>", "payload": { <node registry manifest> } }
# Runs as a systemd --user service on the hub. Interval via PIN_INTERVAL (default 45s).
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$DIR/.env"
# getenv: for simple SCALAR values — strips a trailing " # comment" + trailing whitespace.
getenv() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//'; }
# getenv_raw: for JSON values — JSON legitimately contains '#' (labels, url fragments), and the '#'-comment
# strip above would truncate it mid-string → json.loads fails → every node silently dropped. Only trim
# trailing whitespace here; never treat '#' as a comment.
getenv_raw() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+$//'; }
HUB_URL="${OWUI_URL:-http://localhost:3000}/api/v1/nodes/register"
INTERVAL="${PIN_INTERVAL:-45}"

echo "[pin] pinning remote nodes -> $HUB_URL every ${INTERVAL}s"
while true; do
  HUB_TOKEN="$(getenv NODE_HUB_TOKEN)"
  PINNED="$(getenv_raw PINNED_NODES_JSON)"
  HUB_URL="$HUB_URL" HUB_TOKEN="$HUB_TOKEN" python3 - "$PINNED" <<'PY'
import os, sys, json, urllib.request
hub = os.environ["HUB_URL"]; tok = os.environ.get("HUB_TOKEN", "")
try:
    nodes = json.loads(sys.argv[1] or "[]")
except Exception as e:
    # Loud: a malformed PINNED_NODES_JSON would otherwise drop EVERY node with no trace.
    sys.stderr.write(f"[pin] ERROR: PINNED_NODES_JSON is not valid JSON ({e}); no nodes registered this tick\n")
    nodes = []
for n in nodes:
    health, payload = n.get("health"), n.get("payload")
    if not payload:
        continue
    healthy = True
    if health:
        try:
            urllib.request.urlopen(health, timeout=8)
        except Exception:
            healthy = False  # front-door down -> let it go offline in the registry
    if not healthy:
        continue
    try:
        req = urllib.request.Request(
            hub, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
        urllib.request.urlopen(req, timeout=8)
        print(f"[pin] registered {payload.get('label')}", flush=True)
    except Exception as e:
        print(f"[pin] register failed for {payload.get('label')}: {e}", flush=True)
PY
  sleep "$INTERVAL"
done
