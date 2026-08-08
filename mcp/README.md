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
