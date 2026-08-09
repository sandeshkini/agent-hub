# AGENTS.md — how AI agents should work in this repo

> **Canonical guide: [`CLAUDE.md`](./CLAUDE.md). Read it first — it has the full architecture, the
> build/deploy workflow, the file map, and the debug commands.** This file is the quick, self-sufficient
> version so you don't break anything before you get there.

This is the **Agent Hub**: a forked Open WebUI that fronts multiple agents (Hermes · Claude · OpenCode)
as selectable models, plus an in-app terminal, multi-machine nodes, an inbox, and background runs.
It runs from `~/Documents/apps/agent-hub` on **aibo** (Linux). User setup → `README.md`; roadmap →
`ROADMAP.md`.

## The 10 rules that keep you out of trouble

1. **Fork changes need a patch regen BEFORE building.** `owui-fork/upstream/` is a gitignored, pinned
   OWUI checkout; the ONLY committed source of fork changes is `owui-fork/patches/0001-terminal-page.patch`.
   `build.sh` does `git reset --hard` on `upstream/`, so any edit not captured in the patch is wiped:
   ```bash
   UP=owui-fork/upstream
   git -C "$UP" add -A && git -C "$UP" diff --cached > owui-fork/patches/0001-terminal-page.patch && git -C "$UP" reset -q
   ./owui-fork/deploy.sh                                  # gated: build → staging render-test → promote
   ```
1b. **Ship the fork ONLY via `./owui-fork/deploy.sh` — NEVER a bare `docker compose up -d --force-recreate
   open-webui`.** A broken build returns HTTP 200 but a BLANK page; `deploy.sh` render-tests it in headless
   chromium on a staging container (`:3001`) before prod and keeps a `:prev` rollback (`deploy.sh rollback`).
   Full guide: `owui-fork/DEPLOYING.md`. (`build.sh` alone only builds — it does not gate or deploy.)
2. **Never run two fork builds at once** and **don't double-background** (`run_in_background:true` +
   `nohup &`). Concurrent builds race on the shared `upstream/` git tree and corrupt the image. One
   build, foreground, wait for `== built`.
3. **Always verify the deploy landed in the running container** (docker's COPY cache has shipped stale
   images): `docker exec open-webui sh -c "grep -rl '<your string>' /app/build | head"` for frontend,
   or `grep -c '<marker>' /app/backend/…` for backend. `build.sh` has a stale-image self-heal guard —
   keep it.
4. **Adapters = container rebuild** (`docker compose build owui-claude && … up -d --force-recreate owui-claude`).
   **Host services = restart** (`owui-terminal.service`, `agent-hub-pin.service` run repo files directly).
5. **`RESET_CONFIG_ON_START=true`** — OWUI re-seeds config from env each start; durable config goes in
   compose env / `.env`, never via the API.
6. **`.env` is not bash-sourced** — it has JSON + `$`-hashes. Use the `getenv`/`getenv_raw` helpers;
   `getenv_raw` for JSON (never strip `#`). Never commit `.env` (secrets, gitignored).
7. **Security is fail-closed and non-negotiable:** never use `ANTHROPIC_API_KEY` (Claude uses
   `CLAUDE_CODE_OAUTH_TOKEN`); adapters + terminal server + node `/register` deny on empty key; terminal
   file API is jailed to `$HOME`. Don't loosen these.
8. **Don't touch `claude-monitor`** (separate project, `~/Documents/apps/claude-monitor`, its own systemd).
   **Never run firefox on display `:1`** (breaks Hermes computer-use).
9. **Two OWUI instances exist** — `operator.kingdomofluna.com` (aibo hub) and `macbook.kingdomofluna.com`
   (MacBook's own). Separate DBs/chats/terminal-lists. Cross-machine terminal *discovery* bridges live
   shells; chat lists don't federate.
10. **Finish every unit of work:** build → deploy → verify-in-container → `git commit` (end with the
    `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer) → `git pull --rebase && git push`.

## Fastest orientation
- Whole stack + profiles: `docker-compose.yml` (`COMPOSE_PROFILES=hub|node`).
- Fork UI: `owui-fork/upstream/src/lib/components/layout/Sidebar*` + `src/routes/(app)/terminal/`.
- Fork backend: `owui-fork/upstream/backend/open_webui/routers/agent_*.py`.
- Adapters: `owui-claude/server.mjs`, `owui-hermes/hermes_adapter.py`, `owui-opencode/opencode_adapter.py`.
- Grep `LOCAL PATCH (` to find every fork modification.

For anything not covered here → **`CLAUDE.md`**.
