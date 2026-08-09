# owui-fork — the Agent Hub fork of Open WebUI

Forked to add net-new features OWUI can't do natively (terminal, inbox/summary, machine selector) while
staying upgrade-friendly. **Upstream is pinned** and our changes live as ordered patches.

## Status (2026-08-09) — live in production
`build.sh` reproduces `agent-hub/open-webui:v0.11.0-fork` from source and **the live hub runs it**.
**Ship changes through the render gate — `./deploy.sh`, never a bare `docker compose up --force-recreate
open-webui`** (a broken fork build returns HTTP 200 but a blank page; the gate catches it on staging
before prod). See **`DEPLOYING.md`**. Shipped patches (all in `patches/0001-terminal-page.patch`) now
cover E1 terminal, E2 inbox/summaries, #70 streaming re-render, **E4 machine selector + node registry**,
**E10 per-machine chips/filtering**, **Phase-1 agent-runs presence**, and interactive AskUserQuestion:
- **E1 Terminal** — full-page `/terminal`, multi-session sidebar, native XTerminal over the `/api/v1/terminals`
  proxy (backed by the host `owui-terminal` PTY service), font controls, auto-title, **Files panel** (native
  FileNav over the `/files/*` API, scoped to the active session cwd).
- **E2 Inbox + summaries** — `/inbox` page + `/api/inbox` backend (llama3.2:3b one-liners, cached) + a
  live summary subtitle under each sidebar chat.
- **#70 streaming re-render** — sessionStorage stream-stash so leaving a chat mid-stream and returning
  never truncates what you already saw.

## Layout
- `upstream/` — open-webui checked out at the pinned tag (`v0.11.0`).
- `patches/` — `NNNN-*.patch` (git format-patch / diff), applied in order by `build.sh`.
- `PATCHES.md` — the manifest (what each patch does + rebase notes). **Keep in sync.**
- `Dockerfile` — upstream's own (we build with it after applying patches).
- `build.sh` — reset upstream → apply patches → `docker build`. `--check` (verify apply), `--clone` (refetch).

## Build & run (use the render gate)
```bash
# from the repo root, after editing upstream/ and REGENERATING the patch (see "Adding a patch"):
./owui-fork/deploy.sh            # build → render-test on staging (:3001) → promote to prod → verify
./owui-fork/deploy.sh staging    # ...or stop at staging to review, then: ./owui-fork/deploy.sh promote
./owui-fork/deploy.sh rollback   # instant revert to the previous prod image (:prev)
./owui-fork/build.sh             # build the image ONLY (no deploy) — deploy.sh calls this internally
```
`deploy.sh` gates on `smoke-test.sh` (headless-chromium render check) so a white-screen build can never
reach prod. Full guide: **`DEPLOYING.md`**. Optionally also run the adapter matrix:
`python3 ../../aibo-server/research/agent-hub/harness/run_matrix_full.py`.

## Adding a patch (workflow)
1. Edit files under `upstream/` (make the change, test by building).
2. **Regenerate the patch** (build.sh resets `upstream/`, so an uncaptured edit is wiped):
   `git -C upstream add -A && git -C upstream diff --cached > patches/0001-terminal-page.patch && git -C upstream reset -q`
   (a NEW feature can go in its own `NNNN-title.patch`).
3. Add/refresh a row in `PATCHES.md`.
4. Ship it through the gate: `./deploy.sh` (or `staging` → review → `promote`). Prod stays up if it fails.

## Companion services (not part of the image)
- **owui-terminal** (host systemd, `:7681`) — net-new PTY-over-WS backend for the terminal feature.
  Built + tested (shell exec, real-fs, resize, reconnect-replay). The fork's terminal patches add the
  frontend panel + a thin auth proxy to it.

## Upstream bump
Change `TAG` in `build.sh` → `./build.sh --clone` → `./build.sh --check` (rebase conflicting patches)
→ `./deploy.sh staging` (render-gate the new base on `:3001`) → review → `promote`. Drop any patch that
landed upstream.
