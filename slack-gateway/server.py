#!/usr/bin/env python3
"""slack-gateway — chat with ANY Agent Hub model/agent from Slack, as a first-class alternate frontend.

Slack Bolt in SOCKET MODE (no public URL — works behind Pangolin/NAT). Each Slack THREAD is a session
(mapped to an OWUI chat), so continuing in-thread keeps context, and the same conversation ALSO appears
as a chat in OWUI (bidirectional). Renders like OWUI: streaming text + tool cards + thinking. Drives
OWUI's OpenAI-compatible endpoint so it inherits access control + the shared MCP/tools layer.

Env: SLACK_BOT_TOKEN, SLACK_APP_TOKEN (xapp, Socket Mode), OWUI_BASE, OWUI_API_KEY, DEFAULT_MODEL.
"""
import json
import os
import re
import threading
import time
import urllib.request

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

OWUI_BASE = os.environ.get("OWUI_BASE", "http://open-webui:8080").rstrip("/")
OWUI_API_KEY = os.environ.get("OWUI_API_KEY", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "claude-sonnet-5")
STATE_PATH = os.environ.get("SLACK_STATE_PATH", "/state/sessions.json")
STREAM_EDIT_MS = int(os.environ.get("SLACK_STREAM_EDIT_MS", "900"))  # throttle chat.update

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# ── session store: slack thread key -> {chat_id, model, messages:[{role,content}]} ──
_lock = threading.Lock()
def _load():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}
def _save(d):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(d, open(STATE_PATH, "w"))
    except Exception:
        pass
SESS = _load()

def _skey(channel, thread):
    return f"{channel}:{thread}"

def _get_sess(channel, thread):
    with _lock:
        k = _skey(channel, thread)
        s = SESS.get(k)
        if not s:
            s = {"chat_id": None, "model": DEFAULT_MODEL, "messages": []}
            SESS[k] = s
        return s

def _put_sess():
    with _lock:
        _save(SESS)

# ── OWUI HTTP ──
def _owui(path, method="GET", body=None, stream=False, extra_headers=None):
    url = f"{OWUI_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if OWUI_API_KEY:
        headers["Authorization"] = f"Bearer {OWUI_API_KEY}"
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=600 if stream else 30)

def list_models():
    try:
        d = json.loads(_owui("/api/models").read())
        return [m["id"] for m in d.get("data", []) if not (m.get("info", {}) or {}).get("meta", {}).get("hidden")]
    except Exception:
        return [DEFAULT_MODEL]

# ── stream parse: split assistant content into text + tool cards + thinking ──
DETAILS_RE = re.compile(r'<details type="(tool_calls|reasoning)"[^>]*>.*?</details>', re.S)

def _attr(block, name):
    m = re.search(rf'{name}="([^"]*)"', block)
    return (m.group(1) if m else "").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

def render_blocks(full):
    """Turn accumulated assistant content into Slack Block Kit: text + tool cards + thinking context."""
    tools, thinking = [], []
    def _grab(m):
        blk = m.group(0)
        if m.group(1) == "tool_calls":
            name = _attr(blk, "name") or "tool"
            args = _attr(blk, "arguments")
            res = _attr(blk, "result")
            try: res = json.loads(res)
            except Exception: pass
            tools.append((name, args, str(res)[:600]))
        else:
            # reasoning: text is between <summary>…</summary> and </details>
            t = re.sub(r'^.*?</summary>', '', blk, flags=re.S)
            t = re.sub(r'</details>\s*$', '', t).strip()
            if t: thinking.append(t[:600])
        return ""
    text = DETAILS_RE.sub(_grab, full).strip()

    blocks = []
    if thinking:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "💭 _" + " · ".join(x.replace("\n", " ")[:180] for x in thinking[-2:]) + "_"}]})
    for name, args, res in tools[-8:]:
        argline = f"`{args[:140]}`" if args and args != "{}" else ""
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"🔧 *{name}* {argline}\n{('```'+res[:400]+'```') if res else ''}"}]})
    if text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    return text, blocks

# ── OWUI chat persistence (so the Slack thread appears as a chat in OWUI) ──
def persist_chat(sess, title):
    try:
        msgs = sess["messages"]
        history = {"messages": {}, "currentId": None}
        prev = None
        for i, m in enumerate(msgs):
            mid = f"slk-{i}"
            history["messages"][mid] = {"id": mid, "role": m["role"], "content": m["content"],
                                        "parentId": prev, "childrenIds": [], "timestamp": int(time.time())}
            if prev: history["messages"][prev]["childrenIds"].append(mid)
            prev = mid
        history["currentId"] = prev
        chat = {"models": [sess["model"]], "messages": msgs, "history": history, "title": title[:60] or "Slack"}
        if sess.get("chat_id"):
            _owui(f"/api/v1/chats/{sess['chat_id']}", "POST", {"chat": chat})
        else:
            r = json.loads(_owui("/api/v1/chats/new", "POST", {"chat": chat}).read())
            sess["chat_id"] = r.get("id")
        _put_sess()
    except Exception as e:
        print("[slack] persist_chat failed:", e, flush=True)

# ── run a turn: stream OWUI completion, live-edit the Slack message, render tools/thinking ──
def run_turn(say, client, channel, thread_ts, sess, user_text):
    sess["messages"].append({"role": "user", "content": user_text})
    posted = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="…")
    ts = posted["ts"]
    full, last_edit = "", 0.0
    try:
        body = {"model": sess["model"], "stream": True, "messages": sess["messages"],
                "chat_id": sess.get("chat_id")}
        resp = _owui("/api/chat/completions", "POST", body, stream=True,
                     extra_headers={"X-OpenWebUI-Chat-Id": sess.get("chat_id") or "slack"})
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0].get("delta", {}).get("content", "")
            except Exception:
                delta = ""
            if not delta:
                continue
            full += delta
            now = time.time()
            if (now - last_edit) * 1000 >= STREAM_EDIT_MS:
                last_edit = now
                _text, blocks = render_blocks(full)
                try:
                    client.chat_update(channel=channel, ts=ts, text=(_text or "…")[:2900], blocks=blocks or None)
                except Exception:
                    pass
    except Exception as e:
        full += f"\n\n_⚠️ gateway error: {e}_"
    text, blocks = render_blocks(full)
    try:
        client.chat_update(channel=channel, ts=ts, text=(text or "done")[:2900], blocks=blocks or None)
    except Exception:
        pass
    sess["messages"].append({"role": "assistant", "content": full})
    _put_sess()
    persist_chat(sess, sess["messages"][0]["content"] if sess["messages"] else "Slack")

# ── Slack events ──
def _clean(text, bot_id):
    return re.sub(rf"<@{bot_id}>", "", text or "").strip()

@app.event("app_mention")
def on_mention(event, say, client, context):
    thread = event.get("thread_ts") or event["ts"]
    txt = _clean(event.get("text"), context["bot_user_id"])
    sess = _get_sess(event["channel"], thread)
    if txt.startswith("model:"):
        sess["model"] = txt.split("model:", 1)[1].strip(); _put_sess()
        say(text=f"✅ model set to `{sess['model']}` for this thread", thread_ts=thread); return
    if not txt:
        say(text="Hi! Ask me anything, or `@me model:<id>` to switch model. `/model` lists them.", thread_ts=thread); return
    run_turn(say, client, event["channel"], thread, sess, txt)

@app.event("message")
def on_message(event, say, client):
    # DMs only (channel messages come via app_mention); ignore bots/edits
    if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
        return
    thread = event.get("thread_ts") or event["ts"]
    sess = _get_sess(event["channel"], thread)
    run_turn(say, client, event["channel"], thread, sess, (event.get("text") or "").strip())

@app.command("/model")
def cmd_model(ack, respond, command):
    ack()
    models = list_models()
    arg = (command.get("text") or "").strip()
    if arg:
        respond(text=f"Set model to `{arg}` — mention me in a thread with `model:{arg}` to apply, or DM me.")
    else:
        respond(text="*Hub models:*\n" + "\n".join(f"• `{m}`" for m in models[:60]))

if __name__ == "__main__":
    print(f"[slack-gateway] starting (Socket Mode) — OWUI={OWUI_BASE}, default model={DEFAULT_MODEL}", flush=True)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
