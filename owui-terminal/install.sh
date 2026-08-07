#!/usr/bin/env bash
# Install owui-terminal as a durable HOST service (real host shell for heroku/git/claude — NOT a
# container). Cross-platform: systemd --user (Linux) or launchd (macOS), with a nohup fallback.
# Idempotent: safe to re-run. Prints the TERMINAL_TOKEN for you to paste into .env.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HOME/.config/agent-hub"
ENVFILE="$CFG/terminal.env"
PORT="${PORT:-7681}"
SHELL_BIN="${TERMINAL_SHELL:-${SHELL:-/bin/bash}}"

echo "== owui-terminal installer =="
mkdir -p "$CFG"; chmod 700 "$CFG"

# 1) python venv + aiohttp
PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "!! python3 not found" >&2; exit 1; }
if [ ! -x "$DIR/venv/bin/python" ]; then
  echo "-- creating venv"; "$PY" -m venv "$DIR/venv"
fi
"$DIR/venv/bin/pip" install -q --upgrade pip aiohttp

# 2) token (generate once)
if [ ! -f "$ENVFILE" ] || ! grep -q '^TERMINAL_TOKEN=..' "$ENVFILE" 2>/dev/null; then
  TOK="$("$DIR/venv/bin/python" -c 'import secrets;print(secrets.token_hex(24))')"
  printf 'TERMINAL_TOKEN=%s\n' "$TOK" > "$ENVFILE"; chmod 600 "$ENVFILE"
  echo "-- generated TERMINAL_TOKEN"
fi
TOKEN="$(grep '^TERMINAL_TOKEN=' "$ENVFILE" | cut -d= -f2-)"

OS="$(uname -s)"
if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  UNIT="$HOME/.config/systemd/user/owui-terminal.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=owui-terminal — PTY-over-WS backend for OWUI terminal
After=default.target

[Service]
Type=simple
EnvironmentFile=$ENVFILE
Environment=PORT=$PORT
Environment=TERMINAL_SHELL=$SHELL_BIN
Environment=TERMINAL_CWD=%h
ExecStart=$DIR/venv/bin/python $DIR/server.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now owui-terminal.service
  loginctl enable-linger "$USER" >/dev/null 2>&1 || true
  echo "-- installed systemd --user service (owui-terminal)"

elif [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.agenthub.owui-terminal.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agenthub.owui-terminal</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/venv/bin/python</string><string>$DIR/server.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PORT</key><string>$PORT</string>
    <key>TERMINAL_SHELL</key><string>$SHELL_BIN</string>
    <key>TERMINAL_CWD</key><string>$HOME</string>
    <key>TERMINAL_TOKEN</key><string>$TOKEN</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  echo "-- installed launchd agent (com.agenthub.owui-terminal)"

else
  echo "-- no systemd/launchd; run manually:"
  echo "   PORT=$PORT TERMINAL_SHELL=$SHELL_BIN TERMINAL_TOKEN=$TOKEN nohup $DIR/venv/bin/python $DIR/server.py &"
fi

echo ""
echo "owui-terminal listening on :$PORT"
echo "TERMINAL_TOKEN=$TOKEN"
echo "→ put this token in .env (TERMINAL_TOKEN=...) so OWUI can reach this machine's terminal."
