# owui-fork — the Agent Hub fork of Open WebUI

Forked to add net-new features OWUI can't do natively (terminal, inbox/summary, machine selector) while
staying upgrade-friendly. **Upstream is pinned** and our changes live as ordered patches.

## Status (2026-08-07) — live in production
`build.sh` reproduces `agent-hub/open-webui:v0.11.0-fork` from source and **the live hub runs it**
(compose override; rollback = plain upstream `up`). Shipped patches (all in `patches/0001-terminal-page.patch`):
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

## Build & run
```bash
cd owui-fork && ./build.sh                       # -> agent-hub/open-webui:v0.11.0-fork
cd .. && docker compose -f docker-compose.yml -f docker-compose.fork.yml up -d open-webui
# rollback to stock:  docker compose up -d open-webui
```
Always run the matrix after a build: `python3 ../../aibo-server/research/agent-hub/harness/run_matrix_full.py` (expect 28/28).

## Adding a patch (workflow)
1. Edit files under `upstream/` (make the change, test by building).
2. `git -C upstream diff > ../owui-fork/patches/NNNN-title.patch` (or `git format-patch`), then
   `git -C upstream checkout -- .` so `upstream/` stays clean (patches are the source of truth).
3. Add a row to `PATCHES.md`. Re-run `./build.sh` from clean to confirm it applies + builds.

## Companion services (not part of the image)
- **owui-terminal** (host systemd, `:7681`) — net-new PTY-over-WS backend for the terminal feature.
  Built + tested (shell exec, real-fs, resize, reconnect-replay). The fork's terminal patches add the
  frontend panel + a thin auth proxy to it.

## Upstream bump
Change `TAG` in `build.sh` → `./build.sh --clone` → `./build.sh --check` (rebase conflicting patches)
→ build → matrix. Drop any patch that landed upstream.
