"""owui_mirror — native fire-and-forget: mirror a detached run's output into the
OWUI chat message. OWUI forwards X-OpenWebUI-Chat-Id/-Message-Id/-User-Jwt; POSTing
{content} to /api/v1/chats/{cid}/messages/{mid} both persists AND emits a live socket
update, so a run's progress + result land in the chat whether or not a browser watches.
No-op when the headers aren't present (e.g. a raw API call, not a real OWUI chat)."""
import glob, html, json, os, threading, time, urllib.request

OWUI_BASE = os.getenv("OWUI_BASE", "http://open-webui:8080").rstrip("/")
_MS = float(os.getenv("MIRROR_THROTTLE_MS", "1500")) / 1000.0


def tool_card(name, arguments, result, is_error=False):
    """Markup OWUI renders as a NATIVE tool card ("✓ View Result from <name>", expandable to
    args + result). Verified via screenshot that OWUI renders <details type="tool_calls"> from stored
    content (a *typed* details block — plain <details>/<think> render as literal tags)."""
    res = "" if result is None else str(result)
    if len(res) > 4000:
        res = res[:4000] + "\n…(truncated)"
    args = arguments if isinstance(arguments, str) else json.dumps(arguments or {})

    def esc(s):
        return html.escape("" if s is None else str(s), quote=True)

    return ('\n<details type="tool_calls" done="true" name="' + esc(name)
            + '" arguments="' + esc(args)
            + '" result="' + esc(json.dumps(res)) + '"'
            + (' error="true"' if is_error else "")
            + '>\n<summary>Tool Executed</summary>\n</details>\n')


class Mirror:
    def __init__(self, cid, mid, jwt):
        self.cid, self.mid, self.jwt = cid, mid, jwt
        self.on = bool(cid and mid and jwt)
        self._last = 0.0

    def _post(self, content):
        try:
            req = urllib.request.Request(
                f"{OWUI_BASE}/api/v1/chats/{self.cid}/messages/{self.mid}",
                data=json.dumps({"content": content}).encode(),
                headers={"Authorization": f"Bearer {self.jwt}", "Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass  # OWUI unreachable — best-effort

    def update(self, content):
        if not self.on:
            return
        now = time.monotonic()
        if now - self._last > _MS:
            self._last = now
            # Synchronous (was a daemon thread): update() and done() run on the SAME streaming thread,
            # so posts stay strictly ordered. The old threaded version could let a stale update land
            # AFTER done() and overwrite the final content → "last words missing" / "response gone on
            # reload". Throttled to _MS, so the inline HTTP cost is a small periodic hitch, not per-token.
            self._post(content)

    def done(self, content):
        if not self.on:
            return
        self._post(content)  # final authoritative write (always last, same thread)


# ── FF4: durable mirrored runs ──
# Persist each mirrored run so an adapter restart mid-run can re-issue it and still land the
# result in the OWUI message. Guarded: if FF_STATE_DIR is unavailable, everything is a no-op.
FF_DIR = os.getenv("FF_STATE_DIR", "/ffstate")
try:
    os.makedirs(FF_DIR, exist_ok=True)
    FF_ON = True
except Exception:
    FF_ON = False


def _ff_path(tag, cid, mid):
    return os.path.join(FF_DIR, f"{tag}__{(cid + '|' + mid).encode().hex()}.json")


def ff_write(tag, rec):
    if not FF_ON:
        return
    try:
        with open(_ff_path(tag, rec["cid"], rec["mid"]), "w") as f:
            json.dump(rec, f)
    except Exception:
        pass


def ff_clear(tag, cid, mid):
    if not FF_ON or not (cid and mid):
        return
    try:
        os.remove(_ff_path(tag, cid, mid))
    except Exception:
        pass


def ff_recover(tag, run):
    """On startup, re-issue runs interrupted by a restart. `run(rec)` yields text chunks;
    we mirror the cumulative content into the original OWUI message, then drop the record."""
    if not FF_ON:
        return
    for p in glob.glob(os.path.join(FF_DIR, f"{tag}__*.json")):
        try:
            rec = json.load(open(p))
        except Exception:
            try: os.remove(p)
            except Exception: pass
            continue
        m = Mirror(rec.get("cid"), rec.get("mid"), rec.get("jwt"))
        if not m.on:
            try: os.remove(p)
            except Exception: pass
            continue
        print(f"[{tag}] FF4: re-issuing interrupted run cid={rec.get('cid')}", flush=True)
        full = "_↻ Auto-resumed after an adapter restart._\n\n"
        try:
            m.done(full)
            for chunk in run(rec):
                full += chunk
                m.update(full)
            m.done(full)
        except Exception as e:
            try:
                m.done(full + f"\n\n_⚠️ Could not finish auto-resume ({str(e)[:120]}). Resend to retry._")
            except Exception:
                pass
        try: os.remove(p)
        except Exception: pass
