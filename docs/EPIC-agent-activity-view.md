# EPIC — Agent Activity View (orchestrator → subagent tree)

Status: **✅ COMPLETE (v1 + v2, 2026-08-09).** The live orchestrator→subagent tree is up, and each row
expands on demand to that subagent's full tool timeline. Depends on: Phase-1 presence (`agent-runs`) +
the CC-like view (Tier 1) — both shipped.

### Shipped (T1–T6)
- **T1 ✅** `agent_activity.py` registry (adapter-auth POST, verified-user GET, TTL prune, fail-closed);
  registered at `/api/v1/agent-activity`.
- **T2 ✅** adapter (`owui-claude`): `postActivity` on task_started/_progress/_notification → type, status,
  description, `tool_count` (SDK `usage.tool_uses`), summary, session_id. **Transcript endpoint**
  `GET /subagent/<sessionId>/<agentId>` parses the subagent `.jsonl` into a compact tool timeline
  (path-safe: hex/uuid ids only, confined to `CLAUDE_CONFIG_DIR/projects/*/*/subagents/`). Note:
  `agent_id == task_id` (the file is `agent-<task_id>.jsonl`).
- **T3 ✅** `agentActivity.ts` store — `loadActivity`+poll (2.5s while running) and `loadTranscript` (lazy,
  cached) via the fork proxy `GET /agent-activity/transcript`.
- **T4 ✅** `AgentActivity.svelte` — "🧩 N subagents" collapsible tree (status dot, type, description, tool
  count, summary); **each row expands** to its lazy-loaded tool timeline (▸ tool · result · text). Mounted
  above the input in `Chat.svelte`; renders nothing when a turn used no subagents.
- **T5 ✅** live running dots (poll while running → done/failed), empty state, off-by-default.
- **T6 ✅** verified end-to-end: 2-subagent turn populated the tree (type/status/tool_count/summary);
  browser→fork-proxy→adapter transcript returns the timeline (Bash tool + result); gated fork deploy —
  staging render-checked → prod renders. Docs (this file + `owui-claude/README`) updated.

### Not in scope (future ideas, if wanted)
- Multi-machine: activity is per-hub-chat; remote-node subagents aren't surfaced (v1 assumption).
- Nested subagents (depth > 1) render flat, not as a deeper tree.
- Hermes/OpenCode subagents: the tree is Claude-only (only owui-claude posts activity).

## Why

Tier 1 (shipped) made the chat read like Claude Code: subagent internals are hidden inline
(`parent_tool_use_id` filter in `owui-claude/server.mjs`), and each subagent shows as one compact
`↳ subagent started` / `✓ subagent completed` card. That's the right **default**.

Tier 2 adds an **optional, on-demand way to inspect the agent tree** — see the orchestrator and each
subagent as an expandable node with its own live tool timeline — *without* cluttering the chat. Think of
it as Claude Code's task view: collapsed by default, expand a subagent to watch/replay its work.

```
Orchestrator (this turn)
├─ ● subagent: Explore "map run-state flow"        3 tools · 12s · done
│    └─ (expand) Grep agent_runs · Read server.mjs · Read agent_runs.py
├─ ● subagent: general-purpose "audit ntfy"        6 tools · 40s · running
└─ ✓ result synthesized
```

## Architecture

Three pieces, reusing the presence-layer pattern already in place:

1. **Adapter emits structured subagent lifecycle** (`owui-claude/server.mjs`). The SDK already gives us
   `task_started` / `task_progress` / `task_notification` (with `task_id`, `subagent_type`, `status`,
   `summary`, `usage`) and every message carries `parent_tool_use_id`. The adapter POSTs these as
   per-subagent records to a new activity registry, keyed by `chat_id` + `task_id`.
2. **Fork backend activity registry** (`owui-fork/upstream/backend/open_webui/routers/agent_activity.py`,
   new). In-memory, same shape/discipline as `agent_runs.py` (adapter-auth POST, verified-user GET,
   `_prune` with STALE/DONE TTL, fail-closed). Stores the subagent tree per chat. Plus a proxied
   **transcript** endpoint for on-demand expansion (adapter serves parsed subagent messages via the SDK's
   `getSubagentMessages(sessionId, agentId)` — the `.jsonl` under `~/.claude-owui/projects/**/subagents/`).
3. **Fork frontend Activity view** (`src/lib/utils/agentActivity.ts` store +
   `src/lib/components/chat/AgentActivity.svelte`). A collapsible tree rendered in the chat (a toggle on
   the message, off by default) or a side drawer. Live-updates by polling the activity endpoint while the
   run is active (reuse the `agentRuns` polling cadence). Expanding a subagent lazy-loads its tool timeline.

Data flow: `SDK task_* events → adapter → POST /api/v1/agent-activity → registry → GET (poll) → store → tree UI`;
`expand → GET /api/v1/agent-activity/<chat>/<task>/transcript → adapter getSubagentMessages → timeline`.

## Tasks

### T1 — Backend: activity registry (`agent_activity.py`)
- New router mirroring `agent_runs.py`: `POST /` (adapter-auth via `_adapter_ok`, fail-closed) accepts
  `{chat_id, task_id, subagent_type, status: running|done|failed, summary, tool_count, parent_tool_use_id,
  started_at}`; `GET /?chat_id=` (verified user) returns the tree for that chat. `_prune` with the same
  TTLs. Register in `main.py` at `/api/v1/agent-activity`.
- **Test:** `curl -H "Bearer $ADAPTER_KEY" POST` a few records → `GET` (admin JWT) returns them grouped by
  chat; unknown/empty key → 401; a record with no heartbeat older than STALE_TTL is pruned.

### T2 — Adapter: post lifecycle + serve transcripts (`server.mjs`)
- In the `task_started`/`task_progress`/`task_notification` branches, `postActivity(chatId, {...})` (a
  fire-and-forget POST like `postRun`, per-chat ordered). Track `tool_count` per `task_id` by counting
  suppressed `parent_tool_use_id` tool_results.
- Add `GET /subagent/:sessionId/:agentId` on the adapter that returns
  `getSubagentMessages(sessionId, agentId)` parsed to `[{role, text, tool, result}]` (reuse the tool-card
  shaping; apply `TOOLCARD_MAX`). Fork proxies to it.
- **Test:** run a 2-subagent fan-out; assert two activity records posted with correct `subagent_type` +
  final `status=done` + `tool_count>0`; hit the transcript endpoint for one `agent_id` → non-empty parsed
  messages matching the `.jsonl`.

### T3 — Frontend store (`agentActivity.ts`)
- `loadActivity(chatId)` + a Svelte store; poll every ~2s while any node is `running` (stop when all done,
  like `agentRuns`). `expandSubagent(chatId, taskId)` fetches the transcript once and caches it.
- **Test:** unit-mock the endpoint; store transitions running→done; expand caches (one fetch).

### T4 — Frontend UI (`AgentActivity.svelte`)
- Collapsible tree: orchestrator root → subagent rows (status dot, `subagent_type`, description, tool
  count, duration). Expand a row → lazy-load its tool timeline (compact cards, reuse existing tool-card
  styling). Off by default; a small "🧩 N subagents" affordance on the assistant message toggles it.
- Mount in `Chat.svelte` under the message (or a right drawer on desktop / overlay on mobile).
- **Test (render-gated):** `./owui-fork/deploy.sh staging` → `smoke-test.sh` passes; on staging, a
  subagent turn shows the tree; expand loads a timeline; the MAIN chat stays clean (Tier 1 intact).

### T5 — Live + polish
- Running dot animates; done shows summary; failed shows the error. Empty/no-subagent turns render nothing
  (no affordance). Mobile: overlay, not a cramped drawer.
- **Test:** long subagent → dot stays "running", flips to "done" with summary within one poll; a turn with
  zero subagents shows no activity affordance.

### T6 — Docs + ship
- Update `owui-claude/README.md` parity matrix (ExitPlanMode/Activity rows), `CLAUDE.md §6` file map, this
  EPIC → done. Ship the fork via `./owui-fork/deploy.sh` (gated); adapter via restart.
- **Test:** full E2E below.

## End-to-end acceptance (Definition of Done)

Run a 3-subagent fan-out from OWUI ("inventory X, Y, Z in parallel and synthesize"):
1. **Main chat stays clean** — orchestrator narrative + 3 compact `✓ subagent completed` cards, no internal
   tool dumps (Tier 1). ✅ already true.
2. **Activity affordance** appears on the message ("🧩 3 subagents"); toggling shows the tree with 3 nodes,
   each with `subagent_type`, tool count, duration, status.
3. **Expand** a subagent → its tool timeline loads on demand (Grep/Read/Bash cards), lazily, once.
4. **Live**: while running, nodes show a running dot that flips to done with a summary within a poll cycle.
5. **Render gate green** (`smoke-test.sh`), prod renders, presence spinners still correct.
6. **Fail-closed**: activity POST rejects an empty/invalid `ADAPTER_KEY`.

## Notes / risks
- Keep it **optional + lazy** — never auto-expand (that would reintroduce the noise/scale problem the
  40-card cap + Tier 1 solved).
- Transcript fetch is on-demand only; cap size with `TOOLCARD_MAX`.
- Multi-machine: activity is per-hub-chat; remote-node subagents are out of scope for v1 (note it).
- Ship every fork change through `./owui-fork/deploy.sh` (render gate) — a tree component is exactly the
  kind of Svelte addition that can white-screen if it throws at mount.
