# EPIC — Native "Publish to artifacts board" button + list API

**Goal.** Make publishing a first-class OWUI action instead of an agent-only MCP tool. Any assistant
reply gets a **Publish** button in its action row → the message becomes a shareable web page on the
artifacts board (`apps.kingdomofluna.com/artifacts`) + fires a phone push, and the page opens.

Status: **shipped** (#134 publish button + #135 board list API). Unblocks #136 (retire the
`publish_artifact` MCP tool).

---

## Design

The board's `POST /api/publish` is gated by a server-side `PUBLISH_TOKEN` that must **not** reach the
browser. So the browser calls a JWT-authed **fork route** which proxies to the board with the token
server-side (same pattern as `agent_activity.py`'s transcript proxy).

The board is **hub-only** — every machine (aibo, MacBook node, …) publishes to this one board — so the
board list API *is* the cross-machine aggregation (#135): there's nothing to federate, it's already central.

```
message action row (ResponseMessage.svelte)
   │  publishArtifact(token, title, content)
   ▼
POST /api/v1/artifacts/publish   (fork: routers/agent_artifacts.py, JWT)
   │  + X-Publish-Token (server-side secret)
   ▼
artifacts board  POST /api/publish  → writes summaries/<date>-<slug>.md, fires ntfy
   ▲
   └─ returns {url:/artifacts/summaries/…} → fork prepends ARTIFACTS_PUBLIC_BASE → opens the page
```

---

## What shipped

| Piece | File |
|---|---|
| Fork route: `POST /publish` (proxy+token) + `GET /list` (board as JSON, public URLs) | `backend/open_webui/routers/agent_artifacts.py` (NEW) + registered in `main.py` at `/api/v1/artifacts` |
| Board JSON list endpoint | `artifacts/server.mjs` — `GET /api/list → {items:[{kind,name,href,created}]}` |
| Frontend API client | `src/lib/apis/artifacts/index.ts` (NEW) — `publishArtifact()` + `listArtifacts()` |
| Publish button | `src/lib/components/chat/Messages/ResponseMessage.svelte` — Share icon next to Copy; derives a title from the first line; toast + opens the page |
| Env (owui container) | `docker-compose.yml` `&owui-env`: `ARTIFACTS_API`, `ARTIFACTS_LIST_API`, `ARTIFACTS_PUBLIC_BASE`, `PUBLISH_TOKEN` |

### Behavior
- Title = first non-empty line of the reply (markdown markers stripped, ≤80 chars), fallback "Untitled".
- On success: toast + `window.open(url)`; phone gets the board's own "New artifact" push.
- Fail-safe: `503` if `PUBLISH_TOKEN` unset, `502` if the board is unreachable — the chat is never broken.

---

## How to test
1. Open any assistant reply → hover the action row → click the **Publish** (share) icon.
2. A toast appears and the published page opens at `apps.kingdomofluna.com/artifacts/summaries/…`.
3. Phone buzzes ("New artifact: <title>").
4. `GET /api/v1/artifacts/list` (with a JWT) returns the board items as JSON with absolute URLs.

---

## Not done / next
- **#136 — retire the `publish_artifact` / `notify` MCP tools.** The button replaces the user-facing
  publish; the auto-notifications (done / needs-input) replace the common `notify`. ⚠️ Retiring them
  removes **autonomous agent** publish/notify (matters for scheduled Automations / daily briefs) — decide
  scope before removing.
- **Native Artifacts panel** (optional) — a sidebar list rendered from `GET /list` (the API is ready; no UI yet).
- **Publish as app** (kind=`app`, format=`html`) — the route already accepts it; no UI toggle yet
  (button always publishes a markdown summary).
