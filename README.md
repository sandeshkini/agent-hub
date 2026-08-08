# Agent Hub — a forked Open WebUI + agents + terminal, in one package

The whole hub as **one repo, one compose, one `.env`**. Open WebUI (our **fork**) is the front end;
each agent (Hermes · Claude · OpenCode) is an OpenAI-compatible **adapter** exposed as a selectable
model, plus an in-UI **terminal**, an **inbox** with live summaries, and a **machine selector** that
scopes models + terminal to a chosen machine.

## Quickstart (dumb-simple)
```bash
git clone https://github.com/sandeshkini/agent-hub.git && cd agent-hub
cp .env.example .env         # set COMPOSE_PROFILES=hub|node, secrets, IPs
./setup.sh                   # builds the fork (hub), installs host terminal, brings the stack up
```
`./setup.sh` is idempotent: run it again after editing `.env`. OWUI → http://localhost:3000.

**Minimum you must edit in `.env`:** `COMPOSE_PROFILES` (hub or node), `WORKSPACE_HOST`/`WORKSPACE`
(your home dir — `echo $HOME`; macOS `/Users/you`), and at least one model key
(`OPENROUTER_API_KEY` for Hermes, `CLAUDE_CODE_OAUTH_TOKEN` for Claude). Everything else is optional.

## Using it (day to day)
- **Pick an agent** from the model dropdown — Hermes, Claude, or OpenCode each appear as selectable
  models. Same chat UI for all; switch mid-conversation.
- **Terminal** — the left sidebar has a *Terminals* section (+ to open). A real host shell in the
  browser (git, claude, docker…), scoped to the selected machine. Files panel toggles on the right.
- **Machine selector** — the "All machines" dropdown at the top of the sidebar filters models +
  terminals to one box. Nodes you onboard show up here automatically.
- **Inbox + summaries** — the *Chats* list shows a live one-line summary of what each agent did;
  the Inbox groups them by "needs attention" / recent.
- **Shared MCP tools** — add external MCP servers **once** in `.env` (`MCP_SERVERS=[...]`) and every
  agent gets them. Built-ins: `publish_artifact` (shareable web page) + `notify` (phone push).
- **Slack (optional)** — set `SLACK_*` tokens and add `slack` to `COMPOSE_PROFILES`; chat any
  agent×model from Slack, and threads mirror in as OWUI chats.

## Two roles (one compose, `COMPOSE_PROFILES`)
- **hub** — Open WebUI (fork) + all adapters + ollama + mcp-tools + registrar. The main box.
- **node** — adapters + registrar only; registers into a remote hub so it appears in the machine
  selector. See `node/README.md` for the (few) hub-side entries a node needs.

### Adding a second machine (e.g. a MacBook as a node)
On the second box, clone the repo and in `.env` set:
```bash
COMPOSE_PROFILES=node
HUB_REGISTER_URL=http://<hub-lan-ip>:3000/api/v1/nodes/register   # the hub's LAN address
NODE_API_URL=http://<this-machine-lan-ip>:3000                    # this box's LAN URL
WORKSPACE_HOST=$HOME   WORKSPACE=$HOME                            # macOS: /Users/you
```
Then `./setup.sh`. The node heartbeats into the hub and appears in the machine selector; its models
show up prefixed (e.g. `mb.claude-…`). Both boxes must be on the same LAN (or Tailscale). Hermes
stays on the hub — nodes serve Claude + OpenCode.

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
