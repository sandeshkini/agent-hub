# mcp-tools — shared agent tools (MCP server)

One tiny MCP server that gives **every agent/model in OWUI** the same tools (`publish_artifact`,
`notify`), rendered as native OWUI tool cards. Define a tool once here → all adapters get it.

- `server.py` — FastMCP (`mcp[cli]`), Streamable HTTP at `:8000/mcp`. Tools:
  - `publish_artifact(title, content_markdown)` → POSTs the artifacts API (page on the board + ntfy push).
  - `notify(title, message, priority)` → ntfy phone push (topic from env).
- Runs as the `mcp-tools` compose service (networks: `default` + `apps`; host port `127.0.0.1:8009`).
- Creds/config via env (compose `.env`): `PUBLISH_TOKEN`, `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`.

## Add a tool
Add an `@mcp.tool()` function → `docker compose up -d --build mcp-tools`. All agents get it next turn.

## Wiring (how each agent reaches it) — see full doc
`~/Documents/aibo-server/agent-hub/shared-tools-mcp.md` (why MCP not OWUI-tools, per-adapter config,
reboot survival, adding tools/adapters/machines).
- owui-claude: `opts.mcpServers` + `strictMcpConfig` in `../owui-claude/server.mjs`
- opencode: `../opencode/opencode.json` → `mcp.tools`
- hermes: `~/.hermes/config.yaml` → `mcp_servers.tools` (host → `:8009`)
