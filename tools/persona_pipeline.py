#!/usr/bin/env python3
"""
persona_pipeline.py — autonomous multi-persona hand-off over OWUI Channels.

Why this exists: OWUI Channels natively support "@mention a persona -> it replies
in-thread" and "reply to a persona -> it continues", but the trigger fires ONLY on a
user-posted message (routers/channels.py: model_response_handler is called from
post_new_message, NOT from a model's own reply). So a persona's output does NOT
auto-trigger the next persona. This tiny orchestrator closes that gap: it posts each
hand-off message itself and waits for the reply, giving autonomous A->B->C chaining
while every step still lands natively in the OWUI channel thread (visible, persisted).

Usage:
  persona_pipeline.py "researcher,coder,scribe" "Draft a note about X and save it"
  persona_pipeline.py --channel <id> "researcher,scribe" "..."   # reuse a channel
  OWUI_BASE (default http://localhost:3000) env overrides the base URL.

Each persona is an OWUI Model id (researcher/coder/operator/scribe/...). The chain runs
in ONE thread so each persona sees the full prior context automatically (OWUI builds the
thread history into the model's system prompt). Prints the transcript; the same thread is
viewable in OWUI under the channel.
"""
import json, os, sys, time, urllib.request, urllib.error

BASE = os.environ.get("OWUI_BASE", "http://localhost:3000").rstrip("/")
CHANNEL_NAME = "agents"
POLL_SECS = 3
TIMEOUT_SECS = 300  # per persona step


def _req(method, path, tok=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else None


def admin_token():
    d = _req("POST", "/api/v1/auths/signin", body={"email": "admin@localhost", "password": ""})
    return d["token"]


def ensure_channel(tok):
    chans = _req("GET", "/api/v1/channels/", tok=tok) or []
    for c in chans:
        if c.get("name") == CHANNEL_NAME:
            return c["id"]
    c = _req("POST", "/api/v1/channels/create", tok=tok, body={
        "name": CHANNEL_NAME,
        "description": "Multi-agent workspace (autonomous persona pipeline)",
    })
    return c["id"]


def post(tok, cid, content, parent_id=None):
    body = {"content": content, "data": {}, "meta": {}}
    if parent_id:
        body["parent_id"] = parent_id
    return _req("POST", f"/api/v1/channels/{cid}/messages/post", tok=tok, body=body)


def wait_for_reply(tok, cid, root_id, model_id, after_ns):
    """Poll the thread until model_id posts a done reply created after after_ns."""
    deadline = time.time() + TIMEOUT_SECS
    while time.time() < deadline:
        thread = _req("GET", f"/api/v1/channels/{cid}/messages/{root_id}/thread", tok=tok) or []
        cand = [m for m in thread
                if (m.get("meta") or {}).get("model_id") == model_id
                and m.get("created_at", 0) > after_ns
                and (m.get("meta") or {}).get("done")]
        if cand:
            cand.sort(key=lambda m: m.get("created_at", 0))
            return cand[-1]
        time.sleep(POLL_SECS)
    return None


def main():
    args = sys.argv[1:]
    channel_override = None
    if args and args[0] == "--channel":
        channel_override = args[1]
        args = args[2:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    personas = [p.strip() for p in args[0].split(",") if p.strip()]
    task = args[1]

    tok = admin_token()
    cid = channel_override or ensure_channel(tok)
    print(f"channel={cid}  personas={personas}\n")

    root_id = None
    last_output = None
    for i, persona in enumerate(personas):
        if i == 0:
            directive = f"<@M:{persona}|{persona}> {task}"
        else:
            directive = (f"<@M:{persona}|{persona}> Continue the work above. "
                         f"Use the previous message(s) in this thread as your input. Original task: {task}")
        before = int(time.time() * 1e9)
        msg = post(tok, cid, directive, parent_id=root_id)
        if root_id is None:
            root_id = msg["id"]  # first message roots the thread
        print(f"--> [{persona}] triggered")
        reply = wait_for_reply(tok, cid, root_id, persona, before)
        if not reply:
            print(f"!!! [{persona}] no reply within {TIMEOUT_SECS}s — aborting")
            sys.exit(2)
        last_output = (reply.get("content") or "").strip()
        print(f"<-- [{persona}] {last_output[:400]}\n")

    print("=" * 60)
    print(f"pipeline done. thread: {BASE}  channel {cid}")
    print(f"final output ({personas[-1]}):\n{last_output}")


if __name__ == "__main__":
    main()
