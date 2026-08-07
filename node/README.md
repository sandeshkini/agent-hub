# agent-hub node — bring a machine into the OWUI machine selector

A **node** is any machine you want to appear in OWUI's machine selector, exposing its **terminal** and
its **agents** (models). This dir is what you copy to a new box (e.g. the MacBook).

## The model (why it's simple)
Unlike the old dispatch hub (per-node public hostnames), **the browser only ever talks to OWUI**. A node
just has to be reachable **from the OWUI backend** over the LAN (or Tailscale). So a node needs:

1. **owui-terminal** running on the node (PTY-over-WS; same service as aibo's) → its terminal.
2. **adapters** running on the node (owui-claude / owui-opencode / …) → its agents.
3. **registrar.py** heartbeating the node into OWUI so the selector shows it + scopes models/terminal.

Then, once on the OWUI (aibo) side:
- Add the node's **owui-terminal** to `TERMINAL_SERVER_CONNECTIONS` (id must equal the node's `TERMINAL_ID`).
- Add the node's **adapters** as an OWUI **OpenAI connection** (give it a prefix so model ids don't
  collide with aibo's, e.g. `mb` → `mb.claude-sonnet-5`). List those ids in the node's `NODE_MODELS`.

The machine selector then filters models to the picked machine and points `/terminal` at its terminal.

## Steps on the node (MacBook)
```bash
# 1. clone the repo + bring up this node's adapters + owui-terminal (docker or native — your call)
#    (owui-terminal: run server.py on :7681 with a TERMINAL_TOKEN; adapters: your existing compose)
# 2. configure + run the registrar
cp node.env.example node.env      # edit: HUB_REGISTER_URL, NODE_LABEL, IPs, NODE_MODELS, TERMINAL_*
./register-node.sh                # foreground; or install as a launchd/systemd service (see below)
```
`register-node.sh` just runs `registrar.py`, which POSTs to `HUB_REGISTER_URL` every 30s.

## Steps on the OWUI host (aibo)
1. `TERMINAL_SERVER_CONNECTIONS` (in `~/Documents/apps/agent-hub/.env`) — add an entry:
   `{"id":"macbook","name":"macbook (shell)","url":"http://<macbook-lan-ip>:7681","auth_type":"bearer","key":"<token>","enabled":true}`
2. Add an OWUI **OpenAI connection** for the MacBook's adapters (Admin → Settings → Connections),
   base URL `http://<macbook-lan-ip>:<adapter-port>/v1`, prefix `mb`. Force-recreate `open-webui`.
3. The MacBook appears in the selector within ~30s of the registrar starting.

## Durable run
- **macOS**: wrap `register-node.sh` in a launchd plist (`~/Library/LaunchAgents/…`), `RunAtLoad`+`KeepAlive`.
- **Linux**: a systemd `--user` service running `registrar.py` (see aibo's `owui-node-registrar.service`).

## Notes
- Give the node a **static/reserved LAN IP** so `NODE_API_URL`/`TERMINAL_URL` don't drift.
- `NODE_HUB_TOKEN` must match OWUI's `NODE_HUB_TOKEN` env (leave both empty on a trusted LAN).
- Hermes stays a single brain on aibo; a node typically serves claude + opencode + terminal.
