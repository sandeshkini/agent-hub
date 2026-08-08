#!/usr/bin/env bash
# hermes/install.sh — install/refresh a Hermes BRAIN (host service) with OUR customizations:
#   config (from template), cua-driver SELF-HEAL wrapper + drop-in, guardrail hook, gateway service.
# Cross-platform: systemd --user (Linux) / launchd (macOS). Idempotent; re-run after `hermes update`
# to re-apply everything. Multiple brains: run once per brain with a unique --name/--port (E8.2).
#
#   ./install.sh [--name default] [--port 9119]
#
# NOTE: computer_use needs the HOST GUI (X11 on Linux / Accessibility+Screen-Recording on macOS), so
# Hermes runs on the host — never in a container. Base hermes-agent (public NousResearch repo, installed
# via its own installer) must be present first; this script applies our reproducible layer on top.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

NAME="default"; PORT="9119"
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

# repo .env supplies HERMES_DASH_USER / HERMES_DASH_PW_HASH / OPENROUTER_API_KEY. Read keys individually
# (do NOT bash-source .env — values can contain spaces/parens/special chars that break `source`).
# `$$` -> `$`: docker compose interpolates `$` in .env, so values containing it (the scrypt
# password_hash, `scrypt$16384$8$1$salt$dk`) MUST be written escaped as `$$` or compose warns and
# blanks them. This script reads the same file, so it un-escapes on the way out.
getenv() { [ -f "$ROOT/.env" ] && grep -E "^$1=" "$ROOT/.env" | head -1 | cut -d= -f2- | sed 's/\$\$/$/g' || true; }
export HERMES_DASH_USER="${HERMES_DASH_USER:-$(getenv HERMES_DASH_USER)}"
export HERMES_DASH_PW_HASH="${HERMES_DASH_PW_HASH:-$(getenv HERMES_DASH_PW_HASH)}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(getenv OPENROUTER_API_KEY)}"
export MCP_SERVERS="${MCP_SERVERS:-$(getenv MCP_SERVERS)}"
HERMES_INSTALL_CMD="${HERMES_INSTALL_CMD:-$(getenv HERMES_INSTALL_CMD)}"

HERMES_HOME="$HOME/.hermes"; SVC="hermes-dashboard"
if [ "$NAME" != "default" ]; then HERMES_HOME="$HOME/.hermes-$NAME"; SVC="hermes-$NAME"; fi
VENV_HERMES="$HOME/.hermes/hermes-agent/venv/bin/hermes"

echo "== Hermes brain '$NAME' -> $HERMES_HOME (:$PORT) =="

# 1) base hermes-agent present?
if [ ! -x "$VENV_HERMES" ] && [ ! -x "$HOME/.local/bin/hermes" ]; then
  if [ -n "${HERMES_INSTALL_CMD:-}" ]; then
    echo "-- base hermes-agent missing; running HERMES_INSTALL_CMD"; eval "$HERMES_INSTALL_CMD"
  else
    echo "!! hermes-agent not installed. NousResearch/hermes-agent is PUBLIC — use the official" >&2
    echo "   installer (handles uv, Python 3.11, node, ripgrep, ffmpeg; Linux/macOS/WSL2/Termux):" >&2
    echo "   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" >&2
    echo "   It lands at ~/.hermes/hermes-agent + ~/.local/bin/hermes, which is what we look for." >&2
    echo "   Then re-run this script (or set HERMES_INSTALL_CMD to the line above)." >&2
    exit 1
  fi
fi

mkdir -p "$HERMES_HOME/hooks" "$HERMES_HOME/bin"

# 2) config.yaml from template (secrets + this brain's home substituted)
python3 - "$DIR/config.yaml.template" "$HERMES_HOME/config.yaml" "$HERMES_HOME" <<'PY'
import os, sys
tpl, dst, home = sys.argv[1], sys.argv[2], sys.argv[3]
import json
s = open(tpl).read().replace('__HERMES_HOME__', home)
for k in ('HERMES_DASH_USER', 'HERMES_DASH_PW_HASH'):
    s = s.replace('${'+k+'}', os.environ.get(k, ''))
# External MCP hub: merge http entries from MCP_SERVERS into mcp_servers (one place -> all agents).
# Only round-trips YAML when there ARE extra servers (keeps the default byte-identical); guarded on PyYAML.
try:
    extra = [e for e in json.loads(os.environ.get('MCP_SERVERS', '') or '[]')
             if isinstance(e, dict) and e.get('name') and e.get('url')]
except Exception:
    extra = []
if extra:
    try:
        import yaml
        cfg = yaml.safe_load(s); cfg.setdefault('mcp_servers', {})
        for e in extra:
            entry = {'url': e['url']}
            if e.get('headers'): entry['headers'] = e['headers']
            cfg['mcp_servers'][e['name']] = entry
        s = yaml.safe_dump(cfg, sort_keys=False)
    except Exception as ex:
        print('   (MCP_SERVERS merge skipped: %s)' % ex)
open(dst, 'w').write(s)
print('   config.yaml written')
PY

# 3) guardrail hook + cua-driver self-heal wrapper (update-proof: lives in the brain's dir)
install -m 0755 "$DIR/hooks/deny-destructive.py" "$HERMES_HOME/hooks/deny-destructive.py"
install -m 0755 "$DIR/bin/cua-driver"           "$HERMES_HOME/bin/cua-driver"

# 4) per-brain secrets .env (Hermes reads OPENROUTER_API_KEY etc. from here)
touch "$HERMES_HOME/.env"; chmod 600 "$HERMES_HOME/.env"
grep -q '^OPENROUTER_API_KEY=' "$HERMES_HOME/.env" 2>/dev/null || \
  echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}" >> "$HERMES_HOME/.env"

# 5) durable service
OS="$(uname -s)"
if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  UNIT="$HOME/.config/systemd/user/$SVC.service"; mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=Hermes brain '$NAME' (agent gateway :$PORT)
After=default.target

[Service]
WorkingDirectory=$HERMES_HOME
Environment=HERMES_HOME=$HERMES_HOME
Environment=HERMES_ACCEPT_HOOKS=1
Environment=HERMES_CUA_DRIVER_CMD=$HERMES_HOME/bin/cua-driver
Environment=PATH=$HOME/.local/bin:$HOME/.hermes/hermes-agent/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$HOME/.hermes/hermes-agent/venv/bin/hermes dashboard --host 0.0.0.0 --port $PORT --no-open --skip-build
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$SVC.service"
  loginctl enable-linger "$USER" >/dev/null 2>&1 || true
  echo "-- systemd --user service '$SVC' installed + started"

elif [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.agenthub.$SVC.plist"; mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agenthub.$SVC</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/.hermes/hermes-agent/venv/bin/hermes</string>
    <string>dashboard</string><string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>$PORT</string><string>--no-open</string><string>--skip-build</string>
  </array>
  <key>WorkingDirectory</key><string>$HERMES_HOME</string>
  <key>EnvironmentVariables</key><dict>
    <key>HERMES_HOME</key><string>$HERMES_HOME</string>
    <key>HERMES_ACCEPT_HOOKS</key><string>1</string>
    <key>HERMES_CUA_DRIVER_CMD</key><string>$HERMES_HOME/bin/cua-driver</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  echo "-- launchd agent 'com.agenthub.$SVC' loaded"
  echo "-- cua-driver macOS permissions (grant Accessibility + Screen Recording if prompted):"
  ( export LD_LIBRARY_PATH="$HOME/.local/bin"; "$HOME/.local/bin/cua-driver" permissions status 2>/dev/null || true )
else
  echo "-- no systemd/launchd; run manually:"
  echo "   HERMES_HOME=$HERMES_HOME HERMES_CUA_DRIVER_CMD=$HERMES_HOME/bin/cua-driver \\"
  echo "   $HOME/.hermes/hermes-agent/venv/bin/hermes dashboard --host 0.0.0.0 --port $PORT --no-open --skip-build"
fi

echo "✅ Hermes brain '$NAME' ready on :$PORT (config: $HERMES_HOME/config.yaml, cua self-heal wrapper installed)"
