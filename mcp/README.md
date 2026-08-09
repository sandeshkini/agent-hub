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

**3. Interactive OAuth — works, via a shared `mcp-remote` token cache.** `linear`, `posthog`,
`clickhouse_cloud` and the Google servers have no static token and a container cannot complete a
browser consent flow. Wire them as stdio entries that proxy through `mcp-remote`:
```json
{"name":"linear","type":"stdio","command":"npx",
 "args":["-y","mcp-remote","https://mcp.linear.app/mcp","--transport","http"]}
```
Then authenticate ONCE on the host (browser opens; approve; Ctrl-C):
```bash
npx -y mcp-remote https://mcp.linear.app/mcp --transport http
```
`mcp-remote` caches the token under `~/.mcp-auth`. This carries into the containers because the host
home is mounted at `${WORKSPACE}` **and** `HOME=${WORKSPACE}` inside the adapters — so
`$HOME/.mcp-auth` in the container *is* the host's cache. The mount is read-write, so silent token
refresh writes back. Verified: Claude and Hermes both return real Linear/PostHog/ClickHouse data.

⚠️ `mcp-remote` refreshes *access* tokens on its own, but if a **refresh** token hard-expires the flow
must be re-run interactively on the host — nothing in a container can do it. Symptom: that server's
tools quietly stop appearing. Re-run the one-liner above to fix.

**4. Needs a host binary** (e.g. `planetscale` → `pscale`): the binary does not exist in the adapter
image. Either add it to the image, or expose the data another way. Not wired by default.

`strictMcpConfig: true` is set, so the agent loads ONLY these servers — a stray `.mcp.json` inside the
mounted workspace can never inject an unexpected server.
