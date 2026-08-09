"""owui-terminal — net-new PTY backend implementing Open WebUI's NATIVE "open-terminal" server spec,
so OWUI's built-in terminal UI (XTerminal.svelte) works with ZERO fork / no UI changes.

Contract (from OWUI routers/terminals.py + XTerminal.svelte):
  POST /api/terminals            (Authorization: Bearer <key>, X-Session-Id: <chatId>) -> {"id": "<sid>"}
  WS   /api/terminals/{sid}      first client msg {"type":"auth","token":"<key>"}; then:
       client->server: raw BINARY = keystrokes; {"type":"resize","cols","rows"}; {"type":"ping"}
       server->client: raw BINARY = PTY output
Session keyed by X-Session-Id (chatId) => each chat keeps its own shell, survives reconnect (replay buffer).
Built fresh (PTY techniques from claude-monitor, not coupled). Env: PORT(7681) TERMINAL_TOKEN
TERMINAL_SHELL(/bin/bash) TERMINAL_CWD REPLAY_BYTES(262144) IDLE_TTL(3600)
"""
import asyncio, json, os, pty, fcntl, termios, struct, signal, secrets, time
import shutil, io, zipfile, mimetypes, hmac, subprocess
from aiohttp import web, WSMsgType

HOME = os.path.expanduser("~")

# ── friendly session labels (for cross-machine terminal discovery in the hub sidebar) ──
# list_sessions reports each shell's cwd + foreground command so the UI can show "claude · restock"
# instead of "Shell 6c72". Cross-platform: /proc on Linux, lsof/ps on macOS. Cached ~3s per session.
_SHELLS = {"bash", "sh", "zsh", "fish", "login", "tmux", "screen", "dash"}


def _proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")                      # Linux
    except Exception:
        pass
    try:                                                            # macOS / BSD
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=2).stdout
        for ln in out.splitlines():
            if ln.startswith("n"):
                return ln[1:]
    except Exception:
        pass
    return None


def _fg_cmd(fd):
    """The foreground command running in the PTY (what the user is actually doing), or None if it's just the shell."""
    try:
        pgrp = os.tcgetpgrp(fd)
        out = subprocess.run(["ps", "-o", "comm=", "-p", str(pgrp)],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        cmd = os.path.basename(out.split()[0]) if out else ""
        cmd = cmd.lstrip("-")
        if cmd and cmd not in _SHELLS:
            return cmd
    except Exception:
        pass
    return None


def _session_meta(s):
    now = time.monotonic()
    if getattr(s, "_meta_ts", 0) and now - s._meta_ts < 3:
        return s._meta
    cwd = _proc_cwd(s.pid)
    if cwd and cwd.startswith(HOME):
        cwd = "~" + cwd[len(HOME):]                                 # collapse $HOME → ~
    m = {"cwd": cwd, "cmd": _fg_cmd(s.fd)}
    s._meta, s._meta_ts = m, now
    return m

# SECURITY: confine the HTTP file API to a root (default HOME). The interactive shell stays unrestricted
# (that's its purpose, and it's token-gated); the file API is the high-blast-radius surface (single-call
# read/write/delete of arbitrary paths), so it is jailed. Set TERMINAL_FS_ROOT=/ to disable on trusted setups.
FS_ROOT = os.path.realpath(os.environ.get("TERMINAL_FS_ROOT", HOME))


def _safe(p):
    """Resolve a user-supplied path and confine it under FS_ROOT. Raises PermissionError if it escapes."""
    full = os.path.realpath(os.path.expanduser(p if p else FS_ROOT))
    if full != FS_ROOT and not full.startswith(FS_ROOT + os.sep):
        raise PermissionError("path outside the allowed root")
    return full

PORT = int(os.environ.get("PORT", "7681"))
TOKEN = os.environ.get("TERMINAL_TOKEN", "")
SHELL = os.environ.get("TERMINAL_SHELL", "/bin/bash")
CWD = os.environ.get("TERMINAL_CWD", os.path.expanduser("~"))
REPLAY = int(os.environ.get("REPLAY_BYTES", str(256 * 1024)))
IDLE_TTL = int(os.environ.get("IDLE_TTL", "3600"))

sessions = {}   # sid -> Session


class Session:
    def __init__(self, sid, loop):
        self.sid, self.loop = sid, loop
        self.clients = set()
        self.buffer = bytearray()
        self.last_active = time.monotonic()
        self.alive = True
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(CWD)
            except Exception:
                pass
            env = dict(os.environ, TERM="xterm-256color")
            env.pop("TERMINAL_TOKEN", None)
            os.execvpe(SHELL, [SHELL, "-l"], env)
            os._exit(1)
        self.pid, self.fd = pid, fd
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        loop.add_reader(fd, self._on_pty)

    def _on_pty(self):
        try:
            data = os.read(self.fd, 65536)
        except (OSError, BlockingIOError):
            return
        if not data:
            self.close(exited=True)
            return
        self.buffer += data
        if len(self.buffer) > REPLAY:
            del self.buffer[:-REPLAY]
        self.last_active = time.monotonic()
        for ws in list(self.clients):
            self.loop.create_task(self._send(ws, data))

    async def _send(self, ws, data):
        try:
            await ws.send_bytes(data)
        except Exception:
            self.clients.discard(ws)

    def write(self, data: bytes):
        try:
            os.write(self.fd, data)
            self.last_active = time.monotonic()
        except OSError:
            pass

    def resize(self, cols, rows):
        try:
            # clamp to the u16 range TIOCSWINSZ expects; struct.error (out-of-range) would otherwise
            # escape the handler and tear down the WS, letting a client drop its own session with one
            # crafted resize.
            c = max(1, min(65535, int(cols)))
            r = max(1, min(65535, int(rows)))
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))
        except (OSError, TypeError, ValueError, struct.error):
            pass

    def close(self, exited=False):
        if not self.alive:
            return
        self.alive = False
        try:
            self.loop.remove_reader(self.fd)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except Exception:
            pass
        if not exited:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except Exception:
                pass
        # Reap the child. A single waitpid(WNOHANG) right after SIGKILL usually returns (0,0) because
        # the kernel hasn't delivered the signal yet → the child lingers as a <defunct> zombie forever,
        # leaking a PID slot per session close. Retry briefly until it's actually reaped.
        try:
            for _ in range(50):                       # up to ~0.5s
                pid, _st = os.waitpid(self.pid, os.WNOHANG)
                if pid:
                    break
                time.sleep(0.01)
        except ChildProcessError:
            pass                                      # already reaped (e.g. SIGCHLD elsewhere)
        except Exception:
            pass
        sessions.pop(self.sid, None)


def _bearer_ok(request):
    # SECURITY (fail-CLOSED): empty TOKEN denies ALL requests. Constant-time compare.
    if not TOKEN:
        return False
    got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(got, TOKEN)


async def create_session(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    sid = request.headers.get("X-Session-Id") or secrets.token_hex(8)
    if sid not in sessions or not sessions[sid].alive:
        sessions[sid] = Session(sid, asyncio.get_running_loop())
    return web.json_response({"id": sid})


async def ws_terminal(request):
    sid = request.match_info["id"]
    ws = web.WebSocketResponse(max_msg_size=0, heartbeat=30)
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    authed = False   # SECURITY: fail-closed — the shell is never attached until the token is verified
    sess = None

    async def _auth_timeout():
        await asyncio.sleep(10)
        if not authed:
            try:
                await ws.close(code=4401)
            except Exception:
                pass
    timeout_task = loop.create_task(_auth_timeout())

    async def attach():
        nonlocal sess
        s = sessions.get(sid)
        if s is None or not s.alive:
            s = Session(sid, loop)
            sessions[sid] = s
        sess = s
        sess.clients.add(ws)
        if sess.buffer:
            try:
                await ws.send_bytes(bytes(sess.buffer))
            except Exception:
                pass

    if authed:
        await attach()
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if authed and sess:
                    sess.write(msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    d = json.loads(msg.data)
                except Exception:
                    continue
                t = d.get("type")
                if t == "auth":
                    # SECURITY (fail-CLOSED): require a token match; empty TOKEN never authenticates.
                    if not (TOKEN and hmac.compare_digest(str(d.get("token") or ""), TOKEN)):
                        await ws.close(code=4401)
                        return ws
                    if not authed:
                        authed = True
                        timeout_task.cancel()
                        await attach()
                elif not authed:
                    continue
                elif t == "resize":
                    sess.resize(d.get("cols", 80), d.get("rows", 24))
                elif t == "input":
                    sess.write((d.get("data") or "").encode())
                elif t == "ping":
                    # keepalive only — do NOT reply with a text frame; XTerminal writes any text
                    # frame straight into the terminal (it would print `{"type":"pong"}` repeatedly).
                    pass
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        timeout_task.cancel()
        if sess:
            sess.clients.discard(ws)  # shell keeps running for reconnect
    return ws


async def list_sessions(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"sessions": [
        {"id": sid, "clients": len(s.clients), **_session_meta(s)} for sid, s in sessions.items() if s.alive
    ]})


async def delete_session(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    s = sessions.get(request.match_info["id"])
    if s:
        s.close()
    return web.json_response({"ok": True})


async def _reaper(app):
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for sid, s in list(sessions.items()):
            if not s.clients and (now - s.last_active) > IDLE_TTL:
                s.close()


async def _on_start(app):
    app["reaper"] = asyncio.create_task(_reaper(app))



# ── LOCAL PATCH: filesystem API for OWUI's native terminal workspace (FileNav) ──
def _cwd_for(sid):
    sess = sessions.get(sid)
    if sess and sess.alive:
        try:
            return os.readlink(f"/proc/{sess.pid}/cwd")
        except Exception:
            pass
    return HOME


async def fs_config(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"features": {"terminal": True}})


async def fs_cwd(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    cwd = _cwd_for(request.headers.get("X-Session-Id", ""))
    return web.json_response({"cwd": cwd, "home": HOME, "root": {"path": HOME, "label": "home"}})


async def fs_list(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        d = _safe(request.query.get("directory") or FS_ROOT)
    except PermissionError as ex:
        return web.json_response({"error": str(ex)}, status=403)
    entries = []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    entries.append({"name": e.name, "type": "directory" if e.is_dir() else "file",
                                    "size": st.st_size, "modified": int(st.st_mtime)})
                except Exception:
                    entries.append({"name": e.name, "type": "file"})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)
    entries.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
    return web.json_response({"entries": entries})


async def fs_read(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        p = _safe(request.query.get("path", ""))
        if os.path.getsize(p) > 2_000_000:
            return web.json_response({"error": "file too large to preview"}, status=413)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return web.json_response({"content": f.read()})
    except PermissionError as ex:                       # jail escape → 403, consistent with fs_list/view/mkdir
        return web.json_response({"error": str(ex)}, status=403)
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


async def fs_view(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        p = _safe(request.query.get("path", ""))
    except PermissionError as ex:
        return web.json_response({"error": str(ex)}, status=403)
    if not os.path.isfile(p):
        return web.json_response({"error": "not found"}, status=404)
    ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
    return web.FileResponse(p, headers={
        "Content-Disposition": f'attachment; filename="{os.path.basename(p)}"', "Content-Type": ctype})


async def fs_mkdir(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b = await request.json()
        os.makedirs(_safe(b.get("path", "")), exist_ok=True)
        return web.json_response({"ok": True})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


async def fs_delete(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b = await request.json()
        p = _safe(b.get("path", ""))
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        return web.json_response({"ok": True})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


async def fs_move(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b = await request.json()
        src = _safe(b.get("source") or b.get("from") or "")
        dst = _safe(b.get("destination") or b.get("to") or "")
        shutil.move(src, dst)
        return web.json_response({"ok": True})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


async def fs_upload(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        directory = _safe(request.query.get("directory", FS_ROOT))
        reader = await request.multipart()
        field = await reader.next()
        dest = _safe(os.path.join(directory, os.path.basename(field.filename or "upload.bin")))
        size = 0
        with open(dest, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk); size += len(chunk)
        return web.json_response({"path": dest, "size": size})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


async def fs_archive(request):
    if not _bearer_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b = await request.json()
        paths = [_safe(p) for p in (b.get("paths") or [])]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                if os.path.isdir(p):
                    for root, _, files in os.walk(p):
                        for fn in files:
                            fp = os.path.join(root, fn)
                            z.write(fp, os.path.relpath(fp, os.path.dirname(p)))
                elif os.path.isfile(p):
                    z.write(p, os.path.basename(p))
        buf.seek(0)
        return web.Response(body=buf.read(), headers={
            "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="download.zip"'})
    except Exception as ex:
        return web.json_response({"error": str(ex)}, status=400)


def make_app():
    app = web.Application()
    app.router.add_post("/api/terminals", create_session)
    app.router.add_get("/api/terminals/sessions", list_sessions)   # literal before {id}
    app.router.add_delete("/api/terminals/{id}", delete_session)
    app.router.add_get("/api/terminals/{id}", ws_terminal)
    app.router.add_get("/api/config", fs_config)
    app.router.add_get("/files/cwd", fs_cwd)
    app.router.add_get("/files/list", fs_list)
    app.router.add_get("/files/read", fs_read)
    app.router.add_get("/files/view", fs_view)
    app.router.add_post("/files/mkdir", fs_mkdir)
    app.router.add_post("/files/delete", fs_delete)
    app.router.add_post("/files/move", fs_move)
    app.router.add_post("/files/upload", fs_upload)
    app.router.add_post("/files/archive", fs_archive)
    app.router.add_get("/healthz", lambda r: web.json_response({"ok": True}))
    app.on_startup.append(_on_start)
    return app


if __name__ == "__main__":
    # SECURITY: refuse to start unauthenticated — this serves a real shell + a filesystem API.
    if not TOKEN:
        raise SystemExit("[owui-terminal] FATAL: TERMINAL_TOKEN is empty — refusing to start "
                         "(would be an unauthenticated public shell + file API).")
    print(f"[owui-terminal] open-terminal spec on 0.0.0.0:{PORT} shell={SHELL} cwd={CWD} "
          f"auth=on fs_root={FS_ROOT}", flush=True)
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None)
