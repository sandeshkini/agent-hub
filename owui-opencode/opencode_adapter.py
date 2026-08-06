#!/usr/bin/env python3
"""owui-opencode — OpenAI-compatible adapter over a running `opencode serve`.

Fronts opencode's REST + global `/event` SSE stream (the "front the running engine"
pattern, same as owui-hermes). Real token streaming, visible tool calls, one opencode
session per OWUI chat_id. Auth to the adapter via ADAPTER_KEY (Bearer).

opencode API used:
  POST /session                      -> {id}                      (create session)
  POST /session/{id}/message         -> final assistant message   (drives one turn; blocks)
  GET  /event                        -> SSE: message.part.delta{field,delta} / session.idle / ...
  POST /session/{id}/abort           -> cancel (OWUI Stop)
Model id in OWUI is "opencode/<modelID>"; we map to {providerID, modelID}.
"""
import json
import os
import queue
import threading
import time
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from owui_mirror import Mirror

OC = os.getenv("OPENCODE_BASE", "http://opencode:4096").rstrip("/")
ADAPTER_KEY = os.getenv("ADAPTER_KEY", "")
PORT = int(os.getenv("PORT", "9213"))
PROVIDER = os.getenv("OPENCODE_PROVIDER", "opencode")
MODELS = [m.strip() for m in os.getenv(
    "OPENCODE_MODELS", "deepseek-v4-flash-free,glm-5.2,claude-sonnet-4-6,kimi-k2.6"
).split(",") if m.strip()]
DEFAULT_MODEL = os.getenv("OPENCODE_MODEL", MODELS[0] if MODELS else "deepseek-v4-flash-free")

IDLE_TIMEOUT = float(os.getenv("IDLE_TIMEOUT", "300"))    # end turn after N s w/ no event
ABS_TIMEOUT = float(os.getenv("ABS_TIMEOUT", "1800"))     # hard safety cap
HEARTBEAT_EVERY = 10.0
TOOL_OUTPUT_CAP = int(os.getenv("TOOL_OUTPUT_CAP", "4000"))
CACHE_MAX = 512

_sessions = OrderedDict()          # chat_key -> opencode session id (LRU)
_sess_lock = threading.Lock()


# ── opencode REST helpers ─────────────────────────────────────────────
def _open(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OC + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _json(method, path, body=None, timeout=60):
    return json.loads(_open(method, path, body, timeout).read().decode())


# ── message helpers ───────────────────────────────────────────────────
def _text_of(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c
                        if isinstance(p, dict) and p.get("type") in (None, "text"))
    return c or ""


def _last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return _text_of(m)
    return ""


def _model_id(owui_model):
    m = owui_model or DEFAULT_MODEL
    if m.startswith("opencode/"):
        m = m[len("opencode/"):]
    return m or DEFAULT_MODEL


def _fence(text):
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    bt = "`" * max(3, longest + 1)
    return f"{bt}\n{text}\n{bt}"


def _chat_key(chat_id, messages):
    if chat_id:
        return "id:" + str(chat_id)
    import hashlib
    for m in messages:
        if m.get("role") == "user":
            return "h:" + hashlib.sha1(_text_of(m).encode()).hexdigest()[:16]
    return "h:default"


def _get_session(chat_key):
    with _sess_lock:
        sid = _sessions.get(chat_key)
        if sid:
            _sessions.move_to_end(chat_key)
            return sid
    info = _json("POST", "/session", {})          # create outside the lock
    sid = info.get("id")
    if sid:
        with _sess_lock:
            _sessions[chat_key] = sid
            while len(_sessions) > CACHE_MAX:
                _sessions.popitem(last=False)
    return sid


# ── the turn ──────────────────────────────────────────────────────────
def stream_turn(chat_id, model, messages):
    """Yield {'content': str} / {'heartbeat': True} / {'usage': {...}} for one turn."""
    text = _last_user(messages)
    if not text:
        yield {"content": "_(no user message)_"}
        return
    model_id = _model_id(model)
    chat_key = _chat_key(chat_id, messages)
    sid = _get_session(chat_key)
    if not sid:
        yield {"content": "_opencode: could not create a session._"}
        return

    # 1) open the global event stream BEFORE posting (so we miss no deltas)
    try:
        ev = _open("GET", "/event", timeout=ABS_TIMEOUT)
    except Exception as e:
        yield {"content": f"_opencode: event stream failed ({e})._"}
        return

    q = queue.Queue()
    stop = threading.Event()

    def _reader():
        try:
            for raw in ev:
                if stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
                if line.startswith("data:"):
                    try:
                        q.put(json.loads(line[5:].strip()))
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            q.put({"__eof__": True})

    threading.Thread(target=_reader, daemon=True).start()

    # 2) post the message in a thread (blocks until the turn completes)
    post_result = {}

    def _post():
        try:
            post_result["msg"] = _json(
                "POST", f"/session/{sid}/message",
                {"model": {"providerID": PROVIDER, "modelID": model_id},
                 "parts": [{"type": "text", "text": text}]},
                timeout=ABS_TIMEOUT)
        except Exception as e:
            post_result["err"] = str(e)
        finally:
            post_result["done"] = True

    threading.Thread(target=_post, daemon=True).start()

    # 3) translate events -> OpenAI deltas
    seen_tools = {}            # partID -> True (header emitted)
    tool_done = set()          # partID (result emitted)
    got_text = False
    idle_deadline = time.monotonic() + IDLE_TIMEOUT
    abs_deadline = time.monotonic() + ABS_TIMEOUT
    last_beat = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now > abs_deadline:
                yield {"content": "\n\n_⏱ turn hit the safety time cap._"}
                break
            if now > idle_deadline:
                if post_result.get("err"):
                    yield {"content": f"\n_opencode error: {post_result['err'][:300]}_"}
                elif not got_text:
                    yield {"content": "\n_⏱ no output; ending._"}
                break
            try:
                o = q.get(timeout=1)
            except queue.Empty:
                if post_result.get("done") and post_result.get("err") and not got_text:
                    yield {"content": f"\n_opencode error: {post_result['err'][:300]}_"}
                    break
                if time.monotonic() - last_beat > HEARTBEAT_EVERY:
                    last_beat = time.monotonic()
                    yield {"heartbeat": True}
                continue

            idle_deadline = time.monotonic() + IDLE_TIMEOUT
            if o.get("__eof__"):
                break
            typ = o.get("type")
            props = o.get("properties", {}) or {}
            if props.get("sessionID") != sid:
                continue

            if typ == "message.part.delta" and props.get("field") == "text":
                d = props.get("delta", "")
                if d:
                    got_text = True
                    yield {"content": d}

            elif typ == "message.part.updated":
                part = props.get("part", {}) or {}
                if part.get("type") == "tool":
                    pid = part.get("id") or part.get("callID") or ""
                    name = part.get("tool") or part.get("name") or "tool"
                    state = part.get("state", {}) or {}
                    status = state.get("status") or part.get("status")
                    if pid and pid not in seen_tools:
                        seen_tools[pid] = True
                        title = state.get("title") or ""
                        yield {"content": f"\n\n🔧 **{name}**{(' · ' + title) if title else ''}\n"}
                    if pid and pid not in tool_done and status in ("completed", "error"):
                        tool_done.add(pid)
                        out = state.get("output") or ""
                        if isinstance(out, (dict, list)):
                            out = json.dumps(out)
                        out = (out or "").strip()
                        if len(out) > TOOL_OUTPUT_CAP:
                            out = out[:TOOL_OUTPUT_CAP] + "\n…(truncated)"
                        mark = "⚠️" if status == "error" else "✅"
                        yield {"content": (_fence(out) + "\n" if out else "") + mark + "\n"}

            elif typ == "session.idle":
                break

        # usage from the final message, if available
        msg = post_result.get("msg") or {}
        tok = ((msg.get("info") or {}).get("tokens")) if isinstance(msg, dict) else None
        if isinstance(tok, dict):
            yield {"usage": {"prompt_tokens": tok.get("input", 0),
                             "completion_tokens": tok.get("output", 0),
                             "total_tokens": tok.get("total", 0)}}
    finally:
        stop.set()
        try:
            ev.close()
        except Exception:
            pass


# ── HTTP (OpenAI-compatible) ──────────────────────────────────────────
def _authed(handler):
    return not ADAPTER_KEY or handler.headers.get("Authorization") == f"Bearer {ADAPTER_KEY}"


def _openai_usage(u):
    return {"prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": u.get("completion_tokens", 0),
            "total_tokens": u.get("total_tokens", 0)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send_json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            if not _authed(self):
                return self._send_json(401, {"error": "unauthorized"})
            data = [{"id": f"opencode/{m}", "object": "model", "owned_by": "opencode"} for m in MODELS]
            return self._send_json(200, {"object": "list", "data": data})
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            return self._send_json(404, {"error": "not found"})
        if not _authed(self):
            return self._send_json(401, {"error": "unauthorized"})
        ln = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = json.loads(self.rfile.read(ln) or "{}")
        except Exception as e:
            return self._send_json(400, {"error": str(e)})

        messages = body.get("messages", [])
        stream = bool(body.get("stream"))
        model = body.get("model") or f"opencode/{DEFAULT_MODEL}"
        chat_id = self.headers.get("X-OpenWebUI-Chat-Id") or body.get("chat_id")
        cid = "chatcmpl-opencode"
        created = int(time.time())

        if not stream:
            parts, usage = [], None
            try:
                for d in stream_turn(chat_id, model, messages):
                    if d.get("content"):
                        parts.append(d["content"])
                    elif d.get("usage"):
                        usage = _openai_usage(d["usage"])
            except Exception as e:
                parts.append(f"\n_adapter error: {e}_")
            return self._send_json(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(parts)},
                             "finish_reason": "stop"}],
                "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

        # streaming SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(delta, finish=None):
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()

        # native fire-and-forget: mirror into the OWUI chat; client disconnect DETACHES the run.
        mirror = Mirror(chat_id, self.headers.get("X-OpenWebUI-Message-Id"),
                        self.headers.get("X-OpenWebUI-User-Jwt"))
        gen = stream_turn(chat_id, model, messages)
        gone = [False]
        full = [""]
        def w(fn):
            if gone[0]:
                return
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                gone[0] = True
        def _usage(u):
            self.wfile.write(("data: " + json.dumps({"id": cid, "object": "chat.completion.chunk",
                "created": created, "model": model, "choices": [], "usage": u}) + "\n\n").encode())
            self.wfile.flush()
        try:
            w(lambda: send({"role": "assistant"}))
            for d in gen:
                if gone[0] and not mirror.on:
                    gen.close()
                    break
                if d.get("content"):
                    full[0] += d["content"]
                    c = d["content"]
                    w(lambda: send({"content": c}))
                    mirror.update(full[0])
                elif d.get("usage"):
                    u = _openai_usage(d["usage"])
                    w(lambda: _usage(u))
            mirror.done(full[0])
            w(lambda: send({}, "stop"))
            w(lambda: (self.wfile.write(b"data: [DONE]\n\n"), self.wfile.flush()))
        except (BrokenPipeError, ConnectionResetError):
            gen.close()
        except Exception as e:
            try:
                send({"content": f"\n_adapter error: {e}_"}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
            except Exception:
                pass


def main():
    print(f"[owui-opencode] adapter on 0.0.0.0:{PORT} -> {OC} "
          f"(auth={'on' if ADAPTER_KEY else 'off'}, models={MODELS})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
