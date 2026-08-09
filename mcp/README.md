# External MCP hub — one place → all agents

Add an external MCP server (Linear, a custom tool server, anything speaking the **Model Context
Protocol**) in **one place** and every agent gets it. Same idea as the built-in `publish_artifact` +
`notify`, but extensible — "many apps, their MCPs, all agents can use them."

## The one place: `MCP_SERVERS` in `.env`
A JSON list of **standard MCP server configs**, each carrying a `name`. Standard transports:

```jsonc
MCP_SERVERS=[
  // HTTP / SSE server (most external apps — Linear, hosted tool servers):
  {"name":"linear","type":"http","url":"https://mcp.linear.app/mcp","headers":{"Authorization":"Bearer <token>"}},
  // stdio server (a local command the agent spawns):
  {"name":"github","type":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-github"],"env":{"GITHUB_TOKEN":"<t>"}}
]
```
Fields follow the standard MCP config shape:
- **HTTP/SSE:** `type` (`http`/`sse`), `url`, optional `headers`.
- **stdio:** `type:"stdio"`, `command`, optional `args`, optional `env`.
- `type` may be omitted — it's inferred (`url` ⇒ http, else stdio).

Then re-apply:
```bash
docker compose up -d --build owui-claude opencode     # containers pick it up
./hermes/install.sh                                    # (re)renders Hermes config with the servers
docker compose restart open-webui                      # refresh (harmless)
```

## Who consumes it (all translate the SAME standard entries)
| Agent | Mechanism | File |
|---|---|---|
| **owui-claude** | passes each entry straight into the Agent SDK `opts.mcpServers` (verbatim, minus `name`) | `owui-claude/server.mjs` |
| **opencode** | entrypoint merges into `~/.config/opencode/opencode.json` `mcp` (`http`→`remote`, `stdio`→`local`) | `opencode/entrypoint.sh` |
| **Hermes** | `hermes/install.sh` renders them under `mcp_servers:` in each brain's `config.yaml` | `hermes/config.yaml.template` |
| built-in **mcp-tools** | always present on every adapter (`publish_artifact`, `notify`) | `mcp-tools/server.py` |

All MCP tools render as **native tool cards** in OWUI and are usable in Slack via the gateway.

## Add a new MCP anytime — checklist
1. Append one `{name,...}` entry to `MCP_SERVERS` in `.env`.
2. `docker compose up -d --build owui-claude opencode && ./hermes/install.sh`.
3. Confirm: ask any agent to use a tool from that server — it should appear as a tool card.

Single source of truth — don't hardcode servers in individual adapters.

## Wiring external MCP servers (MCP_SERVERS in `.env`)

One JSON list in `.env` → **every** agent (claude, opencode, hermes) gets it. Each entry is a standard
MCP server config plus a `name`; it is passed through to the Agent SDK verbatim (minus `name`), and
`hermes/install.sh` merges the same list into `~/.hermes/config.yaml`.

Servers fall into three classes, and only the first two work headless:

**1. HTTP + a static header — always works.** Preferred. Inline the token; `.env` is gitignored + 0600.
```json
{"name":"stoq","type":"http","url":"https://app.stoqapp.com/mcp",
 "headers":{"Authorization":"Bearer <token>"}}
```

**2. stdio via `npx` — works in the containers.** The adapter images are node-based, so `npx` resolves.
```json
{"name":"slack","type":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-slack"],
 "env":{"SLACK_BOT_TOKEN":"xoxb-…","SLACK_TEAM_ID":"T…"}}
```
Do **not** wrap a plain HTTP+header server in `mcp-remote` (as `.mcp.json` does for local CLI use) —
that spawns a subprocess per turn for no benefit. Convert it to form 1 instead.

**3. Interactive OAuth — NOT supported headless.** `linear`, `posthog`, `clickhouse_cloud`, and the
Google servers authenticate through a browser consent flow. There is no token to inline, and nothing
in a container can complete the redirect. Options, in order of sanity:
  - use the service's REST API through a custom tool / the built-in MCP server instead;
  - run `mcp-remote` **once interactively on the host** so it caches a token under `~/.mcp-auth`, then
    mount that dir into the adapter and point the entry at the cached profile (fragile — the token
    expires and nothing re-runs the flow);
  - skip it.

**4. Needs a host binary** (e.g. `planetscale` → `pscale`): the binary does not exist in the adapter
image. Either add it to the image, or expose the data another way. Not wired by default.

`strictMcpConfig: true` is set, so the agent loads ONLY these servers — a stray `.mcp.json` inside the
mounted workspace can never inject an unexpected server.
