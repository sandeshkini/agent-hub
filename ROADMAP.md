# Agent Hub — Roadmap & Plan

_A forked Open WebUI that fronts multiple agents (Claude, Hermes, OpenCode) as selectable
models, plus an in-app Terminal, multi-machine nodes, an Inbox with AI summaries, and
background ("fire-and-forget") runs. One repo stands the whole thing up as a hub or a node._

Last updated: 2026-08-08

---

## 1. Where the hub is today (shipped & live)

- **Multi-agent models** — Claude, Hermes, OpenCode selectable as models in OWUI; each runs
  through its own adapter with streaming, tool cards, and a destructive-command guardrail.
- **In-app Terminal** — real shell over WebSocket, per-machine, with a files panel.
- **Multi-machine** — aibo (hub) + MacBook (node) via a Pangolin front-door; a machine
  selector scopes the model list + terminal to a chosen machine.
- **Inbox + summaries** — each chat gets a short "what the agent is doing/said" summary.
- **Background runs** — closing a tab doesn't cancel; the run keeps going and mirrors its
  output back into the chat; survives an adapter restart (bounded, crash-safe).
- **Shared MCP layer** — one place to add tools (publish_artifact, notify, external MCP) that
  every agent picks up.
- **Slack gateway** — drive agents from Slack threads (streaming + tool cards). Partial.

---

## 2. Recently done (this work session)

- **Artifacts + ntfy are now in-house (2026-08-10)** — the artifacts board and the ntfy push bus moved
  from separate compose projects INTO the hub (`agent-hub/artifacts/`, `agent-hub/ntfy/`), so
  `docker compose up` stands up the whole stack. Container names + Traefik labels preserved → public URLs
  (`apps.kingdomofluna.com/artifacts`, `ntfy.kingdomofluna.com`) unchanged, no Pangolin/Cloudflare change.
  Data dirs gitignored (public repo). Old projects marked `DECOMMISSIONED.md` + their compose files
  renamed `.decommissioned` so they can't be `up`'d by accident. Verified end-to-end: publish (401
  unauth / 200 auth → file + ntfy push), board serves all pages, agent→MCP path (`publish_artifact` +
  `notify`) works. **Still MCP-tool driven** (`mcp-tools`); the in-OWUI publish button is future work.
- **Cutover-ready push (2026-08-09)** — verified green (14/14 infra + 9/9 functional):
  - **CC-like view (Tier 1)** — subagent internals hidden inline (only the orchestrator shows), each
    subagent = one compact task card; + a 40-card/turn cap so a huge exploration can't build an
    unrenderable message.
  - **Agent Activity View (Tier 2, EPIC complete)** — optional on-demand orchestrator→subagent tree; each
    row expands to that subagent's tool timeline. `docs/EPIC-agent-activity-view.md`.
  - **ExitPlanMode** — `/plan` → SDK plan mode → interactive Approve / Keep-planning card.
  - **Graceful SIGTERM drain on ALL 3 adapters** — a restart mid-turn now flushes a clean close (no more
    broken/un-typeable chats). Presence heartbeat + `STALE_TTL=90` fix (no false "running" spinners).
  - **SDK-parity review + build doc** (`owui-claude/README.md`): parity matrix + "check a new Claude Code
    version" procedure. Adapter loads the same skills + CLAUDE.md as the terminal CLI.
- **Background subagents now return (2026-08-09)** — the Claude adapter ran one query() per message and
  quit at the first `result`, which tore down parallel/`run_in_background` subagents mid-flight (their
  results never came back). Switched to **streaming-input mode** + a drain loop: track
  `background_tasks_changed`, and only finalize once a `result` arrives with no live background tasks and
  the model idles. Verified end-to-end (6-way and 2-way fan-outs synthesize correctly; plain prompts
  unchanged). This unblocks Phase-2 orchestration.
- **Safe-deploy pipeline (2026-08-09)** — the OWUI fork now ships through a render gate:
  `owui-fork/smoke-test.sh` (headless-chromium check) + a staging container (`:3001`, own volume) +
  `owui-fork/deploy.sh` (`deploy|staging|promote|rollback`). A white-screen build is caught on staging
  before prod, with an instant `:prev` rollback. Verified by intentionally breaking a build. Docs:
  `owui-fork/DEPLOYING.md`. **Ship the fork via `deploy.sh`, never a bare `up --force-recreate open-webui`.**
- **Skills parity (2026-08-09)** — the in-app Claude loads the SAME skills + `CLAUDE.md` as the terminal
  `claude` (`settingSources:['user'] + skills:'all'`; `~/.claude/skills` shared via `~/.claude-owui/skills`).
  Drop a `SKILL.md` in `~/.claude/skills/` → both get it. First repo skill: `.claude/skills/deploy-fork`.
- **Host cutover** — Claude + OpenCode adapters now run as host systemd services (not Docker) for full
  system access (`systemctl`, all drives); OWUI reaches them via `host.docker.internal`.
- **500-on-open fix** — an offline MacBook was hanging the hub's model load. Now fails fast.
- **Bug-hunt pass** (4 audit agents) — fixed: fire-and-forget crash-loop safety, a Claude
  cross-conversation session leak, OpenCode dead-session self-heal, node-registration
  fail-closed, terminal zombie-process leak, and more.
- **Mobile/touch polish (Phase 0)** — terminal keyboard summon + no more double-fired keys +
  scrollable key bar + connection status; sidebar close/rename reachable on touch.
- **Sidebar hierarchy pass** — see §4.
- **Docker cleanup** — reclaimed ~16.7 GB of old build cache + unused images.

> **To see the sidebar/mobile changes: hard-refresh the PWA** (they're deployed; your phone/
> browser is likely showing a cached bundle). On iOS: close the tab fully and reopen, or
> pull-to-refresh; if installed as an app, force-quit and relaunch.

---

## 3. The Roadmap (phases)

### Phase 0 — Mobile & touch polish ✅ DONE
Terminal keyboard/keys/scroll/connection-state; touch-reachable sidebar actions.

### Phase 1 — Agent presence / activity layer  ← NEXT (the keystone)
Make it visible, hub-wide, what every agent is doing. This is both the #1 UX fix and the
foundation the orchestration feature stands on.
- [x] **Offline-machine honesty** — scoping to an offline machine now says so (amber banner).
- [x] **Run registry** — server-side store `/api/v1/agent-runs`; the 3 adapters heartbeat
      `running / needs-input / done / failed` per chat (including detached background runs).
- [x] **"Running" dot** on sidebar chat rows + inbox, driven by the registry (`agentRuns` store).
- [x] **Persist a pending question** — an `AskUserQuestion` survives page reload and shows a
      "needs input" badge from anywhere (parked in the registry, re-hydrated on load).
- [x] **Plan approval / interactive prompts** — `AskUserQuestion` + `ExitPlanMode` render as native
      approve/answer cards (E7).
- [ ] **"Keeps running in background" toast** when you close a tab mid-run.

### Phase 2 — Multi-agent orchestration  (the headline feature)
A coordinator agent decomposes a task and delegates to Claude/Hermes/OpenCode (across
machines), with every sub-run visible/answerable via the Phase-1 presence layer.
_Example: "ship feature X" → OpenCode writes it on the MacBook, Claude reviews + tests,
Hermes updates docs, merged into one chat with a PR link._

- **Agent-view Tier 1 ✅ DONE (2026-08-09)** — the chat reads like Claude Code: subagent internals are
  hidden inline (only the orchestrator shows), each subagent = one compact task card. Plus a 40-card
  per-turn cap so a huge exploration turn can't build an unrenderable message.
- **Agent-view Tier 2 (planned EPIC)** — an optional, on-demand **agent activity view**: a collapsible
  orchestrator→subagent tree with a live tool timeline you can expand per subagent. Full spec + tasks +
  tests: **`docs/EPIC-agent-activity-view.md`**.

### Phase 3 — Shared memory + knowledge (RAG)
Persistent cross-agent memory + retrieval over your docs/repos, so agents know your world
(portfolio, dispatch, homelab) without re-explaining. Makes the orchestrator much smarter.

### Side quests (can slot in anytime — they also consume the Phase-1 presence layer)
- **B. Autonomous scheduled agents** — cron agents that monitor/research/digest and push to
  ntfy/Slack (daily portfolio brief, repo/CI watcher). Quickest visible daily value.
- **C. Slack as a full frontend** — finish interactive buttons so you run everything from Slack.

---

## 4. Sidebar layout — what changed, and what I propose next

### What just shipped (hard-refresh to see it)
The old sidebar had **everything at one visual weight** — nav rows, the machine selector,
section headers, and time dividers all looked the same. Now there are three tiers:

```
  New Chat / Search / Inbox / Notes / Workspace / Terminal   ← primary nav (icon + white label)
  ┌───────────────────────────────────────────────┐
  │ 🖥  MACHINE   All machines            ● ⌄       │        ← scope pill (bordered, distinct)
  └───────────────────────────────────────────────┘
  CHANNELS                                          +        ← SECTION headers (UPPERCASE caps)
  FOLDERS                                           +
  TERMINALS                                         +
    ● ~                                    ·MacBook
  CHATS                                            ···
    today                                                    ← time divider (small, light, subordinate)
    ▸ what do you see on my computer         ·MacBook  1m
```

### Where I think it's STILL confusing (proposals — tell me which to do)

1. **Empty sections are noise.** Channels and Folders show a header + `+` even when you have
   none. Proposal: **collapse/hide a section's header when it's empty** (or show it only under
   a single "＋ New" affordance), so the sidebar isn't padded with empty labels.

2. **Machine scope vs. per-chat machine chips feel redundant.** Every chat row shows a machine
   chip (·MacBook / ·aibo) even when you're already viewing "All machines". Proposal: show the
   chip **only when it differs from the current scope** (or make it much quieter — a colored
   dot, not a pill).

3. **"Chats" doesn't need to look like Channels/Folders/Terminals.** Chats is the main content,
   not a peer utility section. Proposal: give the chat list a slightly stronger header (or drop
   its section chrome entirely) so it reads as "the list" rather than one of four equal sections.

4. **Terminals section could merge its state.** The lone `~` terminal row with a green dot +
   machine chip is doing a lot. Proposal: simplify to `● ~ (MacBook)` with the dot meaning
   "connected".

5. **Order.** Current top-to-bottom is nav → machine → channels → folders → terminals → chats.
   Proposal: consider **machine pill directly under the title** (it's global scope, belongs up
   top), and keep the content sections (terminals, chats) grouped lower.

> Pick any subset of 1–5 and I'll implement them in one pass. My default recommendation:
> **#1 (hide empty sections) + #2 (quieter chips) + #3 (chats reads as the list)** — those three
> remove the most visual noise for the least risk.

---

## 5. Open questions for you

- **Sidebar #1–5** — which layout proposals do you want?
- **Next big feature** — orchestration (A, the coordinator agent — the activity view is a building
  block for it), scheduled agents (B), Slack polish (C), or memory/RAG (D)?

---

## 6. Deferred / next up (as of 2026-08-10 — cutover verified green)

**Cutover is safe now** (all adapters + fork verified). These are the known-open items, none blocking:

- ~~**★ Notifications EPIC**~~ — **✅ DONE (2026-08-10):** phone push (ntfy) when an agent **finishes**
  (priority default) or **needs input** (priority urgent). Away-gated via OWUI's own presence
  (`Users.is_user_active`, 3-min window) — quiet while you're actively in the UI. Proved the make-or-break:
  the done signal (`publish_chat_finished_event` in `utils/middleware.py`) is **not** model-gated, so it
  fires for all external adapters; needs-input rides the `agent_runs` presence registry. Self-contained,
  env-driven (`AGENT_NTFY_*`), same ntfy bus/topic as `mcp-tools notify`. Full writeup:
  `docs/EPIC-agent-notifications.md`. (Task #133.)
- **Artifacts follow-ups** (the migration shipped the board+bus; these make it native):
  - ~~**OWUI publish button** (#134)~~ — **✅ DONE (2026-08-10):** Share button on every assistant reply →
    `POST /api/v1/artifacts/publish` (fork proxies to the board with the server-side token) → shareable
    page + phone push + opens. `docs/EPIC-artifacts-publish.md`.
  - ~~**Node-list API** (#135)~~ — **✅ DONE (2026-08-10):** `GET /api/v1/artifacts/list` (board as JSON,
    absolute URLs). Board is hub-only (all machines publish to it) → this IS the cross-machine aggregation.
  - **Remove the `publish_artifact`/`notify` MCP tools** (#136) — **PENDING A SCOPE DECISION.** Button +
    auto-notifications replace MANUAL use, but the MCP tools are the only path for **autonomous agent**
    publish/notify (scheduled Automation personas, e.g. a daily portfolio brief). Options: keep both
    (recommended — nothing lost) · retire `publish_artifact` only (keep `notify` for custom alerts) ·
    retire both fully (cleanest, but Automations lose auto-publish/alert). Reversible either way.

- ~~Staging subdomain (`L2b`, #120)~~ — **✅ DONE (2026-08-09):** `staging.operator.kingdomofluna.com` is
  live (Pangolin resource 29 → aibo:3001, cloned from `operator`; wildcard DNS). `owui-fork/DEPLOYING.md`.
- **MacBook node is stale** (`E5.2`, task #76) — the `mb.*` agents run OLD adapter code; needs a
  `git pull` + rebuild on the MacBook to get this session's fixes (drains, CC-view, activity, etc.).
- **Agent Activity View — future ideas** (EPIC done): multi-machine subagents aren't surfaced; nested
  subagents render flat; only **Claude** posts activity (Hermes/OpenCode don't emit subagent lifecycle).
- **Behavioral cutover notes** (not bugs): (1) restarting an adapter mid-turn now degrades gracefully
  (clean "resend" message) but still interrupts that turn — restart when idle; (2) never set durable
  config in the OWUI Admin UI (`RESET_CONFIG_ON_START=true` wipes it — use `.env`); (3) ship fork changes
  ONLY via `./owui-fork/deploy.sh`.
- **Bigger tracks still open**: E8 portable install / multiple Hermes brains / master install.sh / Mac
  compat (#89–94); Slack `AskUserQuestion` buttons (#100); Phase 2 orchestration; Phase 3 memory/RAG.
- **TodoWrite** renders as a generic tool card (no dedicated checklist UI) — minor polish if wanted.
