#!/usr/bin/env bash
# install.sh — install/refresh the HOST adapter services (owui-claude, opencode-serve, owui-opencode)
# as systemd --user units so the coding agents run natively with FULL system access. Idempotent.
# See host/README.md. Reads secrets from ../.env; writes env files to ~/.config/agent-hub/ (chmod 600).
set -euo pipefail
cd "$(dirname "$0")"
HOST_DIR="$(pwd)"
REPO="$(cd .. && pwd)"
ENV="$REPO/.env"
UNITS="$HOME/.config/systemd/user"
CFG="$HOME/.config/agent-hub"
mkdir -p "$UNITS" "$CFG" "$HOME/.claude-owui"

[ -f "$ENV" ] || { echo "!! $ENV not found — populate the repo .env first"; exit 1; }
val() { grep -E "^$1=" "$ENV" | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/^"//; s/"$//'; }

# --- pick an opencode binary that is >= 1.18 (older builds have a fatal SQLite `seq` bug) ---
pick_opencode() {
  for b in "$HOME/.opencode/bin/opencode" "$(command -v opencode || true)" "$HOME/.local/bin/opencode"; do
    [ -x "$b" ] || continue
    v="$("$b" --version 2>/dev/null || echo 0)"; major="${v%%.*}"; minor="$(printf '%s' "$v" | cut -d. -f2)"
    if [ "${major:-0}" -gt 1 ] 2>/dev/null || { [ "${major:-0}" -eq 1 ] && [ "${minor:-0}" -ge 18 ]; } 2>/dev/null; then
      echo "$b"; return 0
    fi
  done
  return 1
}
OPENCODE_BIN="$(pick_opencode || true)"
if [ -z "$OPENCODE_BIN" ]; then
  echo "!! No opencode >= 1.18 found. Install it: curl -fsSL https://opencode.ai/install | bash"; exit 1
fi
echo "  opencode: $OPENCODE_BIN ($("$OPENCODE_BIN" --version))"

# --- claude host adapter needs its own config dir so it never clobbers your interactive ~/.claude ---
# Share MEMORY (CLAUDE.md) and SKILLS with the interactive CLI so the in-app Claude === your terminal
# Claude. Drop a SKILL.md in ~/.claude/skills → BOTH pick it up (server.mjs enables settingSources:'user'
# + skills:'all', and 'user' honors CLAUDE_CONFIG_DIR=~/.claude-owui).
[ -f "$HOME/.claude/CLAUDE.md" ] && ln -sf "$HOME/.claude/CLAUDE.md" "$HOME/.claude-owui/CLAUDE.md" || true
mkdir -p "$HOME/.claude/skills"
ln -sfn "$HOME/.claude/skills" "$HOME/.claude-owui/skills"
# ⚠️ Do NOT symlink ~/.claude/settings.json here. It typically sets defaultMode:"bypassPermissions",
# which the adapter loads via settingSources:'user' and which SKIPS canUseTool — that silently breaks
# interactive AskUserQuestion AND the destructive-command guardrail. The adapter pins permissionMode
# 'default' in code, but keeping settings.json out of .claude-owui is the belt-and-suspenders.
rm -f "$HOME/.claude-owui/settings.json"   # remove any stale symlink from older installs

# --- env files (secrets stay here, chmod 600) ---
cat > "$CFG/claude-host.env" <<EOF
CLAUDE_CODE_OAUTH_TOKEN=$(val CLAUDE_CODE_OAUTH_TOKEN)
ADAPTER_KEY=$(val ADAPTER_KEY)
OWUI_CLAUDE_PORT=9212
WORKSPACE=$HOME
MCP_TOOLS_URL=http://localhost:8009/mcp
OWUI_BASE=http://localhost:3000
CLAUDE_MODELS=$(val CLAUDE_MODELS)
CLAUDE_MODEL=$(val CLAUDE_MODEL)
FF_STATE_DIR=$REPO/ff-state
CLAUDE_CONFIG_DIR=$HOME/.claude-owui
NODE_ENV=production
EOF
cat > "$CFG/opencode-host.env" <<EOF
OPENCODE_BASE=http://localhost:4096
ADAPTER_KEY=$(val ADAPTER_KEY)
PORT=9213
OPENCODE_MODELS=$(val OPENCODE_MODELS)
OWUI_BASE=http://localhost:3000
FF_STATE_DIR=$REPO/ff-state
OPENCODE_API_KEY=$(val OPENCODE_API_KEY)
EOF
chmod 600 "$CFG"/*.env
echo "  wrote $CFG/{claude-host,opencode-host}.env"

# --- render unit templates (@HOME@ / @REPO@ / @OPENCODE_BIN@) into ~/.config/systemd/user ---
for u in owui-claude opencode-serve owui-opencode; do
  sed -e "s|@HOME@|$HOME|g" -e "s|@REPO@|$REPO|g" -e "s|@OPENCODE_BIN@|$OPENCODE_BIN|g" \
    "$HOST_DIR/systemd/$u.service" > "$UNITS/$u.service"
done
systemctl --user daemon-reload
systemctl --user enable --now owui-claude.service opencode-serve.service owui-opencode.service
loginctl enable-linger "$USER" >/dev/null 2>&1 || true   # survive reboot without an active login
sleep 3
echo "== status =="
systemctl --user is-active owui-claude opencode-serve owui-opencode | paste -sd' '
echo "Done. OWUI must reach these via host.docker.internal:9212/:9213 (see docker-compose.yml)."
