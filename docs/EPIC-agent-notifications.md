# EPIC — Agent phone notifications (ntfy)  ✅ COMPLETE

**Goal.** Buzz the user's phone when a background agent turn **finishes** or when it **blocks waiting for
input** (AskUserQuestion / ExitPlanMode). "Needs input" is the higher-priority push — the agent is
parked until you answer. Don't buzz while you're actively watching OWUI.

Status: **shipped** (fork backend + compose env). Delivered via the existing ntfy bus (topic `aibo`),
same one `mcp-tools notify` and the artifacts board already use — one notification channel for the fleet.

---

## Design (why this shape)

Single-user hub → **no per-user notification-target config**. Everything is env-driven and self-contained.
Two things had to be true and both were verified:

1. **A "done" signal fires for our external OpenAI-connection adapters.** OWUI calls
   `publish_chat_finished_event(...)` in `utils/middleware.py` at the end of the streaming completion path
   — **not gated by model provider**, so it fires for Claude/Hermes/OpenCode adapter turns too. That's the
   done hook.
2. **A "needs input" signal exists.** The adapters already POST `status: "needs-input"` + the parked
   question to the `agent_runs` presence registry (`routers/agent_runs.py`). That's the needs-input hook.

Away-gating reuses OWUI's own presence: `Users.is_user_active(user_id)` = `last_active_at` within 3 min.
When you're polling OWUI (tab open) you're "active" → suppressed; phone locked / tab closed → after 3 min
you're "away" → push. Delivery mode is `away` by default, `always` for testing.

Transport matches `mcp-tools notify` exactly: JSON POST to the ntfy root with
`Authorization: Bearer <NTFY_TOKEN>`, `topic`, `title`, `message`, `priority`, `tags`, `click`. Verified
end-to-end: HTTP 200 from inside the `open-webui` container against `http://ntfy`.

---

## What shipped

| Piece | File | Notes |
|---|---|---|
| ntfy helper | `owui-fork/upstream/backend/open_webui/utils/agent_notify.py` (NEW) | `notify_done` / `notify_needs_input`; away-gate via `is_user_active`; fail-safe (swallows all errors) |
| done hook | `…/utils/middleware.py` — end of `publish_chat_finished_event` | `notify_done(user.id, chat_id, title, content)`; priority 3 (default) |
| needs-input hook | `…/routers/agent_runs.py` — `set_run` transition into `needs-input` | `_notify_needs_input(cid, question)`; maps chat→user via `Chats.get_chat_by_id`; priority 5 (urgent); fires once per transition (not per heartbeat) |
| env | `docker-compose.yml` `&owui-env` anchor | `NTFY_URL/TOPIC/TOKEN`, `AGENT_NTFY_ENABLED`, `AGENT_NTFY_DELIVERY`, `AGENT_NTFY_CLICK_BASE` |

### Config knobs (env, all optional — sane defaults)
- `AGENT_NTFY_ENABLED` (default `true`) — master switch.
- `AGENT_NTFY_DELIVERY` (`away` | `always`, default `away`) — `always` = buzz every time (testing / audit log).
- `AGENT_NTFY_CLICK_BASE` (default `https://operator.kingdomofluna.com`) — public base for the tap-through
  link (`webui.url` is unset, so a relative path wouldn't open on a phone).
- `AGENT_NTFY_PRIORITY_DONE` (3) / `AGENT_NTFY_PRIORITY_INPUT` (5) — ntfy priorities.
- `NTFY_URL/TOPIC/TOKEN` — reused from the existing bus (topic `aibo`).

---

## How to test

**Transport (no rebuild):** from inside the running container —
```bash
TOK=$(grep -E '^NTFY_TOKEN=' .env | cut -d= -f2- | sed 's/#.*//' | tr -d ' "')
docker exec -i -e TOK="$TOK" open-webui python3 - <<'PY'
import os,json,urllib.request
r=urllib.request.urlopen(urllib.request.Request("http://ntfy/",
  data=json.dumps({"topic":"aibo","title":"test","message":"hi","priority":4}).encode(),
  headers={"Content-Type":"application/json","Authorization":f"Bearer {os.environ['TOK']}"}))
print(r.status)
PY
```

**Done push (end-to-end):** temporarily set `AGENT_NTFY_DELIVERY=always` in `.env`,
`docker compose up -d open-webui`, run a Claude turn through OWUI → phone buzzes with "✓ <chat title>",
tapping opens `…/c/<chat_id>`. Revert to `away`.

**Needs-input push:** send `/plan …` or a prompt that triggers AskUserQuestion; when the card parks, the
adapter posts `needs-input` → phone buzzes (priority high, 🔔). Answer it → no repeat push.

**Away-gate:** with `away`, leave OWUI open (active) and finish a turn → **no** push. Close the tab /
lock the phone for 3+ min, finish a turn → push.

---

## Not done / next
- **No native OWUI notification-target UI wiring** — deliberately skipped (single-user, env is simpler and
  survives DB resets). If multi-user is ever needed, switch to the built-in `utils/notifications.py`
  target system + add an ntfy branch to `utils/webhook.py::post_webhook`.
- **No per-chat mute / quiet hours.** ntfy app-side scheduling covers quiet hours if wanted.
- Ships alongside #134 (native artifacts publish button) and #136 (retire the `publish_artifact`/`notify`
  MCP tools once the button + these pushes fully replace them).
