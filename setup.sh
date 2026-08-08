#!/usr/bin/env bash
# Agent Hub — one-command setup for this machine.
#   1) ensure .env      2) install host owui-terminal   3) build fork (hub)   4) docker compose up
#   5) pull ollama model (hub)   6) health check
# Idempotent — safe to re-run. Role comes from COMPOSE_PROFILES in .env (hub | node).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 1) .env
if [ ! -f .env ]; then
  cp .env.example .env; chmod 600 .env
  echo "→ created .env from .env.example. Edit it (set COMPOSE_PROFILES, secrets, IPs), then re-run ./setup.sh"
  exit 0
fi
# Read keys individually — do NOT bash-source .env (values may contain spaces/parens, e.g.
# TERMINAL_NAME="aibo (host shell)", which break `source`). Docker compose reads .env with its own parser.
getenv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
ROLE="$(getenv COMPOSE_PROFILES)"; ROLE="${ROLE:-hub}"
ADAPTER_BIND="$(getenv ADAPTER_BIND)"
NODE_LABEL="$(getenv NODE_LABEL)"
HUB_REGISTER_URL="$(getenv HUB_REGISTER_URL)"
echo "== Agent Hub setup — role: $ROLE =="

# 2) host owui-terminal (real shell) + auto-fill TERMINAL_TOKEN in .env if blank
echo "-- installing owui-terminal (host service)"
bash ./owui-terminal/install.sh >/tmp/owui-terminal-install.log 2>&1 || { echo "!! terminal install failed"; tail -20 /tmp/owui-terminal-install.log; exit 1; }
TTOK="$(grep '^TERMINAL_TOKEN=' "$HOME/.config/agent-hub/terminal.env" 2>/dev/null | cut -d= -f2- || true)"
if [ -n "$TTOK" ]; then
  if grep -q '^TERMINAL_TOKEN=$' .env || ! grep -q '^TERMINAL_TOKEN=' .env; then
    # hub uses TERMINAL_TOKEN (OWUI terminal-server), node uses TERMINAL_KEY (registrar)
    grep -q '^TERMINAL_TOKEN=' .env && sed -i.bak "s|^TERMINAL_TOKEN=.*|TERMINAL_TOKEN=$TTOK|" .env || echo "TERMINAL_TOKEN=$TTOK" >> .env
    echo "-- wrote TERMINAL_TOKEN into .env"
  fi
fi

# 3) fork image (hub only) — build from source if missing
if [ "$ROLE" = "hub" ]; then
  IMG="${OWUI_IMAGE:-agent-hub/open-webui:v0.11.0-fork}"
  if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    echo "-- building OWUI fork image (first run)"
    [ -d owui-fork/upstream/.git ] && ./owui-fork/build.sh || ./owui-fork/build.sh --clone
  fi
fi

# 4) pre-create the external volumes + network so a FRESH machine doesn't fail on `external: true`
#    (on aibo these already exist → reused, data preserved). Then bring the stack up.
docker volume create open-webui >/dev/null 2>&1 || true
docker volume create ollama >/dev/null 2>&1 || true
docker network create apps >/dev/null 2>&1 || true
# quick secret sanity (warn only)
for k in WEBUI_SECRET_KEY ADAPTER_KEY; do
  v="$(grep "^$k=" .env | cut -d= -f2-)"
  case "$v" in ""|change-me*) echo "⚠️  $k is empty/placeholder in .env — set it before real use";; esac
done
if [ "$ROLE" = "node" ] && [ "${ADAPTER_BIND:-127.0.0.1}" = "127.0.0.1" ]; then
  echo "⚠️  node role but ADAPTER_BIND=127.0.0.1 — the hub can't reach this node's adapters. Set ADAPTER_BIND=0.0.0.0."
fi
echo "-- docker compose up -d --build"
docker compose up -d --build

# 5) ollama model (hub)
if [ "$ROLE" = "hub" ]; then
  echo "-- ensuring ollama model llama3.2:3b"
  docker exec ollama ollama list 2>/dev/null | grep -q 'llama3.2:3b' || docker exec ollama ollama pull llama3.2:3b || true
fi

# 5.5) Hermes brain(s) — host service (computer_use needs the host GUI). Skipped gracefully if the
#      base hermes-agent isn't installed (a node may not run Hermes; a fresh box installs it first).
if [ -x "$HOME/.hermes/hermes-agent/venv/bin/hermes" ] || [ -x "$HOME/.local/bin/hermes" ] || [ -n "$(getenv HERMES_INSTALL_CMD)" ]; then
  INSTANCES="$(getenv HERMES_INSTANCES)"; INSTANCES="${INSTANCES:-default:9119}"
  echo "-- installing Hermes brain(s): $INSTANCES"
  for inst in $(echo "$INSTANCES" | tr ',' ' '); do
    name="${inst%%:*}"; port="${inst##*:}"; [ "$name" = "$port" ] && port=9119
    bash ./hermes/install.sh --name "$name" --port "$port" || echo "⚠️  Hermes brain '$name' failed (see above)"
  done
else
  echo "-- Hermes: base hermes-agent not installed → skipping brains (see hermes/README.md to add)"
fi

# 5.9) refresh OWUI's model list (adapters may have been recreated → avoid the stale "Model '' not
#      found" gate; RESET_CONFIG_ON_START re-seeds connections on boot).
if [ "$ROLE" = "hub" ]; then docker compose restart open-webui >/dev/null 2>&1 || true; fi

# 6) health
echo "-- waiting for health"
if [ "$ROLE" = "hub" ]; then
  for i in $(seq 1 40); do
    [ "$(docker inspect open-webui --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break; sleep 2
  done
  echo ""
  echo "✅ hub up. OWUI: http://localhost:3000   ·   registrar heartbeating '${NODE_LABEL}'."
else
  sleep 4
  echo ""
  echo "✅ node up. Adapters + registrar running; heartbeating '${NODE_LABEL}' → ${HUB_REGISTER_URL}."
  echo "   On the HUB: add this node's terminal to TERMINAL_SERVER_CONNECTIONS + its adapters as an OWUI connection."
fi
docker compose ps --format '  {{.Name}}  {{.Status}}'
