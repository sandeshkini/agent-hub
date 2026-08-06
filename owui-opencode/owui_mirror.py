"""owui_mirror — native fire-and-forget: mirror a detached run's output into the
OWUI chat message. OWUI forwards X-OpenWebUI-Chat-Id/-Message-Id/-User-Jwt; POSTing
{content} to /api/v1/chats/{cid}/messages/{mid} both persists AND emits a live socket
update, so a run's progress + result land in the chat whether or not a browser watches.
No-op when the headers aren't present (e.g. a raw API call, not a real OWUI chat)."""
import json, os, threading, time, urllib.request

OWUI_BASE = os.getenv("OWUI_BASE", "http://open-webui:8080").rstrip("/")
_MS = float(os.getenv("MIRROR_THROTTLE_MS", "1500")) / 1000.0


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
            threading.Thread(target=self._post, args=(content,), daemon=True).start()

    def done(self, content):
        if not self.on:
            return
        self._post(content)  # final write, synchronous
