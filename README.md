# Agent Hub — a forked Open WebUI + agents + terminal, in one package

The whole hub as **one repo, one compose, one `.env`**. Open WebUI (our **fork**) is the front end;
each agent (Hermes · Claude · OpenCode) is an OpenAI-compatible **adapter** exposed as a selectable
model, plus an in-UI **terminal**, an **inbox** with live summaries, and a **machine selector** that
scopes models + terminal to a chosen machine.

## Quickstart (dumb-simple)
```bash
git clone <this-repo> agent-hub && cd agent-hub
cp .env.example .env         # set COMPOSE_PROFILES=hub|node, secrets, IPs
./setup.sh                   # builds the fork (hub), installs host terminal, brings the stack up
```
`./setup.sh` is idempotent: run it again after editing `.env`. OWUI → http://localhost:3000.

## Two roles (one compose, `COMPOSE_PROFILES`)
- **hub** — Open WebUI (fork) + all adapters + ollama + mcp-tools + registrar. The main box (aibo).
- **node** — adapters + registrar only; registers into a remote hub so it appears in the machine
  selector. See `node/README.md` for the (few) hub-side entries a node needs.

## What's in here (one repo)
| Path | What |
|---|---|
| `docker-compose.yml` | the whole stack, `hub`/`node` profiles (fork image default) |
| `docker-compose.stock.yml` | rollback override → upstream `open-webui:main` |
| `setup.sh` · `.env.example` | one-command setup + the single config file |
| `owui-fork/` | the Open WebUI fork (patches + `build.sh`; `upstream/` is gitignored, rebuilt) |
| `owui-hermes/` `owui-claude/` `owui-opencode/` `opencode/` | the agent adapters |
| `owui-terminal/` | host PTY-over-WS terminal service + `install.sh` (systemd/launchd) |
| `mcp-tools/` | shared MCP server (`publish_artifact` + `notify`) for all agents |
| `node/` | the registrar (`registrar.py`) that heartbeats a machine into the selector |

## Operate
```bash
docker compose up -d --build        # start/update (respects COMPOSE_PROFILES from .env)
docker compose ps                   # status
docker compose logs -f owui-claude
docker compose -f docker-compose.yml -f docker-compose.stock.yml up -d open-webui   # rollback OWUI to :main
```

## Notes
- **owui-terminal runs on the host** (real shell for heroku/git/claude) — not a container; `install.sh`
  sets up systemd (Linux) / launchd (macOS).
- **Hermes stays a single brain on the hub** (`hermes-dashboard`, host); nodes serve claude + opencode.
- Volumes `open-webui` + `ollama` are external (chat history preserved). Reboot-safe
  (`restart: unless-stopped` + docker enabled).
- Secrets live in `.env` (gitignored). Architecture docs: `~/Documents/aibo-server/agent-hub/`.
```
