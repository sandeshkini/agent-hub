#!/usr/bin/env python3
"""
agent-tools — one shared MCP server for ALL agents (claude / hermes / opencode).

Exposes two tools that every agent gets from this single place (add a tool here → every agent
gets it, no per-adapter work):
  • publish_artifact(title, content_markdown) → publishes a page to the artifacts board
    (POSTs the artifacts API, which also fires an ntfy phone push) and returns the URL.
  • notify(title, message, priority)          → sends a phone push via ntfy (no page).

Served over Streamable HTTP at /mcp so every agent reaches it by URL:
  - containers (owui-claude, opencode) → http://mcp-tools:8000/mcp   (compose network)
  - host Hermes                        → http://127.0.0.1:<published>/mcp
MCP tool calls surface in each agent as ordinary tool_use/tool_result → they render as the
adapters' native OWUI tool cards ("✓ View Result from …").
"""
import datetime
import json
import os
import re
import urllib.request

from mcp.server.fastmcp import FastMCP

ARTIFACTS_API = os.getenv("ARTIFACTS_API", "http://artifacts:8080/api/publish")
PUBLISH_TOKEN = os.getenv("PUBLISH_TOKEN", "")
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "https://apps.kingdomofluna.com")
NTFY_BASE = os.getenv("NTFY_URL", "http://ntfy").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "aibo")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

mcp = FastMCP("agent-tools", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


@mcp.tool()
def publish_artifact(title: str, content_markdown: str) -> str:
    """Publish a markdown summary as a shareable web page on the artifacts board
    (apps.kingdomofluna.com/artifacts). Use this to SHOW the user something visual — a report,
    a summary, results. A phone notification is sent automatically. Returns the page URL.

    :param title: Short title for the page.
    :param content_markdown: The page body in markdown.
    """
    try:
        body = json.dumps({"title": title, "kind": "summary", "format": "md",
                           "content": content_markdown}).encode()
        req = urllib.request.Request(ARTIFACTS_API, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        return "Published: " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"publish_artifact failed: {e}"


@mcp.tool()
def notify(title: str, message: str, priority: str = "default") -> str:
    """Send a push notification to the user's phone (ntfy). Use for a quick heads-up / alert /
    "task done" — no page, just a buzz.

    :param title: Short notification title.
    :param message: The notification body.
    :param priority: one of min, low, default, high, urgent.
    """
    try:
        payload = {"topic": NTFY_TOPIC, "title": title, "message": message}
        pr = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}.get(priority)
        if pr:
            payload["priority"] = pr
        headers = {"Content-Type": "application/json"}
        if NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
        req = urllib.request.Request(NTFY_BASE + "/", data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        urllib.request.urlopen(req, timeout=10).read()
        return f"Notification sent: {title}"
    except Exception as e:
        return f"notify failed: {e}"


if __name__ == "__main__":
    print(f"[mcp-tools] agent-tools MCP on :{os.getenv('PORT','8000')}/mcp "
          f"(publish→{ARTIFACTS_API}, ntfy→{NTFY_BASE}/{NTFY_TOPIC})", flush=True)
    mcp.run(transport="streamable-http")
