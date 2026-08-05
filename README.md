# agent-hub — Open WebUI + agent adapters (Docker)

The whole **Open WebUI agent hub** as one compose stack. Each agent is exposed to OWUI as an
OpenAI-compatible **adapter** (a selectable "model").

## Services
| Service | Port | Role |
|---|---|---|
| `open-webui` | `3000:8080` | The hub UI (behind Pangolin SSO externally; `WEBUI_AUTH=false` inside) |
| `owui-hermes` | `9211` (internal) | Streams OWUI ⇄ **host** Hermes (`:9119`) over `/api/ws` |
| `owui-claude` | `9212` (internal) | OpenAI-compat over the Claude Agent SDK (subscription OAuth) |
| `ollama` | `127.0.0.1:11434` | Reserved for the Phase-3 local text model |

- OWUI → adapters by **service name** (`http://owui-hermes:9211/v1`, `http://owui-claude:9212/v1`).
- owui-hermes → host Hermes via `host.docker.internal:9119` (Hermes binds `0.0.0.0`).
- **Hermes itself stays on the host** as the `hermes-dashboard` systemd `--user` service (one Hermes).

## Operate
```bash
cd ~/Documents/docker/agent-hub
docker compose up -d --build      # build + start
docker compose ps                 # status
docker compose logs -f owui-claude
docker compose restart owui-claude
docker compose down               # stop (volumes/chats preserved)
```

## Config
- Secrets in `.env` (gitignored, `chmod 600`) — see `.env.example`.
- OWUI custom env lives in `docker-compose.yml`; the rest are the image defaults.
- Volumes `open-webui` + `ollama` are **external** (reused from the pre-compose install → chat
  history preserved). `owui-claude-workspace` is compose-managed.

## Reboot survival
`restart: unless-stopped` on every service + `docker.service` enabled ⇒ the stack comes back on boot.

## Adapter source
- `owui-hermes/hermes_adapter.py` — the `/api/ws` streaming adapter (forwards images via
  `image.attach_bytes`; res-close abort handling).
- `owui-claude/server.mjs` — Agent SDK adapter (streaming SSE, guardrail, vision, web search).

Docs/architecture: `~/Documents/aibo-server/agent-hub/`.
