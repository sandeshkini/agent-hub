# E10 — Multi-machine organization in the UI

**Goal:** with multiple machines registered, every **chat**, **inbox item**, and **terminal** is clearly
attributed to the machine it ran on and filterable by machine — while keeping the existing **time**
organization. An extension of the current sidebar/inbox, not a redesign.

## Grounding (verified in code, 2026-08-08)
- Sidebar chats render from `$chats` grouped by **time** (`getTimeRange` → Today/Yesterday/Previous 7d…):
  `Sidebar.svelte:1632`, `utils/index.ts:1287`, `stores/chatList.ts`.
- **Chat-list items carry NO model id** — `ChatTitleIdResponse` = id/title/updated_at/snippet/active
  (`models/chats.py:226`). Model only in the full chat load. → needs a small backend add.
- **Machine selector filters only the model dropdown + terminal target**, not chats/inbox
  (`utils/machineSelector.ts`). That's the gap.
- Terminals already carry `serverId` (`agentTerminals.ts:12`) but it's never set or shown.
- Inbox groups by needs/recent, no model id (`inbox/+page.svelte`, `agent_inbox.py:142`).

## Design decisions
1. **Attribution by model.** A chat belongs to the machine whose model it used. Derive machine from the
   chat's `model_id` via the node registry (`node.models` contains the id; unprefixed/local → the hub node).
2. **Small backend add.** Include `model_id` (primary model) in the chat-list + inbox responses so the
   client attributes without loading each chat.
3. **Selector filters the list.** Selecting a machine filters the sidebar chat list + inbox + terminals to
   that machine (extends the current model/terminal scoping). **"All machines" = show everything.**
4. **Machine chip.** Each chat/inbox/terminal row shows a subtle machine chip (dot + short label) **in the
   All-machines view** (hidden when already filtered — no redundant noise).
5. **Time-grouping preserved** within each machine's view. Inbox keeps needs/recent.
6. **One address per node (front-door).** The node exposes ONE address `register.mb` via a tiny front-door
   routing `/claude`→9212, `/opencode`→9213, `/term`→7681 → 1 DNS record, auto-covers future agents.
   (Resolves the "why not one API" confusion.)

## Tasks
| ID | Task | Where | Status |
|----|------|-------|--------|
| E10.1 | `model_id` in chat-list + inbox responses | backend `chats.py`, `agent_inbox.py` | ☐ |
| E10.2 | `machineForModel` resolver + `MachineChip.svelte` | `src/lib/utils`, `src/lib/components` | ☐ |
| E10.3 | Sidebar chat list: machine filter + chip | `Sidebar.svelte`, `ChatItem.svelte` | ☐ |
| E10.4 | Inbox: machine filter + chips | `inbox/+page.svelte` | ☐ |
| E10.5 | Terminals: stamp `serverId` + chip + filter | `agentTerminals.ts`, sidebar/inbox | ☐ |
| E10.6 | Node front-door: single `register.mb` address | `node/`, compose, Pangolin | ☐ |
| E10.7 | aibo wiring: pin heartbeat + OWUI connections + terminal server | aibo `.env`, new pin script | ☐ (blocked: DNS + Mac token) |
| E10.8 | End-to-end verify with 2 machines | live | ☐ |

## Not in scope
- Hermes stays hub-only (single brain); nodes serve Claude + OpenCode.
- No chat-history migration between machines (attribution is derived, not stored per-chat).
