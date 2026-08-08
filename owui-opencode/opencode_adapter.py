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
import hmac
import json
import os
import queue
import threading
import time
import traceback
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from owui_mirror import Mirror, ff_write, ff_clear, ff_recover, tool_card

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

# FIX (2): per-conversation lock so two concurrent turns on the SAME chat serialise
# (they share one opencode session id and would otherwise race on /session/{sid}/message).
# Different conversations keep their own lock and stay concurrent. Mirrors owui-hermes.
_conv_locks = {}                   # chat_key -> threading.Lock
_conv_locks_guard = threading.Lock()


def _conv_lock(k):
    with _conv_locks_guard:
        lk = _conv_locks.get(k)
        if lk is None:
            lk = threading.Lock()
            _conv_locks[k] = lk
        return lk


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
    """Plain ``` fence with backticks NEUTRALIZED (-> U+02BB look-alike). Verified via screenshot that
    OWUI renders fenced code blocks (indented / <details> / <think> do NOT); neutralizing backticks
    means nothing in the output can close the fence early and invert the message (the parity bug)."""
    return "```\n" + text.replace("`", "ʻ") + "\n```"


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
def _system_of(messages):
    """Persona/system prompt (OWUI Models send a system message) → opencode native `system`."""
    return "\n\n".join(_text_of(m) for m in messages
                       if isinstance(m, dict) and m.get("role") == "system" and _text_of(m).strip())


def _abort(sid):
    """FIX (1): cancel an in-flight opencode turn (OWUI Stop / client disconnect)."""
    if not sid:
        return
    try:
        _open("POST", f"/session/{sid}/abort", {}, timeout=10).read()
    except Exception as e:
        print(f"[owui-opencode] abort failed sid={sid}: {e!r}", flush=True)


def stream_turn(chat_id, model, messages, sink=None):
    """Yield {'content': str} / {'heartbeat': True} / {'usage': {...}} for one turn.

    FIX (1): `sink` (optional dict) receives the resolved opencode session id under
    key 'sid' so the HTTP handler can POST /session/{sid}/abort on client disconnect.
    """
    text = _last_user(messages)
    _sys = _system_of(messages)
    if not text:
        yield {"content": "_(no user message)_"}
        return
    model_id = _model_id(model)
    chat_key = _chat_key(chat_id, messages)
    # FIX (2): serialise turns for the SAME conversation (shared session id).
    conv_lk = _conv_lock(chat_key)
    conv_lk.acquire()
    try:
        sid = _get_session(chat_key)
        if not sid:
            yield {"content": "_opencode: could not create a session._"}
            return
        if isinstance(sink, dict):     # FIX (1): expose sid for abort
            sink["sid"] = sid
        yield from _stream_turn_locked(sid, model_id, text, _sys)
    finally:
        conv_lk.release()


def _stream_turn_locked(sid, model_id, text, _sys):

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
            _body = {"model": {"providerID": PROVIDER, "modelID": model_id},
                     "parts": [{"type": "text", "text": text}]}
            if _sys:
                _body["system"] = _sys          # persona instructions (opencode native)
            post_result["msg"] = _json("POST", f"/session/{sid}/message", _body, timeout=ABS_TIMEOUT)
        except Exception as e:
            post_result["err"] = str(e)
        finally:
            post_result["done"] = True

    threading.Thread(target=_post, daemon=True).start()

    # 3) translate events -> OpenAI deltas
    seen_tools = {}            # partID -> True (header emitted)
    tool_done = set()          # partID (result emitted)
    got_text = False
    # FIX (4): route reasoning/thinking deltas into a collapsible block instead of dropping.
    part_types = {}            # partID -> announced part type ("reasoning"/"text"/…)
    reasoning_open = False     # whether a <details type="reasoning"> block is mid-stream
    # FIX (5): once session.idle lands, stop extending the turn and ignore late content.
    done_turn = False
    idle_deadline = time.monotonic() + IDLE_TIMEOUT
    abs_deadline = time.monotonic() + ABS_TIMEOUT
    last_beat = time.monotonic()

    def _close_reasoning():
        nonlocal reasoning_open
        if reasoning_open:
            reasoning_open = False
            return "\n</details>\n\n"
        return ""
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

            if not done_turn:                       # FIX (5): don't let late events extend a done turn
                idle_deadline = time.monotonic() + IDLE_TIMEOUT
            if o.get("__eof__"):
                break
            # Per-event processing is wrapped: a single malformed event is logged and SKIPPED
            # instead of killing the whole turn.
            try:
                typ = o.get("type")
                props = o.get("properties", {}) or {}
                if props.get("sessionID") != sid:
                    continue

                # FIX (3): opencode surfaced an error for this session — show it, end the turn.
                if typ == "session.error":
                    if done_turn:
                        continue
                    err = props.get("error") or props.get("message") or props.get("data") or props
                    if isinstance(err, (dict, list)):
                        err = json.dumps(err)
                    tail = _close_reasoning()
                    if tail:
                        yield {"content": tail}
                    yield {"content": f"\n\n_⚠️ opencode error: {str(err)[:400]}_"}
                    done_turn = True         # FIX (5): treat as terminal, like idle
                    break

                # FIX (5): after idle, ignore any late/stale content events for this turn.
                if done_turn:
                    continue

                if typ == "message.part.delta":
                    field = props.get("field")
                    pid = props.get("partID") or props.get("id") or ""
                    ptype = part_types.get(pid, "")
                    d = props.get("delta", "")
                    # FIX (4): reasoning/thinking deltas → collapsible block (was: dropped).
                    is_reasoning = field in ("reasoning", "thinking") or ptype in ("reasoning", "thinking")
                    if d and is_reasoning:
                        if not reasoning_open:
                            reasoning_open = True
                            yield {"content": '\n<details type="reasoning">\n<summary>Thinking</summary>\n\n'}
                        yield {"content": d}
                    elif d and field == "text":
                        tail = _close_reasoning()   # FIX (4): close reasoning before visible text
                        if tail:
                            yield {"content": tail}
                        got_text = True
                        yield {"content": d}

                elif typ == "message.part.updated":
                    part = props.get("part", {}) or {}
                    ptype = part.get("type")
                    pid = part.get("id") or part.get("callID") or ""
                    # FIX (4): an update announces the part type before its deltas — remember it.
                    if pid and ptype:
                        part_types[pid] = ptype
                    if ptype == "tool":
                        name = part.get("tool") or part.get("name") or "tool"
                        state = part.get("state", {}) or {}
                        status = state.get("status") or part.get("status")
                        if pid and pid not in seen_tools:
                            seen_tools[pid] = True   # native card is self-labeled; no header line needed
                        if pid and pid not in tool_done and status in ("completed", "error"):
                            tool_done.add(pid)
                            out = state.get("output") or ""
                            if isinstance(out, (dict, list)):
                                out = json.dumps(out)
                            out = (out or "").strip()
                            args = state.get("input") or state.get("args") or {}
                            tail = _close_reasoning()   # FIX (4): close reasoning before a tool card
                            if tail:
                                yield {"content": tail}
                            # native OWUI tool card (collapsible "View Result from <name>")
                            yield {"content": tool_card(name, args, out, status == "error")}

                elif typ == "session.idle":
                    done_turn = True            # FIX (5): mark terminal; stop extending the turn
                    tail = _close_reasoning()   # FIX (4): flush any open reasoning block
                    if tail:
                        yield {"content": tail}
                    break
            except Exception as ee:
                traceback.print_exc()
                print(f"[owui-opencode] skipped bad event: {ee!r}", flush=True)
                continue

        # FIX (4): if the loop broke (timeout/eof/cap) with a reasoning block still open, close it.
        tail = _close_reasoning()
        if tail:
            yield {"content": tail}

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
    # SECURITY (fail-CLOSED): empty ADAPTER_KEY denies ALL requests (never allow-all). Constant-time compare.
    if not ADAPTER_KEY:
        return False
    return hmac.compare_digest(handler.headers.get("Authorization", ""), f"Bearer {ADAPTER_KEY}")


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
        sink = {}                     # FIX (1): stream_turn deposits the opencode session id here
        gen = stream_turn(chat_id, model, messages, sink)
        aborted = [False]             # FIX (1): guard so we only abort once
        if mirror.on:  # FF4: record the in-flight run so a restart can re-issue it
            ff_write("opencode", {"cid": chat_id, "mid": mirror.mid, "jwt": mirror.jwt,
                                  "model": model, "messages": messages})
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
                    # FIX (1): client disconnected (OWUI Stop) and no FF mirror is detached to
                    # keep the run alive → cancel the opencode turn so it doesn't keep running.
                    if not aborted[0]:
                        aborted[0] = True
                        _abort(sink.get("sid"))
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
            # FIX (1): connection dropped mid-write → abort the turn unless a FF mirror keeps it alive.
            if not mirror.on and not aborted[0]:
                aborted[0] = True
                _abort(sink.get("sid"))
            gen.close()
        except Exception as e:
            traceback.print_exc()
            print(f"[owui-opencode] stream error: {e!r}", flush=True)   # never swallow silently
            note = (f"\n\n_⚠️ adapter hiccup ({type(e).__name__}: {str(e)[:160]}). "
                    f"Partial result is above — resend to continue._")
            full[0] += note
            try:
                send({"content": note}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
            except Exception:
                pass
            try:
                mirror.done(full[0])   # preserve whatever was generated (FF)
            except Exception:
                pass
        finally:
            # FIX (1): safety net — if the client is gone with no FF mirror and we never aborted, do so now.
            if gone[0] and not mirror.on and not aborted[0]:
                aborted[0] = True
                _abort(sink.get("sid"))
            if mirror.on:  # FF4: run finished (or errored) → drop the durable record
                ff_clear("opencode", chat_id, mirror.mid)


def _ff_run(rec):  # FF4: re-issue an interrupted run; yield its text chunks
    for d in stream_turn(rec["cid"], rec.get("model"), rec["messages"]):
        if d.get("content"):
            yield d["content"]


def main():
    # SECURITY: refuse to start unauthenticated — this adapter drives a host-shell-capable agent.
    if not ADAPTER_KEY:
        raise SystemExit("[owui-opencode] FATAL: ADAPTER_KEY is empty — refusing to start (would be unauthenticated RCE).")
    print(f"[owui-opencode] adapter on 0.0.0.0:{PORT} -> {OC} (auth=on, models={MODELS})", flush=True)
    threading.Thread(target=lambda: ff_recover("opencode", _ff_run), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
