# owui-claude — the Claude adapter (Agent SDK → OpenAI-compatible)

Bridges **Claude** into Open WebUI as a selectable model. It is the **same engine as the desktop `claude`
CLI** — `@anthropic-ai/claude-agent-sdk`'s `query()` — run headless behind an OpenAI-compatible HTTP
endpoint (`/v1/chat/completions`, `/v1/models`). Runs as a **host systemd service** (`owui-claude.service`,
`:9212`) for full system access. Auth is `CLAUDE_CODE_OAUTH_TOKEN` (never `ANTHROPIC_API_KEY`).

> **This is a translation layer, not a re-implementation.** The SDK gives us all the agent *capabilities*
> (tools, subagents, skills, MCP, permissions, thinking). What this file hand-rolls is only the
> OpenAI↔OWUI *translation* + presence/fire-and-forget. See the parity matrix below.

- Installed SDK: **`@anthropic-ai/claude-agent-sdk` 0.3.223** (bundles Claude Code 2.1.223). Check with
  `node -e "console.log(require('./node_modules/@anthropic-ai/claude-agent-sdk/package.json').version)"`.
- The full API surface we build against lives in `node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts`.

---

## Same brain as your terminal Claude

The adapter loads the **same filesystem config** as the CLI so the in-app Claude has your skills + memory:

- `CLAUDE_CONFIG_DIR=~/.claude-owui` (isolated from your interactive `~/.claude.json`), with `CLAUDE.md`
  and `skills/` **symlinked** to `~/.claude/{CLAUDE.md,skills}` (`host/install.sh`). Drop a `SKILL.md` in
  `~/.claude/skills/` → both the CLI and this adapter get it.
- `settingSources: ['user']` + `skills: 'all'` — `'user'` honors `CLAUDE_CONFIG_DIR`.
- ⚠️ It deliberately does NOT load `'project'`/`'local'` or symlink `~/.claude/settings.json` — that file's
  `defaultMode:"bypassPermissions"` would skip `canUseTool` and break AskUserQuestion + the guardrail.
  `permissionMode` is pinned to `'default'`. (Full rationale: `../CLAUDE.md` §5b.)

---

## How a turn flows (`streamTurn()`)

1. OWUI POSTs the full messages array. The adapter takes the **last user message** and **resumes** the
   per-chat SDK session (in-memory cache keyed by chat id). No cached session → it **replays a text
   transcript** of prior turns so context survives an adapter restart.
2. It drives `query()` in **streaming-input mode** — an async-iterable prompt kept OPEN past the first
   `result`, so **background subagents finish and their results flow back** (the SDK auto-re-invokes the
   model on each `task_notification`). The turn finalizes only when a `result` arrives with no live
   background tasks and the model idles (grace window `CLAUDE_BG_DRAIN_GRACE_MS`).
3. Stream events → OWUI SSE: text deltas stream through; thinking → a reasoning `<details>` card; tool
   use/results → a tool `<details>` card; subagent progress → `↳ subagent started` / `✓ subagent
   completed` notes; compaction/rate-limit/denied → subtle notes.
4. **Presence:** posts `running` at start, **heartbeats every 25s** during the turn (empty-patch touch),
   `done` at end, to the fork's `/api/v1/agent-runs` (drives the sidebar/inbox dots).
5. **Fire-and-forget:** if the client disconnects and a mirror target exists, the run detaches and mirrors
   its output back into the chat; `recoverFF()` re-issues runs interrupted by a restart (bounded).

---

## Claude Code parity matrix (2026-08-09, SDK 0.3.223)

| Capability | Status | How |
|---|---|---|
| Built-in tools (Bash/Read/Edit/Write/Grep/Glob/WebSearch/WebFetch) | ✅ full | inherited (no `allowedTools` restriction) |
| Subagents / Task + **background** tasks | ✅ full | streaming-input drain loop (see above) |
| Skills | ✅ full | `settingSources:['user']` + `skills:'all']`, shared `~/.claude/skills` |
| MCP servers | ✅ full | `mcpServers` + `strictMcpConfig` (built-in `tools` = publish_artifact/notify; add via `.env` `MCP_SERVERS`) |
| Permissions + destructive guardrail | ✅ full | `canUseTool` + a `PreToolUse` hook (belt-and-suspenders) |
| Interactive **AskUserQuestion** | ✅ full | `canUseTool` → socket event → OWUI card → answer (E7) |
| Extended thinking | ✅ rendered | reasoning cards; depth tunable via `CLAUDE_EFFORT` / `CLAUDE_THINKING` |
| Session resume | ✅ full | in-memory cache → `opts.resume`; transcript replay on miss |
| Images / vision | ✅ full | `buildPrompt()` forwards OWUI `image_url` data-URIs as image blocks |
| System prompt / CLAUDE.md | ✅ full | `appendSystemPrompt` + `settingSources` |
| Token streaming | ✅ full | `includePartialMessages` |
| Progress / compaction / rate-limit / error surfacing | ✅ | new system-message branches + result-subtype messages |
| Cost/turn caps, fallback model, effort | ✅ env-gated | `CLAUDE_MAX_TURNS` / `CLAUDE_MAX_BUDGET_USD` / `CLAUDE_FALLBACK_MODEL` / `CLAUDE_EFFORT` |
| **ExitPlanMode** (plan approve/reject card) | 🟡 gap | renders as a plain tool card; interactive card is the next feature |
| TodoWrite | 🟡 generic | renders as a tool card (no dedicated checklist UI) |
| Structured usage (cache tokens / cost) | 🟡 partial | only input/output tokens surfaced |
| Statusline · checkpoint/rewind UI · output styles · IDE | ⬜ N/A | TUI/editor-only — not applicable to a chat bridge |

---

## Config (env)

Set in `.env` → rendered into `~/.config/agent-hub/claude-host.env` by `host/install.sh`.

| Env | Default | What |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | — | **required** (Max subscription; never an API key) |
| `ADAPTER_KEY` | — | **required**, fail-closed bearer for OWUI↔adapter |
| `CLAUDE_MODEL` / `CLAUDE_MODELS` | `claude-sonnet-5` / `…-5,opus-5,haiku-4-5` | default + `/v1/models` list (SDK also accepts aliases `sonnet`/`opus`/`haiku`) |
| `WORKSPACE` | `$HOME` | agent cwd (real home ⇒ full access) |
| `CLAUDE_CONFIG_DIR` | `~/.claude-owui` | isolated SDK config dir (see above) |
| `MCP_TOOLS_URL` / `MCP_SERVERS` | — | shared MCP (publish_artifact/notify) + extra servers |
| `CLAUDE_SETTING_SOURCES` | `user` | ⚠️ do not add `project`/`local` (bypass trap) |
| `CLAUDE_SKILLS` | `all` | `all` or a comma list |
| `CLAUDE_PERMISSION_MODE` | `default` | keep `default` (canUseTool must run) |
| `CLAUDE_EFFORT` | — | `low|medium|high|xhigh|max` |
| `CLAUDE_THINKING` / `CLAUDE_THINKING_TOKENS` | — | `adaptive`, or an explicit budget |
| `CLAUDE_MAX_TURNS` / `CLAUDE_MAX_BUDGET_USD` | — | safety caps |
| `CLAUDE_FALLBACK_MODEL` | — | used if primary is overloaded |
| `CLAUDE_BG_DRAIN_GRACE_MS` / `CLAUDE_BG_WAIT_MAX_SEC` | 4000 / 900 | background-drain grace + hard cap |

Ship a change: edit `server.mjs` → `systemctl --user restart owui-claude.service`. **⚠️ A restart kills
any in-flight turn** (OWUI shows an error + can leave a broken empty message) — restart only when no turn
is active, or warn the user first. (This is the top resilience item to harden — see Known gaps.)

---

## Checking a NEW Claude Code / SDK version

When bumping `@anthropic-ai/claude-agent-sdk`, run this 5-minute check before trusting it:

```bash
cd owui-claude
npm i @anthropic-ai/claude-agent-sdk@latest          # or the pinned target
node -e "console.log(require('./node_modules/@anthropic-ai/claude-agent-sdk/package.json').version)"

# 1. Options surface changed? diff the type defs for new/removed query() options + message types.
grep -nE "settingSources|skills\??:|thinking|effort|maxTurns|maxBudgetUsd|SDK.*Message" \
  node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts | less

# 2. Current models + slash commands (uses a live session; needs the OAuth token in env):
#    write a tiny probe that calls q.supportedModels() / q.supportedCommands() off the init message
#    (see git history: the "_probe2.mjs" pattern) — update CLAUDE_MODELS if tiers changed.

# 3. Behavior smoke — through the running adapter (K = ADAPTER_KEY):
#    a. plain turn returns fast:          "Reply with exactly: OK"
#    b. tool turn works:                  "Use web search to find <x>, give one URL"
#    c. background fan-out synthesizes:    launch 2 run_in_background subagents that echo tokens →
#       expect "✓ subagent completed" notes + a final synthesis in ONE response
#    d. AskUserQuestion still renders as a card (not a raw tool card)
```

If a message `type`/`subtype` we render disappears or a rendered subtype changes shape, update the
`streamTurn()` consume loop. If `supportedModels()` shows new tiers, update `CLAUDE_MODELS` in `.env`.

---

## Known gaps / operational caveats

- **Restart kills live turns** (top item): a synchronous streaming turn dies if the process restarts →
  OWUI saves a broken empty errored message and the chat can become un-typeable. Mitigations to build: a
  SIGTERM drain that flushes a graceful "_interrupted — resend to continue_" message; and/or the fork
  clearing a stuck errored leaf so no DB repair is needed. For now: don't restart mid-turn.
- **ExitPlanMode** isn't yet an interactive approve/reject card (renders as a tool card). Next feature —
  mirror the AskUserQuestion `canUseTool` → socket-event → answer machinery.
- Session cache is in-memory: an adapter restart drops it; context is preserved via transcript replay
  (text only — fine-grained tool history from before the restart is lost).

## Files
`server.mjs` (the adapter) · `Dockerfile` (node-profile fallback; aibo runs it on the host) ·
`../host/` (systemd unit + install) · `../CLAUDE.md` §2/§5b (build + skills model) · `../AGENTS.md`.
