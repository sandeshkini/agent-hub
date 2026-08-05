#!/bin/sh
set -e
# Seed opencode's auth from the env key (opencode Zen provider). Shape mirrors
# ~/.local/share/opencode/auth.json: {"opencode":{"type":"api","key":"..."}}
mkdir -p "$HOME/.local/share/opencode"
if [ -n "$OPENCODE_API_KEY" ]; then
  printf '{"opencode":{"type":"api","key":"%s"}}\n' "$OPENCODE_API_KEY" \
    > "$HOME/.local/share/opencode/auth.json"
fi
exec opencode serve --hostname 0.0.0.0 --port 4096
