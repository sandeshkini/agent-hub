# artifacts — the publish board

Durable pages published by agents (and by you): **summaries** (`.md`/`.html`) and **apps**
(a folder with an `index.html` — interactive mini-apps).

Unlike Open WebUI's built-in artifacts pane — which is a *live preview* of HTML/CSS/JS in the
current chat, with no backend, no database and **no URL** — this gives an artifact a permanent
home you can link to and open from your phone.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/publish` | `x-publish-token` header | write a page, fire a push, return its URL |
| `GET` | `/` | none¹ | the board — everything, newest first |
| `GET` | `/summaries/<file>` | none¹ | `.md` rendered with `marked`; anything else raw |
| `GET` | `/apps/<slug>/…` | none¹ | static serve; bare dir → `index.html` |

¹ *No auth in the app itself* — reads are protected by whatever fronts it (here: Pangolin SSO).
Don't expose this directly to the internet without a front door.

**Publish body:** `{ title, kind: "summary"|"app", format: "md"|"html", content }`
→ `{ ok: true, url: "/artifacts/summaries/2026-08-10-my-title.md" }`

Filenames are `YYYY-MM-DD-<slugified-title>`, so publishing the same title twice on the same day
**overwrites**. Publishing with `format: html` serves the document raw — no markdown wrapper, no
width cap — which is what you want for pixel-accurate mockups.

## Config

| Env | Default | Notes |
|---|---|---|
| `PUBLISH_TOKEN` | — | required; POST is rejected 401 without a match |
| `CONTENT_DIR` | `/content` | bind-mounted from `${ARTIFACTS_CONTENT:-./artifacts/content}` |
| `BASE_PATH` | `/artifacts` | external prefix; Traefik strips it before proxying |
| `PUBLIC_BASE` | `https://apps.kingdomofluna.com` | used to build click-through links in pushes |
| `NTFY_URL` / `NTFY_TOPIC` / `NTFY_TOKEN` | — | publishing auto-pushes; **no-op if unset** |

## Data

`./artifacts/content/` is **gitignored** — this repo is public and published pages are personal
(they can contain host paths, system detail, internal notes). The code is tracked; the content is not.
Back it up yourself; it is the only copy.

## Runs on nodes too

`profiles: ["hub", "node"]` — each machine serves its own board, so an agent publishes locally
rather than round-tripping to the hub. The hub aggregates across machines (see `node/`).
