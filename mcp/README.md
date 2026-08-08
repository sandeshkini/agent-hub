# External MCP hub — one place → all adapters

Add an external MCP server (Linear, a custom tool server, anything HTTP-MCP) in **one place** and every
agent gets it. This is the "many apps, their MCPs, all agents can use them" layer — same idea as the
built-in `publish_artifact` + `notify`, but extensible.

## Add a server
Set **`MCP_SERVERS`** in `.env` — a JSON list of `{name, url[, type]}`:
```
MCP_SERVERS=[{"name":"linear","url":"https://mcp.linear.app/mcp"},{"name":"custom","url":"http://host:9000/mcp"}]
```
Then `docker compose up -d --build owui-claude opencode` (and re-run `hermes/install.sh` for Hermes).

## Who picks it up
- **owui-claude** — parses `MCP_SERVERS` → `opts.mcpServers` (Agent SDK). ✅ automatic.
- **opencode** — entrypoint merges `MCP_SERVERS` into `~/.config/opencode/opencode.json` `mcp`. ✅ automatic.
- **Hermes** — add matching entries under `mcp_servers:` in the brain's `config.yaml` (the built-in
  `tools` server at :8009 is already there). Auto-merge from `MCP_SERVERS` is a follow-up.
- The built-in **mcp-tools** (`publish_artifact`, `notify`) is always present on every adapter.

All entries render as **native tool cards** in OWUI and are usable in Slack via the gateway.
