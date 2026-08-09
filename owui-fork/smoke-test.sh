#!/usr/bin/env bash
# smoke-test.sh <url> — verify an OWUI page actually RENDERS (not a white screen).
#
# WHY: a broken fork build still returns HTTP 200 (the SPA shell + backend are fine), so `curl`/health
# checks pass and you happily deploy a crash to prod. This loads the page in HEADLESS chromium, lets the
# JS run, dumps the post-render DOM, and asserts the Svelte app actually mounted (the sidebar renders).
# A component-level crash (e.g. an undeclared var in Sidebar.svelte) blanks the whole app → this catches it.
#
# Headless mode uses NO X display, so it does NOT disturb cua-driver on :1.
# Exit 0 = rendered OK · 1 = blank/crash · 2 = no chromium.
set -uo pipefail
URL="${1:?usage: smoke-test.sh <url>   (e.g. http://localhost:3000/)}"

CH="$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null \
      || command -v google-chrome 2>/dev/null || command -v google-chrome-stable 2>/dev/null \
      || { [ -x /snap/bin/chromium ] && echo /snap/bin/chromium; })"
[ -n "${CH:-}" ] && [ -x "$CH" ] || { echo "smoke: no chromium/chrome found on PATH"; exit 2; }

OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT

for attempt in 1 2 3 4 5 6; do
  timeout 45 "$CH" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --hide-scrollbars --virtual-time-budget=9000 --dump-dom "$URL" 2>/dev/null > "$OUT" || true
  bytes=$(wc -c < "$OUT" 2>/dev/null || echo 0)
  # These markers only appear once the Svelte app (and specifically the sidebar) has mounted. If the
  # sidebar throws during render the whole app blanks and none of these will be present.
  if [ "$bytes" -ge 3000 ] && grep -qiE "New Chat|MACHINE|chat-input|sidebar-new-chat" "$OUT"; then
    echo "smoke: OK — $URL rendered (${bytes} bytes, app mounted)"; exit 0
  fi
  echo "smoke: attempt $attempt/6 not ready yet (${bytes} bytes)…"; sleep 4
done

echo "smoke: FAIL — $URL did not render (blank/crash). Rendered ${bytes} bytes."
echo "       (a white screen = the Svelte app threw during mount; check the changed component)"
exit 1
