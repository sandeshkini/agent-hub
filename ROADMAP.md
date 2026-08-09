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
- [ ] **Run registry** — a small server-side store; the 3 adapters heartbeat
      `running / needs-input / done / failed` per chat (including detached background runs).
- [ ] **"Running" dot** on sidebar chat rows + inbox, driven by the registry.
- [ ] **Persist a pending question** — an `AskUserQuestion` survives page reload and shows a
      "needs input" badge from anywhere (today it's lost on reload → the run stalls forever).
- [ ] **Plan approval** — an `ExitPlanMode` approve/reject card (today plan runs look hung).
- [ ] **"Keeps running in background" toast** when you close a tab mid-run.

### Phase 2 — Multi-agent orchestration  (the headline feature)
A coordinator agent decomposes a task and delegates to Claude/Hermes/OpenCode (across
machines), with every sub-run visible/answerable via the Phase-1 presence layer.
_Example: "ship feature X" → OpenCode writes it on the MacBook, Claude reviews + tests,
Hermes updates docs, merged into one chat with a PR link._

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

- **Phase 1 presence layer** — start it now? (It's several build/deploy cycles.)
- **Sidebar #1–5** — which layout proposals do you want?
- **Next big feature after Phase 1** — orchestration (A), scheduled agents (B), Slack (C), or
  memory/RAG (D)?
