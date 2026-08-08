#!/usr/bin/env python3
"""agent-hub node registrar — heartbeats this machine into the OWUI node registry.

A machine ("node") runs its own adapter fleet (owui-claude/opencode/…) + owui-terminal, then this
tiny script POSTs {label,url,api_url,token,capabilities,models,terminal} to the OWUI hub every 30s so
the machine selector shows it online and scopes the model list + terminal target to it.

Portable: Python 3 stdlib only (works on macOS/Linux). Config via env or a node.env file next to it.

Connectivity model (same-LAN or Tailscale):
  - The browser only ever talks to OWUI. Nodes just need to be reachable FROM the OWUI backend.
  - HUB_REGISTER_URL points at OWUI's SSO-free LAN address (e.g. http://<hub-lan-ip>:3000/api/nodes/register).
  - NODE_API_URL / TERMINAL_URL are THIS machine's LAN URLs that the OWUI backend will call.
Give this machine a static/reserved LAN IP so those URLs don't drift.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    # node.env (KEY=VALUE lines) augments the process env; real env vars win.
    path = os.environ.get("NODE_ENV_FILE", os.path.join(HERE, "node.env"))
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def cfg(key, default=None, required=False):
    v = os.environ.get(key, default)
    if required and not v:
        sys.exit(f"registrar: {key} is required (set it in node.env or the environment)")
    return v


def build_payload():
    models = [m.strip() for m in (cfg("NODE_MODELS", "") or "").split(",") if m.strip()]
    caps = [c.strip() for c in (cfg("NODE_CAPABILITIES", "terminal,claude,opencode") or "").split(",") if c.strip()]
    terminal = None
    tid = cfg("TERMINAL_ID")
    turl = cfg("TERMINAL_URL")
    if tid or turl:
        # The selector only needs `id` (must match an OWUI terminal-server id) to switch the /terminal
        # target. url/key are optional metadata (useful when OWUI auto-adds the server for a remote node).
        terminal = {
            "id": tid or cfg("NODE_LABEL", "node"),
            "name": cfg("TERMINAL_NAME", f"{cfg('NODE_LABEL', 'node')} (shell)"),
            "url": turl or "",
            "key": cfg("TERMINAL_KEY", ""),
        }
    return {
        "label": cfg("NODE_LABEL", required=True),
        "url": cfg("NODE_URL", required=True),          # public/SSO URL for direct browser WS (optional use)
        "api_url": cfg("NODE_API_URL", cfg("NODE_URL")),  # URL the OWUI backend calls (LAN/Tailscale)
        "token": cfg("NODE_TOKEN", ""),
        "capabilities": caps,
        "models": models,
        "terminal": terminal,
        "version": "1.0.0",
    }


def main():
    load_env()
    hub = cfg("HUB_REGISTER_URL", required=True)
    hub_token = cfg("NODE_HUB_TOKEN", "")
    interval = int(cfg("HEARTBEAT_SECONDS", "30"))
    payload = build_payload()
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if hub_token:
        headers["Authorization"] = f"Bearer {hub_token}"

    print(f"registrar: node '{payload['label']}' -> {hub} every {interval}s "
          f"({len(payload['models'])} models, terminal={'yes' if payload['terminal'] else 'no'})", flush=True)
    while True:
        try:
            req = urllib.request.Request(hub, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read() or b"{}")
                print(f"registrar: ok id={resp.get('id')} status={resp.get('status')}", flush=True)
        except Exception as e:
            print(f"registrar: heartbeat failed ({e}) — retrying in {interval}s", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
