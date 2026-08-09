# host/ — run the coding agents as HOST processes (full system access)

On the **hub** (aibo), the coding adapters run **on the host as systemd --user services**, NOT in
Docker — so they run *as you* with full access: `systemctl`, all drives, host processes, docker. This
is parity with local Claude Code. (Hermes already runs on the host via its brain.) OWUI, still in
Docker, reaches them via `host.docker.internal:<port>`.

```
  open-webui (docker)
     │  OpenAI-compatible calls  (OPENAI_API_BASE_URLS → host.docker.internal)
     ├─► :9212  owui-claude.service      → node server.mjs (Claude Agent SDK)          [FULL host access]
     └─► :9213  owui-opencode.service    → python opencode_adapter.py
                     └─► :4096  opencode-serve.service → opencode serve (headless)      [FULL host access]
```

## Install / re-apply
```bash
cd ~/Documents/apps/agent-hub/host
./install.sh            # generates env files from ../.env, installs+enables the 3 units, starts them
```
Idempotent. Requires on PATH: `node` (v18+), `python3`, `opencode` (the STANDALONE build — see gotcha),
plus the repo's `.env` populated. `loginctl enable-linger $USER` (already on) makes them survive reboot.

## The units (installed to `~/.config/systemd/user/`)
- `owui-claude.service`   — `node <repo>/owui-claude/server.mjs` (:9212). Env: `~/.config/agent-hub/claude-host.env`.
- `opencode-serve.service`— `opencode serve --hostname 127.0.0.1 --port 4096`.
- `owui-opencode.service` — `python3 <repo>/owui-opencode/opencode_adapter.py` (:9213). Env: `~/.config/agent-hub/opencode-host.env`.

Secrets live only in the generated env files under `~/.config/agent-hub/` (chmod 600), never in the repo.

## Gotchas (learned the hard way)
- **opencode version matters.** A machine can have TWO opencodes: an old `npm -g` one at
  `~/.local/bin/opencode` and the STANDALONE `~/.opencode/bin/opencode` that `https://opencode.ai/install`
  maintains. opencode **< 1.18** has a SQLite bug (`NOT NULL constraint failed: session_message.seq`) that
  500s every turn. `install.sh` points the service at whichever `opencode` is **≥ 1.18**. Upgrade with the
  official installer, not npm (npm global-prefix perms often fail).
- **Claude config isolation + skills parity.** The adapter uses `CLAUDE_CONFIG_DIR=~/.claude-owui` so its
  SDK state never clobbers your interactive `claude`'s `~/.claude.json`. `install.sh` symlinks your global
  **`CLAUDE.md`** and **`skills/`** into that dir (`~/.claude-owui/skills → ~/.claude/skills`) so the
  in-app Claude has the SAME memory + skills as your terminal `claude` — drop a `SKILL.md` in
  `~/.claude/skills/` and both get it (`server.mjs` sets `settingSources:['user'] + skills:'all'`).
  ⚠️ It deliberately does **NOT** symlink `~/.claude/settings.json` — that file usually sets
  `defaultMode:"bypassPermissions"`, which skips `canUseTool` and would break interactive AskUserQuestion
  + the destructive guardrail. The adapter also pins `permissionMode:'default'` in code. (See `CLAUDE.md §5b`.)
- **Reachability from the OWUI container:** `OWUI_BASE=http://localhost:3000`, `MCP_TOOLS_URL=http://localhost:8009/mcp`
  (mcp-tools is published to 127.0.0.1:8009). The adapters bind `0.0.0.0:<port>`; open-webui reaches them
  via the `host.docker.internal:host-gateway` extra_host.
- **Rollback to containers:** in `docker-compose.yml` put `owui-claude`/`opencode`/`owui-opencode` back in the
  `hub` profile + repoint `OPENAI_API_BASE_URLS`/`CLAUDE_ADAPTER_URL` to the service names, then
  `systemctl --user disable --now owui-claude opencode-serve owui-opencode` and `docker compose up -d`.
- The **MacBook node still runs these as containers** (node profile) — it hasn't migrated.

See `../CLAUDE.md` for the overall build/deploy workflow.
