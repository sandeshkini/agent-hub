# OWUI Fork — patches manifest

**Upstream pinned:** `v0.11.0` (matches the image we ran before forking). Build: `./build.sh`
(reset `upstream/` → apply `patches/*.patch` in order → `docker build` → `agent-hub/open-webui:v0.11.0-fork`).
`./build.sh --check` verifies patches apply cleanly; `./build.sh --clone` re-fetches upstream.
**To SHIP a patch to prod, use the render gate — `./deploy.sh`** (build → staging render-test → promote →
`:prev` rollback), never a bare `docker compose up --force-recreate open-webui`. See `DEPLOYING.md`.

## Discipline
- Patches are **surgical + additive**: new route, new component, new backend router, one nav entry.
  Avoid editing churny shared files; when unavoidable, minimal lines + a `LOCAL PATCH` marker.
- One feature = separate backend / frontend patches (clean rebase).
- Feature-flag via env where possible so a patch can be disabled without a rebuild.

## Upgrade procedure
1. Bump `TAG` in `build.sh`; `./build.sh --clone`.
2. `./build.sh --check` → fix any patch that no longer applies (git apply --3way conflicts).
3. `./build.sh` → run `../../aibo-server/research/agent-hub/harness/run_matrix_full.py` → expect 28/28.
4. Cut compose over to the new tag. Delete any patch that landed upstream.

## Patches
Currently **one cumulative patch** — `patches/0001-terminal-page.patch` (~E1+E2 combined; split later if it
gets unwieldy). Files it touches (all additive except small, marker-commented insertions):

| Area | Files | What |
|---|---|---|
| Terminal | `routes/(app)/terminal/+page.svelte` (new), `lib/utils/agentTerminals.ts` (new), `lib/components/chat/XTerminal.svelte` | Full-page terminal reusing OWUI's XTerminal via the native `/api/v1/terminals` proxy; multi-session store; XTerminal `fontSize` prop + `onTitleChange` dispatch; **Files panel toggle** (reuses native `FileNav` against the `aibo` server's `/files/*` API, scoped to the active session cwd — side-drawer desktop / overlay mobile) |
| Sidebar | `lib/components/layout/Sidebar.svelte` | **Inbox** + **Terminal** pinned menu items (getMenuItemMeta/DEFAULT_PINNED_ITEMS/isMenuItemVisible/icons) + a **Terminals** SidebarSection (list/new/close, green dots) |
| Inbox | `routes/(app)/inbox/+page.svelte` (new), `backend/routers/agent_inbox.py` (new), `backend/main.py` (import+include), `lib/utils/agentInbox.ts` (new) | `/inbox` page (polls `/api/inbox`) + backend summary endpoint (`llama3.2:3b`, cached) + shared summaries store |
| Sidebar summaries | `lib/components/layout/Sidebar/ChatItem.svelte` | one-line summary subtitle under each chat title (from `agentInbox.ts`) |
| Chat streaming (#70) | `lib/components/chat/Chat.svelte` | sessionStorage **stream-stash**: `stashStreamingContent` on each socket content event + `restoreStreamingContent` in `loadChat` — leaving a chat mid-stream and returning no longer truncates what you already saw |
| Chat terminal_id | `lib/components/chat/Chat.svelte` | **never attach `terminal_id` to completions** (our fork uses a dedicated `/terminal` page, not OWUI terminal-tools-in-chat). Prevents a poisoned `selectedTerminalId` from 503-ing every message via the backend terminal probe |
| Nodes / machine selector (E4) | `backend/routers/agent_nodes.py` (new), `backend/main.py` (import+include), `lib/utils/machineSelector.ts` (new), `lib/components/layout/Sidebar/MachineSelector.svelte` (new), `lib/components/layout/Sidebar.svelte` (mount), `lib/components/chat/ModelSelector/Selector.svelte` (filter), `routes/(app)/terminal/+page.svelte` (respect selection) | **Multi-machine node registry** (dispatch hub ported to Python): `POST /api/nodes/register` (30s heartbeat, `NODE_HUB_TOKEN`), `GET /api/nodes/` (online/offline, 90s/1h TTL), `id=sha256(url)[:8]`. **Machine selector** in the sidebar scopes the model dropdown (to the node's `models`) AND the `/terminal` target (to the node's terminal id). Companion: `agent-hub/node/` bundle (registrar + register-node.sh) — not in the image |
| Cleanup | `lib/components/chat/MessageInput.svelte` | removed the chat terminal-server (cloud) menu |

Every insertion carries a `LOCAL PATCH` marker for greppability. To re-derive after edits:
`git -C upstream add -A && git -C upstream diff --cached > patches/0001-terminal-page.patch && git -C upstream reset`.

## Companion (not in the image)
- **`owui-terminal`** — host systemd `:7681`, net-new PTY+WS service implementing OWUI's "open-terminal"
  spec (`POST /api/terminals`, `WS /api/terminals/{id}`) **+ file-API** (`/api/config`, `/files/*`) for the
  native workspace. Registered as a terminal server via `TERMINAL_SERVER_CONNECTIONS` env (durable).
  Source: `~/Documents/apps/agent-hub/owui-terminal/`.
- Keep this table in sync with `patches/`.
