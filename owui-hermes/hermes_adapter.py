#!/usr/bin/env python3
"""owui-hermes v2 — hardened OpenAI-compatible adapter over the RUNNING Hermes
dashboard (:9119) via its /api/ws JSON-RPC protocol.

Gives Open WebUI real token streaming + visible tool activity using the one
Hermes (glm-5.2, skills, memory, computer-use, guardrail). No per-message spawn.

v2 hardening (from a 3-subagent review of v1):
- Conversation identity = OWUI chat_id (X-OpenWebUI-Chat-Id header / body metadata),
  NOT a hash of the first user message (which cross-wired chats that opened the same way).
- No lost tokens: prompt.submit is fire-and-forget; a SINGLE reader loop dispatches
  RPC acks (by id) vs events — nothing is discarded (v1's rpc() ate early deltas).
- Idle-timeout that RESETS on every event (long agentic turns no longer truncated at 240s);
  SSE heartbeats during silence; absolute safety cap.
- Per-conversation lock serialises concurrent turns for the same chat (multi-tab/double-send).
- Safe session creation (never caches a None id); bounded LRU session cache.
- message.complete.text fallback if no deltas streamed; usage + finish_reason surfaced.
- Graceful errors (WS/ticket failure yields a clean message, not a 500).
- Tool output: larger cap + backtick-safe fencing.
- Optional adapter auth (ADAPTER_KEY) so only OWUI can drive it; creds via env.
"""
import hashlib
import json
import os
import threading
import time
import traceback
import urllib.request
import http.cookiejar
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from websockets.sync.client import connect as ws_connect
from owui_mirror import Mirror, ff_write, ff_clear, ff_recover, tool_card

BASE = os.getenv("HERMES_BASE", "http://127.0.0.1:9119")
USER = os.getenv("HERMES_DASH_USER", "sandesh")
PW = os.getenv("HERMES_DASH_PW", "hermesluna")
ADAPTER_KEY = os.getenv("ADAPTER_KEY", "")          # if set, require Bearer <key>
PORT = int(os.getenv("PORT", "9211"))
MODEL_ID = "hermes"

IDLE_TIMEOUT = float(os.getenv("IDLE_TIMEOUT", "180"))   # end turn after N s w/ no event
ABS_TIMEOUT = float(os.getenv("ABS_TIMEOUT", "1800"))    # hard safety cap
TOOL_OUTPUT_CAP = int(os.getenv("TOOL_OUTPUT_CAP", "4000"))
HEARTBEAT_EVERY = 10.0
CACHE_MAX = 512

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_http_lock = threading.RLock()          # guards all _opener use (cookie jar not thread-safe)
_last_login = 0.0

_sessions = OrderedDict()               # conv_key -> session_id (LRU)
_sessions_lock = threading.Lock()
_conv_locks = {}                        # conv_key -> Lock (serialise per conversation)
_conv_locks_guard = threading.Lock()


# ── auth ──────────────────────────────────────────────────────────────
def _login():
    global _last_login
    with _http_lock:
        _opener.open(urllib.request.Request(
            BASE + "/auth/password-login",
            data=json.dumps({"provider": "basic", "username": USER,
                             "password": PW, "next": "/"}).encode(),
            headers={"Content-Type": "application/json"}), timeout=15).read()
        _last_login = time.monotonic()


def _ticket():
    """Mint a single-use 30s ws ticket; re-login once (coalesced) on failure."""
    with _http_lock:
        try:
            r = _opener.open(urllib.request.Request(
                BASE + "/api/auth/ws-ticket", data=b"", method="POST"), timeout=15)
            return json.loads(r.read().decode())["ticket"]
        except Exception:
            pass
    # only one thread actually re-logins; others reuse the fresh cookie
    with _http_lock:
        if time.monotonic() - _last_login > 2:
            _login()
        r = _opener.open(urllib.request.Request(
            BASE + "/api/auth/ws-ticket", data=b"", method="POST"), timeout=15)
        return json.loads(r.read().decode())["ticket"]


# ── session cache + per-conv lock ─────────────────────────────────────
def _cache_get(k):
    with _sessions_lock:
        if k in _sessions:
            _sessions.move_to_end(k)
            return _sessions[k]
    return None


def _cache_put(k, v):
    if not v:
        return
    with _sessions_lock:
        _sessions[k] = v
        _sessions.move_to_end(k)
        while len(_sessions) > CACHE_MAX:
            _sessions.popitem(last=False)


def _cache_drop(k):
    with _sessions_lock:
        _sessions.pop(k, None)


def _conv_lock(k):
    with _conv_locks_guard:
        lk = _conv_locks.get(k)
        if lk is None:
            lk = threading.Lock()
            _conv_locks[k] = lk
        return lk


# ── message helpers ───────────────────────────────────────────────────
def _text_of(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") in (None, "text"))
    return c or ""


def _last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return _text_of(m)
    return ""


def _images_of(m):
    """Extract data-URL images from an OpenAI multimodal message's content parts."""
    c = m.get("content")
    if not isinstance(c, list):
        return []
    out = []
    for p in c:
        if isinstance(p, dict) and p.get("type") == "image_url":
            iu = p.get("image_url")
            url = iu.get("url") if isinstance(iu, dict) else iu
            if isinstance(url, str) and url.startswith("data:image/"):
                out.append(url)
    return out


def _last_user_images(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return _images_of(m)
    return []


def _attach_images(conn, sid, images):
    """Queue each data-URL image onto the Hermes session via image.attach_bytes
    (an RPC made pre-stream, so it's safe to wait for the ack). Hermes then
    pre-analyzes them with auxiliary.vision (local Ollama VLM) per image_input_mode."""
    n = 0
    for url in images:
        try:
            r = conn.rpc("image.attach_bytes", {"session_id": sid, "content_base64": url}, timeout=45)
            if isinstance(r.get("result"), dict) and r["result"].get("attached"):
                n += 1
        except Exception:
            pass
    return n


def _conv_key(chat_id, messages):
    if chat_id:
        return "id:" + str(chat_id)
    for m in messages:
        if m.get("role") == "user":
            return "h:" + hashlib.sha1(_text_of(m).encode()).hexdigest()[:16]
    return "h:default"


def _fence(text):
    """Plain ``` fence with backticks NEUTRALIZED (-> U+02BB look-alike). Verified via screenshot that
    OWUI renders fenced code blocks (indented code / <details> / <think> do NOT render reliably);
    neutralizing backticks means nothing in the tool output can close the fence early and invert the
    rest of the message (the parity bug)."""
    return "```\n" + text.replace("`", "ʻ") + "\n```"


# ── websocket client ──────────────────────────────────────────────────
class WS:
    def __init__(self):
        ws_base = BASE.replace("https://", "wss://").replace("http://", "ws://")
        self.ws = ws_connect(f"{ws_base}/api/ws?ticket={_ticket()}",
                             open_timeout=15, max_size=None,
                             additional_headers={"Origin": BASE})
        self._id = 0
        # drain until gateway.ready (or timeout)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 6:
            f = self.recv(2)
            if f and f.get("method") == "event" and (f.get("params") or {}).get("type") == "gateway.ready":
                break
            if f is None:
                continue

    def recv(self, timeout):
        try:
            return json.loads(self.ws.recv(timeout=timeout))
        except TimeoutError:
            return None
        except Exception:
            raise            # ConnectionClosed etc. — let caller handle

    def send(self, method, params):
        self._id += 1
        rid = f"r{self._id}"
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        return rid

    def rpc(self, method, params, timeout=30):
        """For calls made BEFORE a turn is streaming (safe: no events in flight)."""
        rid = self.send(method, params)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            f = self.recv(timeout=3)
            if f is None:
                continue
            if f.get("id") == rid:
                return f
        return {}

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _hermes_port():
    return BASE.rsplit(":", 1)[-1].split("/")[0]


def _transcript(messages):
    """Prior turns (all but the trailing user message) as a plain transcript,
    used to prime a freshly-created Hermes session after a restart (#2)."""
    idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    stop = idxs[-1] if idxs else len(messages)
    lines = []
    for m in messages[:stop]:
        role = m.get("role")
        if role == "system":
            continue
        txt = _text_of(m).strip()
        if txt:
            lines.append(("User: " if role == "user" else "Assistant: ") + txt)
    return "\n".join(lines)


# ── the turn ──────────────────────────────────────────────────────────
def _system_of(messages):
    """Persona/system prompt (OWUI Models send a system message) → forward to Hermes."""
    return "\n\n".join(_text_of(m) for m in messages
                       if isinstance(m, dict) and m.get("role") == "system" and _text_of(m).strip())


def stream_turn(chat_id, messages):
    """Yield dicts: {'content': str} or {'heartbeat': True}. One turn."""
    conv = _conv_key(chat_id, messages)
    last = _last_user(messages)
    images = _last_user_images(messages)
    if not last and not images:
        yield {"content": "_(no user message)_"}
        return
    if images and not last:
        last = "Please analyze the attached image(s)."
    _sys = _system_of(messages)
    if _sys:
        last = f"{_sys}\n\n{last}"          # persona instructions

    with _conv_lock(conv):                       # serialise turns for this chat
        try:
            conn = WS()
        except Exception as e:
            yield {"content": f"\n_Couldn't reach Hermes ({e}). Is the hermes-dashboard service up?_"}
            return
        sid = None
        completed = False
        try:
            sid = _cache_get(conv)
            created = False
            if not sid:
                sid = _create_session(conn)
                if not sid:
                    yield {"content": "_Hermes: could not create a session._"}
                    return
                _cache_put(conv, sid)
                created = True

            # #2 restart resilience: if we just made a fresh session but OWUI has
            # prior turns (adapter/Hermes restarted mid-chat), prime it with context.
            prior = _transcript(messages) if created else ""
            if prior:
                text = (f"[Continuing an earlier conversation — context so far:\n"
                        f"{prior}\n\nThe user now says:]\n{last}")
            else:
                text = last

            if images:
                _attach_images(conn, sid, images)

            submit_rid = conn.send("prompt.submit", {"session_id": sid, "text": text})
            got_delta = False
            last_tool = {}
            complete_text = ""
            usage = None
            reasoning_buf = ""       # FIX: accumulate thinking/reasoning deltas
            reasoning_open = False   # FIX: whether a reasoning block is mid-stream
            idle_deadline = time.monotonic() + IDLE_TIMEOUT
            abs_deadline = time.monotonic() + ABS_TIMEOUT
            last_beat = time.monotonic()

            while True:
                now = time.monotonic()
                if now > abs_deadline:
                    yield {"content": "\n\n_⏱ turn hit the safety time cap; it may still be running in Hermes._"}
                    break
                if now > idle_deadline:
                    yield {"content": "\n\n_⏱ no output for a while; ending. Hermes may still be working._"}
                    break
                try:
                    f = conn.recv(timeout=3)
                except Exception:
                    yield {"content": "\n\n_connection to Hermes dropped._"}
                    break
                if f is None:
                    if time.monotonic() - last_beat > HEARTBEAT_EVERY:
                        last_beat = time.monotonic()
                        yield {"heartbeat": True}
                    continue

                idle_deadline = time.monotonic() + IDLE_TIMEOUT   # reset on any frame

                # Per-frame processing is wrapped: a single malformed/unexpected frame (e.g. a failed
                # computer_use payload) is logged and SKIPPED instead of killing the whole turn.
                try:
                    if f.get("id") == submit_rid:
                        if f.get("error"):                            # stale/expired session -> recreate once
                            _cache_drop(conv)
                            sid = _create_session(conn)
                            if not sid:
                                yield {"content": "_Hermes: session expired and could not be recreated._"}
                                break
                            _cache_put(conv, sid)
                            if images:
                                _attach_images(conn, sid, images)
                            submit_rid = conn.send("prompt.submit", {"session_id": sid, "text": text})
                        continue

                    if f.get("method") != "event":
                        continue
                    p = f.get("params") or {}
                    t = p.get("type")
                    pay = p.get("payload") or {}

                    if t in ("tool.start", "tool.complete", "tool.progress") and os.getenv("HERMES_TOOL_DEBUG"):
                        import sys as _sys
                        print(f"[tooldbg] {t} keys={list(pay.keys())} pay={json.dumps(pay)[:900]}", file=_sys.stderr, flush=True)

                    # FIX: accumulate Hermes reasoning/thinking deltas (both event
                    # names can occur); do NOT emit yet — buffer until real content
                    # arrives, then flush as ONE collapsible OWUI reasoning block.
                    if t in ("thinking.delta", "reasoning.delta"):
                        rtext = pay.get("text") or pay.get("delta") or pay.get("reasoning") or ""
                        if rtext:
                            reasoning_buf += rtext
                            reasoning_open = True
                        continue

                    # FIX: flush any buffered reasoning as a collapsible block before
                    # emitting the next visible content/tool output. Reuse _fence() so
                    # backticks/HTML inside the reasoning can't break OWUI rendering.
                    if reasoning_open and t in ("message.delta", "tool.start",
                                                "tool.complete", "message.complete"):
                        if reasoning_buf.strip():
                            yield {"content": '<details type="reasoning">\n'
                                              "<summary>Thinking</summary>\n"
                                              + _fence(reasoning_buf.strip())
                                              + "\n</details>\n\n"}
                        reasoning_buf = ""
                        reasoning_open = False

                    if t == "message.delta":
                        if pay.get("text"):
                            got_delta = True
                            yield {"content": pay["text"]}
                    # FIX: surface intermediate tool progress (downloading…/processing…)
                    # as a short italic line so it isn't silently dropped.
                    elif t == "tool.progress":
                        ptxt = (pay.get("text") or pay.get("message")
                                or pay.get("status") or pay.get("detail") or "")
                        if ptxt:
                            yield {"content": f"\n_{str(ptxt)[:300]}_\n"}
                    elif t == "tool.start":
                        # tool.start only carries a human `context` preview (often EMPTY, e.g.
                        # computer_use). The REAL structured args arrive on tool.complete.
                        last_tool = {"name": pay.get("name", "tool"), "args": pay.get("context") or {}}
                    elif t == "tool.complete":
                        # Runtime payload (verified live): {tool_id, name, args, duration_s, result}.
                        # INPUT = the structured `args` (tool.start `context` is a preview / empty).
                        # OUTPUT `result` shape VARIES by tool, so never assume one field:
                        #   terminal      -> {output, exit_code, error}
                        #   computer_use  -> {summary, vision_analysis, app, width, height, elements, ...}
                        # Try output -> summary/vision_analysis/result_text/text -> slim JSON; never blank.
                        args = pay.get("args") or last_tool.get("args") or {}
                        res = pay.get("result")
                        out, err = "", False
                        if isinstance(res, dict):
                            out = res.get("output")
                            if out is None:
                                out = "\n".join(str(x) for x in (
                                    res.get("summary"), res.get("vision_analysis"),
                                    res.get("result_text"), res.get("text")) if x)
                            if not out:
                                slim = {k: v for k, v in res.items()
                                        if k != "elements" and not (isinstance(v, str) and len(v) > 2000)}
                                out = json.dumps(slim, ensure_ascii=False)
                            e, ec = res.get("error"), res.get("exit_code")
                            err = bool(e) or (ec not in (None, 0))
                            if e:
                                out = (str(out) + "\n" if out else "") + str(e)
                        elif res is not None:
                            out = str(res)
                        out = ("" if out is None else str(out)).rstrip()
                        name = pay.get("name") or last_tool.get("name", "tool")
                        # native OWUI tool card (collapsible "View Result from <name>")
                        yield {"content": tool_card(name, args, out, err)}
                        last_tool = {}
                    elif t == "error":
                        msg = pay.get("message") if isinstance(pay, dict) else None
                        yield {"content": f"\n\n_error: {str(msg or pay)[:400]}_"}
                        break
                    elif t == "message.complete":
                        complete_text = pay.get("text", "")
                        if not got_delta and complete_text:      # deltas missing -> use final text
                            yield {"content": complete_text}
                        u = pay.get("usage") or {}
                        if u:
                            yield {"usage": u}                    # #4 token usage
                        completed = True
                        break
                    # FIX: defensive — surface guardrail denials so a blocked command
                    # isn't a silent no-op. Match any event type mentioning guard/denied/
                    # blocked and pull a human reason from common payload fields.
                    elif isinstance(t, str) and any(
                            k in t.lower() for k in ("guard", "denied", "blocked")):
                        reason = (pay.get("reason") or pay.get("message")
                                  or pay.get("detail") or "blocked by guardrail")
                        yield {"content": f"\n> ⚠️ {str(reason)[:400]}\n"}
                except Exception as fe:
                    traceback.print_exc()
                    print(f"[owui-hermes] skipped bad frame: {fe!r}", flush=True)
                    continue
        finally:
            # #3 client aborted (OWUI Stop) or errored -> best-effort cancel the Hermes turn.
            if sid and not completed:
                for m in ("prompt.cancel", "prompt.interrupt", "session.interrupt"):
                    try:
                        conn.send(m, {"session_id": sid})
                    except Exception:
                        pass
            conn.close()


def _create_session(conn):
    resp = conn.rpc("session.create", {"cols": 80, "source": "owui"})
    r = resp.get("result") or {}
    return r.get("session_id") or r.get("id")


def _openai_usage(u):
    """Map Hermes message.complete usage -> OpenAI usage block (#4)."""
    return {
        "prompt_tokens": u.get("prompt") or u.get("input") or 0,
        "completion_tokens": u.get("completion") or u.get("output") or 0,
        "total_tokens": u.get("total") or 0,
    }


# ── HTTP (OpenAI-compatible) ──────────────────────────────────────────
def _chat_id_from(handler, body):
    cid = handler.headers.get("X-OpenWebUI-Chat-Id")
    if cid:
        return cid
    md = body.get("metadata") or {}
    return md.get("chat_id") or body.get("chat_id")


class H(BaseHTTPRequestHandler):
    # HTTP/1.0 (default): the connection closes at end-of-response, which is the
    # unambiguous "stream done" signal for OWUI. (HTTP/1.1 keep-alive without
    # chunked encoding left the turn looking "still generating" → messages queued.)

    def log_message(self, *a):
        pass

    def _authed(self):
        if not ADAPTER_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {ADAPTER_KEY}"

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "hermes"}]})
        elif "/health" in self.path:
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        if "chat/completions" not in self.path:
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or "{}")
        except Exception as e:
            self._json(400, {"error": str(e)})
            return
        messages = body.get("messages", [])
        stream = bool(body.get("stream"))
        chat_id = _chat_id_from(self, body)
        created = int(time.time())
        cid = "chatcmpl-hermes"

        if not stream:
            parts = []
            usage_obj = None
            try:
                for d in stream_turn(chat_id, messages):
                    if d.get("content"):
                        parts.append(d["content"])
                    elif d.get("usage"):
                        usage_obj = _openai_usage(d["usage"])
            except Exception as e:
                parts.append(f"\n_adapter error: {e}_")
            self._json(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(parts)},
                             "finish_reason": "stop"}],
                "usage": usage_obj or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def raw(s):
            self.wfile.write(s.encode())
            self.wfile.flush()

        def send(delta, finish=None):
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                     "model": MODEL_ID,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            raw(f"data: {json.dumps(chunk)}\n\n")

        gen = stream_turn(chat_id, messages)
        # native fire-and-forget: mirror into the OWUI chat; a client disconnect
        # DETACHES the run (keep consuming + mirroring) instead of aborting.
        mirror = Mirror(chat_id, self.headers.get("X-OpenWebUI-Message-Id"),
                        self.headers.get("X-OpenWebUI-User-Jwt"))
        if mirror.on:  # FF4: record the in-flight run so a restart can re-issue it
            ff_write("hermes", {"cid": chat_id, "mid": mirror.mid, "jwt": mirror.jwt, "messages": messages})
        gone = [False]
        full = [""]
        def w(fn):
            if gone[0]:
                return
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                gone[0] = True                   # client left; keep running if mirroring
        try:
            w(lambda: send({"role": "assistant"}))
            for d in gen:
                if gone[0] and not mirror.on:
                    gen.close()
                    break                        # no chat to mirror to -> abort like before
                if d.get("heartbeat"):
                    w(lambda: raw(": ping\n\n"))
                elif d.get("usage"):
                    u = _openai_usage(d["usage"])
                    w(lambda: raw("data: " + json.dumps({"id": cid, "object": "chat.completion.chunk",
                        "created": created, "model": MODEL_ID, "choices": [], "usage": u}) + "\n\n"))
                elif d.get("content"):
                    full[0] += d["content"]
                    c = d["content"]
                    w(lambda: send({"content": c}))
                    mirror.update(full[0])
            mirror.done(full[0])
            w(lambda: send({}, finish="stop"))
            w(lambda: raw("data: [DONE]\n\n"))
        except (BrokenPipeError, ConnectionResetError):
            gen.close()
        except Exception as e:
            traceback.print_exc()
            print(f"[owui-hermes] stream error: {e!r}", flush=True)   # never swallow silently
            note = (f"\n\n_⚠️ adapter hiccup ({type(e).__name__}: {str(e)[:160]}). "
                    f"Partial result is above — resend to continue._")
            full[0] += note
            try:
                w(lambda: send({"content": note}, finish="stop")); w(lambda: raw("data: [DONE]\n\n"))
            except Exception:
                pass
            try:
                mirror.done(full[0])   # preserve whatever was generated (FF)
            except Exception:
                pass
        finally:
            if mirror.on:  # FF4: run finished (or errored) → drop the durable record
                ff_clear("hermes", chat_id, mirror.mid)


def _ff_run(rec):  # FF4: re-issue an interrupted run; yield its text chunks
    for d in stream_turn(rec["cid"], rec["messages"]):
        if d.get("content"):
            yield d["content"]


if __name__ == "__main__":
    _login()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"[owui-hermes v2] streaming /api/ws adapter on 0.0.0.0:{PORT} "
          f"(auth={'on' if ADAPTER_KEY else 'off'})", flush=True)
    threading.Thread(target=lambda: ff_recover("hermes", _ff_run), daemon=True).start()
    srv.serve_forever()
