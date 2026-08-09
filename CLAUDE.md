# CLAUDE.md — AI/agent working guide for the Agent Hub

**Read this first.** This is the canonical onboarding doc for any AI session (Claude Code, Cursor,
Codex, etc.) working in this repo. It captures the architecture, the build/deploy workflow, and the
gotchas that have cost real hours. `AGENTS.md` points here.

- **User-facing setup / operate / macOS notes** → `README.md`
- **Roadmap, phases, feature plan** → `ROADMAP.md`
- **The OWUI fork patch mechanics** → `owui-fork/README.md` + `owui-fork/PATCHES.md`
- **Deep architecture docs (hostnames, auth model, deploy)** → `~/Documents/aibo-server/agent-hub/`

---

## 1. What this is (30-second model)

A forked **Open WebUI** ("agent hub") that fronts multiple agents as selectable models, plus an
in-app terminal, multi-machine nodes, an inbox with AI summaries, and background runs. One repo
stands up the whole thing as a **hub** or a **node** via `COMPOSE_PROFILES`.

```
Browser / iOS PWA / Slack
        │
        ▼
  Open WebUI (FORK)  ── the front end (Svelte + FastAPI), image agent-hub/open-webui:v0.11.0-fork
        │  OpenAI-compatible calls
        ├──► owui-hermes  (:9211)  → host Hermes brain (hermes-dashboard :9119)
        ├──► owui-claude  (:9212)  → Anthropic Claude Agent SDK  (CLAUDE_CODE_OAUTH_TOKEN)
        ├──► owui-opencode(:9213)  → `opencode serve` (:4096)
        │  terminal proxy (/api/v1/terminals/<serverId>/…)
        ├──► owui-terminal (host :7681)  → real PTY-over-WebSocket shell
        │  shared tools (MCP, http)
        └──► mcp-tools (:8000/mcp)  → publish_artifact + notify (+ external MCP from .env)

  Multi-machine: aibo = hub (operator.kingdomofluna.com), MacBook = node
  (macbook.kingdomofluna.com), reachable via Pangolin front-door register.mb.kingdomofluna.com.
```

Everything runs from `~/Documents/apps/agent-hub` on **aibo** (Linux). Repo:
`github.com/sandeshkini/agent-hub`.

---

## 2. THE BUILD/DEPLOY WORKFLOW — read before touching the fork

The single most important thing. Getting this wrong silently ships stale/empty images **or a white
screen straight to prod**.

> ⚠️ **DEPLOY THE FORK ONLY VIA `./owui-fork/deploy.sh`.** Never run
> `docker compose up -d --force-recreate open-webui` by hand. A broken fork build still returns HTTP 200
> (backend + SPA shell serve) while the Svelte app throws at mount → the whole page is BLANK. `deploy.sh`
> render-tests the candidate in headless chromium on a **staging** container (`:3001`, own volume) BEFORE
> prod, and keeps an instant rollback point (`:prev`). Full guide: **`owui-fork/DEPLOYING.md`**.
>
> ```bash
> ./owui-fork/deploy.sh            # build → staging → render-gate → promote to prod (+ auto-rollback on fail)
> ./owui-fork/deploy.sh staging    # build → staging → render-gate → STOP (review at staging URL) → then `promote`
> ./owui-fork/deploy.sh rollback   # instant revert to the previous prod image (:prev)
> ```
> Gate failure ⇒ `❌ … PROD UNTOUCHED`. This is verified: an intentional render crash was caught on
> staging while prod kept rendering. The steps below are the underlying mechanics `deploy.sh` runs.

### Fork (Open WebUI) changes — frontend Svelte OR backend FastAPI under `owui-fork/upstream/`
`owui-fork/upstream/` is a **pinned upstream checkout (v0.11.0), gitignored**. Our changes live ONLY
in `owui-fork/patches/0001-terminal-page.patch`. `build.sh` does `git reset --hard HEAD` on upstream,
re-applies the patch, then `docker build`. **So:**

```bash
# 1. edit files under owui-fork/upstream/…  (Svelte in src/, Python in backend/)
# 2. REGENERATE THE PATCH  ← forget this and your edits are WIPED by build.sh's reset
UP=owui-fork/upstream
git -C "$UP" add -A
git -C "$UP" diff --cached > owui-fork/patches/0001-terminal-page.patch
git -C "$UP" reset -q
# 3 + 4. build AND deploy through the render gate (build.sh + staging render-test + promote + rollback point)
./owui-fork/deploy.sh          # ← USE THIS. (build.sh alone only builds; deploy.sh gates + promotes safely)
# 5. verify the change is actually in the running container (not a cache ghost — gotcha #3)
docker exec open-webui sh -c "grep -rl '<a string from your change>' /app/build | head"      # frontend
docker exec open-webui sh -c "grep -c '<marker>' /app/backend/open_webui/routers/<file>.py"    # backend
```

Only the **patch** is committed (`upstream/` is ignored). `git add -A` in the repo root stages the
regenerated patch — commit that.

### Adapter changes — `owui-claude/`, `owui-hermes/`, `owui-opencode/`, `opencode/`, `mcp-tools/`
Plain container builds:
```bash
docker compose build owui-claude          # (or the service you changed)
docker compose up -d --force-recreate owui-claude
```

### Host-service changes — `owui-terminal/server.py`, `node/pin-remote-node.sh`, `hermes/`
These run as **host services**, NOT containers. They run the repo file directly, so just restart:
```bash
systemctl --user restart owui-terminal.service     # PTY terminal server (:7681)
systemctl --user restart agent-hub-pin.service     # re-registers the MacBook node every ~45s
# hermes brain: service hermes-dashboard (do NOT confuse with claude-monitor)
```
(On the MacBook these are launchd agents: `com.agenthub.*`.)

### After ANY change: commit + push
```bash
git add -A && git commit -m "…"        # end message with the Co-Authored-By trailer
git pull --rebase origin main && git push origin main   # the MacBook also pushes; rebase to avoid conflicts
```

---

## 3. GOTCHAS THAT COST HOURS (numbered — grep for these)

1. **Regenerate the patch before building the fork.** (See §2.) `build.sh` resets `upstream/` to HEAD;
   any uncommitted edit not captured in the patch is gone, and the build silently uses the old patch.

2. **Never run two fork builds at once.** Do NOT combine the Bash tool's `run_in_background:true` with
   `nohup … &` — that double-backgrounds. Two `build.sh` instances race on the shared `upstream/` git
   tree (`reset --hard` + `git apply`) and corrupt each other → empty/partial images. Run ONE build,
   in the foreground, and wait for the `== built …` marker. If unsure, `pgrep -af build.sh`.

3. **Docker COPY-cache can ship a STALE image.** Observed: a changed fork file didn't land in the image
   even though the build "succeeded". `build.sh` now self-heals — it sha1-compares a sentinel file
   (`backend/open_webui/routers/agent_nodes.py`) in the image vs the patched tree and auto-rebuilds
   `--no-cache` if they differ. **Keep that guard.** Always verify with the `docker exec … grep` step.

4. **`RESET_CONFIG_ON_START=true`.** OWUI re-seeds its config from env vars on every start, so anything
   you set via the OWUI API is wiped on restart. **Durable config must live in compose env / `.env`.**

5. **`.env` must NOT be bash-sourced.** It holds JSON values (`TERMINAL_SERVERS_JSON`, `PINNED_NODES_JSON`)
   and `$`-containing hashes. Compose interpolates `$` (use `$$` for literals). Scripts read it with
   dedicated `getenv`/`getenv_raw` helpers — `getenv_raw` for JSON (do not strip `#`, JSON contains it).

6. **An offline node must never hang the hub.** `AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST=5` bounds the
   per-connection model-list fetch; without it a sleeping MacBook 500s the whole OWUI page on load.

7. **Two separate OWUI instances exist.** `operator.kingdomofluna.com` (aibo hub) and
   `macbook.kingdomofluna.com` (the MacBook's own OWUI) each have their **own** DB, chats, and terminal
   list. They are not one federated app. Cross-machine terminal *discovery* (see §4) bridges live shells,
   but chat lists are per-instance.

8. **Deploy the fork ONLY via `./owui-fork/deploy.sh`** (render-gate + staging + rollback). A bare
   `docker compose up -d --force-recreate open-webui` ships a white screen (HTTP-200-but-blank) to prod
   with no gate — that's exactly the failure this pipeline prevents. See §2 + `owui-fork/DEPLOYING.md`.
   Under the hood the fork image tag `agent-hub/open-webui:v0.11.0-fork` is replaced in place and the
   container recreated from it; `deploy.sh` wraps that with a render check and a `:prev` rollback point.

9. **Secrets never leave `.env`** (gitignored). Never commit tokens. A leaked default password
   (`hermesluna`) once got into public history and had to be rotated live — don't hardcode secrets.

---

## 4. Multi-machine model (aibo hub + MacBook node)

- **Node registry** — in-memory, in the fork backend (`routers/agent_nodes.py`). A node is `online`
  when `last_seen ≤ 90s`, evicted after 1h. `GET /api/v1/nodes/` lists; `POST /register` is **fail-closed**
  (refuses when `NODE_HUB_TOKEN` unset unless `ALLOW_OPEN_NODE_REG=true`).
- **The MacBook doesn't push to the hub** (OWUI isn't exposed SSO-free). Instead the hub re-registers it
  on a loop: `agent-hub-pin.service` runs `node/pin-remote-node.sh` (reads `PINNED_NODES_JSON`, health-checks
  the front-door, POSTs the manifest to localhost OWUI every ~45s).
- **Remote models are prefixed** `mb.<id>` via OWUI's `OPENAI_API_CONFIGS` (`{"<idx>":{"prefix_id":"mb"}}`).
  The prefix is stripped when calling upstream. `machineForModel()` (frontend `machineSelector.ts`) matches
  exactly first, then on the bare suffix, so attribution survives either form.
- **Front-door** — `register.mb.kingdomofluna.com` (Pangolin/fossorial on VPS `root@46.62.218.143`) →
  MacBook Caddy `:8088` → `/claude`, `/opencode`, `/hermes`, `/term`. Raw-SQL Pangolin resource creation
  does NOT program gerbil's WG route — must use the Pangolin UI/API.
- **Cross-machine terminal discovery** — `agentTerminals.ts::discoverSessions()` polls each terminal
  server's `GET /api/terminals/sessions` (via the OWUI proxy `/api/v1/terminals/<serverId>/…`) and merges
  live PTYs into the sidebar as `discovered` entries. Tapping one attaches to the live shell
  (`create_session` re-attaches by `X-Session-Id`). Discovered entries are display-only (never pushed to
  the sync store).

---

## 5. Security & hard constraints (do not violate)

- **NEVER use `ANTHROPIC_API_KEY`.** Claude auth is `CLAUDE_CODE_OAUTH_TOKEN` (the user's Max subscription).
  Do not add/require an API key. Do not bring it up.
- **claude-monitor is a SEPARATE project** (`~/Documents/apps/claude-monitor`, MCP at :7777). Never touch
  its systemd. Build it only via `make install` from that dir — never `go build ./cmd/cm/`.
- **Never run firefox on display `:1`** — it disrupts the `cua-driver` (Hermes computer-use).
- **Adapters + terminal server are FAIL-CLOSED** — empty `ADAPTER_KEY`/`TOKEN` denies all; constant-time
  compare; startup refuses to run with an empty key. The terminal file API is jailed to `$HOME` via
  realpath. Node `/register` is fail-closed. Keep all of these.
- **Fire-and-forget safety** — a crashing run must not re-execute forever. FF recovery is bounded by
  attempts + TTL and records the attempt BEFORE re-issuing (`owui_mirror.py`, `server.mjs` recoverFF).

---

## 5b. Skills — the in-app Claude === your terminal Claude

The `owui-claude` adapter is the **same Agent SDK** as the desktop `claude` CLI; it just runs headless.
It loads your real **skills** and **memory** so the phone/OWUI Claude behaves like your terminal one:

- **One global skills library:** `~/.claude/skills/`. Drop a `SKILL.md` folder there → **both** the CLI
  (any repo) and the in-app agent get it. The adapter reaches it via `~/.claude-owui/skills` (a symlink to
  `~/.claude/skills`, created by `host/install.sh`).
- **How the adapter loads them** (`owui-claude/server.mjs`): `settingSources: ['user']` + `skills: 'all'`.
  `'user'` honors `CLAUDE_CONFIG_DIR=~/.claude-owui`, so it reads that dir's `skills/` + `CLAUDE.md`
  (also symlinked to `~/.claude/CLAUDE.md`). Toggle/override via env `CLAUDE_SETTING_SOURCES`,
  `CLAUDE_SKILLS`, `CLAUDE_PERMISSION_MODE`.
- **Repo-scoped skills** live in `<repo>/.claude/skills/` (e.g. `deploy-fork`) and only apply to CLI
  sessions working in that repo — the in-app agent (cwd `$HOME`) sees only the global library.
- ⚠️ **Never add `'project'`/`'local'` to the adapter's `settingSources`, and never symlink
  `~/.claude/settings.json` into `~/.claude-owui`.** The adapter's cwd is `$HOME`, so those sources read
  the real `~/.claude/settings.json` whose `defaultMode:"bypassPermissions"` SKIPS `canUseTool` — which
  silently breaks interactive **AskUserQuestion** and the **destructive guardrail**. The adapter pins
  `permissionMode:'default'` in code; `host/install.sh` keeps `settings.json` out of `.claude-owui`.

## 6. Repo map (where things live)

| Path | What | Change → how to ship |
|---|---|---|
| `owui-fork/upstream/` | pinned OWUI v0.11.0 (GITIGNORED, rebuilt from patch) | edit → **regen patch** → `build.sh` |
| `owui-fork/patches/0001-terminal-page.patch` | **all** fork changes (the source of truth) | committed |
| `owui-fork/build.sh` | reset upstream → apply patch → docker build (+ stale-image self-heal) | — |
| `owui-claude/server.mjs` | Claude adapter (Node, Agent SDK); FF mirror, AskUserQuestion, guardrail | `compose build` |
| `owui-hermes/hermes_adapter.py` | Hermes adapter (Python); vision, guardrail, FF | `compose build` |
| `owui-opencode/opencode_adapter.py` | OpenCode adapter (Python, over `opencode serve`); session cache | `compose build` |
| `owui-*/owui_mirror.py` | shared fire-and-forget mirror helper (identical copy in hermes+opencode) | `compose build` |
| `owui-terminal/server.py` | host PTY-over-WS + file API (jailed) + `GET /api/terminals/sessions` | `systemctl --user restart owui-terminal` |
| `mcp-tools/server.py` | shared MCP: `publish_artifact` (→ artifacts board + ntfy) + `notify` | `compose build mcp-tools` |
| `node/pin-remote-node.sh` | hub-side heartbeat that re-registers remote nodes | `systemctl --user restart agent-hub-pin` |
| `node/frontdoor/Caddyfile` | MacBook front-door reverse-proxy (`/claude /opencode /hermes /term`) | on the MacBook |
| `docker-compose.yml` | the whole stack; `hub`/`node` profiles; fork image default | — |
| `setup.sh` · `.env.example` | one-command role-aware setup + the single config file | — |
| `slack-gateway/` | Bolt Socket-Mode gateway → OWUI completions (optional profile) | `compose build` |

### Key fork files inside `upstream/` (edit → regen patch → build)
- `src/lib/components/layout/Sidebar.svelte` — the sidebar (machine hero at top, sections, chats).
- `src/lib/components/layout/Sidebar/MachineSelector.svelte` — the top-level Machine scope hero.
- `src/lib/components/layout/Sidebar/Section.svelte` — section-header styling (uppercase micro-caps).
- `src/lib/components/chat/XTerminal.svelte` — xterm client (WS, mobile tap-to-focus, sendKey, fit).
- `src/routes/(app)/terminal/+page.svelte` — terminal page + mobile key bar + connection status.
- `src/lib/utils/agentTerminals.ts` — terminal-session store: sync + **cross-machine discovery**.
- `src/lib/utils/machineSelector.ts` — node registry store + `machineForModel` (mb.-prefix aware).
- `backend/open_webui/routers/agent_nodes.py` — node registry (fail-closed register).
- `backend/open_webui/routers/agent_inbox.py` — inbox summaries.
- `backend/open_webui/routers/agent_terminal_sync.py` — cross-device terminal-list sync (rev LWW).
- `backend/open_webui/models/chats.py` — `_primary_model_id()` + `model_id` on all 5 chat-list builders.

---

## 7. Verify / debug commands

```bash
# health of the whole stack
docker compose ps
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/health

# adapter reachable + a live completion (real key from .env)
K=$(grep -E '^ADAPTER_KEY=' .env | cut -d= -f2- | sed 's/#.*//' | tr -d ' "')
docker exec open-webui curl -s -H "Authorization: Bearer $K" http://owui-claude:9212/v1/models

# is the MacBook node online / reachable?
curl -s -o /dev/null -w "%{http_code}\n" -m 8 https://register.mb.kingdomofluna.com/health

# mint an admin OWUI JWT to hit protected APIs from the shell
SECRET=$(grep -E '^WEBUI_SECRET_KEY=' .env | cut -d= -f2- | sed 's/#.*//' | tr -d ' "')
AID=$(docker exec open-webui python3 -c "import sqlite3;print(sqlite3.connect('/app/backend/data/webui.db').execute(\"select id from user where role='admin' limit 1\").fetchone()[0])")
JWT=$(docker exec -e S="$SECRET" -e A="$AID" open-webui python3 -c "import os,jwt;print(jwt.encode({'id':os.environ['A']},os.environ['S'],'HS256'))")
curl -s -H "Authorization: Bearer $JWT" http://localhost:3000/api/v1/nodes/            # node registry
```

- **PWA shows stale UI after a deploy** → it's the service-worker cache. Hard-refresh / force-quit the
  installed app. The deployed bundle is authoritative; verify with the `docker exec … grep /app/build` step.
- **Docker disk creep** — the fork rebuilds leave dangling layers. `docker image prune -f` +
  `docker builder prune -f` reclaims most; `docker image prune -a -f` clears unused tagged images
  (running containers are protected).

---

## 8. Conventions

- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Prefer `claude-monitor` MCP tools (agy_run / opencode_run / codex_run at `http://localhost:7777/mcp`)
  for heavy reads/bulk work; always demand concise/JSON output; never dump raw file contents.
- Fork code is marked with `LOCAL PATCH (…)` comments — grep them to find every fork change.
- When you finish a unit of work: build → deploy → **verify in the running container** → commit → push.
